# -*- coding: utf-8 -*-
"""材料记录构造 —— 把一次抓取的散装结果，组装成契约要求的 `CrawlResult`。

《爬虫标准API接口.md》v1.2 §4.2：21+ 个字段，**任何一条记录（含跳过/失败）都必须字段齐全**，
否则下游要么 KeyError，要么拿 None 猜。本模块是记录形状的**唯一出处**。

================================ 结构树 =================================
① 标识
    record_id(job_id, url)     uuid5(NAMESPACE_URL, "job|url") —— 确定性，不是 uuid4
    url_hash10(url)            文件名用的 URL 哈希前 10 位
    site_of(url)               主机名
② 时间与版本
    now_iso()                  ISO8601 含时区
    code_version()             当前代码版本（git 短 hash，取不到给 unknown）
③ 组装
    new_record(...)            全部字段齐全的空记录骨架
    build_ok(...)              成功记录（含来源元数据、正文、表格、指纹）
    build_skipped(...)         跳过记录（robots / 登录墙 / 未允许渲染）
    build_failed(...)          失败记录
④ 自测
    _self_test()               零网络

================================ 调用关系 ================================
api.py ──▶ build_*() ──▶ store 落盘
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps
    from webcrawl import errors
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import errors

# ---------------------------------------------------------------- 常量区
RECORD_NAMESPACE = uuid.NAMESPACE_URL          # 契约 §6.1：三方必须用同一个 NS
_CODE_VERSION_CACHE: dict[str, str] = {}


def url_hash10(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]


def site_of(url: str) -> str:
    try:
        from urllib.parse import urlsplit
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def record_id(job_id: str, url: str) -> str:
    """契约 §6.1：同一 (job_id, url) 永远得到同一条 record_id。"""
    return str(uuid.uuid5(RECORD_NAMESPACE, f"{job_id}|{url}"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def code_version() -> str:
    """当前代码版本：git 短 hash；仓库不可用时返回 unknown（不许抛异常）。"""
    if "v" in _CODE_VERSION_CACHE:
        return _CODE_VERSION_CACHE["v"]
    version = "unknown"
    try:
        root = Path(ps.SOURCE_ROOT)
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(root),
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            version = out.stdout.strip()
    except Exception:
        pass
    _CODE_VERSION_CACHE["v"] = version
    return version


def _empty_signal() -> dict[str, Any]:
    return {"spa_mount": None, "noscript_hint": False, "login_form": False,
            "cookie_script": False, "cf_header": False, "script_ratio": None}


def _rel(path: str | None) -> str | None:
    """绝对路径 → 相对仓库根（契约 §5 要求相对路径）。"""
    if not path:
        return None
    try:
        root = Path(ps.PROJECT_ROOT)
        return Path(path).resolve().relative_to(root).as_posix()
    except Exception:
        return str(path).replace(os.sep, "/")


def _build_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """`meta` 骨架 + **调用方的扩展键原样保留**（契约 v1.3：depth / duplicate_of 等）。

    之前这里只取三个键，调用方塞进来的 depth / discovered_from / main_kind /
    tables_dropped / links_found / duplicate_of 会被**静默丢弃** —— 证据链就断了。
    """
    built: dict[str, Any] = {
        "requested_by": (meta or {}).get("requested_by"),
        "rule_version": (meta or {}).get("rule_version"),
        "code_version": code_version(),
    }
    for key, value in (meta or {}).items():
        if key not in built:
            built[key] = value
    return built


def new_record(*, job_id: str, url: str, project_id: str | None = None,
               meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """全部字段齐全的空记录。所有 build_* 都从这里出发，避免字段漏写。"""
    return {
        "record_id": record_id(job_id, url),
        "job_id": job_id,
        "project_id": project_id,
        "url": url,
        "final_url": None,
        "site": site_of(url),
        "fetched_at": now_iso(),
        "elapsed_ms": 0,
        "meta": _build_meta(meta),
        "source": {"publisher": None, "published_at": None, "canonical_url": None,
                   "author": None, "declaration": None, "is_reprint": None},
        "robots": {"found": False, "allowed": None, "rule": None, "crawl_delay": None},
        "verdict": "error",
        "tool_used": "skip",
        "tool_fallback": False,
        "status": "failed",
        "http_status": None,
        "content_type": None,
        "encoding": None,
        "bytes": 0,
        "title": None,
        "content_sha256": None,
        "storage": {"raw_path": None, "text_path": None, "tables_dir": None},
        "text_chars": 0,
        "text_sha256": None,
        "text_meta": {"paragraphs": 0, "chars": 0, "has_table": False, "table_count": 0},
        "paragraph_file": None,
        "paragraph_count": 0,
        "pdf": None,
        "tables": [],
        "error": None,
    }


def build_skipped(*, job_id: str, url: str, verdict: str, tool: str,
                  robots: dict[str, Any], error: dict[str, Any],
                  elapsed_ms: int = 0, project_id: str | None = None,
                  meta: dict[str, Any] | None = None) -> dict[str, Any]:
    record = new_record(job_id=job_id, url=url, project_id=project_id, meta=meta)
    record.update({
        "verdict": verdict, "tool_used": tool, "status": "skipped",
        "robots": robots, "error": error, "elapsed_ms": elapsed_ms,
    })
    return record


def build_failed(*, job_id: str, url: str, verdict: str, tool: str,
                 robots: dict[str, Any], error: dict[str, Any],
                 http_status: int | None = None,
                 elapsed_ms: int = 0, project_id: str | None = None,
                 meta: dict[str, Any] | None = None) -> dict[str, Any]:
    record = new_record(job_id=job_id, url=url, project_id=project_id, meta=meta)
    record.update({
        "verdict": verdict, "tool_used": tool, "status": "failed",
        "robots": robots, "error": error, "http_status": http_status,
        "elapsed_ms": elapsed_ms,
    })
    return record


def build_ok(*, job_id: str, url: str, final_url: str | None, raw: bytes,
             content_type: str, encoding: str | None, title: str | None,
             verdict: str, tool: str, tool_fallback: bool,
             robots: dict[str, Any], http_status: int | None,
             raw_path: str | None, text: str, paragraphs: list[dict[str, Any]],
             tables: list[dict[str, Any]], source: dict[str, Any] | None = None,
             text_path: str | None = None, tables_dir: str | None = None,
             paragraph_file: str | None = None, pdf: dict[str, Any] | None = None,
             elapsed_ms: int = 0, project_id: str | None = None,
             meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """成功记录。`tables` 里的每项应已带落盘路径。"""
    record = new_record(job_id=job_id, url=url, project_id=project_id, meta=meta)
    text_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    record.update({
        "final_url": final_url or url,
        "elapsed_ms": elapsed_ms,
        "source": source or record["source"],
        "robots": robots,
        "verdict": verdict,
        "tool_used": tool,
        "tool_fallback": tool_fallback,
        "status": "ok_pdf" if verdict == "pdf" else "ok",
        "http_status": http_status,
        "content_type": content_type,
        "encoding": encoding,
        "bytes": len(raw or b""),
        "title": title,
        "content_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
        "storage": {
            "raw_path": _rel(raw_path),
            "text_path": _rel(text_path),
            "tables_dir": _rel(tables_dir),
        },
        "text_chars": text_chars,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "text_meta": {
            "paragraphs": len(paragraphs),
            "chars": len(text),
            "has_table": bool(tables),
            "table_count": len(tables),
        },
        "paragraph_file": _rel(paragraph_file),
        "paragraph_count": len(paragraphs),
        "pdf": pdf,
        "tables": [
            {"index": t.get("index", i), "rows": t.get("rows", 0),
             "cols": t.get("cols", 0), "page": t.get("page"),
             "html_path": _rel(t.get("html_path")),
             "json_path": _rel(t.get("json_path")),
             "csv_path": _rel(t.get("csv_path"))}
            for i, t in enumerate(tables)
        ],
        "error": None,
    })
    return record


def _self_test() -> None:
    """零网络自检：主键确定性、字段齐全、指纹与元信息。"""
    a = record_id("j1", "https://a.cn/x")
    b = record_id("j1", "https://a.cn/x")
    c = record_id("j2", "https://a.cn/x")
    assert a == b and a != c, (a, b, c)
    assert len(a) == 36, a                      # uuid 字符串长度

    rec = new_record(job_id="j1", url="https://a.cn/x",
                     meta={"requested_by": "t", "depth": 2,
                           "duplicate_of": "abc", "tables_dropped": 3})
    expected = {"record_id", "job_id", "project_id", "url", "final_url", "site",
                "fetched_at", "elapsed_ms", "meta", "source", "robots", "verdict",
                "tool_used", "tool_fallback", "status", "http_status", "content_type",
                "encoding", "bytes", "title", "content_sha256", "storage",
                "text_chars", "text_sha256", "text_meta", "paragraph_file",
                "paragraph_count", "pdf", "tables", "error"}
    assert set(rec) == expected, set(rec) ^ expected
    assert rec["site"] == "a.cn" and rec["status"] == "failed"
    assert rec["meta"]["code_version"], rec["meta"]
    # 扩展键必须原样保留（契约 v1.3 的 depth / duplicate_of / tables_dropped 走这里）
    assert rec["meta"]["depth"] == 2 and rec["meta"]["duplicate_of"] == "abc", rec["meta"]
    assert rec["meta"]["tables_dropped"] == 3, rec["meta"]

    ok = build_ok(job_id="j1", url="https://a.cn/x", final_url="https://a.cn/x",
                  raw=b"<html>hi</html>", content_type="html", encoding="utf-8",
                  title="标题", verdict="static", tool="static", tool_fallback=False,
                  robots={"found": True, "allowed": True, "rule": "Allow: /", "crawl_delay": None},
                  http_status=200, raw_path=None, text="中文正文",
                  paragraphs=[{"index": 0}], tables=[])
    assert ok["status"] == "ok" and ok["bytes"] == len(b"<html>hi</html>")
    assert ok["content_sha256"] and len(ok["content_sha256"]) == 64, ok
    assert ok["text_chars"] == 4 and ok["text_meta"]["paragraphs"] == 1, ok

    skip = build_skipped(job_id="j1", url="https://a.cn/y", verdict="robots_disallow",
                         tool="skip", robots={"found": True, "allowed": False,
                                              "rule": "Disallow: /", "crawl_delay": None},
                         error=errors.make(errors.E_ROBOTS_DISALLOW, "禁止"))
    assert skip["status"] == "skipped" and set(skip) == expected, skip

    print("[OK] record 自检通过：主键确定性 / 字段齐全 / 指纹与中日字数（零网络）")


if __name__ == "__main__":
    _self_test()
