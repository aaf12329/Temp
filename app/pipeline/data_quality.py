"""候选数据质量的纯统计，不修改数据、不拟合模型、不填补事实。

结构：profile_candidates → quality_report → field_rows / report_lines。
调用：workflow统计清洗前后；CLI与网页只展示真实计数，plots负责图表。
数据流：原始候选/整理候选/可用候选/待复核记录 → 完整度、重复与排除统计。
质量表不是模型可信度评分；不同阶段分母分别列出，LLM新增候选不算新增真实证据。
"""

import math
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation

FIELDS = {"subject": "产业主体", "region": "地域", "period": "测量年度", "metric": "指标",
          "value": "数值", "unit": "单位", "known_at": "发布时间"}


def _value(row, field):
    """只为统计选择字段，日期兼容既有published_at，不改候选对象。"""
    value = row.get(field)
    if field == "known_at" and (value is None or value == ""):
        return row.get("published_at")
    return value


def _missing(value):
    """缺失与格式错误分别统计；零不是缺失，NaN/None/空白才是。"""
    return value is None or (isinstance(value, str) and not value.strip()) or (
        isinstance(value, float) and math.isnan(value))


def _invalid(value, field):
    """字段格式规则与缺失分开，不使用均值、抓取日期或相邻观测修补。"""
    if _missing(value):
        return False
    if field == "value":
        try:
            return isinstance(value, bool) or not Decimal(str(value)).is_finite()
        except InvalidOperation:
            return True
    if field == "period":
        return not re.fullmatch(r"(?:19|20)\d{2}", str(value))
    if field == "known_at":
        try:
            if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:T| |$)", value):
                return True
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return True
    return False


def profile_candidates(rows):
    """统计七个关键字段的缺失/格式错误、来源数与完全同口径同值同发布日重复。"""
    size = len(rows)
    fields = {}
    for field, label in FIELDS.items():
        missing = sum(_missing(_value(row, field)) for row in rows)
        invalid = sum(_invalid(_value(row, field), field) for row in rows)
        fields[field] = {"label": label, "missing": missing, "invalid": invalid,
                         "missing_fraction": missing / size if size else None,
                         "valid": size - missing - invalid}
    keys = []
    for row in rows:
        values = [_value(row, field) for field in FIELDS]
        if all(not _missing(v) and not _invalid(v, field) for field, v in zip(FIELDS, values)):
            keys.append(tuple(str(value).strip() for value in values) + (
                str(row.get("period_type") or ""), str(row.get("claim_type") or ""),
                str(row.get("scope") or ""), tuple(sorted(row.get("uncertainty_flags") or []))))
    return {"rows": size, "fields": fields, "exact_duplicate_candidates": len(keys) - len(set(keys)),
            "source_records": len({row["record_id"] for row in rows if row.get("record_id")})}


def quality_report(before, candidates, usable, rejected, series, review, *, llm_status):
    """对照三种候选集合，排除原因按记录去重计数，无有效记录时仍如实展示零。"""
    reasons = Counter(reason for row in rejected for reason in set(row.get("cleaning_reasons") or []))
    return {"version": 1, "before": before, "after": profile_candidates(candidates),
            "usable": profile_candidates(usable), "llm_status": llm_status,
            "counts": {"raw_candidates": before["rows"], "organized_candidates": len(candidates),
                       "usable_candidates": len(usable), "rejected_candidates": len(rejected),
                       "review_records": len(review), "annual_rows": len(series)},
            "rejection_reasons": [{"code": code, "count": count} for code, count in reasons.most_common(8)],
            "interpretation": "整理可能增加候选；各阶段分母不同，数量变化不代表新增真实观测。"
                              "重复候选保留来源，年度序列另按原有规则去重；完整度不是事实准确率或模型可信度。"}


def field_rows(report):
    """生成可被Excel直接阅读的字段质量对照行，空集合比例留空而非伪造0%。"""
    result = []
    for stage in ("before", "after", "usable"):
        for field, stats in report[stage]["fields"].items():
            result.append({"stage": stage, "field": field, "label": stats["label"],
                           "rows": report[stage]["rows"], **stats})
    return result


def report_lines(report):
    """输出人类可读的清洗前后计数表；由CLI最终报告调用。"""
    counts = report["counts"]
    return ["## 数据整理质量", "", "|阶段|记录数|", "|---|---:|",
            f"|清洗前规则候选|{counts['raw_candidates']}|",
            f"|整理后候选|{counts['organized_candidates']}|",
            f"|可用候选|{counts['usable_candidates']}|",
            f"|待复核记录|{counts['review_records']}|",
            f"|同口径年度行|{counts['annual_rows']}|", "", report["interpretation"], ""]
