# -*- coding: utf-8 -*-
"""可追溯语料与数字候选；仅做离线分析，所有数字先交人工复核。

函数结构树
① 纯逻辑：_digest / _timestamp / _spans / _scope / _candidates / build_series
② 文件边界：_normalized_path / _read_document / _paragraph_spans
③ 主流程：load_corpus / extract_facts / _self_test
调用关系：main → load_corpus → 文件边界；main → extract_facts → _candidates；
          main → build_series（只有 approved 观测可进入序列）。不调用同层研究模块。
数据流：CrawlResult → 当前正文及校验问题 → 去重段落 → 待复核事实 → 已批准年度序列。
返回契约：load_corpus 返回 documents/passages/issues 三个列表；extract_facts 返回事实列表；
build_series 返回 series_id/period/value/known_at/口径/evidence_ids 的列表。
偏移均为 Python Unicode 码点、左闭右开，excerpt 必须等于正文对应切片。
config.subjects / regions 可用字符串列表或 {规范名: [原文别名]}；不从标题或发布日期猜口径。
不写文件、不发网络请求。缺失/矛盾/目标/预测均不得自动变成训练观测。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

try:
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils

DEFAULT_SUBJECTS = ["酸汤鱼", "酸汤产业", "酸汤", "餐饮业", "辣椒", "番茄", "稻花鱼", "木姜子"]
DEFAULT_REGIONS = ["黔东南", "凯里", "贵州", "贵阳", "全国"]
METRICS = {
    "retail_sales": ("社会消费品零售总额", "零售额"),
    "sales_value": ("销售收入", "销售总额", "销售额"),
    "revenue": ("营业收入", "餐饮收入", "营收", "收入"),
    "industrial_output_value": ("工业总产值",),
    "comprehensive_output_value": ("综合产值",),
    "output_value": ("总产值", "产值"),
    "production": ("总产量", "产量"),
    "planting_area": ("种植面积", "播种面积"),
    "store_count": ("门店数量", "门店数", "门店"),
    "company_count": ("企业数量", "企业数", "企业"),
}
METRIC_DIMENSIONS = {
    "retail_sales": "currency", "sales_value": "currency", "revenue": "currency",
    "output_value": "currency", "industrial_output_value": "currency",
    "comprehensive_output_value": "currency", "production": "mass", "planting_area": "area",
    "store_count": "count", "company_count": "count",
}
UNIT_FACTORS = {
    "亿元": ("元", "100000000", "currency"), "万元": ("元", "10000", "currency"),
    "元": ("元", "1", "currency"), "万吨": ("吨", "10000", "mass"),
    "吨": ("吨", "1", "mass"), "千克": ("吨", "0.001", "mass"),
    "公斤": ("吨", "0.001", "mass"), "万亩": ("亩", "10000", "area"),
    "亩": ("亩", "1", "area"), "万家": ("家", "10000", "count"),
    "家": ("家", "1", "count"), "万户": ("户", "10000", "count"),
    "户": ("户", "1", "count"), "%": ("%", "1", "rate"),
    "％": ("%", "1", "rate"),
}
NUMBER_RE = re.compile(
    r"(?<![\d.])(?P<number>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    r"(?P<unit>亿元|万元|万吨|万亩|万家|万户|千克|公斤|元|吨|亩|家|户|%|％)"
)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})年")
SCOPE_FIELDS = ("subject", "metric", "region", "period_type", "unit")
PASSAGE_LIMIT = 240


# ===== ① 纯逻辑：稳定标识、精确偏移、保守数字候选 =====
def _digest(*parts: Any) -> str:
    """用无歧义 JSON 编码产生稳定标识。"""
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> datetime | None:
    """仅接受显式 ISO 日期，缺失时间不伪造抓取时间。"""
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:T| |$)", value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # 日期精度按当天 UTC 零点处理，仅用于排序/信息可得日期。
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _spans(text: str, start: int = 0, end: int | None = None) -> Iterator[tuple[int, int]]:
    """把精确文本切成不超过 240 字的片段，不 normalize 原文。"""
    end = len(text) if end is None else end
    while start < end:
        stop = min(start + PASSAGE_LIMIT, end)
        if stop < end:
            boundaries = [text.rfind(mark, start, stop) for mark in "。！？；\n"]
            boundary = max(boundaries)
            if boundary >= start + PASSAGE_LIMIT // 2:
                stop = boundary + 1
        if text[start:stop].strip():
            yield start, stop
        start = stop


def _aliases(configured: Any) -> dict[str, list[str]]:
    """把显式口径名单转为规范名及原文别名；非法配置不推断。"""
    if isinstance(configured, list):
        return {name: [name] for name in configured if isinstance(name, str) and name}
    if isinstance(configured, dict):
        return {str(name): [str(name)] + [str(v) for v in aliases if isinstance(v, str)]
                for name, aliases in configured.items() if isinstance(aliases, list) and name}
    return {}


def _scope(text: str, configured: dict[str, list[str]]) -> tuple[str | None, bool]:
    """仅原文明示的实体可匹配；嵌套短实体让位于更长实体。"""
    matches = [(name, alias, match.start(), match.end())
               for name, aliases in configured.items() for alias in aliases if alias
               for match in re.finditer(re.escape(alias), text)]
    names = {name for name, alias, start, end in matches
             if not any(start >= a and end <= b and b - a > end - start
                        for _, _, a, b in matches)}
    return (next(iter(names)), False) if len(names) == 1 else (None, len(names) > 1)


def _metric_matches(text: str) -> list[tuple[int, int, str]]:
    """长指标名称先占位，避免把销售收入二次解释为营业收入。"""
    matches = [(m.start(), m.end(), key) for key, aliases in METRICS.items()
               for alias in aliases for m in re.finditer(re.escape(alias), text)]
    return sorted([match for match in matches
                   if not any(a <= match[0] and b >= match[1] and b - a > match[1] - match[0]
                              for a, b, _ in matches)])


def _claim_type(text: str) -> str:
    """政策目标与预测保留为候选，绝不当成已实现观测。"""
    if re.search(r"力争|目标|规划|计划|拟[于将达建]|将[达建实]|争取|到\s*\d{4}年", text):
        return "target"
    if re.search(r"预计|预测|有望|估计|预期", text):
        return "forecast"
    return "observation"


def _period(text: str, before: int) -> tuple[str | None, list[str]]:
    """使用数值之前最近的显式年度，不用发布日期替代测量时间。"""
    years = list(YEAR_RE.finditer(text[:before]))
    if not years:
        return None, ["missing_measurement_year"]
    latest = years[-1]
    flags: list[str] = []
    context = text[max(0, latest.start() - 8):before]
    if re.search(r"\d{4}\s*[-—至到]\s*\d{4}年|\d{4}年\s*[-—至到]\s*\d{4}年", context):
        flags.append("period_range")
    if re.search(r"\d{4}年\s*[和与及、]\s*\d{4}年", text[:before]):
        flags.append("ambiguous_measurement_year")
    if re.search(r"季度|上半年|下半年|前[一二三四五六七八九十\d]+个?月|\d{1,2}月|截至|累计", context):
        flags.append("nonannual_period")
    return latest.group(1), flags


def _candidate_flags(text: str, match: re.Match[str]) -> list[str]:
    """数值修饰词/区间不能悄悄变成精确观测值。"""
    flags: list[str] = []
    prefix = text[max(0, match.start() - 10):match.start()]
    suffix = text[match.end():match.end() + 6]
    if re.search(r"约|近|超过|超|突破|逾|不足|至少|不低于|不超过|达[到]?近|[<>≥≤]", prefix):
        flags.append("bounded_or_approximate")
    if re.match(r"左右|以上|以下|余|多", suffix):
        flags.append("bounded_or_approximate")
    if re.search(r"\d\s*[-—~～至到]\s*$", prefix) or re.match(r"\s*[-—~～至到]", suffix):
        flags.append("numeric_range")
    return flags


def _candidates(sentence: str, subjects: dict[str, list[str]],
                regions: dict[str, list[str]]) -> Iterator[dict[str, Any]]:
    """每个带单位的数值独立对齐最近指标；跨口径不共享数值。"""
    metrics = _metric_matches(sentence)
    for number in NUMBER_RE.finditer(sentence):
        # 数值前最近的指标，不能越过句号；后面出现的新指标不倒灌。
        preceding = [item for item in metrics if item[1] <= number.start()]
        if not preceding:
            continue
        metric_start, metric_end, metric = preceding[-1]
        boundaries = list(re.finditer(r"[，；;：:]|(?<!\d),(?!\d)", sentence))
        left = max([m.end() for m in boundaries if m.end() <= number.start()] or [0])
        right = min([m.start() for m in boundaries if m.start() >= number.end()]
                    or [len(sentence)])
        clause = sentence[left:right]
        flags = _candidate_flags(sentence, number)
        if metric_start < left:
            if len({entry[2] for entry in metrics}) != 1:
                continue
            flags.append("inherited_metric")
        unit, factor, dimension = UNIT_FACTORS[number.group("unit")]
        metric_context = sentence[metric_end:number.start()]
        if dimension == "rate":
            if not re.search(r"同比|增长|增幅|下降|降幅", metric_context):
                continue
            metric += "_growth_rate"
        elif METRIC_DIMENSIONS[metric] != dimension:
            continue
        elif re.search(r"同比|增长|增加|增幅|下降|减少|降幅", metric_context):
            # “收入增长 2 亿元”是增量，不是收入总量。
            flags.append("change_not_level")
        try:
            value = Decimal(number.group("number").replace(",", "")) * Decimal(factor)
        except InvalidOperation:
            continue
        if not value.is_finite():
            continue
        if re.search(r"下降|减少|降幅", metric_context) and dimension == "rate":
            value = -abs(value)
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            continue
        scope_context = sentence[left:max(left, metric_start)]
        subject, subject_ambiguous = _scope(scope_context, subjects)
        region, region_ambiguous = _scope(scope_context, regions)
        if subject is None and not subject_ambiguous:
            subject, subject_ambiguous = _scope(sentence[:metric_start], subjects)
        if region is None and not region_ambiguous:
            region, region_ambiguous = _scope(sentence[:metric_start], regions)
        if re.search(r"公司|集团|合作社|该企业|某企业", sentence[:metric_start]):
            flags.append("enterprise_scope_requires_review")
        if re.search(r"规模以上|限额以上|限额以下|全产业链", sentence[:metric_start]):
            flags.append("qualified_scope_requires_review")
        if not subject:
            flags.append("ambiguous_subject" if subject_ambiguous else "missing_subject")
        if not region:
            flags.append("ambiguous_region" if region_ambiguous else "missing_region")
        period, period_flags = _period(sentence, number.start())
        flags.extend(period_flags)
        claim_context = clause
        if _claim_type(sentence[:left]) != "observation":
            claim_context = sentence[:right]
        yield {
            "subject": subject, "region": region, "metric": metric,
            "period": period, "period_type": "year", "value": numeric_value, "unit": unit,
            "raw_value": number.group("number"), "raw_unit": number.group("unit"),
            "claim_type": _claim_type(claim_context), "uncertainty_flags": sorted(set(flags)),
            "number_start": number.start(), "number_end": number.end(),
        }


def _scope_key(row: dict[str, Any]) -> tuple[str, ...]:
    """统一序列口径键；不同产业/地域/指标/频率/单位不得合并。"""
    return tuple(str(row.get(field) or "") for field in SCOPE_FIELDS)


def build_series(facts: list[dict[str, Any]], *, require_approval: bool = True
                 ) -> list[dict[str, Any]]:
    """默认从人工批准观测组序列；CLI 自动探索可显式关闭审批，不改候选审核状态。"""
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if ((require_approval and fact.get("review_status") != "approved")
                or fact.get("claim_type") != "observation"):
            continue
        flags = set(fact.get("uncertainty_flags") or []) - {"conflicting_scope_values"}
        if flags or not all(_scope_key(fact)):
            continue
        period = str(fact.get("period") or "")
        known_at = _timestamp(fact.get("published_at") or fact.get("known_at"))
        if fact.get("period_type") != "year" or not re.fullmatch(r"(?:19|20)\d{2}", period):
            continue
        if known_at is None or known_at.date() < date(int(period), 12, 31):
            continue
        try:
            value = float(fact["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(value) or not fact.get("fact_id"):
            continue
        grouped[(*_scope_key(fact), period)].append(fact)
    rows: list[dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        if len({Decimal(str(row["value"])) for row in group}) != 1:
            continue
        known = min(_timestamp(row.get("published_at") or row.get("known_at"))
                    for row in group)
        row = {field: value for field, value in zip(SCOPE_FIELDS, key[:-1])}
        row.update({"series_id": _digest(*key[:-1]), "period": int(key[-1]),
                    "scope": " / ".join(key[:-1]),
                    "value": float(group[0]["value"]), "known_at": known.isoformat(),
                    "evidence_ids": sorted({str(fact["fact_id"]) for fact in group}),
                    "duplicate_groups": sorted({str(fact.get("duplicate_group") or "")
                                                for fact in group})})
        if not require_approval:
            row["evidence_status"] = "machine_candidate_unreviewed"
        rows.append(row)
    return rows


# ===== ② 文件边界：只读 normalized，验证当前快照与记录指纹 =====
def _normalized_path(value: Any, kind: str) -> Path:
    """拒绝绝对/穿越/跨目录路径；resolve 同时阻止链接逃逸。"""
    if not isinstance(value, str) or not value:
        raise ValueError("missing_normalized_path")
    relative = PurePosixPath(value.replace("\\", "/"))
    if relative.is_absolute() or ":" in value or ".." in relative.parts:
        raise ValueError("unsafe_normalized_path")
    allowed = {"text", "pdf"} if kind == "text" else {"para"}
    if len(relative.parts) < 4 or relative.parts[:2] != ("data", "normalized"):
        raise ValueError("not_normalized_path")
    if relative.parts[2] not in allowed:
        raise ValueError("wrong_normalized_kind")
    if relative.suffix != (".txt" if kind == "text" else ".jsonl"):
        raise ValueError("wrong_normalized_extension")
    root = Path(ps.PROJECT_ROOT).resolve()
    target = (root / str(relative)).resolve()
    try:
        target.relative_to(root / "data" / "normalized" / relative.parts[2])
    except ValueError as exc:
        raise ValueError("unsafe_normalized_path") from exc
    return target


def _read_document(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """读最新成功记录正文；共享快照已被覆盖时拒绝陈旧哈希。"""
    pdf = record.get("pdf") or {}
    is_pdf = record.get("status") == "ok_pdf"
    if (record.get("error") or {}).get("code") == "E_PDF_NO_TEXT":
        return None, "E_PDF_NO_TEXT:PDF 无文本层，需 OCR 后再分析。"
    try:
        text_path = (record.get("storage") or {}).get("text_path")
        if not text_path and is_pdf:
            text_path = pdf.get("text_path")
        path = _normalized_path(text_path, "text")
        text = io_utils.read_text(path)
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f"text_unavailable:{exc}"
    actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not record.get("text_sha256"):
        return None, "missing_text_sha256"
    if actual_hash != record.get("text_sha256"):
        return None, "text_hash_mismatch"
    if not text.strip():
        return None, "empty_text"
    source = record.get("source") or {}
    document = {
        "record_id": record.get("record_id"), "job_id": record.get("job_id"),
        "url": record.get("url"), "title": record.get("title"),
        "publisher": source.get("publisher"), "published_at": source.get("published_at"),
        "fetched_at": record.get("fetched_at"), "text": text,
        "text_chars": len(text),
        "text_path": path.relative_to(Path(ps.PROJECT_ROOT).resolve()).as_posix(),
        "text_sha256": actual_hash, "paragraph_file": record.get("paragraph_file"),
        "is_pdf": is_pdf, "page_map": pdf.get("page_map") or [],
        "duplicate_group": _digest(unicodedata.normalize("NFKC", " ".join(text.split()))),
    }
    return document, None


def _paragraph_spans(document: dict[str, Any]) -> tuple[list[tuple[int, int]], str | None]:
    """校验段落锚点；锚点坏掉时使用正文原始行，记录问题而非错用偏移。"""
    text = document["text"]
    if document.get("is_pdf") and not document.get("paragraph_file"):
        anchors = document.get("page_map") or []
        if not anchors:
            return list(_spans(text)), None
        try:
            spans = []
            previous_end, previous_page = 0, 0
            for anchor in anchors:
                start, end, page = anchor["char_start"], anchor["char_end"], anchor["page"]
                if (not all(type(value) is int for value in (start, end, page))
                        or not previous_end <= start < end <= len(text)
                        or page <= previous_page or text[previous_end:start].strip()):
                    raise ValueError("pdf_page_map_offsets_mismatch")
                spans.extend(_spans(text, start, end))
                previous_end, previous_page = end, page
            if text[previous_end:].strip():
                raise ValueError("pdf_page_map_coverage_mismatch")
            return spans, None
        except (KeyError, ValueError, TypeError) as exc:
            return list(_spans(text)), f"pdf_page_map_fallback:{exc}"
    try:
        if not document.get("paragraph_file"):
            raise ValueError("missing_paragraph_file")
        path = _normalized_path(document["paragraph_file"], "para")
        anchors = [json.loads(line) for line in io_utils.read_text(path).splitlines() if line]
        spans = []
        previous_end = 0
        for anchor in anchors:
            start, end = anchor["char_start"], anchor["char_end"]
            if (not isinstance(start, int) or not isinstance(end, int)
                    or not previous_end <= start < end <= len(text)
                    or text[start:end] != anchor["text"]
                    or text[previous_end:start].strip()):
                raise ValueError("paragraph_offsets_mismatch")
            spans.extend(_spans(text, start, end))
            previous_end = end
        if not spans or text[previous_end:].strip():
            raise ValueError("paragraph_coverage_mismatch")
        return spans, None
    except (OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        return list(_spans(text)), f"paragraph_fallback:{exc}"


# ===== ③ 主流程：零写盘编排 =====
def load_corpus(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """选择每 URL 最新成功快照，校验后跨 URL 精确去重，保留来源变体。"""
    latest: dict[str, dict[str, Any]] = {}
    floor = datetime.min.replace(tzinfo=timezone.utc)
    for record in records:
        if not isinstance(record, dict) or not record.get("url"):
            continue
        no_pdf_text = (record.get("error") or {}).get("code") == "E_PDF_NO_TEXT"
        if record.get("status") not in {"ok", "ok_pdf"} and not no_pdf_text:
            continue
        url = str(record["url"])
        previous = latest.get(url)
        stamp = (_timestamp(record.get("fetched_at")) or floor, str(record.get("record_id", "")))
        old_stamp = ((_timestamp(previous.get("fetched_at")) or floor,
                      str(previous.get("record_id", ""))) if previous else None)
        if old_stamp is None or stamp > old_stamp:
            latest[url] = record
    groups: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for url, record in sorted(latest.items()):
        document, problem = _read_document(record)
        if problem:
            issues.append({"record_id": record.get("record_id"), "url": url, "issue": problem,
                           "action": "重新抓取或核对当前 normalized 正文及索引指纹。"})
            continue
        assert document is not None
        variant = {key: document.get(key) for key in (
            "record_id", "job_id", "url", "title", "publisher", "published_at", "fetched_at",
            "text_path", "text_sha256")}
        group = document["duplicate_group"]
        if group in groups:
            groups[group]["source_variants"].append(variant)
            groups[group]["record_ids"].append(document["record_id"])
        else:
            document["source_variants"] = [variant]
            document["record_ids"] = [document["record_id"]]
            groups[group] = document
    documents = list(groups.values())
    passages: list[dict[str, Any]] = []
    for document in documents:
        spans, problem = _paragraph_spans(document)
        if problem:
            issues.append({"record_id": document["record_id"], "url": document["url"],
                           "issue": problem, "action": "核对段落锚点；本轮使用正文精确偏移。"})
        for start, end in spans:
            passage = {key: value for key, value in document.items()
                       if key not in ("text", "paragraph_file", "page_map")}
            passage.update({"passage_id": _digest(document["text_sha256"], start, end),
                            "char_start": start, "char_end": end,
                            "text": document["text"][start:end]})
            passage["pages"] = ([anchor["page"] for anchor in document.get("page_map", [])
                                 if anchor["char_start"] <= start < end <= anchor["char_end"]]
                                if document.get("is_pdf") and not document.get("paragraph_file")
                                and problem is None else [])
            passages.append(passage)
    return {"documents": documents, "passages": passages, "issues": issues}


def extract_facts(selected_passages: list[dict[str, Any]],
                  config: dict[str, Any]) -> list[dict[str, Any]]:
    """抽取带原句的数字候选；任何候选的 review_status 初始值始终为 pending。"""
    subjects = _aliases(config.get("subjects", DEFAULT_SUBJECTS))
    regions = _aliases(config.get("regions", DEFAULT_REGIONS))
    facts: dict[str, dict[str, Any]] = {}
    for passage in selected_passages:
        text = passage.get("text") or ""
        base = passage.get("char_start", 0)
        if not isinstance(text, str) or not isinstance(base, int):
            continue
        for sentence_match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
            sentence = sentence_match.group()
            for candidate in _candidates(sentence, subjects, regions):
                start = base + sentence_match.start()
                end = base + sentence_match.end()
                number_start = start + candidate.pop("number_start")
                number_end = start + candidate.pop("number_end")
                normalized_value = str(Decimal(str(candidate["value"])).normalize())
                fact_id = _digest(passage.get("passage_id"), number_start, number_end,
                                  *_scope_key(candidate), candidate["period"],
                                  normalized_value, candidate["claim_type"])
                fact = {key: passage.get(key) for key in (
                    "passage_id", "record_id", "url", "title", "publisher", "published_at",
                    "fetched_at", "text_path", "text_sha256", "duplicate_group", "pages")}
                fact.update(candidate)
                fact.update({"fact_id": fact_id, "evidence_id": passage.get("passage_id"),
                             "review_status": "pending", "known_at": passage.get("published_at"),
                             "char_start": start, "char_end": end, "evidence_excerpt": sentence,
                             "number_char_start": number_start, "number_char_end": number_end})
                if _timestamp(fact["published_at"]) is None:
                    fact["uncertainty_flags"].append("missing_publication_date")
                facts[fact_id] = fact
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts.values():
        if all(_scope_key(fact)) and fact.get("period") and fact["claim_type"] == "observation":
            groups[(*_scope_key(fact), str(fact["period"]))].append(fact)
    for group in groups.values():
        if len({Decimal(str(fact["value"])) for fact in group}) > 1:
            for fact in group:
                fact["uncertainty_flags"].append("conflicting_scope_values")
    return list(facts.values())


def _self_test() -> None:
    """内置字符串冒烟，不读写真实 data，也不发请求。"""
    text = "2024年凯里酸汤鱼产值12亿元。"
    passage = {"text": text, "char_start": 0, "passage_id": "self-test",
               "published_at": "2025-01-01"}
    facts = extract_facts([passage], {})
    assert len(facts) == 1 and facts[0]["value"] == 1200000000
    assert facts[0]["review_status"] == "pending" and build_series(facts) == []


if __name__ == "__main__":
    _self_test()
