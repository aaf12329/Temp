# -*- coding: utf-8 -*-
"""抽取与去重 —— 把 data\\pages.json + 原始快照变成一张能喂给模型的表。

职责（《AGENTS.md》§4 的 ③ 去重 / ④ 字段抽取）：
    ① 读 data\\pages.json，取出每个成功抓取的快照文件
    ② 从原始 HTML 抽：标题、正文纯文本、正文字数、站点
    ③ 按「正文归一化指纹」标出转载重复（同一篇内容多处转载）
    ④ 落成 data\\articles.json（全字段）+ data\\articles.csv（Excel 直接打开）

只做抽取与去重，不做清洗规则、不做建模。

================================ 函数结构树 ================================
① 纯逻辑（不读文件、不联网，可离线单测）
    _HtmlText(HTMLParser)            扫 HTML，攒正文文本、跳过 script/style
    text_from_html(html)             HTML → 正文纯文本
    parse_title(html)                <title> 取标题
    normalize_text(text)             去空白/空白字符，做去重用的归一化
    fingerprint(text)                归一化正文的 sha256
② 抽取 + 去重
    extract_one(record, raw_dir)     一条抓取记录 → 一条文章记录
    mark_duplicates(records)         原地标 is_duplicate / duplicate_of
    extract_all(pages, raw_dir)      批量抽取 + 去重
③ 输出
    save_outputs(records, data_dir)  写 articles.json 与 articles.csv
    print_summary(records)
    main(argv)                       命令行入口
    _self_test()                     零文件、零网络的离线自检

============================== 调用关系总览 ==============================
main ──▶ io_utils.read_json(ps.PAGES_INDEX_JSON)
     ──▶ extract_all ──▶ extract_one ──▶ io_utils.read_bytes(快照)
     │                              └─▶ text_from_html / parse_title / fingerprint
     │                └─▶ mark_duplicates ──▶ fingerprint
     ──▶ save_outputs ──▶ io_utils.write_json / write_bytes(csv)
     ──▶ print_summary

================================ 对外返回契约 ============================
extract_one(record, raw_dir) -> {
    "url": str, "site": str,               # 站点主机名
    "title": str|None,
    "text": str,                           # 正文纯文本
    "text_len": int,                       # 正文字数（归一化前）
    "fingerprint": str,                    # 正文指纹（归一化后 sha256）
    "encoding": str|None,                  # 抓取时用的编码
    "snapshot": str|None,                  # 快照文件（绝对路径）
    "fetched_at": str,
    "is_duplicate": bool,                  # 是否是转载重复
    "duplicate_of": str|None,              # 重复时指向首次出现的 URL
    "error": str|None,
}

字段一旦发布不改名；要扩展只加新字段。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from Path_Store import Path_Store as ps
    from webcrawl import console, html_utils, io_utils
except ImportError:                     # 直接运行本文件（自测入口）时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import console, html_utils, io_utils

# ---------------------------------------------------------------- 常量区
SPACE_RE = re.compile(r"\s+")
DECODE_FALLBACK = ("utf-8", "gb18030")

ARTICLES_JSON = ps.ARTICLES_JSON
ARTICLES_CSV = ps.ARTICLES_CSV
CSV_FIELDS = ("site", "title", "text_len", "is_duplicate", "duplicate_of",
              "fetched_at", "encoding", "url", "snapshot")


# ============================================ ① 纯逻辑（不读文件、不联网）
# 去标签实现已合并到共用层 html_utils.to_text（原先这里有一份重复的 HTMLParser）
text_from_html = html_utils.to_text
parse_title = html_utils.parse_title


def normalize_text(text: str) -> str:
    """去重用的归一化：空白全部去掉，标点保留（保守，避免误判不同文章）。"""
    return SPACE_RE.sub("", text or "")


def fingerprint(text: str) -> str:
    """归一化正文的 sha256 —— 指纹相同 = 同一篇内容。"""
    return hashlib.sha256(normalize_text(text).encode("utf-8", "replace")).hexdigest()


# ============================================ ② 抽取 + 去重
def _decode_snapshot(raw: bytes, encoding: str | None) -> str:
    """快照解码：优先用抓取时记录的编码，失败再回退。"""
    candidates = [encoding] if encoding else []
    candidates.extend(DECODE_FALLBACK)
    for name in candidates:
        if not name or name.endswith("(replace)"):
            continue
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def extract_one(record: dict[str, Any], raw_dir: str = ps.RAW_HTML_DIR) -> dict[str, Any]:
    """一条抓取记录 → 一条文章记录（标题 / 正文 / 字数 / 指纹）。"""
    url = record.get("url") or ""
    snapshot = record.get("file")
    item: dict[str, Any] = {
        "url": url,
        "site": urlsplit(url).netloc,
        "title": record.get("title"),
        "text": "",
        "text_len": 0,
        "fingerprint": None,
        "encoding": record.get("encoding"),
        "snapshot": snapshot,
        "fetched_at": record.get("fetched_at"),
        "is_duplicate": False,
        "duplicate_of": None,
        "error": None,
    }
    if not snapshot or not os.path.isfile(snapshot):
        item["error"] = f"快照不存在：{snapshot}"
        return item

    html = _decode_snapshot(io_utils.read_bytes(snapshot), record.get("encoding"))
    text = text_from_html(html)
    item["text"] = text
    item["text_len"] = len(text)
    item["fingerprint"] = fingerprint(text)
    item["title"] = item["title"] or parse_title(html)
    if not text:
        item["error"] = "正文为空（可能是纯 JS 页面）"
    return item


def mark_duplicates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """原地标记转载重复：指纹相同的，后出现的指向首次出现的 URL。

    两条字段(`is_duplicate` / `duplicate_of`)对**每一条**都会补齐，调用方不用再判 None。
    """
    seen: dict[str, str] = {}
    for item in records:
        item.setdefault("is_duplicate", False)
        item.setdefault("duplicate_of", None)
        mark = item.get("fingerprint")
        if not mark or not item.get("text_len"):
            continue
        first = seen.get(mark)
        if first is None:
            seen[mark] = item["url"]
        elif first != item["url"]:
            item["is_duplicate"] = True
            item["duplicate_of"] = first
    return records


def extract_all(pages: list[dict[str, Any]],
                raw_dir: str = ps.RAW_HTML_DIR) -> list[dict[str, Any]]:
    """批量抽取：只处理抓取成功的记录，然后统一标重。"""
    records = [extract_one(page, raw_dir) for page in pages
               if page.get("status") == "ok"]
    return mark_duplicates(records)


# ============================================ ③ 输出
def save_outputs(records: list[dict[str, Any]],
                 data_dir: str = ps.PARSED_DIR) -> dict[str, str]:
    """写 articles.json（全字段）和 articles.csv（Excel 直接打开，带 BOM）。"""
    json_path = os.path.join(data_dir, "articles.json")
    csv_path = os.path.join(data_dir, "articles.csv")
    io_utils.write_json(json_path, records)
    io_utils.write_csv(csv_path, records, CSV_FIELDS)
    return {"json": os.path.abspath(json_path), "csv": os.path.abspath(csv_path)}


def print_summary(records: list[dict[str, Any]]) -> None:
    """终端汇总：多少篇、多少重复、正文长度分布。"""
    total = len(records)
    dups = sum(1 for r in records if r.get("is_duplicate"))
    empty = sum(1 for r in records if not r.get("text_len"))
    lengths = sorted(r["text_len"] for r in records if r.get("text_len"))
    print("=" * 70)
    print(f"文章 {total} 篇 | 转载重复 {dups} 篇 | 正文为空 {empty} 篇")
    if lengths:
        mid = lengths[len(lengths) // 2]
        print(f"正文字数：最短 {lengths[0]} / 中位 {mid} / 最长 {lengths[-1]}")
    print("=" * 70)
    dup_items = [r for r in records if r.get("is_duplicate")]
    if dup_items:
        print("重复明细：")
        for item in dup_items[:10]:
            print(f"  - {item['url'][:60]}  ≡  {item['duplicate_of'][:60]}")
    print()


def _force_utf8_output() -> None:
    """终端输出改 UTF-8，避开 Windows 默认 GBK。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv: list[str]) -> int:
    """命令行：读抓取清单 → 抽取 + 去重 → 落 articles.json / articles.csv。"""
    _force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="Extract_Article.py",
        description="从原始快照抽取标题与正文，标出转载重复，落成表。")
    parser.add_argument("--pages", default=ps.PAGES_INDEX_JSON, help="抓取清单 JSON")
    parser.add_argument("--data-dir", default=ps.PARSED_DIR, help="输出目录")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，0 = 全部")
    parser.add_argument("--self-test", action="store_true", help="跑离线自检")
    args = parser.parse_args(argv)

    if args.self_test:
        _self_test()
        return 0

    if not os.path.isfile(args.pages):
        print(f"找不到抓取清单：{args.pages}\n先跑 src\\crawl\\Crawl_Page.py")
        return 1

    pages = io_utils.read_json(args.pages)
    if args.limit and args.limit > 0:
        pages = pages[:args.limit]
    records = extract_all(pages)
    print_summary(records)
    outputs = save_outputs(records, args.data_dir)
    print(f"已写入：{outputs['json']}\n已写入：{outputs['csv']}")
    return 0


# ============================================ 自测（零文件、零网络）
def _self_test() -> None:
    """用内置字符串验证抽取与去重。"""
    html = ("<html><head><title> 凯里酸汤鱼 </title>"
            "<script>var x=1;</script></head>"
            "<body><p>第一段</p><p>第二段</p><style>.a{}</style></body></html>")
    text = text_from_html(html)
    assert text == "第一段 第二段", text          # script/style 必须被跳过
    assert parse_title(html) == "凯里酸汤鱼", parse_title(html)
    assert parse_title("<html></html>") is None

    assert normalize_text(" 酸 汤\t鱼\n") == "酸汤鱼"
    assert fingerprint("酸汤鱼") == fingerprint(" 酸 汤 鱼 ")

    # 去重：同一篇内容出现两次 → 第二篇被标重复
    records = [
        {"url": "https://a.example/1", "text_len": 5, "fingerprint": fingerprint("同一篇文章")},
        {"url": "https://b.example/2", "text_len": 5, "fingerprint": fingerprint("同一篇文章")},
        {"url": "https://c.example/3", "text_len": 5, "fingerprint": fingerprint("另一篇文章")},
    ]
    mark_duplicates(records)
    assert records[1]["is_duplicate"] is True
    assert records[1]["duplicate_of"] == "https://a.example/1"
    assert not records[2].get("is_duplicate")
    print("[OK] 自检通过：正文抽取 / 标题 / 归一化 / 指纹去重（零网络）")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
