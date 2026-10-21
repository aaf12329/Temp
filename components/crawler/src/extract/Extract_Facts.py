# -*- coding: utf-8 -*-
"""数字事实抽取 —— 从**本地已爬到的文章**里抽出「指标 + 数值 + 单位 + 年份 + 地域」。

输入：`data\\articles.json`（`src\\extract\\Extract_Article.py` 的产物，正文已抽好）
输出：`data\\facts.json`（全字段）+ `data\\facts.csv`（Excel 直接打开）

**不联网、不读原始 HTML** —— 只吃本地文本，跑多少遍结果都一样。
本文件独立：不 import 任何功能层模块，只依赖共用层与路径地址簿（Path_Store）。

抽什么（规则都在下面的常量区，改规则只改那几张表）：
    产值/营收、产业规模、产量产能、种养面积、企业/门店数、SKU 数、增速、政策目标
每条事实都带**原句**做证据，方便人工回溯核对。

================================ 函数结构树 ================================
① 纯逻辑（不读文件、不联网，可离线单测）
    split_sentences(text)            长文按句号/分号/换行切句
    find_numbers(sentence)           找「数字 + 单位」，长的单位优先（万亩先于万）
    classify_metric(sentence)        按关键词判这条属于哪个指标
    find_year(sentence)              找 20xx 年
    find_region(sentence)            找地域（黔东南 / 凯里 / 三都…）
    extract_from_text(text, ctx)     一段正文 → 事实列表
② 批量
    extract_all(articles)            34 篇文章 → 全部事实 + 去重
    dedupe_facts(facts)              同文同值同单位同指标只留一条
③ 输出
    save_outputs(facts, data_dir)    写 facts.json / facts.csv
    print_summary(facts)             终端汇总（指标分布 / 地域分布）
    main(argv)                       命令行入口
    _self_test()                     零文件、零网络的离线自检

============================== 调用关系总览 ==============================
main ──▶ console.force_utf8_output()          （进 CLI 先把终端切 UTF-8）
     ──▶ io_utils.read_json(ps.ARTICLES_JSON)
     ──▶ extract_all ──▶ extract_from_text ──▶ split_sentences
     │                                  ├─▶ find_numbers / find_year / find_region
     │                                  └─▶ classify_metric
     │                └─▶ dedupe_facts
     ──▶ save_outputs ──▶ io_utils.write_json(facts.json)
     │                └─▶ io_utils.write_csv(facts.csv)     （utf-8-sig 带 BOM）
     ──▶ print_summary

================================ 对外返回契约 ============================
extract_from_text(text, ctx) -> [{
    "url": str, "site": str, "title": str|None,
    "metric": str|None,          # 产值/营收、种养面积、企业/门店数…；无法归类为 None
    "value": float,              # 数值
    "unit": str,                 # 亿元 / 万亩 / 家 / % …
    "year": int|None,            # 原句里的 20xx 年
    "region": str|None,          # 原句里的地域词
    "sentence": str,             # 原句（证据，必看）
}]

字段一旦发布不改名；要扩展只加新字段。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps
    from webcrawl import console, io_utils
except ImportError:                     # 直接运行本文件时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import console, io_utils

# ============================================== 规则区（要抽新东西只改这里）
# 指标判定：句子里出现这些关键词，就归到这个指标（按顺序优先，先命中先算）
METRIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("产值/营收", ("产值", "营业收入", "营收", "销售收入", "销售额", "零售额", "营业额")),
    ("产量/产能", ("产量", "产能", "年产", "出栏", "加工量")),
    ("种养面积", ("面积", "种植", "养殖", "基地")),
    ("企业/门店数", ("企业", "商家", "门店", "主体")),
    ("SKU/商品数", ("SKU", "商品数", "单品", "款")),
    ("增速", ("增长", "增速", "同比", "环比", "增幅", "翻番")),
    ("政策目标", ("目标", "力争", "计划", "到20")),
    ("产业规模", ("产业规模", "市场规模", "规模")),
)

# 单位：**长的放前面**，否则「万亩」会被「万」先吃掉
UNITS: tuple[str, ...] = ("亿元", "万元", "万吨", "万亩", "万单", "万人次", "亿元以上",
                          "亿", "万", "亩", "吨", "家", "款", "个", "单", "%", "倍", "人")
UNIT_PATTERN = "|".join(re.escape(unit) for unit in UNITS)
NUMBER_UNIT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(" + UNIT_PATTERN + r")")
YEAR_RE = re.compile(r"(20\d{2})\s*年")
REGIONS: tuple[str, ...] = ("黔东南", "黔南", "黔西南", "贵州", "凯里", "贵阳", "遵义",
                            "毕节", "铜仁", "安顺", "六盘水", "三都", "麻江", "雷山",
                            "丹寨", "黄平", "施秉", "镇远", "台江", "剑河", "黎平")
SENTENCE_SPLIT_RE = re.compile(r"[。！？；\n]+")
MIN_SENTENCE_LEN = 8                    # 太短的碎片（如"2025年。"）直接丢
NAV_SPACE_LIMIT = 8                     # 无逗号且空格切词超过这个数 → 判为导航栏文字


def _looks_like_navigation(sentence: str) -> bool:
    """导航栏/页脚的特征：被空格切成很多短词（"首页 新闻 产经要闻 …"）。

    中文正文里空格很少；导航栏/面包屑几乎全是 2~4 字的短词，
    所以用「短词占比」判断，比看有没有逗号更稳。
    """
    tokens = sentence.split()
    if len(tokens) <= NAV_SPACE_LIMIT:
        return False
    short = sum(1 for token in tokens if len(token) <= 4)
    return short / len(tokens) >= 0.6


# ============================================ ① 纯逻辑（不读文件、不联网）
def split_sentences(text: str) -> list[str]:
    """长文切句：句号 / 叹号 / 问号 / 分号 / 换行都算分句符。"""
    parts = (part.strip() for part in SENTENCE_SPLIT_RE.split(text or ""))
    return [part for part in parts if len(part) >= MIN_SENTENCE_LEN]


def find_numbers(sentence: str) -> list[tuple[float, str]]:
    """找「数字 + 单位」，返回 [(数值, 单位)]；同一句里可能有多个。"""
    results: list[tuple[float, str]] = []
    for raw_value, unit in NUMBER_UNIT_RE.findall(sentence):
        try:
            results.append((float(raw_value), unit))
        except ValueError:
            continue
    return results


def classify_metric(sentence: str) -> str | None:
    """按关键词判断这句话讲的是哪个指标；都不命中返回 None（不硬凑）。"""
    for metric, keywords in METRIC_RULES:
        if any(keyword in sentence for keyword in keywords):
            return metric
    return None


def find_year(sentence: str) -> int | None:
    """找句中的 20xx 年。"""
    match = YEAR_RE.search(sentence)
    return int(match.group(1)) if match else None


def find_region(sentence: str) -> str | None:
    """找句中出现的地域词（长的优先，如「黔东南」先于「黔」）。"""
    for region in REGIONS:
        if region in sentence:
            return region
    return None


def extract_from_text(text: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """一段正文 → 事实列表；每条都带原句做证据。"""
    ctx = ctx or {}
    facts: list[dict[str, Any]] = []
    for sentence in split_sentences(text):
        if _looks_like_navigation(sentence):      # 导航栏里的数字不是事实
            continue
        numbers = find_numbers(sentence)
        if not numbers:
            continue
        metric = classify_metric(sentence)
        year = find_year(sentence)
        region = find_region(sentence)
        for value, unit in numbers:
            facts.append({
                "url": ctx.get("url"),
                "site": ctx.get("site"),
                "title": ctx.get("title"),
                "metric": metric,
                "value": value,
                "unit": unit,
                "year": year,
                "region": region,
                "sentence": sentence,
            })
    return facts


# ============================================ ② 批量 + 去重
def dedupe_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一篇里「指标 + 数值 + 单位 + 年份」完全相同的，只留第一条。"""
    seen: set[tuple[Any, ...]] = set()
    kept: list[dict[str, Any]] = []
    for fact in facts:
        key = (fact.get("url"), fact.get("metric"), fact.get("value"),
               fact.get("unit"), fact.get("year"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(fact)
    return kept


def extract_all(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """34 篇文章 → 全部数字事实（已去重）。"""
    facts: list[dict[str, Any]] = []
    for article in articles:
        text = article.get("text") or ""
        if not text:
            continue
        facts.extend(extract_from_text(text, ctx={
            "url": article.get("url"),
            "site": article.get("site"),
            "title": article.get("title"),
        }))
    return dedupe_facts(facts)


# ============================================ ③ 输出
def save_outputs(facts: list[dict[str, Any]],
                 data_dir: str = ps.PARSED_DIR) -> dict[str, str]:
    """写 facts.json（全字段）+ facts.csv（带 BOM，Excel 直开）。"""
    json_path = os.path.join(data_dir, "facts.json")
    csv_path = os.path.join(data_dir, "facts.csv")
    io_utils.write_json(json_path, facts)

    fields = ("metric", "value", "unit", "year", "region", "site", "url",
              "title", "sentence")
    io_utils.write_csv(csv_path, facts, fields)
    return {"json": os.path.abspath(json_path), "csv": os.path.abspath(csv_path)}


def print_summary(facts: list[dict[str, Any]]) -> None:
    """终端汇总：总条数、指标分布、地域分布、几条示例。"""
    print("=" * 74)
    print(f"数字事实 {len(facts)} 条")
    by_metric: dict[str, int] = {}
    by_region: dict[str, int] = {}
    for fact in facts:
        metric = fact.get("metric") or "未归类"
        by_metric[metric] = by_metric.get(metric, 0) + 1
        region = fact.get("region") or "未标地域"
        by_region[region] = by_region.get(region, 0) + 1
    print("指标分布：" + "，".join(f"{k} {v}" for k, v in
                                  sorted(by_metric.items(), key=lambda kv: -kv[1])))
    print("地域分布：" + "，".join(f"{k} {v}" for k, v in
                                  sorted(by_region.items(), key=lambda kv: -kv[1])[:6]))
    print("-" * 74)
    for fact in facts[:5]:
        print(f"  {fact['metric'] or '未归类':10s} {fact['value']:>8g}{fact['unit']}  "
              f"{fact['year'] or '-'}  {fact['region'] or '-'}")
        print(f"      「{fact['sentence'][:60]}」")
    print("=" * 74)


def main(argv: list[str]) -> int:
    """命令行：读本地 articles.json → 抽事实 → 落 facts.json / facts.csv。"""
    console.force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="Extract_Facts.py",
        description="从本地已爬文章里抽数字事实（指标/数值/单位/年份/地域），不联网。")
    parser.add_argument("--articles", default=ps.ARTICLES_JSON, help="本地文章 JSON")
    parser.add_argument("--data-dir", default=ps.PARSED_DIR, help="输出目录")
    parser.add_argument("--self-test", action="store_true", help="跑离线自检")
    args = parser.parse_args(argv)

    if args.self_test:
        _self_test()
        return 0

    if not os.path.isfile(args.articles):
        print(f"找不到本地文章数据：{args.articles}")
        print("先跑：src\\extract\\Extract_Article.py")
        return 1

    articles = io_utils.read_json(args.articles)
    facts = extract_all(articles)
    print_summary(facts)
    outputs = save_outputs(facts, args.data_dir)
    print(f"已写入：{outputs['json']}\n已写入：{outputs['csv']}")
    return 0


# ============================================ 自测（零文件、零网络）
def _self_test() -> None:
    """用内置句子验证规则：指标归类、单位识别、年份、地域、去重。"""
    ctx = {"url": "https://e.example/a", "site": "e.example", "title": "测试"}

    facts = extract_from_text("2025年黔东南州酸汤产业产值突破25亿元。", ctx)
    assert len(facts) == 1, facts
    fact = facts[0]
    assert fact["metric"] == "产值/营收", fact
    assert fact["value"] == 25.0 and fact["unit"] == "亿元", fact
    assert fact["year"] == 2025 and fact["region"] == "黔东南", fact

    facts = extract_from_text("三都县稻花鱼养殖面积达7.1万亩，同比增长20%。", ctx)
    metrics = {f["metric"] for f in facts}
    assert "种养面积" in metrics, metrics
    assert any(f["unit"] == "万亩" and f["value"] == 7.1 for f in facts), facts
    assert any(f["unit"] == "%" for f in facts), facts          # 百分号也要能抽

    facts = extract_from_text("到2028年力争综合产值达到200亿元。", ctx)
    assert facts[0]["metric"] in ("政策目标", "产值/营收"), facts
    assert facts[0]["year"] == 2028, facts

    assert find_numbers("涨价了") == []
    assert classify_metric("毫无数字的一句话") is None
    assert split_sentences("短。") == []                        # 太短的碎片不要
    nav = "首页 新闻 产经要闻 部委动态 时政新闻 市场环境 统计数据 政策动态 9.54亿"
    assert extract_from_text(nav, ctx) == [], "导航栏文字不该被当成事实"

    same = [{"url": "u", "metric": "m", "value": 1.0, "unit": "亿元", "year": 2025},
            {"url": "u", "metric": "m", "value": 1.0, "unit": "亿元", "year": 2025}]
    assert len(dedupe_facts(same)) == 1
    print("[OK] 自检通过：指标归类 / 数值单位 / 年份 / 地域 / 短句过滤 / 去重")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
