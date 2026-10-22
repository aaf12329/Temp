"""LLM清洗的纯证据边界，不访问API、磁盘或旧研究全局状态。

结构：source_blocks → plan_batches；parse_response → validate_rows → merge_facts。
调用：workflow构造清洗前CSV；pipeline_llm编排批次；workflow.finalize重建年度序列。
数据流：已校验正文和段落 → 带坐标的CSV行 → 不可信LLM JSON → 可追溯候选/待复核记录。
只接受原文中出现的数字，单位换算使用本地表；不从用户问题填产业事实。
"""

import hashlib
import json
import math
import re
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from app.contracts.extraction import ExtractedRow


def source_blocks(selected, documents, limit):
    """文档轮流选取有精确锚点的段落，为清洗前CSV保留前后文和正文头部。"""
    docs = {doc["text_sha256"]: doc for doc in documents}
    groups = defaultdict(deque)
    seen = set()
    for passage in selected:
        doc = docs.get(passage.get("text_sha256"))
        if not doc:
            continue
        start, end = passage["char_start"], passage["char_end"]
        if doc["text"][start:end] != passage["text"] or passage["passage_id"] in seen:
            continue
        seen.add(passage["passage_id"])
        left, right = max(0, start - 480), min(len(doc["text"]), end + 400)
        block = {key: doc.get(key) for key in ("record_id", "url", "title", "publisher",
                 "published_at", "text_path", "text_sha256", "duplicate_group")}
        block.update(block_id=passage["passage_id"], passage_id=passage["passage_id"],
                     char_start=start, char_end=end, text=passage["text"],
                     context_start=left, context_end=right, context=doc["text"][left:right],
                     header=doc["text"][:800], semantic_score=passage.get("semantic_score"),
                     relevance_reason=passage.get("relevance_reason"))
        groups[doc["text_sha256"]].append(block)
    queues, result = deque(groups.values()), []
    while queues and len(result) < limit:
        queue = queues.popleft()
        result.append(queue.popleft())
        if queue:
            queues.append(queue)
    return result


def plan_batches(blocks, batch_chars, max_batches):
    """按实际JSON字符预算组批，不截断数字引句；超预算块保留本地并明确计数。"""
    batches, current, size, omitted = [], [], 0, 0
    for block in blocks:
        cost = len(json.dumps(block, ensure_ascii=False)) + 2
        if cost > batch_chars:
            omitted += 1
            continue
        if current and size + cost > batch_chars:
            batches.append(current)
            current, size = [], 0
        if len(batches) >= max_batches:
            omitted += 1
            continue
        current.append(block)
        size += cost
    if current:
        batches.append(current)
    return batches, omitted


def parse_response(content):
    """只解析完整JSON对象，可去除外层json代码围栏；不修复或猜测截断内容。"""
    text = content.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    result = json.loads(text)
    if not isinstance(result, dict) or set(result) != {"rows"} or not isinstance(result["rows"], list):
        raise ValueError('清洗返回必须为 {"rows": [...]}')
    if len(result["rows"]) > 128:
        raise ValueError("单批清洗记录超过128条，结果未采纳，请缩小批次")
    return result["rows"]


def _quoted(value, block):
    """检查字段引句确实存在于同一材料的已发送标题、头部或上下文。"""
    return bool(value.quote and any(value.quote in str(block.get(name) or "")
                                   for name in ("context", "title", "header")))


def _entity(value, block, vocabulary):
    """规范名只接受显式原词或配置别名，不能靠无关引句证明主体或地域。"""
    if not value.value or not _quoted(value, block):
        return None
    aliases = vocabulary.get(value.value, [value.value])
    return value.value if any(alias in value.quote for alias in aliases) else None


def _title_entity(title, vocabulary):
    """年度统计公报标题可唯一确定地域；不用于其他页面或歧义标题。"""
    if not title or not re.search(r"统计公报|年度统计报告|年度报告|年报", title):
        return None
    if not re.search(r"(?<!\d)(?:19|20)\d{2}\s*年", title):
        return None
    found = [canonical for canonical, aliases in vocabulary.items()
             if any(alias in title for alias in {canonical, *aliases})]
    return found[0] if len(found) == 1 else None


def _date(value):
    """仅接受明确ISO日期，不使用抓取时刻替代发布时间。"""
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:T| |$)", value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _publication(row, block):
    """优先来源元数据；缺失时仅接纳有明确发布标签及对应完整日期的原文。"""
    known = _date(block.get("published_at"))
    if known:
        return known
    value = row.publication_date
    if not _quoted(value, block) or not _date(value.value):
        return None
    quote = value.quote
    if not re.search(r"发布时间|发布日期|发布于|发文日期", quote):
        return None
    matches = re.findall(r"((?:19|20)\d{2})[年./-](\d{1,2})[月./-](\d{1,2})", quote)
    dates = {f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in matches}
    return _date(value.value) if dates == {value.value[:10]} else None


def _year(value, block):
    """测量年度需要独立原文证据，统计标题可关联；发布日和提问均不能填年。"""
    if not value.value or not re.fullmatch(r"(?:19|20)\d{2}", value.value) or not _quoted(value, block):
        return None
    if value.value not in value.quote or re.search(r"发布时间|发布日期|发布于|发文日期", value.quote):
        return None
    title = block.get("title") or ""
    if value.quote in title:
        years = set(re.findall(r"(?:19|20)\d{2}", title))
        if years != {value.value} or not re.search(r"统计公报|年度报告|年报", title):
            return None
    elif not re.search(re.escape(value.value) + r"\s*年", value.quote):
        return None
    return value.value


def _convert(row, block, metadata):
    """逐行验证数字、字段引句、单位维度和统计限制，返回pending候选。"""
    flags = []
    context = block["context"]
    start = context.find(row.quote)
    if start < 0 or context.count(row.quote) != 1:
        raise ValueError("数值引句在本块中不存在或不唯一")
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", row.raw_value):
        raise ValueError("原始数字不是受支持的精确数值")
    matches = list(re.finditer(r"(?<![\d.])" + re.escape(row.raw_value) + r"(?![\d.])", row.quote))
    if len(matches) != 1:
        raise ValueError("原始数字在数值引句中不存在或不唯一")
    number_start = block["context_start"] + start + matches[0].start()
    relative = number_start - block["char_start"]
    if not (0 <= relative <= len(block["text"]) - len(row.raw_value)
            and block["text"][relative:relative + len(row.raw_value)] == row.raw_value):
        raise ValueError("数字只在邻段出现，不属于本次匹配段落")
    metric = row.metric
    base_metric = metric.removesuffix("_growth_rate")
    labels = metadata["metrics"].get(base_metric, [])
    metric_proof = bool(row.metric_quote and any(row.metric_quote in str(block.get(key) or "")
                        for key in ("context", "title", "header")))
    if not metric_proof or not any(label in row.metric_quote for label in labels):
        flags.append("llm_metric_unverified")
    unit = row.unit.value
    conversion = metadata["units"].get(unit)
    if not conversion or not _quoted(row.unit, block) or unit not in row.unit.quote:
        flags.append("llm_unit_unverified")
        numeric, normalized_unit = None, unit
    else:
        normalized_unit, factor, dimension = conversion
        expected = "rate" if metric.endswith("_growth_rate") else metadata["dimensions"].get(metric)
        if dimension != expected:
            flags.append("llm_unit_dimension_mismatch")
        numeric = float(Decimal(row.raw_value.replace(",", "")) * Decimal(factor))
        if not math.isfinite(numeric):
            raise ValueError("单位换算结果不是有限数")
    subject = _entity(row.subject, block, metadata["subjects"])
    region = _entity(row.region, block, metadata["regions"])
    region_from_title = False
    if not region:
        titled = _title_entity(block.get("title"), metadata["regions"])
        if titled:
            region, region_from_title = titled, True
    period = _year(row.year, block)
    known = _publication(row, block)
    for value, flag in [(subject, "missing_subject"), (region, "missing_region"),
                        (period, "missing_measurement_year"), (known, "missing_publication_date")]:
        if not value:
            flags.append(flag)
    if row.period_type != "year":
        flags.append("nonannual_period")
    if row.scope != "total":
        flags.append("llm_scope_requires_review")
    if row.scope_quote and not any(row.scope_quote in str(block.get(key) or "")
                                   for key in ("context", "title", "header")):
        flags.append("llm_scope_unverified")
    # 即使LLM宣称无疑问，数值句内的限定与预测仍由本地复查。
    restrictions = row.quote + "\n" + (row.scope_quote or "") + "\n" + (row.year.quote or "")
    preceding = context[max(0, start - 120):start].split("\n")[-1]
    scope_text = restrictions + preceding + str(block.get("title") or "")
    if re.search(r"限额以上|限额以下|规模以上|规模以下|全产业链", scope_text):
        flags.append("qualified_scope_requires_review")
    if re.search(r"公司|集团|合作社|该企业|某企业", scope_text):
        flags.append("enterprise_scope_requires_review")
    if re.search(r"不变价|可比价|现价|当年价", scope_text):
        flags.append("price_basis_requires_review")
    # 年度公报的年份引句常带“2021年2月28日”发布日，月份不能当成季度/月度口径。
    if re.search(r"季度|半年|\d{1,2}\s*月|前[一二三四五六七八九十\d]+个?月|期间|春节|国庆",
                 row.quote + "\n" + (row.scope_quote or "") + "\n" + preceding):
        flags.append("nonannual_period")
    if re.search(r"目标|规划|计划|预计|预测|有望|力争", restrictions + preceding):
        flags.append("context_claim_requires_review")
    if re.search(r"约|近|超过|突破|不足|至少|不低于|不超过|左右|余|[<>≥≤]", row.quote):
        flags.append("bounded_or_approximate")
    # 数字前的“增加/减少”说明这是变动量，不是当年水平值。
    before_number = row.quote[:matches[0].start()]
    if (not metric.endswith("_growth_rate")
            and re.search(r"增加|减少|增长|下降|上升|提高|降低", before_number)):
        flags.append("change_not_level")
    # 数值紧前的“X产量”必须与主体一致；粮食主体不能覆盖稻谷、肉类等分品种。
    labels = re.findall(r"([\u4e00-\u9fff]{2,14}产量)\s*$", before_number)
    if labels and subject:
        aliases = metadata["subjects"].get(subject, [subject])
        if not any(alias in labels[-1] for alias in aliases):
            flags.append("subject_metric_mismatch")
            subject = None
    if row.needs_review:
        flags.append("llm_requested_review")
    if known and period and known[:10] < f"{period}-12-31":
        flags.append("publication_before_period_end")
    identity = [block["record_id"], number_start, row.raw_value, unit, subject, region, period, metric]
    fact_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    source = {key: block.get(key) for key in ("record_id", "url", "title", "publisher",
              "text_path", "text_sha256", "duplicate_group", "passage_id")}
    evidence = [{"kind": "llm_field_quote", "field": key,
                 **getattr(row, key).model_dump()} for key in
                ("subject", "region", "year", "publication_date", "unit")]
    if region_from_title:
        evidence.append({"kind": "document_title", "field": "region", "value": region,
                         "quote": block.get("title") or ""})
    return {**source, "fact_id": fact_id, "evidence_id": fact_id,
            "subject": subject, "region": region, "period": period,
            "period_type": row.period_type, "metric": metric, "value": numeric,
            "unit": normalized_unit, "raw_value": row.raw_value, "raw_unit": unit,
            "claim_type": row.claim_type, "published_at": known, "known_at": known,
            "review_status": "pending", "review_note": row.review_reason,
            "uncertainty_flags": sorted(set(flags)), "evidence_excerpt": row.quote,
            "char_start": block["context_start"] + start,
            "char_end": block["context_start"] + start + len(row.quote),
            "number_char_start": number_start, "number_char_end": number_start + len(row.raw_value),
            "extraction_method": "llm_source_validated", "scope": row.scope,
            "context_evidence": evidence}


def normalize_llm_row(raw):
    """把模型常见形状偏差归一到契约，不猜测缺失事实。

    scope 只能是字符串；部分模型返回 {"value": "total", "quote": null}。
    review_reason 缺失时用空串，scope_quote 对象只取 quote。其余字段仍严格校验。
    """
    fixed = dict(raw)
    scope = fixed.get("scope")
    if isinstance(scope, dict):
        fixed["scope"] = scope.get("value")
    quote = fixed.get("scope_quote")
    if isinstance(quote, dict):
        fixed["scope_quote"] = quote.get("quote")
    if fixed.get("review_reason") is None:
        fixed["review_reason"] = ""
    if fixed.get("needs_review") is None:
        fixed["needs_review"] = False
    return fixed


def _reattach(row, origin, pool, metadata):
    """同文档内把数字重挂到真正包含该引句的匹配段落；不跨文档、不猜值。"""
    quote = row.quote
    if not quote or not row.raw_value:
        return None
    candidates = []
    for block in pool:
        if block is origin or block.get("record_id") != origin.get("record_id"):
            continue
        if (block.get("text") or "").count(quote) != 1:
            continue
        candidates.append(block)
    if len(candidates) != 1:
        return None
    try:
        fact = _convert(row, candidates[0], metadata)
    except (ValueError, TypeError, ArithmeticError):
        return None
    fact["context_evidence"] = [*fact.get("context_evidence", []), {
        "kind": "llm_block_reattached", "field": "block_id",
        "value": candidates[0].get("block_id"), "quote": fact["evidence_excerpt"],
        "record_id": candidates[0].get("record_id"),
        "text_sha256": candidates[0].get("text_sha256"),
        "original_block_id": origin.get("block_id")}]
    return fact


def validate_rows(rows, blocks, metadata, reattach_blocks=None):
    """逐条验证，某条损坏不丢弃同批其他结果；损坏原记录进入待复核清单。

    reattach_blocks 提供同文档的全部清洗块，用于纠正模型把数字挂错 block_id；
    重挂仍要求引句逐字出现且数字落在该块匹配段落内。
    """
    indexed = {block["block_id"]: block for block in blocks}
    pool = list(reattach_blocks or blocks)
    facts, review = [], []
    for raw in rows:
        raw = normalize_llm_row(raw)
        try:
            row = ExtractedRow.model_validate(raw)
            if row.block_id not in indexed:
                raise ValueError("引用了本批次之外的来源块")
            origin = indexed[row.block_id]
            try:
                facts.append(_convert(row, origin, metadata))
            except (ValueError, TypeError, ArithmeticError) as exc:
                reattached = _reattach(row, origin, pool, metadata)
                if reattached is None:
                    raise exc
                facts.append(reattached)
        except (ValidationError, ValueError, TypeError, ArithmeticError) as exc:
            reason = ("LLM记录格式、引用或原文数字未通过校验"
                      if isinstance(exc, (ValidationError, ArithmeticError))
                      else f"LLM记录格式、引用或原文数字未通过校验：{exc}")
            review.append({"extraction_method": "llm_invalid", "review_status": "pending",
                           "cleaning_reasons": [reason], "llm_raw": raw})
    return facts, review


def merge_facts(original, extracted, covered_passages=()):
    """同来源同数字的LLM结构化记录替换规则候选；去重后同年同口径冲突留待复核。"""
    replacements = {(row.get("record_id"), row.get("number_char_start")) for row in extracted}
    rows = []
    for row in original:
        if (row.get("record_id"), row.get("number_char_start")) in replacements:
            continue
        row = {**row, "uncertainty_flags": list(row.get("uncertainty_flags") or [])}
        if row.get("passage_id") in covered_passages:
            row["uncertainty_flags"].append("llm_not_selected")
        rows.append(row)
    rows.extend(extracted)
    unique = {row["fact_id"]: row for row in rows}
    groups = defaultdict(list)
    for row in unique.values():
        key = tuple(row.get(field) for field in ("subject", "region", "metric", "period_type", "unit", "period"))
        if all(key) and row.get("claim_type") == "observation":
            groups[key].append(row)
    for group in groups.values():
        # 已带其他问题的行（变动量、分品种、口径限制等）不参与冲突判定，
        # 否则“比上年增加”会被当成同年第二观测，把正确的总产量一起排除。
        comparable = [row for row in group if not (
            set(row.get("uncertainty_flags") or []) - {"conflicting_scope_values", "llm_not_selected"})]
        if len({row.get("value") for row in comparable}) > 1:
            for row in comparable:
                row["uncertainty_flags"] = sorted(set(row.get("uncertainty_flags") or []) | {"conflicting_scope_values"})
    return list(unique.values())
