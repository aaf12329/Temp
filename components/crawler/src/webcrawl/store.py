# -*- coding: utf-8 -*-
"""落盘与索引 —— 原始材料 + 规范化产物 + 抓取清单，唯一写盘出口。

《爬虫标准API接口.md》v1.2 §5 的目录结构由本模块实现：
    data/raw/{html,files}/<site>_<urlhash10>.<ext>     字节保真
    data/normalized/text|para|pdf|tables/…             规范化产物
    data/raw/pages.json                                CrawlResult[]（按 (job_id,url) upsert）

🔴 文件名只由 **URL 哈希** 决定：同一 URL 重抓覆盖同一文件。
🔴 内容去重靠 `content_sha256` 字段比对，**不靠文件名**。

================================ 结构树 =================================
① 路径推导（纯函数）
    raw_name(url, suffix)      <site>_<hash10><suffix>
    text_path_of(url) / para_path_of(url) / table_path_of(url, i)
② 写盘
    save_raw(src_bytes, url, kind)       → 路径
    save_text(text, url)                 → 路径
    save_paragraphs(paragraphs, url)     → 路径（JSON Lines）
    save_tables(tables, url)             → 带路径的表格列表（长表另写宽表）
    wide_path_of(url, index)             → 宽表路径（normalized/wide）
    wide_of_csv(csv_path)                → 由 csv_path 推宽表路径
    save_pdf_text(text, url)             → 路径
③ 索引
    load_index() / upsert_index(records) 按 (job_id, url) 合并，不整文件覆盖
④ 自测
    _self_test()                        只在临时目录里写

================================ 调用关系 ================================
api.py ──▶ save_* / upsert_index ──▶ io_utils（唯一 I/O 出口）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils, record
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils, record


def ensure_dirs() -> None:
    """确保所有产物目录存在（幂等）。"""
    for directory in (ps.PROFILE_DIR, ps.POLICY_DIR, ps.RAW_HTML_DIR, ps.RAW_FILES_DIR,
                      ps.TEXT_DIR, ps.PARA_DIR, ps.PDF_TEXT_DIR, ps.TABLES_DIR,
                      ps.WIDE_DIR, ps.JOBS_DIR, ps.DISCOVER_DIR):
        os.makedirs(directory, exist_ok=True)


def raw_name(url: str, suffix: str) -> str:
    return f"{record.site_of(url)}_{record.url_hash10(url)}{suffix}"


def raw_path_of(url: str, kind: str = "html") -> str:
    name = raw_name(url, ".pdf" if kind == "pdf" else ".html")
    return os.path.join(ps.RAW_FILES_DIR if kind == "pdf" else ps.RAW_HTML_DIR, name)


def text_path_of(url: str) -> str:
    return os.path.join(ps.TEXT_DIR, raw_name(url, ".txt"))


def para_path_of(url: str) -> str:
    return os.path.join(ps.PARA_DIR, raw_name(url, ".jsonl"))


def pdf_text_path_of(url: str) -> str:
    return os.path.join(ps.PDF_TEXT_DIR, raw_name(url, ".txt"))


def table_path_of(url: str, index: int, suffix: str) -> str:
    return os.path.join(ps.TABLES_DIR, raw_name(url, f"_t{index:02d}{suffix}"))


def save_raw(data: bytes, url: str, kind: str = "html") -> str:
    target = raw_path_of(url, kind)
    io_utils.write_bytes(target, data)
    return target


def save_text(text: str, url: str) -> str:
    target = text_path_of(url)
    io_utils.write_text(target, text)
    return target


def save_pdf_text(text: str, url: str) -> str:
    target = pdf_text_path_of(url)
    io_utils.write_text(target, text)
    return target


def save_paragraphs(paragraphs: list[dict[str, Any]], url: str) -> str:
    """段落锚点写 JSON Lines：一行一段，下游可流式读取。"""
    target = para_path_of(url)
    io_utils.ensure_parent(target)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        for paragraph in paragraphs:
            handle.write(json.dumps(paragraph, ensure_ascii=False) + "\n")
    return target


def save_tables(tables: list[dict[str, Any]], url: str) -> list[dict[str, Any]]:
    """每张表写三件套：原始 HTML（字节保真）、结构化 JSON、展平 CSV。

    长表（指标 × 年份）额外写一份宽表 CSV（`_tNN.wide.csv`），下游算同比直接读它，
    不用再自己 unstack；wide_path 由 csv_path 的命名约定可推导，不占用记录字段。
    """
    saved: list[dict[str, Any]] = []
    for index, table in enumerate(tables):
        html_path = table_path_of(url, index, ".html")
        json_path = table_path_of(url, index, ".json")
        csv_path = table_path_of(url, index, ".csv")
        io_utils.write_text(html_path, table.get("html", ""))
        io_utils.write_json(json_path, {"rows": table.get("rows", 0),
                                        "cols": table.get("cols", 0),
                                        "kind": table.get("kind"),
                                        "header": table.get("header"),
                                        "cells": table.get("cells", [])})
        io_utils.write_text(csv_path, table.get("csv", ""))
        entry = {"index": index, "rows": table.get("rows", 0),
                 "cols": table.get("cols", 0), "page": table.get("page"),
                 "kind": table.get("kind"),
                 "html_path": html_path, "json_path": json_path,
                 "csv_path": csv_path}
        if table.get("wide_csv"):
            wide_path = wide_path_of(url, index)
            io_utils.write_text(wide_path, table["wide_csv"])
            entry["wide_path"] = wide_path
        saved.append(entry)
    return saved


def wide_path_of(url: str, index: int) -> str:
    """宽表落盘路径：wide 目录 + 同一套命名（`_t00.wide.csv`）。"""
    return os.path.join(ps.WIDE_DIR, raw_name(url, f"_t{index:02d}.wide.csv"))


def wide_of_csv(csv_path: str | None) -> str | None:
    """由已落盘的 csv_path 推出宽表路径（同文件名搬进 wide 目录）。"""
    if not csv_path:
        return None
    name = os.path.basename(str(csv_path))
    if not name.lower().endswith(".csv"):
        return None
    return os.path.join(ps.WIDE_DIR, name[:-4] + ".wide.csv")


def load_index() -> list[dict[str, Any]]:
    """读抓取清单；不存在或坏掉返回空列表（工具层不因为清单坏了就崩）。"""
    if not os.path.isfile(ps.PAGES_INDEX_JSON):
        return []
    try:
        data = io_utils.read_json(ps.PAGES_INDEX_JSON)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def upsert_index(records: list[dict[str, Any]]) -> int:
    """按 (job_id, url) 合并旧清单，**不整文件覆盖**（契约 §6.2）。返回总条数。"""
    if not records:
        return len(load_index())
    index: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for existing in load_index():
        key = (existing.get("job_id", ""), existing.get("url", ""))
        index[key] = existing
        order.append(key)
    for item in records:
        key = (item.get("job_id", ""), item.get("url", ""))
        if key not in index:
            order.append(key)
        index[key] = item
    merged = [index[key] for key in order]
    io_utils.write_json(ps.PAGES_INDEX_JSON, merged)
    return len(merged)


def _self_test() -> None:
    """零网络自检：只在临时目录里验命名与 upsert 合并。"""
    import tempfile

    url = "https://a.cn/x.html"
    assert raw_name(url, ".html").endswith(".html"), raw_name(url, ".html")
    assert raw_path_of(url, "pdf").endswith(".pdf")
    assert table_path_of(url, 3, ".csv").endswith("_t03.csv")
    assert wide_path_of(url, 3).endswith("_t03.wide.csv"), wide_path_of(url, 3)
    assert wide_of_csv(table_path_of(url, 3, ".csv")).endswith("_t03.wide.csv")
    assert wide_of_csv(None) is None and wide_of_csv("a.txt") is None
    assert len(record.url_hash10(url)) == 10

    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "pages.json")
        original = ps.PAGES_INDEX_JSON
        try:
            ps.PAGES_INDEX_JSON = target
            base = {"job_id": "j1", "url": url, "record_id": "r1", "status": "ok"}
            upsert_index([base])
            assert len(load_index()) == 1, load_index()

            changed = dict(base, status="failed", record_id="r1")
            upsert_index([changed])                 # 同 (job,url) → 覆盖，不新增
            assert len(load_index()) == 1, load_index()
            assert load_index()[0]["status"] == "failed"

            upsert_index([{"job_id": "j2", "url": url, "record_id": "r2"}])
            assert len(load_index()) == 2, load_index()
        finally:
            ps.PAGES_INDEX_JSON = original

    print("[OK] store 自检通过：命名 / 三件套路径 / upsert 合并（只写临时目录）")


if __name__ == "__main__":
    _self_test()
