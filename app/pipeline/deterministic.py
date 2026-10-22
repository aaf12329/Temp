"""确定性规则抽取（PDF 式）：每份正文按配置正则取数，不调用 LLM。

结构：extract → _period/_known/_match；调用：workflow.analyze 的 deterministic 分支。
数据流：已校验正文 + 配置规则 + 单位表 → 带原文偏移的数值候选。
边界：只接受规则唯一命中的数值；同页冲突、缺年份、缺发布时间或单位不符都留待人工，不猜。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation


def _digest(*parts) -> str:
    """用无歧义 JSON 编码产生稳定标识；同规则同位置重跑不漂移。"""
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _period(title: str) -> int | None:
    """只接受唯一年度统计公报标题；计划、预测和普通新闻标题不提供测量年。"""
    years = set(re.findall(r"(?<!\d)((?:19|20)\d{2})\s*年", title or ""))
    if len(years) != 1:
        return None
    if not re.search(r"统计公报|年度统计报告|年度报告|年报", title or ""):
        return None
    if re.search(r"计划|规划|目标|预计|预测", title or ""):
        return None
    return int(next(iter(years)))


def _known_at(value) -> datetime | None:
    """发布时间必须是明确 ISO 日期；抓取时刻不能冒充发布日。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _fact(document, rule, match, period, known, units, dimensions) -> dict | None:
    """把单条规则命中转换成现有事实格式；任一硬条件不满足返回 None。"""
    raw = match.group(1).replace(",", "")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    conversion = units.get(rule["unit"])
    if not conversion:
        return None
    unit, factor, dimension = conversion
    expected = "rate" if rule["metric"].endswith("_growth_rate") else dimensions.get(rule["metric"])
    if dimension != expected:
        return None
    value = float(number * Decimal(str(factor)))
    if not value >= 0:
        return None
    start, end = match.span(1)
    quote_start, quote_end = match.span(0)
    identity = [document["record_id"], rule["subject"], rule["metric"], period, raw,
                rule["unit"], quote_start]
    fact_id = _digest(*identity)
    context = [{"field": "period", "value": str(period), "kind": "annual_statistical_title",
                "quote": document.get("title") or "", "record_id": document.get("record_id"),
                "text_sha256": document.get("text_sha256")},
               {"field": "value", "value": raw, "kind": "deterministic_rule",
                "quote": match.group(0), "record_id": document.get("record_id"),
                "text_sha256": document.get("text_sha256")}]
    return {
        "fact_id": fact_id, "evidence_id": fact_id,
        "record_id": document.get("record_id"), "url": document.get("url"),
        "subject": rule["subject"], "metric": rule["metric"], "region": rule["region"],
        "period": period, "period_type": rule["period_type"], "value": value, "unit": unit,
        "claim_type": rule["claim_type"], "review_status": "pending", "review_note": "",
        "uncertainty_flags": [], "known_at": known.isoformat(),
        "evidence_excerpt": match.group(0), "char_start": quote_start, "char_end": quote_end,
        "text_path": document.get("text_path"), "text_sha256": document.get("text_sha256"),
        "pages": [], "raw_value": raw, "raw_unit": rule["unit"], "number_char_start": start,
        "number_char_end": end, "duplicate_group": document.get("duplicate_group"),
        "publisher": document.get("publisher"), "title": document.get("title"),
        "passage_id": "deterministic-" + fact_id[:24], "scope": "total",
        "extraction_method": "deterministic_rule", "context_evidence": context,
    }


def extract(documents, rules, units, dimensions):
    """按规则逐文档抽取；返回 (facts, issues)，问题不静默丢弃。"""
    facts, issues = [], []
    if not rules:
        return facts, [{"code": "no_rules", "message": "确定性路径未配置抽取规则，未产生候选。"}]
    for document in documents:
        text = document.get("text") or ""
        title = document.get("title") or ""
        period = _period(title)
        known = _known_at(document.get("published_at"))
        for index, rule in enumerate(rules):
            matches = list(re.finditer(rule["pattern"], text))
            if not matches:
                continue
            values = {match.group(1).replace(",", "") for match in matches}
            label = rule["subject"] + "/" + rule["metric"]
            if len(values) != 1:
                issues.append({"code": "rule_conflict", "record_id": document.get("record_id"),
                               "url": document.get("url"),
                               "message": f"{label} 在同一正文命中多个不同数值，未自动选取。"})
                continue
            if not period or not known:
                issues.append({"code": "missing_period_or_date", "record_id": document.get("record_id"),
                               "url": document.get("url"),
                               "message": f"{label} 缺少唯一年度标题或明确发布时间，未采纳。"})
                continue
            if known.date() < date(period, 12, 31):
                issues.append({"code": "publication_before_period_end",
                               "record_id": document.get("record_id"), "url": document.get("url"),
                               "message": f"{label} 发布时间早于测量年末，未采纳。"})
                continue
            fact = _fact(document, rule, matches[0], period, known, units, dimensions)
            if fact is None:
                issues.append({"code": "invalid_value_or_unit", "record_id": document.get("record_id"),
                               "url": document.get("url"),
                               "message": f"{label} 的数值或单位未通过本地校验。"})
                continue
            facts.append(fact)
    unique = {fact["fact_id"]: fact for fact in facts}
    return list(unique.values()), issues
