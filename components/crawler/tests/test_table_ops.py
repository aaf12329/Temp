# -*- coding: utf-8 -*-
"""表格算子测试 —— 零网络、零落盘，只吃内置 table dict（真实数据只做只读回归）。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest tests.test_table_ops -v

覆盖：
    ① 常量契约：SEPARATOR_CHARS / YEAR_RE / NUM_RE / LAYOUT_WORDS / UNIT_WORDS
    ② grid：合并单元格只看左上角、越界 cell 不炸、超大跨度裁剪、行列缺失兜底
    ③ numeric_ratio：真数据表 vs 空表
    ④ is_layout_table：导航条（分隔符 / 版式词）/ 目录文字块 / 单列 / 标签碎片 → True；
       真数据表（412.68 / 7.9% / 带单位）→ False
    ⑤ header_rows / header_map：双行表头拼平、无 th 伪表头、无表头
    ⑥ classify：layout / wide / long / data / unknown 全分支
    ⑦ to_wide / unstack：长表展开、缺年份留空、宽表原样、版式表不转
    ⑧ to_csv：逗号 / 引号 / 换行转义可被 csv.reader 无损读回
    ⑨ 确定性：同一输入重复调用结果完全一致
"""

import csv
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from webcrawl import table_ops                              # noqa: E402


def _cell(r, c, text, rowspan=1, colspan=1, header=False):
    """构造一个 parse_table 同构的 cell。"""
    return {"r": r, "c": c, "rowspan": rowspan, "colspan": colspan,
            "header": header, "text": text}


def _table(rows, cols, cells):
    """构造一个 parse_table 同构的 table。"""
    return {"rows": rows, "cols": cols, "cells": cells}


def _from_rows(rows, header_rows=0):
    """二维文本 → table；前 header_rows 行标成 <th>。"""
    cells = []
    cols = 0
    for r, row in enumerate(rows):
        cols = max(cols, len(row))
        for c, text in enumerate(row):
            cells.append(_cell(r, c, text, header=r < header_rows))
    return _table(len(rows), cols, cells)


def _nav_table():
    """真实数据里那类整页导航：文字与 | 分隔符交替。"""
    return _from_rows([["首页", "|", "原创", "|", "推荐", "|", "视频", "|", "评论"]])


def _data_table():
    """真数据表：3 行 3 列，餐饮收入 412.68 / 同比 7.9%（绝不能被版式判据误杀）。"""
    return _from_rows([
        ["指标", "绝对值", "同比"],
        ["餐饮收入", "412.68", "7.9%"],
        ["住宿收入", "88.10", "3.2%"],
    ], header_rows=1)


def _double_header_table():
    """双行表头：指标 rowspan=2；2024年 / 2025年 各带 绝对值 / 同比。"""
    return _table(3, 5, [
        _cell(0, 0, "指标", rowspan=2, header=True),
        _cell(0, 1, "2024年", colspan=2, header=True),
        _cell(0, 3, "2025年", colspan=2, header=True),
        _cell(1, 1, "绝对值", header=True),
        _cell(1, 2, "同比", header=True),
        _cell(1, 3, "绝对值", header=True),
        _cell(1, 4, "同比", header=True),
        _cell(2, 0, "餐饮收入"),
        _cell(2, 1, "412.68"),
        _cell(2, 2, "7.9%"),
        _cell(2, 3, "456.70"),
        _cell(2, 4, "8.1%"),
    ])


def _long_table():
    """长表：年份在行、指标在列；住宿收入缺 2024 年这一行。"""
    return _from_rows([
        ["年份", "指标", "值"],
        ["2022", "餐饮收入", "412.68"],
        ["2023", "餐饮收入", "456.70"],
        ["2024", "餐饮收入", "512.00"],
        ["2022", "住宿收入", "88.10"],
        ["2023", "住宿收入", "95.20"],
    ], header_rows=1)


class TestConstants(unittest.TestCase):
    """常量契约：下游按名字取用，不能悄悄改名。"""

    def test_separator_chars_contains_pipe(self):
        self.assertIsInstance(table_ops.SEPARATOR_CHARS, str)
        self.assertIn("|", table_ops.SEPARATOR_CHARS)

    def test_year_re_matches_year_label(self):
        self.assertEqual(table_ops.YEAR_RE.search("2024年").group(1), "2024")
        self.assertEqual(table_ops.YEAR_RE.search("（2023）").group(1), "2023")
        self.assertIsNone(table_ops.YEAR_RE.search("12024"))

    def test_num_re_matches_numbers(self):
        self.assertTrue(table_ops.NUM_RE.search("412.68 亿元"))
        self.assertTrue(table_ops.NUM_RE.search("7.9%"))
        self.assertFalse(table_ops.NUM_RE.search("餐饮收入"))

    def test_word_sets(self):
        self.assertIn("首页", table_ops.LAYOUT_WORDS)
        self.assertIn("原创", table_ops.LAYOUT_WORDS)
        self.assertIn("亿元", table_ops.UNIT_WORDS)
        self.assertIn("%", table_ops.UNIT_WORDS)


class TestGrid(unittest.TestCase):
    """cells → 二维文本：合并单元格只看左上角，越界不炸。"""

    def test_merged_cell_only_top_left(self):
        table = _table(3, 3, [
            _cell(0, 0, "合并", rowspan=2, colspan=2),
            _cell(2, 2, "右下"),
        ])
        self.assertEqual(table_ops.grid(table), [
            ["合并", "", ""],
            ["", "", ""],
            ["", "", "右下"],
        ])

    def test_out_of_range_cell_is_ignored(self):
        table = _table(2, 2, [
            _cell(0, 0, "A"),
            _cell(-1, 9, "越界"),
            _cell(10, 0, "越界2"),
        ])
        self.assertEqual(table_ops.grid(table), [["A", ""], ["", ""]])

    def test_oversized_span_is_clipped(self):
        table = _table(2, 2, [_cell(0, 0, "X", rowspan=99, colspan=99)])
        self.assertEqual(table_ops.grid(table), [["X", ""], ["", ""]])

    def test_shape_derived_when_missing(self):
        table = _table(0, 0, [_cell(1, 1, "Z")])
        self.assertEqual(table_ops.grid(table), [["", ""], ["", "Z"]])

    def test_empty_table_returns_empty_grid(self):
        self.assertEqual(table_ops.grid(_table(0, 0, [])), [])


class TestNumericRatio(unittest.TestCase):
    """数字密度：含数字单元格 / 非空单元格。"""

    def test_data_table_ratio(self):
        ratio = table_ops.numeric_ratio(_data_table())
        self.assertGreater(ratio, 0.3)
        self.assertLess(ratio, 0.6)

    def test_empty_table_ratio_is_zero(self):
        self.assertEqual(table_ops.numeric_ratio(_table(0, 0, [])), 0.0)
        self.assertEqual(table_ops.numeric_ratio(_table(2, 2, [])), 0.0)


class TestIsLayoutTable(unittest.TestCase):
    """版式表：整页导航 / 目录 / 单列文字块；真数据表必须放过。"""

    def test_navigation_with_separators_is_layout(self):
        flagged, reason = table_ops.is_layout_table(_nav_table())
        self.assertTrue(flagged, reason)
        self.assertIn("分隔符", reason)

    def test_navigation_words_without_separators_is_layout(self):
        table = _from_rows([["首页", "原创", "推荐", "视频", "图库", "评论"]])
        flagged, reason = table_ops.is_layout_table(table)
        self.assertTrue(flagged, reason)
        self.assertIn("版式词", reason)

    def test_real_data_table_is_not_layout(self):
        flagged, reason = table_ops.is_layout_table(_data_table())
        self.assertFalse(flagged, reason)
        self.assertIn("数字骨架", reason)

    def test_data_table_with_units_is_not_layout(self):
        table = _from_rows([
            ["指标", "2024年", "同比"],
            ["餐饮收入", "412.68 亿元", "7.9%"],
        ], header_rows=1)
        flagged, reason = table_ops.is_layout_table(table)
        self.assertFalse(flagged, reason)

    def test_empty_table_is_layout(self):
        flagged, reason = table_ops.is_layout_table(_table(0, 0, []))
        self.assertTrue(flagged, reason)
        self.assertIn("空表", reason)

    def test_single_column_is_layout(self):
        table = _from_rows([["项目"], ["合计"], ["餐饮收入"]])
        flagged, reason = table_ops.is_layout_table(table)
        self.assertTrue(flagged, reason)
        self.assertIn("有效列数", reason)

    def test_directory_blob_is_layout(self):
        blob = "一 行政区划和气候" + "1-1 黔东南州行政区划" * 80
        table = _table(4, 3, [
            _cell(0, 0, "《黔东南统计年鉴2024》 - [内容：2024]"),
            _cell(1, 0, "时间：2026-03-16 16:50:06 来源： 作者： 浏览量：0"),
            _cell(2, 0, "历年汇总"),
            _cell(2, 1, "获取资料"),
            _cell(2, 2, blob),
        ])
        flagged, reason = table_ops.is_layout_table(table)
        self.assertTrue(flagged, reason)
        self.assertIn("单格文本", reason)

    def test_short_label_rows_are_layout(self):
        table = _from_rows([["甲", "乙"], ["丙", "丁"], ["戊", "己"],
                            ["庚", "辛"], ["壬", "1"]])
        flagged, reason = table_ops.is_layout_table(table)
        self.assertTrue(flagged, reason)
        self.assertIn("平均长度", reason)

    def test_layout_word_ratio_boundary(self):
        # 2 个版式词 + 4 个普通文字：占比 1/3，仍算版式词堆叠
        table = _from_rows([["首页", "原创", "产业", "收入", "规模", "总额"]])
        flagged, reason = table_ops.is_layout_table(table)
        self.assertTrue(flagged, reason)
        self.assertIn("版式词", reason)


class TestHeader(unittest.TestCase):
    """多行表头：深度与拼平列名。"""

    def test_double_header_depth(self):
        self.assertEqual(table_ops.header_rows(_double_header_table()), 2)

    def test_double_header_names(self):
        mapped = table_ops.header_map(_double_header_table())
        self.assertEqual(mapped["depth"], 2)
        self.assertEqual(mapped["names"], [
            "指标", "2024年/绝对值", "2024年/同比", "2025年/绝对值", "2025年/同比",
        ])
        self.assertEqual(mapped["header"][0],
                         ["指标", "2024年", "2024年", "2025年", "2025年"])

    def test_no_header_returns_zero(self):
        table = _from_rows([["餐饮收入", "很好"], ["住宿收入", "一般"]])
        self.assertEqual(table_ops.header_rows(table), 0)
        self.assertEqual(table_ops.header_map(table),
                         {"header": [], "depth": 0, "names": []})

    def test_pseudo_header_without_th(self):
        # 统计年鉴常用 <td> 做表头：无 th 时按“开头全文字 + 后面有数字”退化识别
        table = _from_rows([
            ["指标", "2024年"],
            ["餐饮收入", "412.68"],
        ])
        self.assertEqual(table_ops.header_rows(table), 1)
        self.assertEqual(table_ops.header_map(table)["names"], ["指标", "2024年"])

    def test_bare_year_th_is_header_not_data(self):
        # th 里只写 “2024”（不带“年”）也是年份标签，不能被当成数字行切断表头
        table = _from_rows([
            ["指标", "2024", "2025"],
            ["餐饮收入", "412.68", "456.70"],
        ], header_rows=1)
        self.assertEqual(table_ops.header_rows(table), 1)
        self.assertEqual(table_ops.header_map(table)["names"], ["指标", "2024", "2025"])


class TestClassify(unittest.TestCase):
    """分类全分支。"""

    def test_layout(self):
        self.assertEqual(table_ops.classify(_nav_table()), "layout")

    def test_wide(self):
        self.assertEqual(table_ops.classify(_double_header_table()), "wide")

    def test_wide_with_bare_year_header(self):
        table = _from_rows([
            ["指标", "2024", "2025"],
            ["餐饮收入", "412.68", "456.70"],
        ], header_rows=1)
        self.assertEqual(table_ops.classify(table), "wide")

    def test_long_year_first(self):
        self.assertEqual(table_ops.classify(_long_table()), "long")

    def test_long_indicator_first(self):
        table = _from_rows([
            ["指标", "年份", "值"],
            ["餐饮收入", "2022", "412.68"],
            ["餐饮收入", "2023", "456.70"],
        ], header_rows=1)
        self.assertEqual(table_ops.classify(table), "long")

    def test_data(self):
        self.assertEqual(table_ops.classify(_data_table()), "data")

    def test_unknown_for_empty_shape(self):
        self.assertEqual(table_ops.classify(_table(0, 0, [])), "unknown")

    def test_layout_for_empty_cells(self):
        table = _table(2, 1, [_cell(1, 0, "")])
        self.assertEqual(table_ops.classify(table), "layout")


class TestToWide(unittest.TestCase):
    """长表转宽：一年一列、缺年份留空、宽表原样、版式表不转。"""

    def test_unstacked_long_table(self):
        wide = table_ops.to_wide(_long_table())
        self.assertEqual(wide["mode"], "unstacked")
        self.assertEqual(wide["years"], ["2022", "2023", "2024"])
        self.assertEqual(wide["rows"], [
            {"指标": "餐饮收入", "2022": "412.68", "2023": "456.70", "2024": "512.00"},
            {"指标": "住宿收入", "2022": "88.10", "2023": "95.20", "2024": ""},
        ])

    def test_year_label_with_suffix_is_normalized(self):
        table = _from_rows([
            ["指标", "年份", "值"],
            ["餐饮收入", "2022年", "412.68"],
            ["餐饮收入", "2023年", "456.70"],
        ], header_rows=1)
        wide = table_ops.to_wide(table)
        self.assertEqual(wide["years"], ["2022", "2023"])

    def test_already_wide(self):
        wide = table_ops.to_wide(_double_header_table())
        self.assertEqual(wide["mode"], "already_wide")
        self.assertEqual(wide["years"], ["2024", "2025"])
        self.assertEqual(wide["rows"], [{
            "指标": "餐饮收入",
            "2024年/绝对值": "412.68",
            "2024年/同比": "7.9%",
            "2025年/绝对值": "456.70",
            "2025年/同比": "8.1%",
        }])

    def test_already_wide_with_bare_year_header(self):
        table = _from_rows([
            ["指标", "2024", "2025"],
            ["餐饮收入", "412.68", "456.70"],
        ], header_rows=1)
        wide = table_ops.to_wide(table)
        self.assertEqual(wide["mode"], "already_wide")
        self.assertEqual(wide["years"], ["2024", "2025"])
        self.assertEqual(wide["rows"], [
            {"指标": "餐饮收入", "2024": "412.68", "2025": "456.70"},
        ])

    def test_layout_not_applicable(self):
        wide = table_ops.to_wide(_nav_table())
        self.assertEqual(wide["mode"], "not_applicable")
        self.assertEqual(wide["rows"], [])
        self.assertIn("版式表", wide["reason"])

    def test_not_applicable_without_year(self):
        wide = table_ops.to_wide(_data_table())
        self.assertEqual(wide["mode"], "not_applicable")
        self.assertEqual(wide["rows"], [])

    def test_unstack_columns(self):
        columns = table_ops.unstack(_long_table())["columns"]
        self.assertEqual([column["name"] for column in columns],
                         ["指标", "2022", "2023", "2024"])
        self.assertEqual(columns[0]["values"], ["餐饮收入", "住宿收入"])
        self.assertEqual(columns[3]["values"], ["512.00", ""])

    def test_unstack_wide_keeps_header_names(self):
        columns = table_ops.unstack(_double_header_table())["columns"]
        self.assertEqual(columns[0]["name"], "指标")
        self.assertEqual([column["name"] for column in columns][1:],
                         ["2024年/绝对值", "2024年/同比",
                          "2025年/绝对值", "2025年/同比"])


class TestCsv(unittest.TestCase):
    """CSV 转义：交给 csv.reader 能无损读回。"""

    def test_escape_roundtrip(self):
        rows = [["a,b", '说"引号"', "换行\n第二行"], ["1", "", "3"]]
        parsed = list(csv.reader(io.StringIO(table_ops.to_csv(rows))))
        self.assertEqual(parsed, rows)

    def test_table_to_csv(self):
        text = table_ops.to_csv(_data_table())
        self.assertEqual(text.splitlines(), [
            "指标,绝对值,同比",
            "餐饮收入,412.68,7.9%",
            "住宿收入,88.10,3.2%",
        ])
        self.assertFalse(text.endswith("\n"))

    def test_wide_result_to_csv(self):
        text = table_ops.to_csv(table_ops.to_wide(_long_table()))
        self.assertEqual(text.splitlines()[0], "指标,2022,2023,2024")

    def test_dict_rows_union_columns(self):
        text = table_ops.to_csv([
            {"指标": "餐饮收入", "2022": "412.68"},
            {"2023": "456.70", "指标": "住宿收入"},
        ])
        self.assertEqual(text.splitlines(), [
            "指标,2022,2023",
            "餐饮收入,412.68,",
            "住宿收入,,456.70",
        ])

    def test_empty_returns_empty_string(self):
        self.assertEqual(table_ops.to_csv([]), "")
        self.assertEqual(table_ops.to_csv({"rows": [], "years": []}), "")
        self.assertEqual(table_ops.to_csv(_table(0, 0, [])), "")


class TestDeterminism(unittest.TestCase):
    """确定性与边界：同一输入重复调用一致，越界/坏值不抛异常。"""

    def test_repeat_calls_are_equal(self):
        table = _long_table()
        self.assertEqual(table_ops.to_wide(table), table_ops.to_wide(table))
        self.assertEqual(table_ops.classify(table), table_ops.classify(table))
        self.assertEqual(table_ops.header_map(table), table_ops.header_map(table))

    def test_bad_cell_fields_do_not_raise(self):
        table = _table(2, 2, [
            {"r": "0", "c": "1", "text": "坏行列"},
            {"r": 1, "c": 0, "rowspan": "x", "colspan": None, "text": None},
            {"r": 99, "c": 99, "text": "越界"},
        ])
        self.assertEqual(table_ops.grid(table), [["", "坏行列"], ["", ""]])
        self.assertEqual(table_ops.is_layout_table(table)[0], True)
        self.assertEqual(table_ops.classify(table), "layout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
