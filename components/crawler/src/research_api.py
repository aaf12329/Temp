# -*- coding: utf-8 -*-
"""研究 API 编排入口，与 main.py 同属入口层，供将来的服务适配器调用。

结构：① validate_request / capabilities；② _export；③ analyze / train。
调用：main / future adapter → 本入口 → 独立 research 功能模块 + io_utils。
数据流：CrawlResult[] → 去重材料 → 语义候选 → 证据事实 → CSV/JSON → 可比序列 → 模型。
契约 v1.0：公开方法统一返回 {api_version, status, data, error}，可 JSON 序列化。
本入口是显式编排层，可横向调用功能；功能模块之间无 import。
不启动 HTTP 服务，不接入 Temp 或建议助手，不读取其他项目凭据。
"""
from __future__ import annotations

import math
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from Path_Store import Path_Store as ps
from webcrawl import io_utils
from research import evidence, retrieval, review, trend

API_VERSION = "1.0"
CSV_FIELDS = {
    "documents": ("record_id", "url", "title", "publisher", "published_at", "fetched_at",
                  "text_path", "text_sha256", "duplicate_group", "text_chars"),
    "passages": ("passage_id", "record_id", "url", "title", "published_at", "char_start",
                 "char_end", "text", "keyword_hits", "semantic_score", "best_query",
                 "selected", "semantic_only", "semantic_exclusion_hits", "relevance_reason",
                 "text_path", "text_sha256"),
    "facts": ("fact_id", "evidence_id", "record_id", "url", "subject", "metric", "region",
              "period", "period_type", "value", "unit", "claim_type", "review_status",
              "review_note", "uncertainty_flags", "known_at", "evidence_excerpt",
              "char_start", "char_end", "text_path", "text_sha256", "pages",
              "raw_value", "raw_unit", "number_char_start", "number_char_end",
              "duplicate_group", "publisher", "title"),
    "series": ("series_id", "scope", "subject", "metric", "region", "period_type",
               "period", "value",
               "unit", "known_at", "evidence_ids"),
    "review_template": ("fact_id", "review_status", "review_note", "subject", "metric",
                        "region", "period", "value", "unit", "uncertainty_flags",
                        "evidence_excerpt", "url"),
}


# ===== ① 纯逻辑与合同 =====
def _response(status: str, data: dict | None = None, error: dict | None = None) -> dict:
    """版本化返回壳；失败与数据不足区分。"""
    return {"api_version": API_VERSION, "status": status, "data": data, "error": error}


def capabilities() -> dict:
    """只读能力声明，便于三个项目日后做能力协商。"""
    return _response("ok", {"methods": ["analyze", "train", "capabilities"],
                            "crawl_api_version": "1.3", "research_api_version": API_VERSION,
                            "network_default": False, "human_review_required": True,
                            "integration_state": "standalone"})


def validate_request(request: dict[str, Any]) -> None:
    """入口校验，不允许未知模式或阈值 NaN 绕过筛选。"""
    if not isinstance(request, dict):
        raise ValueError("request 必须是对象")
    mode = request.get("mode", "semantic")
    if not isinstance(mode, str) or mode not in {"semantic", "lexical"}:
        raise ValueError("mode 只接受 semantic / lexical")


def _report(data: dict) -> str:
    """结果报告包含样本范围与进入训练前的具体缺口。"""
    counts = data["counts"]
    lines = ["# 爬虫研究运行报告", "", f"运行时间：{data['created_at']}", "",
             "这些数字描述本地已采材料，不代表产业总体或增长率。", "",
             "| 项目 | 数量 |", "|---|---:|"]
    lines.extend(f"| {key} | {value} |" for key, value in counts.items())
    lines.extend(["", "## 数据与模型状态", "",
                  f"趋势状态：`{data['trend']['status']}`。所有机器抽取初始为 pending。",
                  "政策目标、缺失口径、冲突数字和未审核事实不会直接进入训练。",
                  "语义阈值尚未经过人工标注集校准，semantic_only 是补查候选。",
                  "", "## 使用", "",
                  "1. 查看 passages.csv 的原文片段及相似度。",
                  "2. 对照来源核对 facts.csv 的指标、年份、地域、单位与主体。",
                  "3. 编辑 review_template.csv 的 review_status 与 review_note。",
                  "4. 运行 train --reviews <审核CSV>，通过口径检查的批准记录才组序列。",
                  "", "原始候选、审核决定、训练报告分别保存；重跑分析使用新的输出目录。",
                  "", "## 依据", "",
                  "- [BGE 原始模型说明](https://huggingface.co/BAAI/bge-small-zh-v1.5)",
                  "- [量化 ONNX 文件](https://huggingface.co/Xenova/bge-small-zh-v1.5)",
                  "- [XGBoost 参数](https://xgboost.readthedocs.io/en/stable/parameter.html)"])
    return "\n".join(lines) + "\n"


# ===== ② 输出 =====
def _export(data: dict, directory: str) -> dict[str, str]:
    """固定字段 CSV + 完整 JSON；JSON 保留列表和源文，不受 CSV 展示影响。"""
    paths: dict[str, str] = {}
    for name in CSV_FIELDS:
        rows = data["facts"] if name == "review_template" else data[name]
        path = ps.research_output(directory, name)
        io_utils.write_csv(path, rows, CSV_FIELDS[name])
        paths[name] = path
    for name in ("issues", "manifest", "trend", "analysis"):
        value = data if name == "analysis" else data[name]
        path = ps.research_output(directory, name)
        io_utils.write_json(path, value)
        paths[name] = path
    paths["report"] = ps.research_output(directory, "report")
    io_utils.write_text(paths["report"], _report(data))
    return paths


def _code_version() -> str:
    """只返回提交版本及 dirty 标记，不输出机器身份。"""
    try:
        version = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ps.SOURCE_ROOT, text=True).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ps.SOURCE_ROOT, text=True)
        return version + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _source_fingerprint() -> str:
    """在未提交工作区也记录源码指纹，补足 +dirty 无法定位内容的问题。"""
    source = Path(ps.SRC_DIR)
    hashes = {str(path.relative_to(source)): io_utils.sha256_of(io_utils.read_bytes(path))
              for path in sorted(source.rglob("*.py"))}
    return io_utils.sha256_of(json.dumps(hashes, sort_keys=True))


def _restore_references(value, mapping: dict) -> None:
    """将隔离副本引用还原为平台不可变相对引用；由服务分析导出前调用，不改原文。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"text_path", "paragraph_file"} and isinstance(child, str):
                value[key] = mapping.get(child, child)
            else:
                _restore_references(child, mapping)
    elif isinstance(value, list):
        for child in value:
            _restore_references(child, mapping)


# ===== ③ 主流程 =====
def analyze(request: dict[str, Any]) -> dict:
    """本地已采材料分析；不会隐式联网或下载模型。"""
    try:
        validate_request(request)
        config = io_utils.read_json(request.get("config_path", ps.RESEARCH_CONFIG))
        if not isinstance(config, dict):
            raise ValueError("研究配置必须是 JSON 对象")
        raw_threshold = config.get("semantic_threshold", 0.72)
        if isinstance(raw_threshold, bool) or not isinstance(raw_threshold, (float, int)):
            raise ValueError("semantic_threshold 必须为数字")
        threshold = float(raw_threshold)
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("semantic_threshold 需要为 0..1 的有限数值")
        records = request.get("records")
        if records is None:
            records = io_utils.read_json(request.get("index_path", ps.PAGES_INDEX_JSON))
        if not isinstance(records, list):
            raise ValueError("records / pages.json 必须为 CrawlResult 数组")
        corpus = evidence.load_corpus(records)
        encoder = retrieval.Encoder() if request.get("mode", "semantic") == "semantic" else None
        scored = retrieval.score_passages(corpus["passages"], config, encoder)
        selected = [p for p in scored if p["selected"]]
        facts = evidence.extract_facts(selected, config)
        series = evidence.build_series(facts)
        trained = trend.analyze_series(series, config)
        counts = {"input_records": len(records), "documents": len(corpus["documents"]),
                  "passages": len(scored), "selected_passages": len(selected),
                  "semantic_only": sum(bool(p.get("semantic_only")) for p in selected),
                  "candidate_facts": len(facts), "approved_facts": 0,
                  "series_rows": len(series), "issues": len(corpus["issues"])}
        manifest = {"config": config, "config_sha256": io_utils.sha256_of(
            io_utils.read_bytes(request.get("config_path", ps.RESEARCH_CONFIG))),
            "code_version": _code_version(), "code_sha256": _source_fingerprint(),
            "input_records_sha256": io_utils.sha256_of(
                json.dumps(records, ensure_ascii=False, sort_keys=True)),
            "embedding": encoder.report() if encoder else {"mode": "lexical"},
            "rule_version": config.get("rule_version"),
            "dependencies": _dependency_versions()}
        data = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "counts": counts,
                "documents": corpus["documents"], "passages": scored, "facts": facts,
                "series": series, "issues": corpus["issues"], "manifest": manifest,
                "trend": trained}
        references = request.get("reference_map", {})
        if references:
            _restore_references(data, references)
            data["manifest"]["material_reference_map"] = references
        paths = _export(data, request.get("output_dir", ps.RESEARCH_DIR))
        return _response("ok", {"counts": counts, "paths": paths,
                                "trend_status": trained["status"]})
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        return _response("error", error={"code": "E_ANALYSIS", "message": str(exc),
                                          "retryable": False})


def _dependency_versions() -> dict[str, str]:
    """记录实际安装版本，未知包不伪造版本。"""
    from importlib.metadata import PackageNotFoundError, version
    result = {}
    for name in ("numpy", "onnxruntime", "tokenizers", "xgboost", "scikit-learn"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "not-installed"
    return result


def train(request: dict[str, Any]) -> dict:
    """应用独立审核后建模，保持原 analysis.json 和候选事实不变。"""
    try:
        if not isinstance(request, dict):
            raise ValueError("request 必须为对象")
        directory = request.get("output_dir", ps.RESEARCH_DIR)
        data = io_utils.read_json(request.get("analysis_path", ps.research_output(
            directory, "analysis")))
        reviewed = review.apply_reviews(data["facts"], review.load_reviews(request["reviews"]))
        series = evidence.build_series(reviewed)
        report = trend.analyze_series(series, data["manifest"]["config"])
        report["review_status_counts"] = dict(Counter(f["review_status"] for f in reviewed))
        report["reviews_sha256"] = io_utils.sha256_of(io_utils.read_bytes(request["reviews"]))
        report["applied_reviews"] = [{"fact_id": fact["fact_id"],
                                       "review_status": fact["review_status"],
                                       "review_note": fact.get("review_note", "")}
                                      for fact in reviewed]
        report["analysis_manifest"] = data["manifest"]
        report["series_rows"] = series
        io_utils.write_json(ps.research_output(directory, "trend"), report)
        io_utils.write_csv(ps.research_output(directory, "series"), series, CSV_FIELDS["series"])
        return _response("ok", {"trend": report, "series_rows": len(series)})
    except (OSError, ValueError, KeyError, RuntimeError, ImportError) as exc:
        return _response("error", error={"code": "E_TRAIN", "message": str(exc),
                                          "retryable": False})


def _self_test() -> None:
    """仅验证纯合同。"""
    assert capabilities()["api_version"] == API_VERSION
    validate_request({"mode": "lexical"})


if __name__ == "__main__":
    _self_test()
