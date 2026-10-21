# -*- coding: utf-8 -*-
"""表格算子 —— 版式表过滤、多行表头拼平、指标×年份长表转宽表。

输入统一是 ``normalize.parse_table()`` 的产物：

    {"rows": int, "cols": int,
     "cells": [{"r": int, "c": int, "rowspan": int, "colspan": int,
                "header": bool, "text": str}, ...]}

解决的问题（都有真实数据证据）：
- **版式表混进数据表**：整页导航被抽成一行 ``首页 | 原创 | 推荐 …``，
  连分隔符 ``|`` 也占一个单元格；目录 / 面包屑 / 页脚同样会伪装成表格。
- **长表没法直接算同比**：统计年鉴 / 年报常把「指标、年份、数值」各放一列，
  下游要的是「指标 × 年份」宽表，必须先按指标把年份展开成列。

本模块是**纯函数层**：不联网、不落盘、不打印（只有 ``_self_test`` 会 print），
同一输入重复调用结果完全一致（无时间戳、无随机数、不依赖 set 遍历顺序）。

================================ 结构树 =================================
① 基础还原
    SEPARATOR_CHARS / YEAR_RE / NUM_RE / LAYOUT_WORDS / UNIT_WORDS   常量
    grid(table)                       cells → 二维文本（合并单元格只看左上角）
    numeric_ratio(table)              含数字单元格 / 非空单元格
② 版式判定
    is_layout_table(table)            (bool, reason)：导航条 / 目录 / 单列文字块
    _stats(table)                     内部统计（纯数值占比 / 分隔符占比 / 均长…）
③ 表头
    header_rows(table)                多行表头深度（全 header 且不含纯数值）
    header_map(table)                 上下级用 "/" 拼平成列名
④ 分类与转宽
    classify(table)                   layout / wide / long / data / unknown
    unstack(table)                    长表 → [{name, values}]（列式宽表）
    to_wide(table)                    指标 × 年份 宽表（rows / years / mode / reason）
⑤ 导出
    to_csv(rows_or_table)             CSV 转义（逗号 / 引号 / 换行）
⑥ 自测
    _self_test()                      零网络

================================ 调用关系 =================================
将来由入口层 / 清洗层编排（本模块不 import 任何项目内模块，避免工具层环依赖）：

    normalize.parse_table(fragment) ──▶ classify(table)
        ├─ "layout"  ──▶ 丢弃（导航条 / 目录 / 单列文字块）
        ├─ "wide"    ──▶ to_wide()（already_wide）→ to_csv() 给下游
        ├─ "long"    ──▶ to_wide()（unstacked）   → to_csv() 给下游
        └─ "data" / "unknown" ──▶ 按原表 to_csv() 导出

================================ 数据流单链路 ============================
<table 片段> → normalize.parse_table → {rows, cols, cells} → table_ops
    → grid（二维还原）→ _stats（版式判据）→ header_map（列名）
    → classify → to_wide（宽表）→ to_csv（给下游的文本）

================================ 对外返回契约 =============================
grid             list[list[str]]                    行优先；合并单元格非左上角留空
numeric_ratio    float                              0.0~1.0；空表 0.0
is_layout_table  tuple[bool, str]                   第二项是中文判据（便于日志复核）
header_rows      int                                0 表示没识别到表头
header_map       {"header": [[...]], "depth": int, "names": [str]}
                                                     header 为 colspan 展开后的表头行
classify         str                                layout | wide | long | data | unknown
unstack          {"columns": [{"name": str, "values": [str]}]}
                                                     无法展开时 columns=[]
to_wide          {"rows": [dict], "years": [str], "mode": str, "reason": str}
                                                     mode ∈ unstacked / already_wide /
                                                     not_applicable
to_csv           str                                行以 \\n 连接，无尾随换行
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "SEPARATOR_CHARS", "YEAR_RE", "NUM_RE", "LAYOUT_WORDS", "UNIT_WORDS",
    "grid", "numeric_ratio", "is_layout_table", "header_rows", "header_map",
    "classify", "unstack", "to_wide", "to_csv",
]

# ---------------------------------------------------------------- 常量区
# 导航条里的“装饰性分隔”字符：整格只有这些字符时不算内容列。
SEPARATOR_CHARS = "|｜/／·•　>＞»"

# 年份：前后不能再挨着数字，避免把 12024 这种计数当成 2024 年。
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

# “含数字”：数字（可带千分位小数）出现即命中；不做单位约束。
NUM_RE = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")

# “纯数值”：整格就是一个数（可带百分号），用于区分“数据格”和“带数字的说明文字”。
_PURE_NUM_RE = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?\s*(?:%|％)?")

# 单独的年份标签（2024 / 2025）：纯数值但不应当作“数字数据行”切断表头识别。
_YEAR_ONLY_RE = re.compile(r"(?:19|20)\d{2}")

# 常见导航 / 页脚 / 目录词：整格等于这些词时按版式内容计数（不进入正文语义）。
LAYOUT_WORDS = frozenset({
    "首页", "主页", "网站首页", "原创", "推荐", "视频", "图库", "评论",
    "新闻", "新闻中心", "全站", "登录", "登陆", "注册", "搜索", "导航",
    "网站地图", "站点地图", "关于我们", "联系我们", "版权所有",
    "下一页", "上一页", "更多", "查看更多", "尾页", "末页", "返回",
    "打印", "分享", "rss", "english", "服务中心",
    "年度汇总", "行业年鉴", "地区年鉴",
})

# 数值单位：纯数值判定时剥掉一个后缀单位（“412.68亿元”算纯数值）。
UNIT_WORDS = (
    "百分点", "平方公里", "平方米", "万人次", "亿千瓦时", "万千瓦时",
    "亿元", "万元", "亿吨", "万吨", "万头", "万只", "万人", "千米", "公斤",
    "公顷", "千瓦", "元", "万", "亿", "%", "％", "人", "户", "吨", "亩",
    "家", "个", "次", "倍", "辆", "台", "头", "只", "张", "床", "所",
    "件", "项", "条", "座", "米",
)

# 缺失值占位符：出现在数值列里时按“没有这个数”处理（宽表里留空）。
_MISSING_MARKERS = frozenset({"—", "–", "-", "－", "/", "／", "…", "...", "——"})

# 数值列的表头提示词：多列都是数字时优先选“值 / 金额 / 收入”这类主指标列。
_VALUE_HINTS = ("值", "数值", "金额", "数量", "产值", "收入", "产量",
                "绝对", "指标", "数据", "余额", "总额")

# 序号 / 编号类表头：它们也是纯数字列，但不是数值列，要排除。
_SERIAL_HINTS = ("序号", "编号", "代码", "排名", "id")

# 版式判定阈值（都经过 21 个真实表格文件抽查，见 tests/test_table_ops.py 的历史数据说明）
_SEPARATOR_RATIO = 0.25         # 分隔符单元格占比 ≥ 25% → 导航条
_PURE_NUM_RATIO = 0.10          # 纯数值占比 < 10% → 没有数字骨架
_AVG_CELL_LEN = 4.0             # 单元格平均长度 < 4 字
_MANY_ROWS = 4                  # 且“有内容行” ≥ 4 → 标签碎片
_LONG_CELL = 500                # 单格文本 ≥ 500 字 → 目录 / 正文被灌进单元格
_LAYOUT_WORD_RATIO = 0.30       # 版式词占比 ≥ 30%
_YEAR_COL_RATIO = 0.60          # 某列 ≥ 60% 的值为年份 → 年份列


# ============================================================ ① 基础还原
def _to_int(value: Any, default: int = 0) -> int:
    """把 JSON 里的行列/跨度转成整数；坏值不抛异常，退回默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(cell: dict[str, Any]) -> str:
    """单元格文本归一：合并空白、去掉首尾；None / 非字符串都不炸。"""
    raw = cell.get("text", "")
    if raw is None:
        return ""
    return " ".join(str(raw).split())


def _shape(table: dict[str, Any]) -> tuple[int, int]:
    """行列数：优先用表里的 rows / cols；缺失时按 cells 的跨度兜底推导。"""
    rows = _to_int(table.get("rows"), 0)
    cols = _to_int(table.get("cols"), 0)
    cells = table.get("cells") or []
    if rows <= 0 or cols <= 0:
        need_rows = rows <= 0
        need_cols = cols <= 0
        max_r = max_c = 0
        for cell in cells:
            row = _to_int(cell.get("r"), 0)
            col = _to_int(cell.get("c"), 0)
            max_r = max(max_r, row + max(1, _to_int(cell.get("rowspan"), 1)))
            max_c = max(max_c, col + max(1, _to_int(cell.get("colspan"), 1)))
        if need_rows:
            rows = max_r
        if need_cols:
            cols = max_c
    return max(0, rows), max(0, cols)


def grid(table: dict[str, Any]) -> list[list[str]]:
    """把 cells 还原成二维文本（行优先）。

    - 合并单元格只在**左上角**写值，被覆盖的位置留空（与 normalize.table_to_csv 同口径）；
    - 越界 cell 直接跳过，超大 rowspan / colspan 自动裁剪，**绝不抛异常**；
    - 同一位置重复出现时先到先得（确定性：只由 cells 顺序决定）。
    """
    rows, cols = _shape(table)
    if rows <= 0 or cols <= 0:
        return []
    out = [[""] * cols for _ in range(rows)]
    for cell in table.get("cells") or []:
        row = _to_int(cell.get("r"), -1)
        col = _to_int(cell.get("c"), -1)
        if row < 0 or col < 0 or row >= rows or col >= cols:
            continue                                   # 左上角都不在表内 → 整格忽略
        text = _text(cell)
        rowspan = max(1, _to_int(cell.get("rowspan"), 1))
        colspan = max(1, _to_int(cell.get("colspan"), 1))
        for dr in range(rowspan):
            for dc in range(colspan):
                r, c = row + dr, col + dc
                if not (0 <= r < rows and 0 <= c < cols):
                    continue
                if dr == 0 and dc == 0:
                    if not out[r][c]:                  # 先到先得，不覆盖已有值
                        out[r][c] = text
                elif not out[r][c]:
                    out[r][c] = ""                     # 被合并覆盖：保持空
    return out


def _is_separator(text: str) -> bool:
    """整格是否只由分隔符 / 空白组成（导航条里的 ``|`` 单元格就是这种）。"""
    stripped = text.strip()
    if not stripped:
        return False
    return all(ch in SEPARATOR_CHARS or ch.isspace() for ch in stripped)


def _has_digit(text: str) -> bool:
    """文本里是否含数字（“2024年”“7.9%”“412.68 亿元”都算）。"""
    return bool(NUM_RE.search(text))


def _is_pure_number(text: str) -> bool:
    """是否“纯数值”单元格：``412.68`` / ``7.9%`` / ``412.68亿元`` 是，``2024年`` 不是。

    关键区别：版式表里的数字通常嵌在说明文字中，数据表的数字格通常就是一个数。
    ``2024年`` 故意不算纯数值——它是表头年份标签，不能阻止多行表头识别。
    """
    compact = text.replace(" ", "").replace("\u3000", "")
    if not compact:
        return False
    if _PURE_NUM_RE.fullmatch(compact):
        return True
    for unit in UNIT_WORDS:
        if compact.endswith(unit):
            head = compact[:-len(unit)]
            if head and _PURE_NUM_RE.fullmatch(head):
                return True
    return False


def _year_of(text: str) -> str:
    """取出文本里的四位年份（``2022年`` → ``2022``）；没有返回空串。"""
    match = YEAR_RE.search(text or "")
    return match.group(1) if match else ""


def _is_data_number(text: str) -> bool:
    """是否“数字数据格”：纯数值，且不是单独一个年份标签。

    ``2024`` / ``2025`` 出现在表头里是年份列名，不能当成数字数据行把多行表头切断；
    出现在数据区里（如 ``412.68``）才算数据。单独的年份值交给 ``_year_of`` 识别。
    """
    if not _is_pure_number(text):
        return False
    compact = text.replace(" ", "").replace("\u3000", "")
    return not _YEAR_ONLY_RE.fullmatch(compact)


def _is_missing(text: str) -> bool:
    """数值列里的缺失占位符（``—`` / ``-`` / ``/`` …）。"""
    return text.strip() in _MISSING_MARKERS


def _is_layout_word(text: str) -> bool:
    """整格是否就是导航 / 页脚词（大小写不敏感，仅整格相等才算）。"""
    return text.strip().lower() in LAYOUT_WORDS


def numeric_ratio(table: dict[str, Any]) -> float:
    """含数字单元格占**非空单元格**的比例（空表返回 0.0）。

    注意：这是“含数字”（说明文字里出现 2024 也算），用来给调用方一个整体数字密度；
    版式判定内部另用更严格的“纯数值占比”，避免目录里的大段文字因带年份而蒙混过关。
    """
    filled = [text for row in grid(table) for text in row if text]
    if not filled:
        return 0.0
    return sum(1 for text in filled if _has_digit(text)) / len(filled)


def _stats(table: dict[str, Any]) -> dict[str, Any]:
    """版式判定的内部统计（一次算清，避免各判据重复扫表）。"""
    g = grid(table)
    rows = len(g)
    cols = len(g[0]) if rows else 0
    filled = [(r, c, text) for r in range(rows)
              for c, text in enumerate(g[r]) if text]
    content = [(r, c, text) for r, c, text in filled if not _is_separator(text)]
    lengths = [len(text) for _, _, text in filled]
    pure = [text for _, _, text in content if _is_pure_number(text)]
    return {
        "grid": g,
        "rows": rows,
        "cols": cols,
        "filled": filled,
        "content": content,
        "sep_ratio": (len(filled) - len(content)) / len(filled) if filled else 0.0,
        "pure_count": len(pure),
        "pure_ratio": len(pure) / len(content) if content else 0.0,
        "eff_cols": len({c for _, c, _ in content}),
        "rows_with_data": len({r for r, _, _ in filled}),
        "avg_len": sum(lengths) / len(lengths) if lengths else 0.0,
        "max_len": max(lengths) if lengths else 0,
        "layout_word_ratio": (
            sum(1 for _, _, text in content if _is_layout_word(text)) / len(content)
            if content else 0.0
        ),
    }


def _flags(table: dict[str, Any]) -> dict[tuple[int, int], bool]:
    """(行, 列) → 是否 ``<th>``；只用于表头识别。"""
    flags: dict[tuple[int, int], bool] = {}
    for cell in table.get("cells") or []:
        row = _to_int(cell.get("r"), -1)
        col = _to_int(cell.get("c"), -1)
        if row < 0 or col < 0:
            continue
        flags[(row, col)] = bool(cell.get("header"))
    return flags


# ============================================================ ② 版式判定
def is_layout_table(table: dict[str, Any]) -> tuple[bool, str]:
    """判断一张表是**版式表**还是**数据表**：返回 ``(bool, 中文判据)``。

    判据按顺序命中即返回（顺序即优先级，便于日志复核）：

    ① 空表（没有任何有效单元格）→ 版式表
    ② 数字骨架豁免：纯数值格 ≥2 且有效列 ≥2 且（纯数值占比 ≥10% 或 有内容行 ≥2）
       或 表头含年份列且至少有 1 个纯数值格 → **数据表**（防误杀稀疏 / 带备注的真表）
    ③ 分隔符单元格占比 ≥25% → 导航条
    ④ 有效列数 ≤1 → 单列文字块（面包屑 / 单列目录）
    ⑤ 单格文本 ≥500 字 → 目录 / 正文被灌进单元格
    ⑥ 版式词占比 ≥30% → 导航 / 页脚词堆叠
    ⑦ 纯数值占比 <10% 且没有表头年份列 → 没有数字骨架
    ⑧ 单元格平均长度 <4 字且“有内容行” ≥4 → 标签碎片（翻页 / 栏目列表）
    ⑨ 其余 → 数据表

    真数据表（如「餐饮收入 / 412.68 / 7.9%」）在 ② 命中豁免，不会被 ⑦ 误杀。
    """
    stats = _stats(table)
    if not stats["filled"]:
        return True, f"空表：{stats['rows']} 行 {stats['cols']} 列没有有效单元格"

    year_header = _has_year_header(table)
    if stats["pure_count"] >= 2 and stats["eff_cols"] >= 2 and (
            stats["pure_ratio"] >= _PURE_NUM_RATIO or stats["rows_with_data"] >= 2):
        return False, (
            f"数字骨架：{stats['pure_count']} 个纯数值单元格，"
            f"纯数值占比 {stats['pure_ratio']:.0%}，视为数据表"
        )
    if year_header and stats["pure_count"] >= 1:
        return False, (
            f"表头含年份列且有 {stats['pure_count']} 个纯数值单元格，视为宽表（数据表）"
        )

    if stats["sep_ratio"] >= _SEPARATOR_RATIO:
        return True, (
            f"分隔符单元格占比 {stats['sep_ratio']:.0%}"
            f"（{len(stats['filled']) - len(stats['content'])} 个 `|` 类分隔格），"
            "整行是导航条"
        )
    if stats["eff_cols"] <= 1:
        return True, (
            f"有效列数仅 {stats['eff_cols']}（{stats['rows']} 行文字挤在一列），"
            "是面包屑 / 单列目录"
        )
    if stats["max_len"] >= _LONG_CELL:
        return True, (
            f"单格文本长达 {stats['max_len']} 字（≥{_LONG_CELL}），"
            "目录或正文被整段塞进单元格"
        )
    if stats["layout_word_ratio"] >= _LAYOUT_WORD_RATIO:
        return True, (
            f"版式词占比 {stats['layout_word_ratio']:.0%}"
            f"（首页 / 原创 / 推荐 / 登录 这类导航词 {len(stats['content'])} 格中占多数）"
        )
    if stats["pure_ratio"] < _PURE_NUM_RATIO and not year_header:
        return True, (
            f"纯数值占比仅 {stats['pure_ratio']:.0%}（<{_PURE_NUM_RATIO:.0%}），"
            "没有数字骨架"
        )
    if stats["avg_len"] < _AVG_CELL_LEN and stats["rows_with_data"] >= _MANY_ROWS:
        return True, (
            f"单元格平均长度 {stats['avg_len']:.1f} 字"
            f"（<{_AVG_CELL_LEN:.0f}）且有内容行 {stats['rows_with_data']} 行"
            f"（≥{_MANY_ROWS}），是标签碎片"
        )
    return False, (
        f"含 {stats['pure_count']} 个纯数值单元格、有效列 {stats['eff_cols']}，"
        "无版式特征，视为数据表"
    )


def _has_year_header(table: dict[str, Any]) -> bool:
    """表头里是否出现年份（多行表头拼平后仍含四位年份）。"""
    names = header_map(table)["names"]
    return any(YEAR_RE.search(name or "") for name in names)


# ============================================================ ③ 表头
def header_rows(table: dict[str, Any]) -> int:
    """多行表头深度：从第 0 行起，连续「全是表头格且没有数字数据格」的行数。

    - “数字数据格”才算数字行；表头里的 ``2024年`` / ``2024`` 是年份标签，不算数字
      → 双行表头深度=2，裸年份表头也能保住深度=1；
    - 遇到第一行非表头格（或数字数据格）立即停止；
    - **无 ``<th>`` 兜底**：整张表都没有表头标记时，若开头连续若干行全是文字、
      且后面出现了数字数据格，则把这些文字行当作“伪表头”（统计年鉴大量用 ``<td>`` 做表头）。
      这是从真实数据倒逼出来的兼容规则，纯 th 表仍走上面的严格判定。
    """
    g = grid(table)
    if not g:
        return 0
    flags = _flags(table)
    depth = 0
    for row_index, row in enumerate(g):
        cells = [(c, text) for c, text in enumerate(row) if text]
        if not cells:
            break
        if not all(flags.get((row_index, c), False) for c, _ in cells):
            break
        if any(_is_data_number(text) for _, text in cells):
            break
        depth += 1
    if depth:
        return depth
    return _pseudo_header_rows(g)


def _pseudo_header_rows(g: list[list[str]]) -> int:
    """无 ``<th>`` 时的伪表头深度：开头连续全文字行，且其后确实有数字数据。"""
    depth = 0
    for row in g:
        texts = [text for text in row if text]
        if not texts:
            break
        if any(_is_data_number(text) for text in texts):
            break
        depth += 1
    if depth <= 0 or depth >= len(g):
        return 0
    has_number_below = any(
        _is_data_number(text) for row in g[depth:] for text in row if text
    )
    return depth if has_number_below else 0


def header_map(table: dict[str, Any]) -> dict[str, Any]:
    """多行表头 → 拼平的列名。

    返回 ``{"header": [[...]], "depth": int, "names": [str]}``：

    - ``header``：前 ``depth`` 行表头，``colspan`` 已横向展开（合并格的值铺满其跨度）；
    - ``names``：逐列自上而下把非空表头用 ``/`` 连接，如 ``2024年/绝对值``；
    - 同一列上下重复的相同文字只保留一次；``depth=0`` 时 ``header`` 与 ``names`` 都为空。
    """
    g = grid(table)
    depth = header_rows(table)
    if depth <= 0 or not g:
        return {"header": [], "depth": 0, "names": []}
    cols = len(g[0])
    expanded: list[list[str]] = []
    for row_index in range(depth):
        row = [""] * cols
        for cell in table.get("cells") or []:
            if _to_int(cell.get("r"), -1) != row_index:
                continue
            col = _to_int(cell.get("c"), -1)
            if col < 0 or col >= cols:
                continue
            text = _text(cell)
            colspan = max(1, _to_int(cell.get("colspan"), 1))
            for c in range(col, min(cols, col + colspan)):
                if not row[c]:
                    row[c] = text
        for c in range(cols):                          # cells 缺记录时用 grid 兜底
            if not row[c]:
                row[c] = g[row_index][c]
        expanded.append(row)

    names: list[str] = []
    for c in range(cols):
        parts: list[str] = []
        for r in range(depth):
            text = expanded[r][c]
            if not text:
                continue
            if parts and parts[-1] == text:
                continue
            parts.append(text)
        names.append("/".join(parts))
    return {"header": expanded, "depth": depth, "names": names}


# ============================================================ ④ 分类与转宽
def _column_values(g: list[list[str]], c: int, start_row: int) -> list[str]:
    """取某列从 ``start_row`` 起的非空值（保持行序）。"""
    rows = len(g)
    if rows == 0 or c < 0:
        return []
    return [g[r][c] for r in range(max(0, start_row), rows) if c < len(g[r]) and g[r][c]]


def _pick_year_column(g: list[list[str]], start_row: int) -> int | None:
    """找年份列：值里 ≥60% 是年份的那一列；多列命中时年份率最高、列号最小者优先。"""
    cols = len(g[0]) if g else 0
    best: tuple[tuple[float, int, int], int] | None = None
    for c in range(cols):
        values = _column_values(g, c, start_row)
        if not values:
            continue
        hits = sum(1 for value in values if _year_of(value))
        ratio = hits / len(values)
        if hits < 1 or ratio < _YEAR_COL_RATIO:
            continue
        key = (ratio, hits, -c)
        if best is None or key > best[0]:
            best = (key, c)
    return best[1] if best else None


def _header_name(g: list[list[str]], start_row: int, c: int) -> str:
    """某列的表头文字（取表头行里该列的非空值，用 / 连接）。"""
    parts = [g[r][c] for r in range(max(0, start_row)) if c < len(g[r]) and g[r][c]]
    return "/".join(parts)


def _looks_serial(values: list[str]) -> bool:
    """数值列是否像“序号”：连续整数 1,2,3…（≥3 个才判）。"""
    numbers: list[int] = []
    for value in values:
        if not _is_pure_number(value) or "%" in value or "％" in value:
            return False
        try:
            numbers.append(int(float(value.replace(",", "").replace("，", ""))))
        except ValueError:
            return False
    return len(numbers) >= 3 and numbers == list(range(numbers[0], numbers[0] + len(numbers)))


def _pick_value_column(g: list[list[str]], start_row: int, exclude: set[int]) -> int | None:
    """找数值列：非空值里 ≥50% 是纯数值；排除序号 / 编号列，优先表头带“值/金额/收入”的列。"""
    cols = len(g[0]) if g else 0
    candidates: list[tuple[int, list[str]]] = []
    for c in range(cols):
        if c in exclude:
            continue
        values = _column_values(g, c, start_row)
        real = [value for value in values if not _is_missing(value)]
        if not real:
            continue
        hits = sum(1 for value in real if _is_pure_number(value))
        if hits < 1 or hits / len(real) < 0.5:
            continue
        candidates.append((c, real))
    if not candidates:
        return None
    named = [item for item in candidates
             if not any(hint in _header_name(g, start_row, item[0]).lower()
                        for hint in _SERIAL_HINTS)]
    pool = named or candidates
    hinted = [item for item in pool
              if any(hint in _header_name(g, start_row, item[0]) for hint in _VALUE_HINTS)]
    if hinted:
        return hinted[0][0]
    serial = [item for item in pool if _looks_serial(item[1])]
    picked = [item for item in pool if item not in serial] or pool
    best = max(picked, key=lambda item: (
        sum(1 for value in item[1] if _is_pure_number(value)), -item[0]))
    return best[0]


def _pick_indicator_column(g: list[list[str]], start_row: int, exclude: set[int]) -> int | None:
    """找指标列：非空值里 ≥60% 是文字（既不是纯数值也不是年份）的最左一列。"""
    cols = len(g[0]) if g else 0
    for c in range(cols):
        if c in exclude:
            continue
        values = _column_values(g, c, start_row)
        real = [value for value in values if not _is_missing(value)]
        if not real:
            continue
        text_hits = sum(
            1 for value in real if not _is_pure_number(value) and not _year_of(value))
        if text_hits >= 1 and text_hits / len(real) >= 0.6:
            return c
    return None


def _find_long_columns(
        g: list[list[str]], start_row: int) -> tuple[int, int, int] | None:
    """定位长表三要素：返回 ``(指标列, 年份列, 数值列)``；不是长表返回 None。"""
    year_col = _pick_year_column(g, start_row)
    if year_col is None:
        return None
    value_col = _pick_value_column(g, start_row, {year_col})
    if value_col is None:
        return None
    indicator_col = _pick_indicator_column(g, start_row, {year_col, value_col})
    if indicator_col is None:
        return None
    return indicator_col, year_col, value_col


def classify(table: dict[str, Any]) -> str:
    """给表格分类：``layout`` / ``wide`` / ``long`` / ``data`` / ``unknown``。

    - ``unknown``：rows / cols 都是 0 的坏表（连格子都没有）；
    - ``layout``：``is_layout_table`` 判为版式表；
    - ``wide``：表头里已有年份（指标在左、年份在列），下游可直接算同比；
    - ``long``：有年份列、有文字指标列、有数值列（年份在行），需 ``to_wide`` 展开；
    - ``data``：其余有数字骨架的表。
    """
    g = grid(table)
    if not g:
        return "unknown"
    if is_layout_table(table)[0]:
        return "layout"
    if _has_year_header(table):
        return "wide"
    if _find_long_columns(g, header_map(table)["depth"]) is not None:
        return "long"
    return "data"


def unstack(table: dict[str, Any]) -> dict[str, Any]:
    """把表拆成列式结构 ``{"columns": [{"name", "values"}...]}``（``to_wide`` 复用）。

    - 宽表（表头含年份）：按原列返回，name 取拼平后的表头；
    - 长表（存在年份列 / 指标列 / 数值列）：返回 ``指标`` 一列 + 每年一列，缺年份留空；
    - 无法识别时返回 ``{"columns": []}``。
    """
    g = grid(table)
    if not g:
        return {"columns": []}
    mapped = header_map(table)
    depth = mapped["depth"]
    header_names = mapped["names"]
    year_header_cols = [c for c, name in enumerate(header_names) if YEAR_RE.search(name or "")]
    if year_header_cols:
        data_rows = [g[r] for r in range(depth, len(g)) if any(g[r])]
        columns: list[dict[str, Any]] = []
        for c, name in enumerate(header_names):
            columns.append({
                "name": name or f"列{c + 1}",
                "values": [row[c] if c < len(row) else "" for row in data_rows],
            })
        return {"columns": columns}

    found = _find_long_columns(g, depth)
    if found is None:
        return {"columns": []}
    indicator_col, year_col, value_col = found
    order: list[str] = []
    pivot: dict[str, dict[str, str]] = {}
    for r in range(depth, len(g)):
        row = g[r]
        indicator = row[indicator_col] if indicator_col < len(row) else ""
        year = _year_of(row[year_col] if year_col < len(row) else "")
        if not indicator or not year:
            continue
        if indicator not in pivot:
            pivot[indicator] = {}
            order.append(indicator)
        value = row[value_col] if value_col < len(row) else ""
        pivot[indicator][year] = "" if _is_missing(value) else value
    if not order:
        return {"columns": []}
    years = sorted({year for values in pivot.values() for year in values}, key=int)
    columns = [{"name": "指标", "values": order}]
    for year in years:
        columns.append({
            "name": year,
            "values": [pivot[indicator].get(year, "") for indicator in order],
        })
    return {"columns": columns}


def to_wide(table: dict[str, Any]) -> dict[str, Any]:
    """把「指标 × 年份」长表组装成宽表；宽表原样返回；版式表不转。

    返回 ``{"rows": [dict], "years": [str], "mode": str, "reason": str}``：

    - ``unstacked``：长表 → 每行一个指标，年份各占一列，缺年份留空；
    - ``already_wide``：表头本来就有年份，直接按数据行返回（键为拼平后的列名）；
    - ``not_applicable``：版式表 / 空表 / 找不到三要素，``rows`` 为空并给出原因。

    行顺序 = 指标在源表里首次出现的顺序；年份按数值升序。同一指标同年份重复出现时，
    后出现的行覆盖先前的值（可复现，仅由输入顺序决定）。
    """
    g = grid(table)
    if not g:
        return {"rows": [], "years": [], "mode": "not_applicable",
                "reason": "空表：rows=0，没有可转的表格"}
    layout, layout_reason = is_layout_table(table)
    if layout:
        return {"rows": [], "years": [], "mode": "not_applicable",
                "reason": f"版式表不转宽：{layout_reason}"}

    header = header_map(table)
    depth = header["depth"]
    names = header["names"]
    year_header_cols = [(c, name) for c, name in enumerate(names)
                        if YEAR_RE.search(name or "")]
    if year_header_cols:
        year_cols = {c for c, _ in year_header_cols}
        indicator_col = next(
            (c for c in range(len(names)) if c not in year_cols), 0)
        years = sorted({_year_of(name) for _, name in year_header_cols if _year_of(name)},
                       key=int)
        rows: list[dict[str, str]] = []
        for r in range(depth, len(g)):
            row = g[r]
            if not any(row):
                continue
            item: dict[str, str] = {
                "指标": row[indicator_col] if indicator_col < len(row) else "",
            }
            for c, name in year_header_cols:
                item[name or f"列{c + 1}"] = row[c] if c < len(row) else ""
            rows.append(item)
        return {"rows": rows, "years": years, "mode": "already_wide",
                "reason": f"表头已含 {len(year_header_cols)} 个年份列，无需展开"}

    found = _find_long_columns(g, depth)
    if found is None:
        return {"rows": [], "years": [], "mode": "not_applicable",
                "reason": "没找到「年份列 + 指标列 + 数值列」，既不是长表也不是宽表"}
    indicator_col, year_col, value_col = found
    columns = unstack(table)["columns"]
    if not columns:
        return {"rows": [], "years": [], "mode": "not_applicable",
                "reason": "长表三要素已定位，但展开后没有数据行"}
    years = [column["name"] for column in columns[1:] if _year_of(column["name"])]
    rows = []
    for index in range(len(columns[0]["values"])):
        rows.append({column["name"]: column["values"][index] for column in columns})
    return {
        "rows": rows,
        "years": years,
        "mode": "unstacked",
        "reason": (f"年份列={year_col}、指标列={indicator_col}、数值列={value_col}，"
                   f"已展开为 指标×年份（{len(rows)} 个指标 × {len(years)} 年）"),
    }


# ============================================================ ⑤ 导出
def _csv_escape(value: Any) -> str:
    """CSV 单元格转义：逗号 / 引号 / 换行必须加引号，内部引号双写。"""
    text = "" if value is None else str(value)
    if any(ch in text for ch in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def _rows_for_csv(payload: Any) -> list[Any]:
    """把 table / to_wide 结果 / 行列表统一成“行序列”。"""
    if isinstance(payload, dict):
        if "cells" in payload:
            return grid(payload)
        rows = payload.get("rows")
        return list(rows) if isinstance(rows, (list, tuple)) else []
    if isinstance(payload, (list, tuple)):
        return list(payload)
    return []


def to_csv(rows_or_table: Any) -> str:
    """CSV 文本（逗号分隔、无尾随换行）。

    支持三种入参：
    - ``normalize.parse_table`` 的 table（走 ``grid``）；
    - ``to_wide(...)`` 的整个返回值（取 ``rows``）；
    - 行列表：``list[dict]``（表头取键的首次出现顺序）或 ``list[list]``。
    """
    rows = _rows_for_csv(rows_or_table)
    if not rows:
        return ""
    if isinstance(rows[0], dict):
        names: list[str] = []
        for row in rows:
            for key in row:
                if key not in names:
                    names.append(key)
        lines = [",".join(_csv_escape(name) for name in names)]
        for row in rows:
            lines.append(",".join(_csv_escape(row.get(name, "")) for name in names))
        return "\n".join(lines)
    return "\n".join(
        ",".join(_csv_escape(value) for value in row) for row in rows)


# ============================================================ ⑥ 自测
def _table_from_rows(rows: list[list[str]], header_rows: int = 0) -> dict[str, Any]:
    """自测辅助：二维文本 → parse_table 同构 table（可选把前几行标成 th）。"""
    cells = []
    cols = 0
    for r, row in enumerate(rows):
        cols = max(cols, len(row))
        for c, text in enumerate(row):
            cells.append({
                "r": r, "c": c, "rowspan": 1, "colspan": 1,
                "header": r < header_rows, "text": text,
            })
    return {"rows": len(rows), "cols": cols, "cells": cells}


def _self_test() -> None:
    """零网络自检：版式过滤 / 真数据表 / 双行表头 / 长表转宽 / CSV / 边界与确定性。"""
    import csv
    import io

    # ① 导航条：| 分隔符也是单元格 → 版式表
    nav = _table_from_rows([["首页", "|", "原创", "|", "推荐", "|", "视频", "|", "评论"]])
    flagged, reason = is_layout_table(nav)
    assert flagged, reason
    assert "分隔符" in reason, reason

    # ② 真数据表：含 412.68 / 7.9%，绝不能被误杀
    data = _table_from_rows([
        ["指标", "绝对值", "同比"],
        ["餐饮收入", "412.68", "7.9%"],
        ["住宿收入", "88.10", "3.2%"],
    ], header_rows=1)
    flagged, reason = is_layout_table(data)
    assert flagged is False, reason
    assert 0.3 < numeric_ratio(data) < 0.6, numeric_ratio(data)
    assert classify(data) == "data", classify(data)

    # ③ 双行表头：指标 rowspan=2 + 2024/2025 各带绝对值/同比
    double = {"rows": 3, "cols": 5, "cells": [
        {"r": 0, "c": 0, "rowspan": 2, "colspan": 1, "header": True, "text": "指标"},
        {"r": 0, "c": 1, "rowspan": 1, "colspan": 2, "header": True, "text": "2024年"},
        {"r": 0, "c": 3, "rowspan": 1, "colspan": 2, "header": True, "text": "2025年"},
        {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "header": True, "text": "绝对值"},
        {"r": 1, "c": 2, "rowspan": 1, "colspan": 1, "header": True, "text": "同比"},
        {"r": 1, "c": 3, "rowspan": 1, "colspan": 1, "header": True, "text": "绝对值"},
        {"r": 1, "c": 4, "rowspan": 1, "colspan": 1, "header": True, "text": "同比"},
        {"r": 2, "c": 0, "rowspan": 1, "colspan": 1, "header": False, "text": "餐饮收入"},
        {"r": 2, "c": 1, "rowspan": 1, "colspan": 1, "header": False, "text": "412.68"},
        {"r": 2, "c": 2, "rowspan": 1, "colspan": 1, "header": False, "text": "7.9%"},
        {"r": 2, "c": 3, "rowspan": 1, "colspan": 1, "header": False, "text": "456.70"},
        {"r": 2, "c": 4, "rowspan": 1, "colspan": 1, "header": False, "text": "8.1%"},
    ]}
    assert header_rows(double) == 2, header_rows(double)
    assert header_map(double)["names"] == [
        "指标", "2024年/绝对值", "2024年/同比", "2025年/绝对值", "2025年/同比",
    ], header_map(double)["names"]
    assert classify(double) == "wide", classify(double)

    # ④ 长表：年份在行、指标在列 → 转宽后一年一列，缺年份留空
    long_table = _table_from_rows([
        ["年份", "指标", "值"],
        ["2022", "餐饮收入", "412.68"],
        ["2023", "餐饮收入", "456.70"],
        ["2024", "餐饮收入", "512.00"],
        ["2022", "住宿收入", "88.10"],
        ["2023", "住宿收入", "95.20"],
    ], header_rows=1)
    assert classify(long_table) == "long", classify(long_table)
    wide = to_wide(long_table)
    assert wide["mode"] == "unstacked", wide
    assert wide["years"] == ["2022", "2023", "2024"], wide["years"]
    assert wide["rows"] == [
        {"指标": "餐饮收入", "2022": "412.68", "2023": "456.70", "2024": "512.00"},
        {"指标": "住宿收入", "2022": "88.10", "2023": "95.20", "2024": ""},
    ], wide["rows"]

    # ⑤ 边界：越界 cell / 超大合并跨度不抛异常；确定性：同一输入重复调用一致
    weird = {"rows": 2, "cols": 2, "cells": [
        {"r": 0, "c": 0, "rowspan": 2, "colspan": 2, "header": False, "text": "合并"},
        {"r": -1, "c": 9, "rowspan": 1, "colspan": 1, "header": False, "text": "越界"},
    ]}
    assert grid(weird) == [["合并", ""], ["", ""]], grid(weird)
    assert to_wide(long_table) == to_wide(long_table)

    # ⑥ CSV 转义：逗号 / 引号 / 换行经 csv.reader 读回必须无损
    tricky = [["a,b", '说"引号"', "换行\n第二行"], ["1", "", "3"]]
    parsed = list(csv.reader(io.StringIO(to_csv(tricky))))
    assert parsed == tricky, parsed
    assert to_csv(to_wide(long_table)).splitlines()[0] == "指标,2022,2023,2024"

    print("[OK] table_ops 自检通过：版式过滤 / 多行表头 / 长表转宽 / CSV / 边界与确定性"
          "（零网络）")


if __name__ == "__main__":
    _self_test()
