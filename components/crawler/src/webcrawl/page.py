# -*- coding: utf-8 -*-
"""网页抓取 —— 抓正文、存原始快照、返回一条结构化记录（**工具层，不打印**）。

本文件是「网页爬取工具包」的一环：不做任何终端输出，只返回数据；
进度通过 crawl_many(..., progress=回调) 交给入口层 main.py 显示。

流程（每个 URL 走一遍）：
    ① web_check.check_robots(url)  → allowed 不是 True 就**放弃**，正文请求根本不发
    ② 分流：PDF 走文件快照，网页走 HTML 快照
    ③ net.fetch(url)               → 抓正文（按站点 charset 解码）
    ④ io_utils.write_bytes(...)    → 存到 data\\raw\\html\\<主机>_<URL哈希10>.html
                                      或 data\\raw\\files\\<主机>_<URL哈希10>.pdf
                                      （文件名只由 URL 决定：同一 URL 重抓**覆盖**同一文件）
    ⑤ 返回一条记录 dict            → 交给入口层 / 后续清洗阶段

============================== 函数结构树 ==============================
① 纯逻辑（不碰网络、不写文件，可离线单测）
    _snapshot_name(url, kind)       快照文件名（由 URL 唯一决定，重抓即覆盖）
    _is_pdf(url, content_type)      是不是 PDF（决定存哪个目录）
    _snapshot_dir(kind)             PDF 与 HTML 分开放
② 网络 + 落盘
    crawl(url, ...)                 单个 URL 全流程，返回一条记录
    crawl_many(urls, ...)           顺序抓多条，带站点级间隔，可选进度回调
    save_index(records, path)       写 data\\raw\\pages.json
③ 自测
    _self_test()                    零网络自检

============================== 调用关系总览 ==============================
main.py ──▶ crawl_many ──▶ crawl ──▶ web_check.check_robots   （合规闸门在前）
        │                     ├─▶ net.fetch                  （唯一 HTTP 出口）
        │                     ├─▶ io_utils.make_file_name / html_utils.parse_title
        │                     └─▶ io_utils.write_bytes        （唯一落盘出口）
        │               └─▶ net.polite_wait                   （站点级间隔）
        └─▶ save_index ──▶ io_utils.write_json

⭐ 本模块是工具层：print 一行都没有。所有终端显示都在入口层 main.py。
   调用者只有 src\\main.py（唯一调用源）。

============================== 数据流单链路 ==============================
URL → check_robots(合规) → net.fetch → 原始字节 → write_bytes(快照) → 记录 → pages.json
"""


from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps
    from webcrawl import html_utils, io_utils, net, web_check
except ImportError:                     # 直接运行本文件（自测入口）时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import html_utils, io_utils, net, web_check

# ---------------------------------------------------------------- 常量区
HTML_SUFFIX = ".html"
PDF_SUFFIX = ".pdf"


# ============================================ ① 纯逻辑（不碰网络、不写文件）
def _snapshot_name(url: str, kind: str = "html") -> str:
    """快照文件名：由 URL 唯一决定，同一 URL 重抓覆盖同一文件。"""
    suffix = PDF_SUFFIX if kind == "pdf" else HTML_SUFFIX
    return io_utils.make_file_name(url, suffix)


def _is_pdf(url: str, content_type: str | None) -> bool:
    """判断这一条产物是不是 PDF：先看 Content-Type，再看 URL 后缀。"""
    if content_type and "pdf" in content_type.lower():
        return True
    return (url or "").lower().split("?")[0].endswith(PDF_SUFFIX)


def _snapshot_dir(kind: str) -> str:
    """PDF 与 HTML 分开放：前者是原始文件，后者是网页。"""
    return ps.RAW_FILES_DIR if kind == "pdf" else ps.RAW_HTML_DIR


# ============================================ ② 网络 + 落盘
def crawl(url: str, out_dir: str | None = None,
          timeout: float = net.DEFAULT_TIMEOUT, retries: int = net.DEFAULT_RETRIES,
          user_agent: str = net.DEFAULT_USER_AGENT, save: bool = True,
          fetcher: Any = None, fetcher_name: str = "static") -> dict[str, Any]:
    """抓一个 URL：先过 robots 闸门，再抓正文、存快照，返回一条记录。

    fetcher：可选的抓取器（签名 `fetcher(url, timeout=...) -> dict`，返回契约与
    `net.fetch` 对齐）。由**入口层**按 `route.choose()` 的结果注入——
    本模块不 import 任何其它工具模块，保持工具层之间零横向依赖。
    传 None 时走默认的 `net.fetch`（静态档）。
    """
    path = web_check.judge_path_of(url)
    # 这里只要合规判定：页面类型稍后用**已经抓到的 HTML** 就地判，避免同一页抓两次
    gate = web_check.check_robots(url, path=path, timeout=timeout, retries=retries,
                                  user_agent=user_agent, probe_render=False)
    if gate.get("allowed") is None:
        # robots 因为网络抖动抓不到 → 多给几次重试再问一遍（真踩过：新浪财经偶发超时）
        gate = web_check.check_robots(url, path=path, timeout=timeout,
                                      retries=retries + 2, user_agent=user_agent,
                                      probe_render=False)
    fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
    record: dict[str, Any] = {
        "url": url,
        "status": "robots_blocked",
        "kind": "html",
        "robots_allowed": gate.get("allowed"),
        "robots_rule": gate.get("rule"),
        "robots_disallow": (gate.get("rules") or {}).get("disallow") or [],
        "crawl_delay": (gate.get("rules") or {}).get("crawl_delay"),
        "http_status": None, "content_type": None, "encoding": None,
        "bytes": 0, "sha256": None, "file": None, "title": None,
        "render_mode": None, "fetched_at": fetched_at, "error": None,
    }

    # ① 闸门：只有明确允许才继续；None（抓不到 robots）也放弃，按"不确定就不抓"处理
    if gate.get("allowed") is not True:
        return record

    # ② 抓正文：入口层给了抓取器就用它（TLS 伪装 / 浏览器档），否则走默认静态档
    if fetcher is not None:
        response = fetcher(url, timeout=timeout)
    else:
        response = net.fetch(url, timeout=timeout, retries=retries, user_agent=user_agent)
    record["fetcher"] = fetcher_name
    content_type = (response["headers"].get("content-type") or "").split(";")[0].strip() or None
    record["http_status"] = response["status"]
    record["content_type"] = content_type
    if response.get("error") or not response.get("found", True):
        record["status"] = "fetch_failed"
        record["error"] = response["error"] or f"HTTP {response['status']}"
        return record

    kind = "pdf" if _is_pdf(url, content_type) else "html"
    record["kind"] = kind

    # 快照存「解压后的原始字节」：文本解码可能是有损的（如 utf-8(replace)），字节才保真
    payload = response.get("raw") or response["text"].encode("utf-8", errors="replace")
    record.update({"sha256": io_utils.sha256_of(payload),
                   "encoding": response["encoding"], "error": None})

    if kind == "pdf":
        # PDF 这一阶段**存但不解析**：没有 PDF 解析依赖，先保证原始文件不丢
        record.update({"status": "ok_pdf", "bytes": len(payload)})
    else:
        html = response["text"]
        record.update({"status": "ok", "bytes": len(html),
                       "title": html_utils.parse_title(html),
                       # 页面类型就地判：复用已抓到的 HTML，不再多发一次请求
                       "render_mode": web_check.classify_render(html)["mode"]})

    # ③ 落盘原始快照（文件名只由 URL 决定 → 同一 URL 覆盖同一文件）
    if save:
        target_dir = out_dir or _snapshot_dir(kind)
        file_path = os.path.join(target_dir, _snapshot_name(url, kind))
        io_utils.write_bytes(file_path, payload)
        record["file"] = os.path.abspath(file_path)
    return record


def crawl_many(urls: list[str], delay: float = net.DEFAULT_DELAY,
               progress: Any = None, **kwargs: Any) -> list[dict[str, Any]]:
    """顺序抓多条：每条之间 sleep，遵守站点 crawl-delay（站点要求更长就用站点的）。

    本函数**不打印任何东西**（工具层只管干活）；进度通过 `progress` 回调交给入口层：
    `progress(index, total, url, record)`。
    """
    records: list[dict[str, Any]] = []
    total = len(urls)
    for index, url in enumerate(urls, 1):
        try:
            record = crawl(url, **kwargs)
        except Exception as exc:                 # 单条失败不中断整批
            record = {"url": url, "status": "fetch_failed", "kind": "html",
                      "error": f"{type(exc).__name__}: {exc}", "robots_allowed": None,
                      "robots_rule": None, "robots_disallow": [], "crawl_delay": None,
                      "http_status": None, "content_type": None, "encoding": None,
                      "bytes": 0, "sha256": None, "file": None, "title": None,
                      "render_mode": None,
                      "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        records.append(record)
        if progress is not None:
            progress(index, total, url, record)

        if index < total:
            net.polite_wait(delay, record.get("crawl_delay"))
    return records


def save_index(records: list[dict[str, Any]],
               index_path: str = ps.PAGES_INDEX_JSON) -> str:
    """把这一批的记录写进 data\\raw\\pages.json（清单，后续清洗阶段的输入）。"""
    io_utils.write_json(index_path, records)
    return os.path.abspath(index_path)


# ============================================ 自测（零网络，不碰真实数据）
def _self_test() -> None:
    """验证纯逻辑与文件名规则，不发请求、不写真实目录。"""
    assert len(io_utils.sha256_of("凯里酸汤鱼")) == 64

    url = "https://cinic.org.cn/xy/a.html"
    name = _snapshot_name(url)
    assert name.startswith("cinic.org.cn_") and name.endswith(".html"), name
    assert name == _snapshot_name(url), "同一 URL 必须得到同一个文件名（覆盖而非新增）"
    assert name != _snapshot_name("https://cinic.org.cn/xy/b.html"), "不同 URL 不能撞名"
    assert _snapshot_name("https://a.gov.cn/x.pdf", "pdf").endswith(".pdf")
    assert ":" not in _snapshot_name("http://e.com:8080/x")
    assert _snapshot_name("http://e.com:8080/x").startswith("e.com-8080_")

    assert _is_pdf("https://a.gov.cn/x.pdf", None) is True
    assert _is_pdf("https://a.gov.cn/x.pdf?v=1", None) is True
    assert _is_pdf("https://a.gov.cn/x.html", "application/pdf") is True
    assert _is_pdf("https://a.gov.cn/x.html", "text/html; charset=utf-8") is False
    assert _is_pdf("https://a.gov.cn/x.pdf.html", None) is False

    assert html_utils.parse_title(
        "<html><head><title> 酸\t汤 鱼 </title></head></html>") == "酸 汤 鱼"
    assert html_utils.parse_title("<html><body>无标题</body></html>") is None
    print("[OK] 自检通过：指纹 / 快照文件名（URL 唯一决定）/ PDF 判定 / "
          "主机名清洗 / <title> 提取（零网络）")


if __name__ == "__main__":
    _self_test()
