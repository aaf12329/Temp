# -*- coding: utf-8 -*-
"""站点访问策略 —— 人工声明「这个站允许怎么抓、多久抓一次」。

《爬虫标准API接口.md》v1.2 §6 / 《目标》§七.3：
「各来源允许的访问方式、频率和技术适配需求」需要被**声明**，而不是每次靠探测猜。

🔴 人工改过的条目，AI **不得自动覆盖**（契约明确要求）。

================================ 结构树 =================================
① 读写
    load_policies()            读 data/policy/site_policy.json，坏文件返回 {}
    save_policies(mapping)     整表写回
    get_policy(host)           单条查询
② 维护
    upsert_policy(policy, overwrite=False)  新增；已存在时默认不覆盖
    from_profile(profile)      由探测结果生成**建议**策略（调用方决定要不要落）
    write_from_profiles(...)   批量把探测画像落成策略（人工值永不被覆盖）
③ 生效
    min_interval(host, default) 返回该站最小请求间隔（人工值 与 robots crawl-delay 取最大）
④ 自测
    _self_test()               只写临时目录

================================ 调用关系 ================================
api.py ──▶ get_policy / min_interval / upsert_policy
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils

POLICY_KEYS = ("host", "allowed_paths", "disallowed_paths", "min_interval_s",
               "preferred_tool", "requires_login", "has_pdf", "note")


def load_policies(path: str | None = None) -> dict[str, dict[str, Any]]:
    target = path or ps.SITE_POLICY_JSON
    if not os.path.isfile(target):
        return {}
    try:
        data = io_utils.read_json(target)
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict):
        return {str(k).lower(): v for k, v in data.items() if isinstance(v, dict)}
    if isinstance(data, list):
        return {str(item.get("host", "")).lower(): item
                for item in data if isinstance(item, dict) and item.get("host")}
    return {}


def save_policies(mapping: dict[str, dict[str, Any]], path: str | None = None) -> str:
    target = path or ps.SITE_POLICY_JSON
    io_utils.write_json(target, mapping)
    return target


def get_policy(host: str, path: str | None = None) -> dict[str, Any] | None:
    return load_policies(path).get(host.lower())


def upsert_policy(policy: dict[str, Any], *, overwrite: bool = False,
                  path: str | None = None) -> dict[str, Any]:
    """写入一条策略。已存在且 overwrite=False 时**保持原样**并返回旧值。"""
    host = str(policy.get("host", "")).lower()
    if not host:
        raise ValueError("policy.host 不能为空")
    policies = load_policies(path)
    if host in policies and not overwrite:
        return policies[host]
    merged = {key: policy.get(key) for key in POLICY_KEYS}
    merged["host"] = host
    policies[host] = merged
    save_policies(policies, path)
    return merged


def from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """由一次探测结果生成**建议**策略（不落盘，也不覆盖人工值）。"""
    robots = profile.get("robots") or {}
    verdict = profile.get("verdict")
    return {
        "host": str(profile.get("site", "")).lower(),
        "allowed_paths": ["/"],
        "disallowed_paths": [],
        "min_interval_s": robots.get("crawl_delay") or 1.5,
        "preferred_tool": "skip" if verdict in ("robots_disallow", "login_wall") else "static",
        "requires_login": verdict == "login_wall",
        "has_pdf": profile.get("content_type") == "pdf",
        "note": "由 probe 自动生成，待人工确认",
    }


def min_interval(host: str, default: float = 1.5, path: str | None = None) -> float:
    """该站最小请求间隔：人工声明值 与 robots crawl-delay 取最大（保守优先）。"""
    policy = get_policy(host, path)
    declared = float(policy.get("min_interval_s") or 0) if policy else 0.0
    return max(default, declared)


def write_from_profiles(profiles: list[dict[str, Any]], *, overwrite: bool = False,
                        path: str | None = None) -> dict[str, Any]:
    """把一批探测画像落成站点策略（契约 §6：策略要能被**写入**，不是只在内存里算）。

    🔴 人工改过的条目默认不动：overwrite=False 时已存在的 host 一律保留原值，
       只在结果里标 "kept"，让人一眼看出哪些是机器生成的、哪些是人写的。
    同一 host 出现多次时只取第一条（首个探测结果说话，避免后探测的覆盖先探测的）。
    """
    written: list[str] = []
    kept: list[str] = []
    skipped: list[str] = []
    seen: dict[str, dict[str, Any]] = {}
    for profile in profiles or []:
        if not isinstance(profile, dict):
            continue
        suggestion = from_profile(profile)
        host = suggestion.get("host") or ""
        if not host:
            skipped.append(str(profile.get("url") or ""))
            continue
        if host in seen:
            continue                          # 同 host 只落一次
        seen[host] = suggestion
        before = get_policy(host, path)
        result = upsert_policy(suggestion, overwrite=overwrite, path=path)
        if before is not None and not overwrite:
            kept.append(host)
        elif before is not None:
            written.append(host)
        else:
            written.append(host)
        # 防御：upsert 返回的必须还是这条 host（防止调用方传入畸形策略）
        assert str(result.get("host")) == host, result
    return {"written": written, "kept": kept, "skipped": skipped, "total": len(seen)}


def _self_test() -> None:
    """零网络自检：写入不覆盖人工值、间隔取最大。"""
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "policy.json")

        first = upsert_policy({"host": "A.cn", "min_interval_s": 3.0,
                               "note": "人工"}, path=target)
        assert first["host"] == "a.cn" and first["min_interval_s"] == 3.0, first

        again = upsert_policy({"host": "a.cn", "min_interval_s": 1.0}, path=target)
        assert again["min_interval_s"] == 3.0, again          # 不覆盖人工值

        forced = upsert_policy({"host": "a.cn", "min_interval_s": 1.0},
                               overwrite=True, path=target)
        assert forced["min_interval_s"] == 1.0, forced

        assert min_interval("a.cn", default=0.5, path=target) == 1.0
        assert min_interval("b.cn", default=2.0, path=target) == 2.0

        suggestion = from_profile({"site": "b.cn", "verdict": "login_wall",
                                   "robots": {"crawl_delay": 5.0}, "content_type": "html"})
        assert suggestion["requires_login"] is True, suggestion
        assert suggestion["min_interval_s"] == 5.0, suggestion

        # 批量写入：人工值不被覆盖；同 host 只落一次；没有 host 的画像跳过
        batch = write_from_profiles([
            {"site": "A.cn", "verdict": "static", "robots": {"crawl_delay": 4.0}},
            {"site": "c.cn", "verdict": "static", "robots": {"crawl_delay": 2.0}},
            {"site": "c.cn", "verdict": "dynamic", "robots": {"crawl_delay": 9.0}},
            {"url": "https://d.cn/x", "verdict": "static"},
        ], path=target)
        assert batch["total"] == 2, batch                       # a.cn / c.cn
        assert batch["kept"] == ["a.cn"], batch                 # 人工值保留
        assert batch["written"] == ["c.cn"], batch              # 新 host 写入
        assert batch["skipped"] == ["https://d.cn/x"], batch    # 无 host 的画像跳过
        assert get_policy("a.cn", target)["min_interval_s"] == 1.0, "已有条目被覆盖了"
        assert get_policy("c.cn", target)["min_interval_s"] == 2.0, "同 host 应以首条为准"

    print("[OK] policy 自检通过：不覆盖人工值 / 间隔取最大 / 建议策略（只写临时目录）")


if __name__ == "__main__":
    _self_test()
