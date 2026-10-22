"""CLI 离线管线的显式聚合层，只在统一解释器的隔离进程中加载旧算法。

结构：analyze → 均衡召回/初步CSV；finalize → LLM后CSV/缺失/质量对照；model → 逐序列训练。
调用：crawler.bridge → 本入口 → research / wgan_tools / 本地清洗与建模模块。
数据流：已采材料+一句问题 → 原文/候选/审计 CSV → 同口径年度探索 → 模型报告与 PNG。
边界：不访问数据库，不调用 LLM，不把 pending 候选伪装成人工 approved。
"""

import hashlib
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd
import psutil

from app.common.file_io import read_csv_rows, read_json, write_csv_rows, write_json
from app.common.paths import PipelinePaths
from app.contracts.pipeline import CLIConfig
from app.pipeline.cleaning import clean_candidates
from app.pipeline.context import complete_context
from app.pipeline.data_quality import field_rows, profile_candidates, quality_report
from app.pipeline.deterministic import extract as extract_deterministic
from app.pipeline.diagnostics import diagnose, gan_gate, no_model_reasons
from app.pipeline.llm_records import merge_facts, source_blocks
from app.pipeline.plots import data_quality_plot, missing_plot
from app.pipeline.selection import (
    balanced_passages,
    expand_numeric_neighbors,
    retrieval_settings,
    supplement_numeric,
)
from app.pipeline.training import train_series


def _runtime(started):
    """记录本次实际进程时间和 Windows 峰值工作集，不能代表用户笔记本实测。"""
    memory = psutil.Process().memory_info()
    return {"seconds": round(time.perf_counter() - started, 3),
            "rss_mb": round(memory.rss / 1024**2, 2),
            "peak_rss_mb": round(getattr(memory, "peak_wset", memory.rss) / 1024**2, 2)}


def _information_rows(selected, facts):
    """匹配原文附上对应候选的口径限制；规则抽取的候选也保留自己的证据句。"""
    grouped = defaultdict(list)
    for fact in facts:
        grouped[fact.get("passage_id")].append(fact)
    result, known = [], set()
    for passage in selected:
        known.add(passage["passage_id"])
        related = grouped[passage["passage_id"]]
        notes = [note for fact in related for note in fact.get("context_evidence", [])]
        unique = {str(note): note for note in notes}
        result.append({**passage, "context_evidence": list(unique.values())[:12],
                       "candidate_flags": sorted({flag for fact in related
                                                  for flag in fact.get("uncertainty_flags", [])})})
    # 确定性规则直接在全文命中，不绑定匹配段落；把证据句补进来，供报告与最终助手引用。
    for fact in facts:
        if fact.get("passage_id") in known:
            continue
        result.append({
            "passage_id": fact.get("passage_id"), "record_id": fact.get("record_id"),
            "url": fact.get("url"), "title": fact.get("title"),
            "published_at": fact.get("published_at"), "text": fact.get("evidence_excerpt") or "",
            "text_path": fact.get("text_path"), "text_sha256": fact.get("text_sha256"),
            "char_start": fact.get("char_start"), "char_end": fact.get("char_end"),
            "semantic_score": "", "relevance_reason": fact.get("extraction_method"),
            "context_evidence": fact.get("context_evidence", []),
            "candidate_flags": fact.get("uncertainty_flags", [])})
    return result


def _missing_report(facts, paths):
    """以当前候选重算原有缺失诊断，不填补真实年份、标签或未知口径。"""
    from wgan_tools.missing_statistics import analyze_missing_statistics

    numeric = pd.DataFrame([{name: fact.get(name) for name in
                            ("period", "value", "subject", "region", "metric", "unit", "known_at")}
                           for fact in facts])
    missing = {"status": "no_numeric_candidates", "interpretation": "没有可诊断的数值候选"}
    if not numeric.empty:
        numeric = numeric.replace("", None)
        for name in ("period", "value"):
            numeric[name] = pd.to_numeric(numeric[name], errors="coerce")
        categorical = [name for name in numeric if name not in {"period", "value"}]
        missing = analyze_missing_statistics(numeric, categorical=categorical)
        missing["interpretation"] = "混合候选表的探索性缺失诊断；不据此证明MAR/MNAR，不补真实标签或年份"
        missing_plot(numeric, paths.file("missing_plot"))
    write_json(paths.file("missing"), missing)


def _save_quality(paths, before, facts, cleaned, rejected, series, review, status):
    """统一导出质量JSON、字段对照CSV及图表，供所有入口共用。"""
    report = quality_report(before, facts, cleaned, rejected, series, review, llm_status=status)
    write_json(paths.file("data_quality"), report)
    write_csv_rows(paths.file("data_quality_fields"), field_rows(report))
    data_quality_plot(report, paths.file("data_quality_plot"))
    return report


def analyze(message):
    """有界本地召回和抽取，保留不能建模的匹配信息，供最终 API 助手总结。"""
    from research import evidence, retrieval
    from research_api import CSV_FIELDS, _restore_references

    started = time.perf_counter()
    config = CLIConfig.model_validate(message["config"])
    paths = PipelinePaths(Path(message["output"]), organized=message.get("output_layout", False))
    corpus = evidence.load_corpus(message["records"])
    settings = retrieval_settings(message["question"], config, evidence.DEFAULT_SUBJECTS,
                                  evidence.DEFAULT_REGIONS, evidence.METRICS)
    passages = balanced_passages(corpus["passages"], settings, config.max_passages)
    print(f"[整理] {len(corpus['documents'])} 份材料，{len(corpus['passages'])} 个段落；"
          f"按文档均衡、数值优先处理 {len(passages)} 段", flush=True)
    print(f"[检索词] {settings['keywords']}；阈值 {config.semantic_threshold}；"
          "另保留相关指标数值候选", flush=True)
    encoder, warning = None, None
    if config.retrieval != "keywords":
        try:
            encoder = retrieval.Encoder(threads=config.laptop.threads, batch_size=8, max_length=256)
            print("[检索模型] BGE-small-zh-v1.5 / INT8 ONNX / CPU / batch=8", flush=True)
        except (ImportError, ValueError, OSError) as exc:
            raise ValueError("本地 BGE 模型不可用，请先运行 model-setup；"
                             "或在 config/cli.json 显式选择 keywords。") from exc
    else:
        warning = "当前为显式关键词/指标数值召回，不提供向量分数；可在 JSON 设置更精确的 keywords"
    if not settings["keywords"]:
        scope_warning = ("需求未识别出明确产业或指标关键词；数值补查保留各来源原口径，不把它们自动归为同一产业。"
                         "新产业可在JSON配置subjects/keywords。")
        warning = f"{warning}；{scope_warning}" if warning else scope_warning
    scored = expand_numeric_neighbors(supplement_numeric(
        retrieval.score_passages(passages, settings, encoder), settings))
    selected = [passage for passage in scored if passage["selected"]]
    mode = config.extraction.mode
    extraction_issues = []
    if mode == "deterministic":
        if config.extraction.rules:
            facts, extraction_issues = extract_deterministic(
                corpus["documents"], [rule.model_dump() for rule in config.extraction.rules],
                evidence.UNIT_FACTORS, evidence.METRIC_DIMENSIONS)
            method = "configured_rules"
            print(f"[抽取] 确定性规则 {len(config.extraction.rules)} 条，候选 {len(facts)} 条；"
                  f"未采纳 {len(extraction_issues)} 项", flush=True)
        else:
            facts = complete_context(evidence.extract_facts(selected, settings), selected,
                                     corpus["documents"], settings)
            method = "local_rules_no_llm"
            extraction_issues = [{"code": "no_rules",
                                  "message": "未配置确定性正则规则，使用本地规则且不调用LLM清洗。"}]
            print(f"[抽取] 未配置正则规则，使用本地规则（不调用LLM），候选 {len(facts)} 条", flush=True)
        for issue in extraction_issues[:5]:
            print(f"[未采纳] {issue['message']}", flush=True)
        blocks = []
    else:
        facts = complete_context(evidence.extract_facts(selected, settings), selected,
                                 corpus["documents"], settings)
        blocks = source_blocks(selected, corpus["documents"], config.llm_cleaning.max_blocks)
        method = "llm_cleaning"
    truncated_facts = len(facts) > config.laptop.max_rows
    facts = facts[:config.laptop.max_rows]
    _restore_references(corpus, message.get("reference_map", {}))
    _restore_references(scored, message.get("reference_map", {}))
    _restore_references(facts, message.get("reference_map", {}))
    _restore_references(blocks, message.get("reference_map", {}))
    # 先落盘清洗输入，再由主进程读CSV调用API；子进程始终没有凭据。
    block_fields = ["block_id", "passage_id", "record_id", "url", "title", "publisher", "published_at",
                    "text_path", "text_sha256", "duplicate_group", "char_start", "char_end", "text",
                    "context_start", "context_end", "context", "header", "semantic_score", "relevance_reason"]
    write_csv_rows(paths.file("llm_input"), blocks, block_fields)
    write_json(paths.file("extraction_context"), {"version": 1, "metrics": evidence.METRICS,
        "units": evidence.UNIT_FACTORS, "dimensions": evidence.METRIC_DIMENSIONS,
        "subjects": settings["subjects"], "regions": settings["regions"]})
    cleaned, rejected = clean_candidates(facts)
    series = evidence.build_series(cleaned, require_approval=False)
    _missing_report(facts, paths)
    result = {"question": message["question"], "counts": {
        "documents": len(corpus["documents"]), "passages": len(passages),
        "selected_passages": len(selected), "candidate_facts": len(facts),
        "cleaned_candidates": len(cleaned), "excluded_candidates": len(rejected),
        "series_rows": len(series)}, "issues": corpus["issues"], "warning": warning,
        "truncated": {"passages": len(corpus["passages"]) > len(passages), "facts": truncated_facts},
        "evidence_status": "machine_candidate_unreviewed", "runtime": _runtime(started),
        "retrieval": encoder.report() if encoder else {"mode": "keywords"},
        "extraction": {"mode": mode, "method": method,
                       "rule_count": len(config.extraction.rules),
                       "issues": extraction_issues}}
    if mode == "deterministic":
        result["llm_cleaning"] = {"status": "disabled", "model": "",
                                  "reason": "确定性规则路径不调用LLM清洗",
                                  "source_validated_rows": len(facts), "invalid_rows": 0}
    selection = {"keywords": settings["keywords"], "scope_source": settings["scope_source"],
                 "subjects": settings["subjects"], "regions": settings["regions"],
                 "semantic_threshold": config.semantic_threshold,
                 "numeric_context_selected": sum(p["relevance_reason"] == "numeric_context" for p in selected),
                 "budget_policy": "document_round_robin_numeric_priority",
                 "available_passages": len(corpus["passages"]), "processed_passages": len(passages)}
    result["diagnostics"] = diagnose(result["counts"], rejected, selection)
    result["data_quality"] = _save_quality(paths, profile_candidates(facts), facts, cleaned,
        rejected, series, rejected, "deterministic_rules" if mode == "deterministic" else (
            "pending" if config.llm == "api" else "local_rules"))
    write_csv_rows(paths.file("information"), _information_rows(selected, facts), ["passage_id", "record_id", "url", "title",
                   "published_at", "text", "text_path", "text_sha256", "char_start", "char_end",
                   "semantic_score", "relevance_reason", "context_evidence", "candidate_flags"])
    write_csv_rows(paths.file("passages"), scored, list(CSV_FIELDS["passages"]))
    for name, values in [("raw", facts), ("candidates", facts), ("cleaned", cleaned),
                         ("rejected", rejected), ("review", rejected), ("series", series)]:
        fields = list(CSV_FIELDS["series"]) + ["evidence_status"] if name == "series" else (
            list(CSV_FIELDS["facts"]) + ["cleaning_reasons", "extraction_source_id", "context_evidence"])
        write_csv_rows(paths.file(name), values, fields)
    write_json(paths.file("analysis"), {**result, "facts": facts, "series": series})
    write_json(paths.file("diagnostics"), result["diagnostics"])
    print(f"[数据诊断] {result['diagnostics']['message']}", flush=True)
    print(f"[清洗] 候选 {len(facts)}，可比候选 {len(cleaned)}，排除 {len(rejected)}；"
          f"同口径年度行 {len(series)}", flush=True)
    for row in rejected[:10]:
        print(f"[排除] {row.get('fact_id')}: {'; '.join(row['cleaning_reasons'])}", flush=True)
    if len(rejected) > 10:
        print(f"[排除] 其余原因见本轮 {paths.file('rejected').relative_to(paths.root).as_posix()}", flush=True)
    print("[完成] 信息整理完成。候选仍未人工复核，自动建模仅为探索。", flush=True)
    return result


def finalize(message):
    """清洗CSV后合并有据字段，保留原始raw.csv，重建本地审核表和年度序列。"""
    from research import evidence
    from research_api import CSV_FIELDS

    paths = PipelinePaths(Path(message["output"]), organized=message.get("output_layout", False))
    config = CLIConfig.model_validate(message["config"])
    analysis = read_json(paths.file("analysis"))
    report = read_json(paths.file("llm_cleaning"))
    before = (analysis.get("data_quality") or {}).get("before") or profile_candidates(analysis.get("facts", []))
    covered = {key for batch in report.get("batches", []) if batch["status"] == "completed"
               for key in batch["block_ids"]}
    facts = merge_facts(analysis.get("facts", []), report.get("facts", []), covered)
    omitted = len(facts) > config.laptop.max_rows
    facts = facts[:config.laptop.max_rows]
    for fact in facts:
        fact.setdefault("extraction_method", "local_rules")
    cleaned, rejected = clean_candidates(facts)
    series = evidence.build_series(cleaned, require_approval=False)
    review = [*rejected, *report.get("invalid_rows", [])]
    counts = {**analysis["counts"], "candidate_facts": len(facts), "cleaned_candidates": len(cleaned),
              "excluded_candidates": len(rejected), "review_records": len(review), "series_rows": len(series)}
    selection = (analysis.get("diagnostics") or {}).get("selection", {})
    analysis.update(counts=counts, facts=facts, series=series,
                    diagnostics=diagnose(counts, rejected, selection),
                    llm_cleaning={key: report[key] for key in ("status", "model", "reason") if key in report})
    analysis["data_quality"] = _save_quality(paths, before, facts, cleaned, rejected, series,
                                             review, report["status"])
    analysis["llm_cleaning"].update(source_validated_rows=len(report.get("facts", [])),
                                     invalid_rows=len(report.get("invalid_rows", [])))
    analysis["truncated"] = {**(analysis.get("truncated") or {}), "llm_rows": omitted}
    if report.get("status") != "completed":
        analysis["warning"] = "; ".join(filter(None, [analysis.get("warning"), report.get("reason")]))
    fields = list(CSV_FIELDS["facts"]) + ["cleaning_reasons", "extraction_source_id", "context_evidence",
              "extraction_method", "scope", "llm_raw", "block_id"]
    for name, values in [("candidates", facts), ("cleaned", cleaned), ("rejected", rejected), ("review", review)]:
        write_csv_rows(paths.file(name), values, fields)
    write_csv_rows(paths.file("series"), series, list(CSV_FIELDS["series"]) + ["evidence_status"])
    if paths.file("information").is_file():
        passages = read_csv_rows(paths.file("information"))
        if passages:
            write_csv_rows(paths.file("information"), _information_rows(passages, facts),
                           list(passages[0]))
    write_json(paths.file("analysis"), analysis)
    write_json(paths.file("diagnostics"), analysis["diagnostics"])
    _missing_report(facts, paths)
    print(f"[清洗完成] 候选 {len(facts)}；可用 {len(cleaned)}；待复核 {len(review)}；年度行 {len(series)}", flush=True)
    return analysis


def model(message):
    """检查各口径门槛，计划阶段不训练 GAN；正式阶段保存真实模型和图表。"""
    started = time.perf_counter()
    config = CLIConfig.model_validate(message["config"])
    paths = PipelinePaths(Path(message["output"]), organized=message.get("output_layout", False))
    analysis = read_json(paths.file("analysis"))
    grouped = defaultdict(list)
    for row in analysis["series"]:
        grouped[row["series_id"]].append(row)
    reports = []
    for key in sorted(grouped)[:config.max_series]:
        digest = hashlib.sha256(key.encode()).hexdigest()[:12]
        destination = paths.series(digest)
        try:
            report = train_series(grouped[key], config, destination, message["forecast_at"],
                                  plan=message.get("plan", False), stage=message.get("stage", "fit"),
                                  use_generated=message.get("use_generated", False))
        except (ValueError, RuntimeError, ImportError, FloatingPointError) as exc:
            report = {"series_id": key, "scope": grouped[key][0]["scope"], "status": "model_error",
                      "requirements": [f"本地模型未完成：{type(exc).__name__}，保留数据并继续总结"],
                      "needs_gan": False, "gan": {"status": "unavailable",
                          "reason": "本地模型未完成，保留已有数据和错误原因"}}
        report["directory"] = destination.root.relative_to(paths.root).as_posix()
        reports.append(report)
        print(f"[建模] {report['scope']}：{report['status']}", flush=True)
        for reason in report.get("requirements", []):
            print(f"[不能建模] {reason}", flush=True)
        if not message.get("plan") and report.get("gan", {}).get("reason"):
            print(f"[WGAN] {report['gan']['reason']}", flush=True)
    requirements = [] if reports else no_model_reasons(analysis)
    for reason in requirements:
        print(f"[不能建模] {reason}", flush=True)
    result = {"status": "awaiting_gan_review" if message.get("stage") == "gan" else (
              "planned" if message.get("plan") and any(r["status"] == "planned" for r in reports) else
              "modeled" if any(r["status"] == "exploratory_model" for r in reports)
              else "data_only"), "series_reports": reports, "requirements": requirements,
              "needs_gan": any(r.get("needs_gan") for r in reports),
              "gan_gate": gan_gate(reports, config.gan.min_real_samples, config.gan.mode,
                                   planning=message.get("plan", False)),
              "series_limit_reached": len(grouped) > config.max_series,
              "runtime": _runtime(started), "candidate_evidence_only": True}
    if not message.get("plan"):
        write_json(paths.file("result"), result)
    return result
