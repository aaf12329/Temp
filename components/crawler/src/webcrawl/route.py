# -*- coding: utf-8 -*-
"""抓取选档 —— 按 profile 的 verdict 决定这次用哪个抓取工具（纯逻辑，不碰网络 / 文件）。

本文件是「探测判定 → 选工具」链路的第二环：判定层（probe.py）产出 profile 并落盘，
本模块只读 profile 的 `verdict` 字段，返回一个决策 dict 交给入口层 main.py 执行。
不含任何产业名词，换一个调查项目也能原样复用；正常调用时零 I/O、零网络、零 print
（只有 `_self_test()` 在自测入口里打印）。

设计依据是 `docs\\抓取策略表.md`（唯一契约）：
    §2 的 8 个 verdict、§4 的 7 个 tool 与依赖、§5 的接口、§6 的编码约束。

============================== 函数结构树 ==============================
① 常量（契约 §2 / §4 的枚举与依赖表）
    VERDICTS          8 个 verdict（判定层产出的全集）
    TOOLS             7 个 tool（工具箱的全集）
    TOOL_REQUIRES     tool → 第三方依赖包名；None = 零依赖、现在就能用
    VERDICT_TOOL      verdict → tool 的选档表（契约 §4）
    FALLBACK_OF       tool 不可用 / 失败后的下一档（降级链）
    REASON_OF         verdict → 一句话中文理由

② 纯逻辑（只吃 dict / str，不碰网络、不读写文件，可离线单测）
    choose(profile)          核心：profile → {tool, available, reason, fallback, requires}
    group_by_tool(profiles)  批量：按 tool 分组，值为 URL 列表
    _verdict_of(profile)     安全取 verdict（缺键 / None / 非字符串 → 空串）
    _decision(tool, reason)  组装返回 dict，统一从 TOOL_REQUIRES 推 available
    _iter_profiles(profiles) 统一 list / dict[url, profile] / 单个 profile 三种输入
    _url_of(profile, hint)   安全取 URL（profile 内的 url 优先，其次是分组键）

③ 自测
    _self_test()             零网络：8 个 verdict 各跑一遍 + 残缺 profile 不崩，打印对照表

============================== 调用关系总览 ==============================
main.py ──▶ choose(profile) ──┬─▶ _verdict_of        （安全取判定值）
        │                     └─▶ _decision ──▶ TOOL_REQUIRES（推 available / requires）
        └─▶ group_by_tool ──▶ _iter_profiles / _url_of / choose（每条复用同一套选档规则）

⭐ 本模块是纯逻辑层：不 import net / io_utils / page，也从不读写文件。
   调用者只有入口层 main.py（唯一调用源）与 tests\\test_logic.py。

============================== 数据流单链路 ==============================
profile(dict) → verdict 字符串 → VERDICT_TOOL 查表 → tool
             → TOOL_REQUIRES 查依赖 → {available, requires} → 决策 dict → main.py 执行

============================== 对外返回契约 ============================
choose(profile) -> {
    "tool":       str,        # TOOLS 之一
    "available":  bool,       # 只由 TOOL_REQUIRES[tool] 是否为 None 决定（零依赖即可用）
    "reason":     str,        # 一句话中文，说明为什么选它、降级到哪
    "fallback":   str | None, # 当前档不可用 / 失败后改用哪个 tool
    "requires":   str | None, # 缺的第三方包名，如 "curl_cffi"
}

# 残缺 profile 一律不抛异常：profile=None / 缺 verdict / verdict 为 None /
# verdict 非字符串 / verdict 是未知字符串 → 统一走保守兜底：
# {"tool": "skip", "available": True, "reason": "未知判定值，保守跳过",
#  "fallback": None, "requires": None}

group_by_tool(profiles) -> dict[str, list[str]]
    # 键 = tool，值 = 该组的 URL 列表；空组不出现（没有 URL 的条目直接跳过）
    # profiles 兼容三种输入：list[profile] / dict[url, profile] / 单个 profile

字段一旦发布不改名；要扩展只加新字段。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

# ---------------------------------------------------------------- 常量区
# 契约 §2：8 个 verdict，不多不少（顺序按契约表格，不是优先级顺序）
VERDICTS: tuple[str, ...] = (
    "robots_disallow",   # robots 明确禁止
    "pdf",               # 非网页，直接当文件存
    "static",            # 静态 HTML，正文够用
    "thin_static",       # 静态但正文太少
    "dynamic",           # 需 JS 渲染才出正文
    "blocked",           # 403 / 503 / 风控拦截
    "login_wall",        # 登录墙
    "error",             # 探测本身失败
)

# 契约 §4：7 个抓取档位（顺序同契约表格）
TOOLS: tuple[str, ...] = (
    "skip",
    "pdf",
    "static",
    "static_retry",
    "impersonate",
    "browser",
    "skip_retry",
)

# 契约 §4：tool 需要的第三方依赖；None = 零依赖，现在就能跑。
# browser 的依赖按契约原文写 Patchright（pip 包名是 patchright）。
TOOL_REQUIRES: dict[str, str | None] = {
    "skip": None,
    "pdf": None,
    "static": None,
    "static_retry": None,
    "impersonate": "curl_cffi",
    "browser": "patchright",
    "skip_retry": None,
}

# 契约 §4：verdict → tool 的选档表（本模块的判定核心）
VERDICT_TOOL: dict[str, str] = {
    "robots_disallow": "skip",
    "pdf": "pdf",
    "static": "static",
    "thin_static": "static_retry",
    "dynamic": "browser",
    "blocked": "impersonate",
    "login_wall": "skip",
    "error": "skip_retry",
}

# 当前档不可用 / 失败后的下一档；None = 没有更进一步的档位。
# 降级链按"下一档"写，而不是直接写终态：
#   blocked → impersonate（curl_cffi）→ browser（patchright）→ 实际落到 skip
# 三档都不可用时由 main.py 沿 fallback 走到 skip；完整链路见 REASON_OF["blocked"]。
FALLBACK_OF: dict[str, str | None] = {
    "skip": None,
    "pdf": None,
    "static": None,
    "static_retry": "skip",
    "impersonate": "browser",
    "browser": "skip",
    "skip_retry": "skip",
}

# 一句话中文理由（给人和日志看）；必须把"为什么"和"降级到哪"说清
REASON_OF: dict[str, str] = {
    "robots_disallow": "robots 明确禁止，合规优先，直接跳过不抓",
    "pdf": "非网页（PDF），直接按文件归档，不走网页抓取链路",
    "static": "静态 HTML 且正文够用，零依赖直抓即可",
    "thin_static": "静态但正文偏少（< 300 字），换编码 / 精简路径再试一次（static_retry）",
    "dynamic": "页面靠 JS 渲染才出正文：需 browser（Patchright，未安装），实际落到 skip",
    "blocked": "风控拦截（403/503/CF）：目标档 impersonate（需 curl_cffi，未安装）；"
               "browser（Patchright）亦未安装，当前实际落到 skip",
    "login_wall": "登录墙，不绕行也不猜凭据，直接跳过",
    "error": "探测本身失败（超时 / 5xx / 解析错误），留给上层决定是否 skip_retry 重试",
}

# 未知判定值的统一兜底理由（契约外的输入，不能让它炸掉整批）
UNKNOWN_REASON = "未知判定值，保守跳过"


# ============================================ ① 纯逻辑（不碰网络、不读写文件）
def _verdict_of(profile: Any) -> str:
    """安全取 verdict：profile 非 dict、缺键、None、非字符串一律返回空串（按未知处理）。

    已知枚举都是小写，这里顺手 strip + lower，容忍 " Static " 这类脏数据；
    但不会把未知字符串"猜"成某个枚举。
    """
    if not isinstance(profile, dict):
        return ""
    value = profile.get("verdict")
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _decision(tool: str, reason: str) -> dict[str, Any]:
    """组装统一返回 dict：available / requires 只由 TOOL_REQUIRES 决定。"""
    requires = TOOL_REQUIRES.get(tool)
    return {
        "tool": tool,
        "available": requires is None,
        "reason": reason,
        "fallback": FALLBACK_OF.get(tool),
        "requires": requires,
    }


def choose(profile: Any) -> dict[str, Any]:
    """按一条 profile 选抓取档位，返回契约 §5 的 5 字段决策 dict。

    - 只认 profile["verdict"]；其余字段（url / signals / http_status 等）不参与选档。
    - 未知或缺失的 verdict：不猜、不抛异常，保守返回 skip。
    - available 只取决于该 tool 是否需要第三方依赖（impersonate / browser 当前一律 False）。
    """
    verdict = _verdict_of(profile)
    tool = VERDICT_TOOL.get(verdict)
    if tool is None:                       # 缺键 / None / 未知字符串 / 非 dict
        return _decision("skip", UNKNOWN_REASON)
    return _decision(tool, REASON_OF[verdict])


def _url_of(profile: Any, hint: Any = None) -> str:
    """安全取 URL：profile 内的 url 优先，其次用分组键（dict[url, profile] 时）。"""
    if isinstance(profile, dict):
        url = profile.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    if isinstance(hint, str) and hint.strip():
        return hint.strip()
    return ""


def _iter_profiles(profiles: Any) -> Iterator[tuple[Any, Any]]:
    """把三种输入统一成 (profile, url_hint) 序列：list / dict[url, profile] / 单个 profile。

    - dict 且所有值都是 dict → 视为 url → profile 映射；
    - dict 且值不全是 dict → 视为「单个 profile」；
    - None / str / 空输入 → 不产出任何条目。
    """
    if profiles is None or isinstance(profiles, str):
        return
    if isinstance(profiles, dict):
        if not profiles:
            return
        if all(isinstance(value, dict) for value in profiles.values()):
            for url, profile in profiles.items():
                yield profile, url
            return
        yield profiles, profiles.get("url")
        return
    try:
        iterator = iter(profiles)
    except TypeError:                      # 不可迭代的怪输入（如 int）→ 当作空
        return
    for item in iterator:
        yield item, None


def group_by_tool(profiles: Any) -> dict[str, list[str]]:
    """按 tool 分组，值为该组的 URL 列表；空组不出现，没有 URL 的条目跳过。

    输入顺序即输出顺序（便于入口层做稳定统计与批量执行）。
    """
    groups: dict[str, list[str]] = {}
    for profile, hint in _iter_profiles(profiles):
        url = _url_of(profile, hint)
        if not url:                        # 没有 URL 就没法执行，也不进统计
            continue
        tool = choose(profile)["tool"]
        groups.setdefault(tool, []).append(url)
    return groups


# ============================================ ② 自测（零网络，不碰真实数据）
def _self_test() -> None:
    """8 个 verdict 各造一个最小 profile 跑 choose，再验证残缺 profile 不崩并打印对照表。"""
    assert len(VERDICTS) == 8 and len(set(VERDICTS)) == 8
    assert len(TOOLS) == 7 and len(set(TOOLS)) == 7
    assert set(TOOL_REQUIRES) == set(TOOLS), set(TOOL_REQUIRES) ^ set(TOOLS)
    assert set(VERDICT_TOOL) == set(VERDICTS), set(VERDICT_TOOL) ^ set(VERDICTS)
    assert set(FALLBACK_OF) == set(TOOLS), set(FALLBACK_OF) ^ set(TOOLS)
    assert set(REASON_OF) == set(VERDICTS), set(REASON_OF) ^ set(VERDICTS)

    # ① 8 个 verdict 逐个选档：tool / available / requires / fallback 全部自洽
    for verdict in VERDICTS:
        decision = choose({"url": f"https://example.com/{verdict}", "verdict": verdict})
        want = VERDICT_TOOL[verdict]
        assert decision["tool"] == want, (verdict, decision)
        assert decision["available"] == (TOOL_REQUIRES[want] is None), (verdict, decision)
        assert decision["requires"] == TOOL_REQUIRES[want], (verdict, decision)
        assert isinstance(decision["reason"], str) and decision["reason"], (verdict, decision)
        assert decision["fallback"] is None or decision["fallback"] in TOOLS, (verdict, decision)

    # ② 两个 ❌ 档位当前一律不可用，降级链必须写清（fallback = 下一档，不是终态）
    blocked = choose({"verdict": "blocked"})
    assert blocked["tool"] == "impersonate" and blocked["available"] is False
    assert blocked["requires"] == "curl_cffi" and blocked["fallback"] == "browser"
    assert "patchright" in blocked["reason"].lower() and "skip" in blocked["reason"]
    dynamic = choose({"verdict": "dynamic"})
    assert dynamic["tool"] == "browser" and dynamic["available"] is False
    assert dynamic["requires"] == "patchright" and dynamic["fallback"] == "skip"
    assert "skip" in dynamic["reason"]

    # ②b 降级链可走通：blocked → impersonate → browser → skip，且不许成环
    seen = []
    tool = choose({"verdict": "blocked"})["tool"]
    while tool is not None:
        assert tool not in seen, f"降级链成环：{seen} → {tool}"
        seen.append(tool)
        tool = FALLBACK_OF.get(tool)
    assert seen == ["impersonate", "browser", "skip"], seen

    # ③ 残缺 profile：缺键 / None / 未知值 / 非字符串 / 非 dict 都不能抛异常
    broken_profiles = [
        ({}, "缺 verdict"),
        ({"verdict": None}, "verdict=None"),
        ({"verdict": "not_a_verdict"}, "未知字符串"),
        (None, "profile=None"),
        ({"verdict": 123}, "verdict 非字符串"),
    ]
    for profile, label in broken_profiles:
        decision = choose(profile)
        assert decision["tool"] == "skip" and decision["available"] is True, (label, decision)
        assert decision["reason"] == UNKNOWN_REASON, (label, decision)
        assert decision["fallback"] is None and decision["requires"] is None, (label, decision)

    # ④ 脏但可救的判定值：前后空白 / 大小写不应被当成未知
    assert choose({"verdict": "  Static  "})["tool"] == "static"

    # ⑤ 分组：list 与 dict（url → profile）两种输入都支持，顺序稳定
    samples = [
        {"url": "https://a.example/x", "verdict": "static"},
        {"url": "https://b.example/y", "verdict": "blocked"},
        {"url": "https://c.example/z", "verdict": "robots_disallow"},
        {"url": "https://d.example/w", "verdict": "static"},
    ]
    groups = group_by_tool(samples)
    assert groups["static"] == ["https://a.example/x", "https://d.example/w"], groups
    assert groups["impersonate"] == ["https://b.example/y"], groups
    assert groups["skip"] == ["https://c.example/z"], groups
    assert set(groups) == {"static", "impersonate", "skip"}, groups
    assert group_by_tool({}) == {} and group_by_tool([]) == {} and group_by_tool(None) == {}
    assert group_by_tool(samples[0]) == {"static": ["https://a.example/x"]}
    assert group_by_tool([{"verdict": "static"}]) == {}      # 没有 URL，不进统计

    # ⑥ 打印对照表（只在自测时输出；模块正常调用零 print）
    header = (f"{'verdict':<16} {'tool':<12} {'avail':<5} "
              f"{'requires':<11} {'fallback':<8} reason")
    print(header)
    print("-" * 96)
    for verdict in VERDICTS:
        decision = choose({"verdict": verdict})
        avail = "Y" if decision["available"] else "N"
        requires = str(decision["requires"] or "-")
        fallback = str(decision["fallback"] or "-")
        print(f"{verdict:<16} {decision['tool']:<12} {avail:<5} "
              f"{requires:<11} {fallback:<8} {decision['reason']}")
    print("-" * 96)
    print("[OK] 自测通过：8 个 verdict 选档 / 未知判定兜底 / 残缺 profile 不崩 / "
          "依赖可用性 / 分组（零网络）")


if __name__ == "__main__":
    _self_test()
