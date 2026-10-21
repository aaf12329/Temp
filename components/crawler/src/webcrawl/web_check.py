# -*- coding: utf-8 -*-
r"""网站可爬性判定 —— 合规（能不能爬）+ 渲染方式（怎么爬才拿得到内容）两块纯逻辑。

本文件 = 合规判定 + 渲染判定，两者都是纯技术，与具体产业无关，可整体复用到别的爬虫项目。
只做判定、不做采集：不存快照、不写文件、不碰业务数据；唯一的 I/O 走共用层 net。

================================ 函数结构树 ================================
① robots 纯逻辑（只吃字符串，不碰网络 / 文件，可离线单测）
    build_robots_url(url)            由页面 URL 推出 /robots.txt 地址
    _pattern_to_regex(pattern)       把 robots 通配模式（* / $）编译成正则
    _ua_matches(user_agent, agents)  某 UA 是否命中某个规则组
    parse_robots(text)               robots.txt 文本 → 规则组 + sitemap
    applicable_groups(parsed, ua)    取出适用于本 UA 的规则组
    summarize_rules(parsed, ua)      → 该站对我们禁止/允许的清单 + crawl_delay
    is_allowed(path, parsed, ua)     按 RFC 9309 最长匹配判定某路径
    judge_path_of(url)               取 URL 的路径部分（含 query）

② 渲染判定纯逻辑（只吃 HTML 字符串，不联网、不读文件）
    _noscript_hint(html)             <noscript> 里是否有"请开启 JavaScript"类提示
    _meta_generator(html)            <meta name="generator"> 的 content（CMS 特征）
    classify_render(html)            页面类型判定：static / dynamic / unknown + 命中证据

③ 网络层（HTTP 只走共用层 net，本文件不直接 import urllib 的请求设施）
    fetch_robots(url, ...)           只抓 robots.txt，返回全文
    detect_render_mode(url, ...)     抓目标页面 → classify_render；失败不抛异常
    check_robots(url, path, ...)     抓取 + 解析 + 判定 + 页面类型判定，爬虫直接调这个

================================ 调用关系总览 ==============================
check_robots ──▶ fetch_robots ──▶ build_robots_url
             │                └─▶ net.fetch                （共用层，唯一 HTTP 出口）
             ├─▶ parse_robots / summarize_rules / is_allowed
             └─▶ detect_render_mode ──▶ net.fetch          （目标页面，仅 allowed 非 False 时）
                                     └─▶ classify_render ──▶ html_utils.to_text
                                                         ├─▶ _noscript_hint
                                                         └─▶ _meta_generator
             （调用方已给 page_html 时直接 classify_render，不再发请求）

被谁用：`src\crawl\Crawl_Page.py`（采集层合规闸门）与 `Robots_detect\Robots_Detect.py`
（CLI 壳，只保留 print_* / main / _self_test）都调用这里的 `check_robots`。

================================ 数据流单链路 ==============================
合规：URL → build_robots_url → net.fetch → parse_robots
      → summarize_rules / is_allowed → {allowed, rules}
渲染：页面 HTML → html_utils.to_text / classify_render → {mode, confidence, signals}
两条链路在 check_robots 汇合；allowed is False 时**不进入**渲染链路（合规红线）。

================================ 对外返回契约 ============================
fetch_robots(url) -> {"site", "robots_url", "status", "found", "ok", "content", "error"}
    # found=False 表示站点没有 robots.txt（404/410），不是错误

classify_render(html) -> {
    "mode": "static" | "dynamic" | "unknown",
    "confidence": float,                 # 0.0~1.0
    "reason": str,                       # 一句话中文结论
    "text_len": int,                     # 可见正文长度
    "script_bytes": int,                 # <script> 内联内容总字节（UTF-8）
    "script_ratio": float,               # script_bytes / len(html)（分子分母都用字节）
    "signals": {                         # 命中的证据；没命中为 None / False
        "spa_mount": str|None,           # SPA 挂载点原文，如 'id="app"'
        "ssr_state": str|None,           # 内嵌状态名，如 "__NEXT_DATA__"
        "noscript_hint": bool,
        "js_redirect": bool,             # document.cookie / location 跳转等
        "meta_generator": str|None,      # CMS 特征（只作证据，不定性）
    },
}

check_robots(url, path="/", ..., probe_render=True, page_html=None) -> {
    "site": str, "robots_url": str, "path": str,
    "status": int|None,                  # HTTP 状态码
    "found": bool,                       # 站点是否有 robots.txt（404 → False）
    "error": str|None,
    "content": str,                      # robots.txt 全文
    "rules": {                           # ← 站点对我们禁止/允许的东西
        "disallow": [str, ...],          # 禁止的路径模式
        "allow": [str, ...],             # 允许的路径模式（覆盖用）
        "crawl_delay": float|None,       # 站点要求的抓取间隔
        "user_agent_groups": int,        # 适用了几组规则
    },
    "allowed": bool|None,                # 目标路径是否放行；None = 抓不到、无法判定
    "rule": str|None,                    # 命中的那条规则原文，如 "Disallow: /admin"
    "render": {                          # 页面类型判定；未检测时为 None（见下）
        "mode": "static" | "dynamic" | "unknown",
        "confidence": float,             # 0.0~1.0
        "reason": str,                   # 一句话中文结论
        "text_len": int,                 # 可见正文长度
        "script_bytes": int,             # <script> 内联内容总字节（UTF-8）
        "script_ratio": float,           # script_bytes / len(html)
        "signals": {                     # 命中的证据；没命中为 None / False
            "spa_mount": str|None,
            "ssr_state": str|None,
            "noscript_hint": bool,
            "js_redirect": bool,
            "meta_generator": str|None,
        },
        # 下面 3 个键只有 detect_render_mode 真的取过页面时才有
        # （page_html 直判时是 classify_render 原样结果，用 .get 取）
        "page_url": str,                 # 跟随重定向后的最终地址
        "http_status": int|None,
        "error": str|None,               # 取页面失败时有值
    } | None,                            # None = 未检测（见下）

    # render 为 None（未检测）的两种情况，务必分清：
    #   ① probe_render=False    —— 调用方主动关闭探测
    #   ② allowed is False      —— robots 明确禁止：**绝不取页面**（合规红线）
    # 另：调用方给了 page_html 且 probe_render=True —— 直接分类，不再发请求
}

detect_render_mode(url, ...) -> classify_render(...) 的全部键 + "page_url" +
    "http_status" + "error"；取页面失败时 mode="unknown"（不抛异常）

字段一旦发布不改名；要扩展只加新字段。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from webcrawl import html_utils, net
except ImportError:                     # 直接运行本文件（人工试跑）时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from webcrawl import html_utils, net

# ---------------------------------------------------------------- 常量区
DEFAULT_PATH = "/"                      # 要判定的目标路径，默认站点根

# —— 页面类型判定的阈值（改这里就能调松紧，别在函数里写魔法数）
VISIBLE_TEXT_MIN_STATIC = 500           # 可见正文 >= 500 字 → 静态（强证据）
VISIBLE_TEXT_WEAK_STATIC = 200          # 200~500 字 → 静态（弱证据）
SCRIPT_RATIO_DYNAMIC = 0.5              # <script> 内容占总字节 >= 0.5 → 重脚本
DEFAULT_PROBE_RENDER = True             # check_robots 默认就取页面判静态/动态

# —— 页面类型判定的特征正则
SPACE_RE = re.compile(r"\s+")
SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.S | re.I)
# SPA 挂载点：id="app" / id="root" / id="__next" / <app-root> / ng-app
SPA_MOUNT_RE = re.compile(
    r"""id\s*=\s*(?:"(?:app|root|__next)"|'(?:app|root|__next)'|(?:app|root|__next)\b)"""
    r"""|<app-root\b|\bng-app\b""", re.I)
# 内嵌 SSR 状态：数据已经在 HTML 的 JSON 里，内容不一定非得执行脚本
SSR_STATE_RE = re.compile(
    r"(__NEXT_DATA__|__INITIAL_STATE__|__NUXT__|window\.__PRELOADED_STATE__)")
# <noscript> 里的提示：含 javascript 且（enable / disabled / 开启 / 需要）
NOSCRIPT_BLOCK_RE = re.compile(r"<noscript\b[^>]*>(.*?)</noscript\s*>", re.S | re.I)
NOSCRIPT_KEYWORDS = ("javascript", "enable", "disabled", "开启", "需要")
# JS 跳转 / 反爬
JS_REDIRECT_RE = re.compile(
    r"document\.cookie|location\.href\s*=|location\.replace\s*\(|window\.location\s*=",
    re.I)
# <meta name="generator" content="...">：静态 CMS 特征，只作证据、不定性
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
META_NAME_RE = re.compile(r"""\bname\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
META_CONTENT_RE = re.compile(r"""\bcontent\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)


# ============================================ ① robots 纯逻辑（不碰 I/O）
def build_robots_url(url: str) -> str:
    """由任意页面 URL 推出站点 robots.txt 地址。"""
    parts = urlsplit(url if "://" in url else "https://" + url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/robots.txt", "", ""))


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """把 robots 的通配模式编译成正则：`*` = 任意串，结尾 `$` = 锚定结束。"""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile("^" + regex + ("$" if anchored else ""))


def _ua_matches(user_agent: str, agents: list[str]) -> bool:
    """某一 UA 是否命中该规则组；`*` 匹配任何人，其余按子串不区分大小写比较。"""
    ua = user_agent.lower()
    for agent in agents:
        token = agent.strip().lower()
        if token == "*" or (token and token in ua):
            return True
    return False


def parse_robots(text: str) -> dict[str, Any]:
    """把 robots.txt 文本解析成规则组；语法错误只跳过该行，不抛异常。"""
    groups: list[dict[str, Any]] = []
    sitemaps: list[str] = []
    current: dict[str, Any] | None = None
    last_was_agent = False               # 连续的 User-agent 行属于同一组

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            if current is None or not last_was_agent:
                current = {"agents": [], "rules": [], "crawl_delay": None}
                groups.append(current)
            current["agents"].append(value)
            last_was_agent = True
            continue

        if field == "sitemap":
            if value:
                sitemaps.append(value)
            last_was_agent = False
            continue

        if current is None:
            last_was_agent = False
            continue

        last_was_agent = False
        if field in ("allow", "disallow"):
            if not value:                 # 空的 `Disallow:` = 全部放行
                continue
            current["rules"].append({"allow": field == "allow", "path": value})
        elif field == "crawl-delay":
            try:
                current["crawl_delay"] = float(value)
            except ValueError:
                pass

    return {"groups": groups, "sitemaps": sitemaps}


def applicable_groups(parsed: dict[str, Any], user_agent: str) -> list[dict[str, Any]]:
    """取出适用于本 UA 的规则组；没有专属组就退回 `*` 组。"""
    groups = parsed.get("groups") or []
    matched = [g for g in groups if _ua_matches(user_agent, g["agents"])]
    if matched:
        return matched
    return [g for g in groups if any(a.strip() == "*" for a in g["agents"])]


def summarize_rules(parsed: dict[str, Any],
                    user_agent: str = net.DEFAULT_USER_AGENT) -> dict[str, Any]:
    """列出该站对我们**禁止什么、允许什么** —— 就是爬虫需要的"黑名单"。"""
    groups = applicable_groups(parsed, user_agent)
    disallow: list[str] = []
    allow: list[str] = []
    crawl_delay: float | None = None
    for group in groups:
        for rule in group["rules"]:
            bucket = allow if rule["allow"] else disallow
            if rule["path"] not in bucket:
                bucket.append(rule["path"])
        if crawl_delay is None and group["crawl_delay"]:
            crawl_delay = group["crawl_delay"]
    return {"disallow": disallow, "allow": allow, "crawl_delay": crawl_delay,
            "user_agent_groups": len(groups)}


def is_allowed(path: str, parsed: dict[str, Any],
               user_agent: str = net.DEFAULT_USER_AGENT) -> dict[str, Any]:
    """按 RFC 9309 判定某路径：最长匹配胜出，同长度 Allow 优先。"""
    groups = applicable_groups(parsed, user_agent)
    best: dict[str, Any] | None = None
    best_key: tuple[int, bool] = (-1, False)
    for group in groups:
        for rule in group["rules"]:
            if _pattern_to_regex(rule["path"]).match(path):
                key = (len(rule["path"]), rule["allow"])   # 长优先，平手 Allow 胜
                if key > best_key:
                    best_key, best = key, rule

    crawl_delay = next((g["crawl_delay"] for g in groups if g["crawl_delay"]), None)
    if best is None:
        return {"allowed": True, "rule": None, "crawl_delay": crawl_delay}
    return {"allowed": best["allow"],
            "rule": ("Allow: " if best["allow"] else "Disallow: ") + best["path"],
            "crawl_delay": crawl_delay}


def judge_path_of(url: str) -> str:
    """取 URL 的路径部分（含 query），用来和 robots 规则比对。"""
    parts = urlsplit(url if "://" in url else "https://" + url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


# ==================================== ② 页面类型判定（纯逻辑，不碰网络 / 文件）
# 可见正文统一由共用层 html_utils.to_text 提供（跳过 script / style / noscript /
# template，空白归一化）。本文件不再自己实现 HTML 解析：抽取逻辑只留一份，
# 避免两处实现随修 bug 漂移（《工程架构规范》§01 共用层复用）。
def _noscript_hint(html: str) -> bool:
    """<noscript> 里是否写着"请开启 / 需要 JavaScript"这类提示（典型 CSR 空壳特征）。"""
    for block in NOSCRIPT_BLOCK_RE.findall(html):
        lowered = block.lower()
        if "javascript" in lowered and any(k in lowered for k in NOSCRIPT_KEYWORDS):
            return True
    return False


def _meta_generator(html: str) -> str | None:
    """取 <meta name="generator" content="..."> 的 content（CMS 特征，只作证据、不定性）。"""
    for tag in META_TAG_RE.findall(html):
        name = META_NAME_RE.search(tag)
        if not name:
            continue
        label = (name.group(1) or name.group(2) or name.group(3) or "").strip().lower()
        if label != "generator":
            continue
        content = META_CONTENT_RE.search(tag)
        if not content:
            return None
        value = (content.group(1) or content.group(2) or content.group(3) or "").strip()
        return value or None
    return None


def classify_render(html: str) -> dict[str, Any]:
    """判定一页 HTML 是「静态直出」还是「靠 JS 动态渲染」，不联网、不读文件。

    判定顺序（**按顺序短路**，第一条命中的就是结论）：
        ① 页面为空 → unknown
        ② 可见正文 < 200 字：命中任一动态特征
           （JS 跳转/反爬、SPA 挂载点、noscript 提示、内嵌 SSR 状态、脚本占比 >= 0.5）
           → dynamic；一条都没有 → unknown（正文少但没有证据，不硬判）
        ③ 可见正文 >= 500 字 → static（正文已经直出到 HTML 里，脚本再多也不用渲染）
        ④ 可见正文 200~500 字：命中强 CSR 特征（SPA 挂载点 / noscript / 内嵌 SSR）
           → dynamic（0.6，偏保守）；否则 static（0.65，弱证据）

    注意 **正文长度优先于脚本特征**：正文已经够长就说明抓静态 HTML 拿得到内容，
    `document.cookie` / `window.location=` 这类工具函数在正常文章页里也大量存在
    （真踩过：新浪财经、腾讯新闻正文 2500~6400 字，却被 js_redirect 误判成动态站）。
    """
    html = html or ""
    text = html_utils.to_text(html)
    text_len = len(text)
    script_bytes = sum(len(match.group(1).encode("utf-8"))
                       for match in SCRIPT_BLOCK_RE.finditer(html))
    # 分子分母统一用字节：用「字节 / 字符数」会让中文页的占比虚假放大（真踩过，偏判动态）
    html_bytes = len(html.encode("utf-8")) or 1
    script_ratio = script_bytes / html_bytes if html else 0.0
    spa = SPA_MOUNT_RE.search(html)
    ssr = SSR_STATE_RE.search(html)
    signals: dict[str, Any] = {
        "spa_mount": spa.group(0) if spa else None,
        "ssr_state": ssr.group(1) if ssr else None,
        "noscript_hint": _noscript_hint(html),
        "js_redirect": bool(JS_REDIRECT_RE.search(html)),
        "meta_generator": _meta_generator(html),
    }
    result: dict[str, Any] = {"mode": "unknown", "confidence": 0.0, "reason": "页面为空",
                              "text_len": text_len, "script_bytes": script_bytes,
                              "script_ratio": script_ratio, "signals": signals}

    # ① 空页面：没有内容可看
    if not html.strip():
        return result

    # ② 正文极少（< 200 字）才看动态特征：正文少 = 内容可能还没渲染出来
    evidence: list[str] = []
    if signals["spa_mount"]:
        evidence.append(f"SPA 挂载点 {signals['spa_mount']}")
    if signals["ssr_state"]:
        evidence.append(f"内嵌 {signals['ssr_state']}")
    if signals["noscript_hint"]:
        evidence.append("<noscript> 提示需开启 JS")
    if script_ratio >= SCRIPT_RATIO_DYNAMIC:
        evidence.append(f"脚本占比 {script_ratio:.2f}")

    if text_len < VISIBLE_TEXT_WEAK_STATIC:
        if signals["js_redirect"]:
            reason = "JS 跳转/反爬页：正文要执行脚本才拿得到"
            confidence = 0.9
        elif evidence:
            reason = f"正文极少（{text_len} 字）且靠 JS 渲染：命中 " + "、".join(evidence)
            confidence = 0.85
        else:
            # ⑤ 正文过少又没有动态特征：不硬判
            result.update({"confidence": 0.3,
                           "reason": f"可见正文只有 {text_len} 字，且没有明确的动态特征，无法判定"})
            return result
        if signals["ssr_state"]:
            # 给后续开发的关键提示：数据就在 HTML 里，不一定非要上浏览器
            reason += (f"；HTML 内嵌 {signals['ssr_state']}，内容在 JSON 里，"
                       "可绕过渲染直接取")
        result.update({"mode": "dynamic", "confidence": confidence, "reason": reason})
        return result

    # ③④ 正文够长：内容已经在 HTML 里，按静态处理（脚本再多也不改判）
    if text_len >= VISIBLE_TEXT_MIN_STATIC:
        result.update({"mode": "static", "confidence": 0.9,
                       "reason": f"可见正文 {text_len} 字，静态 HTML 直出，不需要渲染"})
        return result

    # 200~500 字：正文偏少但已有正文；若命中强 CSR 特征仍提示可能要渲染
    strong_csr = [e for e in evidence if not e.startswith("脚本占比")]
    if strong_csr:
        result.update({"mode": "dynamic", "confidence": 0.6,
                       "reason": f"正文偏少（{text_len} 字）且命中 " + "、".join(strong_csr)
                       + "，可能需要渲染"})
        return result
    result.update({"mode": "static", "confidence": 0.65,
                   "reason": f"可见正文 {text_len} 字，偏少但已有正文，按静态 HTML 处理"})
    return result


# ============================================== ③ 网络层（委托共用层 net）
def fetch_robots(url: str, timeout: float = net.DEFAULT_TIMEOUT,
                 retries: int = net.DEFAULT_RETRIES,
                 user_agent: str = net.DEFAULT_USER_AGENT) -> dict[str, Any]:
    """抓一个站点的 robots.txt，把全文放进返回值的 `content` 里。

    `found=False` 表示站点**没有** robots.txt（404/410）——这是正常情况，不是错误；
    真出错（超时、5xx、连接失败）时 `error` 才有值。
    """
    robots_url = build_robots_url(url)
    response = net.fetch(robots_url, timeout=timeout, retries=retries,
                         user_agent=user_agent)
    found = bool(response["found"])
    ok = bool(found and not response["error"])
    return {"site": url,
            "robots_url": robots_url,
            "status": response["status"],
            "found": found,
            "ok": ok,
            "content": response["text"] if ok else "",
            "error": response["error"]}


def detect_render_mode(url: str, timeout: float = net.DEFAULT_TIMEOUT,
                       retries: int = net.DEFAULT_RETRIES,
                       user_agent: str = net.DEFAULT_USER_AGENT) -> dict[str, Any]:
    """取一个页面，判定它是静态直出还是 JS 动态渲染；**取页面失败不抛异常**。

    返回 classify_render 的全部键 + `page_url` / `http_status` / `error`。
    失败时 `mode="unknown"`、`error` 有值、`reason` 写明失败原因。
    只给域名（"example.com/x"）也认，和 build_robots_url 一样自动补 https://。
    """
    target = url if "://" in url else "https://" + url
    response = net.fetch(target, timeout=timeout, retries=retries, user_agent=user_agent)
    render = classify_render(response.get("text") or "")
    render["page_url"] = response.get("final_url") or target
    render["http_status"] = response.get("status")
    render["error"] = response.get("error")
    if render["error"] or not response.get("found", True):
        detail = render["error"] or f"HTTP {render['http_status']}"
        render.update({"mode": "unknown", "confidence": 0.0,
                       "reason": f"页面抓取失败：{detail}，无法判定",
                       "error": detail})
    return render


def check_robots(url: str, path: str = DEFAULT_PATH,
                 timeout: float = net.DEFAULT_TIMEOUT,
                 retries: int = net.DEFAULT_RETRIES,
                 user_agent: str = net.DEFAULT_USER_AGENT,
                 probe_render: bool = DEFAULT_PROBE_RENDER,
                 page_html: str | None = None) -> dict[str, Any]:
    """抓 robots.txt + 解析 + 判定 + 页面类型判定，**爬虫直接调用这个函数**。

    返回值里 `rules.disallow` 就是"这站禁止我们碰的东西"，
    `allowed` 是"目标路径能不能爬"（None = 抓不到，无法判定，按不确定处理），
    `render` 是页面类型判定（静态 HTML / JS 动态渲染 / 未知）。

    页面类型判定的开关顺序（与合规相关，不要随意调）：
        ① probe_render=False          → render=None，一个页面请求都不发
        ② page_html 非 None           → 直接分类调用方给的 HTML，**不再发请求**
        ③ allowed is False            → render=None，**绝不取页面**（合规红线）
        ④ allowed 是 True / None      → detect_render_mode 取页面判定
    """
    fetched = fetch_robots(url, timeout=timeout, retries=retries, user_agent=user_agent)
    report: dict[str, Any] = {
        "site": url, "robots_url": fetched["robots_url"], "path": path,
        "status": fetched["status"], "found": bool(fetched["ok"]),
        "error": fetched["error"], "content": fetched["content"],
        "rules": {"disallow": [], "allow": [], "crawl_delay": None,
                  "user_agent_groups": 0},
        "allowed": None, "rule": None,
        "render": None,                  # 页面类型判定；None = 未检测
    }

    if fetched["ok"]:
        parsed = parse_robots(fetched["content"])
        report["rules"] = summarize_rules(parsed, user_agent)
        verdict = is_allowed(path, parsed, user_agent)
        report["allowed"] = verdict["allowed"]
        report["rule"] = verdict["rule"]
    elif fetched["found"] is False:
        # 站点没有 robots.txt（404/410）：按 RFC 9309 视为无限制
        report["allowed"] = True
        report["rule"] = f"robots.txt 不存在（HTTP {fetched['status']}），按 RFC 9309 视为无限制"
    else:
        # 网络失败：无法判定，按"不确定就放弃"处理
        report["allowed"] = None
        report["rule"] = "robots.txt 抓取失败，无法判定"

    # 页面类型判定（顺序见函数 docstring）
    if not probe_render:
        report["render"] = None
    elif page_html is not None:
        report["render"] = classify_render(page_html)      # 调用方已抓过，不再发请求
    elif report["allowed"] is False:
        # 合规红线：robots 明确禁止 → 连页面请求都不发，render 保持 None
        report["render"] = None
    else:
        report["render"] = detect_render_mode(url, timeout=timeout, retries=retries,
                                              user_agent=user_agent)
    return report
