"""数值候选的有据上下文关联，只处理已验证语料，不调用 LLM。

结构：_names/_annual_title/_nonannual → _complete → complete_context（重查补全后的口径冲突）。
调用：workflow 抽取后 → complete_context → 纯判定；不导入其他功能模块。
数据流：pending候选 + 对应正文/标题/词表 → 保留原偏移的候选及 context_evidence。
仅唯一年度统计标题可补测量年；不以发布日期或用户问题填事实，不跨文档借值。
限额/企业/非年度/跨地区等上下文仍标记待审核，不解除原有不确定性门槛。
"""

import hashlib
import json
import re
from collections import defaultdict


def _names(text, vocabulary):
    """匹配唯一实体，长实体覆盖嵌套短实体；由上下文关联调用。"""
    matches = [(key, match.start(), match.end()) for key, words in vocabulary.items()
               for word in words for match in re.finditer(re.escape(word), text)]
    return {key for key, start, end in matches if not any(
        a <= start and b >= end and b - a > end - start for _, a, b in matches)}


def _nonannual(text):
    """季度、月份、半年、累计、活动期间均不得当成完整年度观测。"""
    return bool(re.search(r"季度|半年|\d{1,2}\s*月|前[一二三四五六七八九十\d]+个?月|"
                          r"截至|累计|期间|数博会|黄金周|春节|国庆", text))


def _annual_title(title):
    """只接受单一显式年度的统计报告，不把计划、年鉴出版年或新闻年份当测量年。"""
    years = set(re.findall(r"(?<!\d)((?:19|20)\d{2})\s*年", title))
    return next(iter(years)) if (len(years) == 1 and not _nonannual(title)
        and re.search(r"统计公报|年度统计报告|年度报告|年报", title)
        and not re.search(r"计划|规划|目标|预计|预测", title)) else None


def _complete(fact, document, settings):
    """关联当前候选，记录字段证据；由 complete_context逐候选调用。"""
    row = {**fact, "uncertainty_flags": list(fact.get("uncertainty_flags") or []),
           "context_evidence": []}
    flags = set(row["uncertainty_flags"])
    title = document.get("title") or ""
    excerpt = row.get("evidence_excerpt") or ""
    start = row.get("char_start", 0)
    end = row.get("char_end", start)
    text = document["text"]
    # 只查看紧邻上文；跨段出现限制词时宁可待审核，不把局部口径升格为全行业。
    context_start = max(0, start - 240)
    # 口径限制只看上一个句号以来的文字；上一段的“规模以上工业”不能污染粮食产量。
    boundary = text.rfind("。", context_start, start)
    same_sentence = text[boundary + 1:start]
    references = row["context_evidence"]

    def record(field, value, kind, quote, **offsets):
        """当前函数内部统一记录补全或限制证据，不做文件 I/O。"""
        references.append({"field": field, "value": value, "kind": kind, "quote": quote,
                           "record_id": row.get("record_id"), "text_sha256": row.get("text_sha256"),
                           **offsets})

    def fill(field, value, missing_flag, kind, quote):
        """只修复明确缺失字段；不覆盖原文明示值或歧义判定。"""
        if not row.get(field) and missing_flag in flags and value:
            row[field] = value
            flags.discard(missing_flag)
            record(field, value, kind, quote)

    title_regions = _names(title, settings["regions"])
    body_regions = _names(excerpt, settings["regions"])
    # 未列入词表的其他城市也不能被统计标题覆盖。
    place_text = re.sub(r"全市|全县|全区|该市|该县|该区", "", excerpt)
    unknown_place = not body_regions and bool(re.search(
        r"[\u4e00-\u9fff]{2,4}(?:市(?!场)|县|区)", place_text))
    if len(title_regions) == 1 and not unknown_place:
        region = next(iter(title_regions))
        if not body_regions or body_regions == {region}:
            fill("region", region, "missing_region", "document_title", title)

    title_subjects = _names(title, settings["subjects"])
    if len(title_subjects) == 1 and not _names(excerpt, settings["subjects"]):
        fill("subject", next(iter(title_subjects)), "missing_subject", "document_title", title)
        if any(item["field"] == "subject" for item in references):
            flags.add("document_subject_requires_review")
    # “餐饮收入”的指标名明确指出产业；不把同句其他行业的收入也归给餐饮。
    before_number = text[start:row.get("number_char_start", start)]
    if ("餐饮业" in settings["subjects"] and row.get("metric") == "revenue"
            and re.search(r"餐饮收入\s*(?:实现|为|达到|达)?\s*$", before_number)):
        fill("subject", "餐饮业", "missing_subject", "metric_label", before_number)
        if row.get("subject") == "餐饮业":
            if "document_subject_requires_review" in flags:
                record("subject", "餐饮业", "metric_label", before_number)
            flags.discard("document_subject_requires_review")

    nonannual = _nonannual(title + excerpt)
    if nonannual:
        flags.add("nonannual_period")
        record("period_type", "nonannual", "period_restriction", title + "\n" + excerpt)
    elif not row.get("period") and re.search(r"去年|前年|上年|次年|今年|本年|上一年度", before_number):
        flags.add("relative_period_requires_review")
        record("period", "relative_period_requires_review", "relative_period", before_number)
    else:
        fill("period", _annual_title(title), "missing_measurement_year", "annual_statistical_title", title)

    restriction_pattern = r"限额以上|限额以下|规模以上|全产业链"
    if re.search(restriction_pattern, title):
        flags.add("qualified_scope_requires_review")
        record("scope", "qualified_scope_requires_review", "title_scope_restriction", title)
    if re.search(restriction_pattern, same_sentence + excerpt):
        flags.add("qualified_scope_requires_review")
        record("scope", "qualified_scope_requires_review", "scope_restriction", same_sentence + excerpt,
               char_start=context_start + boundary + 1, char_end=end)
    if re.search(r"公司|集团|合作社|该企业|某企业", same_sentence + excerpt):
        flags.add("enterprise_scope_requires_review")
    if same_sentence and _nonannual(same_sentence):
        flags.add("context_period_requires_review")
        record("period_type", "context_period_requires_review", "preceding_context", same_sentence,
               char_start=context_start + boundary + 1, char_end=start)
    claim_pattern = r"目标|规划|计划|预计|预测|有望|力争"
    if re.search(claim_pattern, title):
        flags.add("context_claim_requires_review")
        record("claim_type", "context_claim_requires_review", "title_claim_restriction", title)
    if re.search(claim_pattern, same_sentence):
        flags.add("context_claim_requires_review")
        record("claim_type", "context_claim_requires_review", "claim_restriction", same_sentence,
               char_start=context_start + boundary + 1, char_end=start)
    # 数值紧前的“X产量”才是该数字的指标；粮食主体不能跨到肉类、稻谷等分品种。
    if row.get("subject") and row.get("metric") == "production":
        number_start = row.get("number_char_start", start)
        label_zone = text[max(0, number_start - 48):number_start]
        labels = re.findall(r"([\u4e00-\u9fff]{2,14}产量)\s*$", label_zone)
        if labels:
            aliases = settings["subjects"].get(row["subject"], [row["subject"]])
            if not any(alias in labels[-1] for alias in aliases):
                flags.add("subject_metric_mismatch")
                row["subject"] = None
    row["uncertainty_flags"] = sorted(flags)
    if references:
        # 口径补全后的标识包含原候选及证据，仍是机器候选，不修改原文、数值和审核状态。
        row["extraction_source_id"] = row["fact_id"]
        row["fact_id"] = hashlib.sha256(json.dumps(
            [row["fact_id"], references], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return row


def complete_context(facts, passages, documents, settings):
    """只关联同正文指纹和原始切片匹配的候选，返回带补全证据的新候选列表。"""
    indexed = {passage["passage_id"]: passage for passage in passages}
    docs = {document["text_sha256"]: document for document in documents}
    result = []
    for fact in facts:
        passage = indexed.get(fact.get("passage_id"))
        document = docs.get(fact.get("text_sha256"))
        if (passage and document and passage.get("text_sha256") == fact.get("text_sha256")
                and passage["char_start"] <= fact["char_start"] < fact["char_end"] <= passage["char_end"]
                and document["text"][fact["char_start"]:fact["char_end"]] == fact["evidence_excerpt"]):
            result.append(_complete(fact, document, settings))
        else:
            result.append({**fact, "context_evidence": []})
    grouped = defaultdict(list)
    for row in result:
        key = tuple(row.get(field) for field in ("subject", "region", "metric", "period_type", "unit", "period"))
        if all(key) and row.get("claim_type") == "observation":
            grouped[key].append(row)
    for group in grouped.values():
        if len({row["value"] for row in group}) > 1:
            for row in group:
                row["uncertainty_flags"] = sorted(set(row["uncertainty_flags"]) | {"conflicting_scope_values"})
    return result
