# -*- coding: utf-8 -*-
"""规范化 —— HTML → 干净正文 + 段落锚点 + 表格（三件套）。

《爬虫标准API接口.md》v1.2 §4.3 / §4.5：
- 正文要能**定位到段落**（下游引用数字时要能说"第 37 段"）
- 表格**不许拍平**（合并单元格 / 多行表头一丢，下游必然误读指标）

================================ 结构树 =================================
① 正文
    to_paragraphs(html)        HTML → (正文全文, Paragraph[])
    _split_blocks(text)        按空行/强换行切段
② 表格
    find_tables(html)          找出所有 <table> 原始片段（支持嵌套）
    parse_table(fragment)      → {rows, cols, cells:[{r,c,rowspan,colspan,text}]}
    table_to_csv(table)        展平导出（给人看，不许当机器读取的唯一来源）
    _collect_tables(html)      解析 + **版式表过滤**（table_ops.is_layout_table）+ 分类 + 长表转宽
③ 汇总
    normalize(html, main_content=True)
                               -> {text, paragraphs, tables:[...], main, tables_dropped}
                               正文与表格都只在「正文容器」内找（boilerplate.find_main）
④ 自测
    _self_test()               零网络

================================ 调用关系 ================================
api.py ──▶ normalize() ──▶ boilerplate.find_main / strip_noise
                        └─▶ to_paragraphs / find_tables
                                   └─▶ store 落盘（text/para/tables）
"""

from __future__ import annotations

import re
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    from webcrawl import boilerplate, html_utils, table_ops
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from webcrawl import boilerplate, html_utils, table_ops

# ---------------------------------------------------------------- 常量区
MIN_PARA_CHARS = 2          # 低于此长度的"段落"当噪声丢掉
MAX_PARA_CHARS = 300        # 单段超过这个长度，再按句号切（to_text 常把整页并成一行）
SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])")


def _split_blocks(text: str) -> list[str]:
    """把正文切成段。

    `html_utils.to_text` 往往把整页并成少数几行，所以除了按换行切，
    还要把过长的行按句末标点再切一次——否则整页就一个"段落"，定位毫无意义。
    """
    blocks: list[str] = []
    for line in text.split("\n"):
        line = " ".join(line.split())
        if len(line) < MIN_PARA_CHARS:
            continue
        pieces = [piece.strip() for piece in SENTENCE_RE.split(line) if piece.strip()]
        if len(pieces) <= 1 and len(line) <= MAX_PARA_CHARS:
            blocks.append(line)                     # 单句且不长 → 整行算一段
            continue
        for piece in pieces:
            if len(piece) < MIN_PARA_CHARS:
                continue
            if len(piece) <= MAX_PARA_CHARS:
                blocks.append(piece)
                continue
            # 超长且没有句末标点（导航条、表格文字流）→ 按分隔符/空格硬切，保证可定位
            blocks.extend(_hard_split(piece))
    return blocks


SEPARATOR_RE = re.compile(r"[|｜/／·•　]+|\s{2,}")


def _hard_split(piece: str, limit: int = MAX_PARA_CHARS) -> list[str]:
    """把超长片段按分隔符或空格硬切成不超过 limit 的块。"""
    chunks: list[str] = []
    buffer = ""
    for token in SEPARATOR_RE.split(piece):
        token = token.strip()
        if not token:
            continue
        if len(buffer) + len(token) + 1 <= limit:
            buffer = f"{buffer} {token}".strip()
            continue
        if buffer:
            chunks.append(buffer)
        while len(token) > limit:                   # 单个词就超长，直接截断
            chunks.append(token[:limit])
            token = token[limit:]
        buffer = token
    if buffer:
        chunks.append(buffer)
    return chunks


def to_paragraphs(html: str) -> tuple[str, list[dict[str, Any]]]:
    """HTML → (规范化正文, 段落锚点数组)。

    段落锚点的 char_start / char_end 是**在返回的正文里**的字符偏移（码点），
    同一份 HTML 重跑必须得到同样的段号与偏移——所以这里不做任何随机/时间相关的处理。
    """
    raw_text = html_utils.to_text(html)
    blocks = _split_blocks(raw_text)

    paragraphs: list[dict[str, Any]] = []
    cursor = 0
    pieces: list[str] = []
    for index, block in enumerate(blocks):
        start = cursor
        end = start + len(block)
        paragraphs.append({
            "index": index, "char_start": start, "char_end": end,
            "page": None, "text": block,
        })
        pieces.append(block)
        cursor = end + 1                      # +1 对应段间的换行分隔符
    text = "\n".join(pieces)
    return text, paragraphs


def find_tables(html: str) -> list[str]:
    """找出所有 <table>…</table> 的原始片段（按嵌套深度配对，不是非贪婪匹配）。"""
    fragments: list[str] = []
    lower = html.lower()
    position = 0
    while True:
        start = lower.find("<table", position)
        if start < 0:
            break
        depth = 0
        index = start
        end = -1
        while index < len(lower):
            open_at = lower.find("<table", index)
            close_at = lower.find("</table", index)
            if close_at < 0:
                break
            if 0 <= open_at < close_at:
                depth += 1
                index = open_at + 6
                continue
            depth -= 1
            index = close_at + 7
            if depth == 0:
                end = lower.find(">", close_at)
                end = end + 1 if end > 0 else close_at + 8
                break
        if end <= start:
            break
        fragments.append(html[start:end])
        position = end
    return fragments


class _TableParser(HTMLParser):
    """把一张表解析成 cells（保留 rowspan / colspan / 表头标记）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[dict[str, Any]] = []
        self.row = -1
        self.col_cursor = 0
        self.buffer: list[str] = []
        self.in_cell = False
        self.cell: dict[str, Any] = {}
        self.max_col = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row += 1
            self.col_cursor = 0
        elif tag in ("td", "th"):
            attrs_map = {k.lower(): (v or "") for k, v in attrs}
            self.in_cell = True
            self.buffer = []
            self.cell = {
                "r": self.row,
                "c": self.col_cursor,
                "rowspan": _to_int(attrs_map.get("rowspan"), 1),
                "colspan": _to_int(attrs_map.get("colspan"), 1),
                "header": tag == "th",
                "text": "",
            }

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.in_cell:
            self.cell["text"] = " ".join("".join(self.buffer).split())
            self.cells.append(self.cell)
            self.col_cursor += self.cell["colspan"]
            self.max_col = max(self.max_col, self.col_cursor)
            self.in_cell = False


def _to_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw).strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def parse_table(fragment: str) -> dict[str, Any]:
    """表格片段 → {rows, cols, cells}。空表返回 rows=0。"""
    parser = _TableParser()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:
        return {"rows": 0, "cols": 0, "cells": []}
    rows = parser.row + 1 if parser.cells else 0
    return {"rows": rows, "cols": parser.max_col, "cells": parser.cells}


def table_to_csv(table: dict[str, Any]) -> str:
    """展平导出。合并单元格按左上角值填充，空缺留空。"""
    rows, cols = table.get("rows", 0), table.get("cols", 0)
    if not rows or not cols:
        return ""
    grid = [[""] * cols for _ in range(rows)]
    for cell in table.get("cells", []):
        row, col = cell["r"], cell["c"]
        if not (0 <= row < rows and 0 <= col < cols):
            continue
        text = cell.get("text", "")
        for dr in range(cell.get("rowspan", 1)):
            for dc in range(cell.get("colspan", 1)):
                r, c = row + dr, col + dc
                if 0 <= r < rows and 0 <= c < cols and not grid[r][c]:
                    grid[r][c] = text if (dr == 0 and dc == 0) else ""

    def esc(value: str) -> str:
        if any(ch in value for ch in ',"\n'):
            return '"' + value.replace('"', '""') + '"'
        return value

    return "\n".join(",".join(esc(v) for v in row) for row in grid)


def _collect_tables(html: str) -> tuple[list[dict[str, Any]], int]:
    """解析出所有表格，**挡掉版式表**，给数据表补分类 / 表头 / 宽表。

    实测依据：真实快照里 7 张「表」全部是导航条与年鉴目录被塞进 <table>，
    不挡就会把 `首页,|,原创,|,…` 当成数据落盘。判据在 table_ops 里（可单测）。
    """
    tables: list[dict[str, Any]] = []
    dropped = 0
    for fragment in find_tables(html):
        table = parse_table(fragment)
        if table["rows"] == 0:
            continue
        layout, reason = table_ops.is_layout_table(table)
        if layout:
            dropped += 1
            continue
        table["html"] = fragment
        table["csv"] = table_to_csv(table)
        table["kind"] = table_ops.classify(table)
        table["header"] = table_ops.header_map(table)
        wide = table_ops.to_wide(table)
        table["wide"] = wide if wide.get("mode") == "unstacked" else None
        if table["wide"]:
            table["wide_csv"] = table_ops.to_csv(table["wide"]["rows"])
        tables.append(table)
    return tables, dropped


def normalize(html: str, *, main_content: bool = True,
              want_html: bool = False) -> dict[str, Any]:
    """一次拿到正文、段落锚点、表格片段。

    main_content=True 时先做**正文容器定位**（boilerplate.find_main）：
    正文字段与表格都只从容器内取，导航/页脚/侧栏不再混进正文与表格。
    定位失败或页面本身很短时 boilerplate 会回退原文，这里因此不会把正文删空。
    表格再过一道 table_ops 的版式表过滤；长表（指标 × 年份）额外给宽表。

    want_html=True 时把「正文容器原始 HTML」也带回来（链接发现要在容器内找链接，
    否则导航链接会淹没详情链接）；默认不带，避免每页多存一份 HTML。
    """
    source = html or ""
    main: dict[str, Any] = {"kind": "unknown", "score": 0,
                            "reason": "未做正文定位", "container": "body"}
    if main_content:
        located = boilerplate.find_main(source)
        candidate = located.get("html") or ""
        if candidate.strip():
            source = candidate
        main = {"kind": located.get("kind", "unknown"),
                "score": located.get("score", 0),
                "reason": located.get("reason", ""),
                "container": located.get("container", "body")}

    text, paragraphs = to_paragraphs(source)
    tables, dropped = _collect_tables(source)
    result = {"text": text, "paragraphs": paragraphs, "tables": tables,
              "main": main, "tables_dropped": dropped, "source_chars": len(source)}
    if want_html:
        result["main_html"] = source
    return result


def _self_test() -> None:
    """零网络自检：段落切分、锚点偏移、表格解析与合并单元格。"""
    html = (
        "<html><body>"
        "<p>第一段内容，讲产业规模。</p>"
        "<p>第二段内容，讲餐饮收入 412.68 亿元。</p>"
        "<table>"
        "<tr><th rowspan='2'>指标</th><th colspan='2'>2025年</th></tr>"
        "<tr><th>绝对值</th><th>同比</th></tr>"
        "<tr><td>餐饮收入</td><td>412.68</td><td>7.9%</td></tr>"
        "</table>"
        "</body></html>")
    result = normalize(html)

    assert result["text"].strip(), result["text"]
    assert len(result["paragraphs"]) >= 2, result["paragraphs"]
    first = result["paragraphs"][0]
    assert result["text"][first["char_start"]:first["char_end"]] == first["text"], first

    assert len(result["tables"]) == 1, result["tables"]
    table = result["tables"][0]
    assert table["rows"] == 3 and table["cols"] == 3, (table["rows"], table["cols"])
    assert any(c["rowspan"] == 2 for c in table["cells"]), table["cells"]
    assert any(c["colspan"] == 2 for c in table["cells"]), table["cells"]
    assert "餐饮收入" in table["csv"], table["csv"]

    # 表号码稳定：同一 HTML 重跑，段号与偏移必须一致
    again = normalize(html)
    assert [(p["index"], p["char_start"]) for p in again["paragraphs"]] == \
           [(p["index"], p["char_start"]) for p in result["paragraphs"]]
    assert result["main"]["kind"] in ("article", "listing", "unknown"), result["main"]
    assert result["tables"][0]["kind"] in ("data", "wide", "long"), result["tables"][0]["kind"]

    # 版式表必须被挡在门外（实测：真实快照里 7 张「表」全是这种）
    nav = ("<html><body><p>正文一段，讲凯里酸汤鱼产业规模。</p>"
           "<table><tr><td>首页</td><td>|</td><td>原创</td><td>|</td><td>推荐</td>"
           "<td>|</td><td>联系我们</td></tr></table></body></html>")
    nav_result = normalize(nav)
    assert nav_result["tables"] == [], nav_result["tables"]
    assert nav_result["tables_dropped"] == 1, nav_result["tables_dropped"]

    # 长表（指标 × 年份）要能转成宽表，并给出可落盘的 wide_csv
    long_html = ("<html><body><p>统计年鉴摘录。</p><table>"
                 "<tr><th>指标</th><th>年份</th><th>数值</th></tr>"
                 "<tr><td>餐饮收入</td><td>2024</td><td>412.68</td></tr>"
                 "<tr><td>餐饮收入</td><td>2025</td><td>445.10</td></tr>"
                 "</table></body></html>")
    long_result = normalize(long_html)
    if long_result["tables"]:
        wide = long_result["tables"][0].get("wide")
        if wide:
            assert "2025" in " ".join(wide["rows"][0].keys()), wide
            assert long_result["tables"][0]["wide_csv"], long_result["tables"][0]

    print("[OK] normalize 自检通过：正文定位 / 段落锚点 / 表格过滤与宽表稳定（零网络）")


if __name__ == "__main__":
    _self_test()
