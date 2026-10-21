"""CLI 离线管线的显式聚合层，只在统一解释器的隔离进程中加载旧算法。

结构：analyze → 召回/抽取/规则清洗/缺失统计；model → 逐序列 training.train_series。
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

from app.common.file_io import read_json, write_csv_rows, write_json
from app.common.paths import PipelinePaths
from app.contracts.pipeline import CLIConfig
from app.pipeline.cleaning import clean_candidates
from app.pipeline.plots import missing_plot
from app.pipeline.training import train_series


def _runtime(started):
    """记录本次实际进程时间和 Windows 峰值工作集，不能代表用户笔记本实测。"""
    memory = psutil.Process().memory_info()
    return {"seconds": round(time.perf_counter() - started, 3),
            "rss_mb": round(memory.rss / 1024**2, 2),
            "peak_rss_mb": round(getattr(memory, "peak_wset", memory.rss) / 1024**2, 2)}


def analyze(message):
    """有界本地召回和抽取，保留不能建模的匹配信息，供最终 API 助手总结。"""
    from research import evidence, retrieval
    from research_api import CSV_FIELDS, _restore_references
    from wgan_tools.missing_statistics import analyze_missing_statistics

    started = time.perf_counter()
    config = CLIConfig.model_validate(message["config"])
    paths = PipelinePaths(Path(message["output"]), organized=message.get("output_layout", False))
    corpus = evidence.load_corpus(message["records"])
    passages = corpus["passages"][:config.max_passages]
    print(f"[整理] {len(corpus['documents'])} 份材料，{len(corpus['passages'])} 个段落；"
          f"本轮处理前 {len(passages)} 段", flush=True)
    settings = {"queries": [message["question"]], "keywords": config.keywords,
                "subjects": config.subjects, "regions": config.regions,
                "semantic_threshold": config.semantic_threshold}
    encoder, warning = None, None
    if config.retrieval != "keywords":
        try:
            encoder = retrieval.Encoder(threads=config.laptop.threads, batch_size=8, max_length=256)
            print("[检索模型] BGE-small-zh-v1.5 / INT8 ONNX / CPU / batch=8", flush=True)
        except (ImportError, ValueError, OSError) as exc:
            raise ValueError("本地 BGE 模型不可用，请先运行 model-setup；"
                             "或在 config/cli.json 显式选择 keywords。") from exc
    else:
        # 不把整句当成单个必须完整匹配的关键词；高级关键词为空时明确提示。
        import re

        terms = config.keywords or [term for term in re.split(r"[\s，,。；;、？！?!]+", message[
            "question"]) if len(term) >= 2]
        settings["keywords"] = terms
        warning = "当前为显式关键词模式，不提供向量分数；可在 JSON 设置更精确的 keywords"
        print(f"[检索] 关键词模式：{terms}", flush=True)
    scored = retrieval.score_passages(passages, settings, encoder)
    selected = [passage for passage in scored if passage["selected"]]
    facts = evidence.extract_facts(selected, settings)
    truncated_facts = len(facts) > config.laptop.max_rows
    facts = facts[:config.laptop.max_rows]
    _restore_references(corpus, message.get("reference_map", {}))
    _restore_references(scored, message.get("reference_map", {}))
    _restore_references(facts, message.get("reference_map", {}))
    cleaned, rejected = clean_candidates(facts)
    series = evidence.build_series(cleaned, require_approval=False)
    numeric = pd.DataFrame([{name: fact.get(name) for name in
                            ("period", "value", "subject", "region", "metric", "unit", "known_at")}
                           for fact in facts])
    missing = {"status": "no_numeric_candidates", "interpretation": "没有可诊断的数值候选"}
    if not numeric.empty:
        numeric = numeric.replace("", None)
        for name in ("period", "value"):
            numeric[name] = pd.to_numeric(numeric[name], errors="coerce")
        categorical = [name for name in numeric if name not in {"period", "value"}]
        # 只做诊断，不用全表统计拟合清洗、填充或 GAN，更不补标签与缺年。
        missing = analyze_missing_statistics(numeric, categorical=categorical)
        missing["interpretation"] = "混合候选表的探索性缺失诊断；不据此证明 MAR/MNAR，不补真实标签或年份"
        missing_plot(numeric, paths.file("missing_plot"))
    result = {"question": message["question"], "counts": {
        "documents": len(corpus["documents"]), "passages": len(passages),
        "selected_passages": len(selected), "candidate_facts": len(facts),
        "cleaned_candidates": len(cleaned), "excluded_candidates": len(rejected),
        "series_rows": len(series)}, "issues": corpus["issues"], "warning": warning,
        "truncated": {"passages": len(corpus["passages"]) > len(passages), "facts": truncated_facts},
        "evidence_status": "machine_candidate_unreviewed", "runtime": _runtime(started),
        "retrieval": encoder.report() if encoder else {"mode": "keywords"}}
    write_csv_rows(paths.file("information"), selected, ["passage_id", "record_id", "url", "title",
                   "published_at", "text", "text_path", "text_sha256", "char_start", "char_end",
                   "semantic_score", "relevance_reason"])
    write_csv_rows(paths.file("passages"), scored, list(CSV_FIELDS["passages"]))
    for name, values in [("raw", facts), ("candidates", facts), ("cleaned", cleaned),
                         ("rejected", rejected), ("series", series)]:
        fields = list(CSV_FIELDS["series"]) + ["evidence_status"] if name == "series" else (
            list(CSV_FIELDS["facts"]) + ["cleaning_reasons"])
        write_csv_rows(paths.file(name), values, fields)
    write_json(paths.file("missing"), missing)
    write_json(paths.file("analysis"), {**result, "facts": facts, "series": series})
    print(f"[清洗] 候选 {len(facts)}，可比候选 {len(cleaned)}，排除 {len(rejected)}；"
          f"同口径年度行 {len(series)}", flush=True)
    for row in rejected[:10]:
        print(f"[排除] {row.get('fact_id')}: {'; '.join(row['cleaning_reasons'])}", flush=True)
    if len(rejected) > 10:
        print(f"[排除] 其余原因见本轮 {paths.file('rejected').relative_to(paths.root).as_posix()}", flush=True)
    print("[完成] 信息整理完成。候选仍未人工复核，自动建模仅为探索。", flush=True)
    return result


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
    requirements = [] if reports else ["没有口径、发布时间、年份均明确且无冲突的同口径年度候选；直接总结已有信息"]
    for reason in requirements:
        print(f"[不能建模] {reason}", flush=True)
    result = {"status": "awaiting_gan_review" if message.get("stage") == "gan" else (
              "planned" if message.get("plan") and any(r["status"] == "planned" for r in reports) else
              "modeled" if any(r["status"] == "exploratory_model" for r in reports)
              else "data_only"), "series_reports": reports, "requirements": requirements,
              "needs_gan": any(r.get("needs_gan") for r in reports),
              "series_limit_reached": len(grouped) > config.max_series,
              "runtime": _runtime(started), "candidate_evidence_only": True}
    if not message.get("plan"):
        write_json(paths.file("result"), result)
    return result
