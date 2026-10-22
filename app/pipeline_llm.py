"""CSV清洗的显式聚合入口；API凭据始终留在主Python进程。

结构：clean_csv → CSV读入/批次计划 → advisor.clean_records → 原文验证 → cli-finalize。
调用：pipeline_cli.run / pipeline_service.execute；横向编排适配器和纯证据模块。
数据流：llm_input.csv + 原口径表 → 分批无记忆LLM → 审计JSON → 本地重建CSV/年度序列。
失败保留本地结果；未知批次不自动重发，已完成批次按输入指纹重放。
"""

import hashlib
import json

from app.advisor.integrated import IntegratedAdvisor
from app.common.component import run_component
from app.common.file_io import read_csv_rows, read_json, write_json
from app.core.errors import ServiceError
from app.pipeline.llm_records import parse_response, plan_batches, validate_rows


def clean_csv(question, analysis, config, settings, paths, workspace, *, advisor=None, on_event=None):
    """读取清洗前CSV并处理有限批次；供CLI和网页共同调用，不改变后续建模参数。"""
    if paths.file("analysis").is_file():
        # 子进程只返回有界摘要；完整facts/series留在磁盘，不能用摘要覆盖它们。
        analysis = {**read_json(paths.file("analysis")), **analysis}
    report = {"status": "disabled", "memory_enabled": False, "model": settings.advisor_model,
              "source": paths.file("llm_input").relative_to(paths.root).as_posix(),
              "batches": [], "facts": [], "invalid_rows": [], "omitted_blocks": 0}

    def emit(message):
        """同时打印CLI与持久网页消息；令牌失效时不继续调用API。"""
        print(f"[LLM清洗] {message}", flush=True)
        if on_event:
            on_event("llm_cleaning", message)

    if config.llm != "api" or config.llm_cleaning.mode == "off":
        report["reason"] = "本轮关闭API清洗，保留本地规则结果与待复核CSV"
    elif not settings.advisor_api_key.get_secret_value():
        report.update(status="missing_key", reason="未配置LLM密钥，保留本地规则结果与待复核CSV")
    elif not paths.file("llm_input").is_file() or not paths.file("extraction_context").is_file():
        report.update(status="no_input", reason="没有可用于LLM清洗的匹配CSV")
    else:
        blocks = read_csv_rows(paths.file("llm_input"))
        for block in blocks:
            for name in ("char_start", "char_end", "context_start", "context_end"):
                block[name] = int(block[name])
        metadata = read_json(paths.file("extraction_context"))
        fingerprint = hashlib.sha256(json.dumps(
            [question, blocks, metadata, config.llm_cleaning.model_dump(), settings.advisor_model],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if paths.file("llm_cleaning").is_file():
            existing = read_json(paths.file("llm_cleaning"))
            if existing.get("fingerprint") == fingerprint:
                emit("已有同批清洗记录，复用结果，不重复付费请求")
                report = existing
                return _finalize(report, config, settings, paths, workspace)
        batches, omitted = plan_batches(blocks, config.llm_cleaning.batch_chars,
                                        config.llm_cleaning.max_batches)
        report.update(status="running", fingerprint=fingerprint, omitted_blocks=omitted,
                      selected_blocks=len(blocks), total_selected_passages=analysis["counts"]["selected_passages"],
                      input_block_limit_reached=analysis["counts"]["selected_passages"] > len(blocks))
        engine = advisor or IntegratedAdvisor(settings)
        write_json(paths.file("llm_cleaning"), report)
        emit(f"模型 {settings.advisor_model}；逐批独立请求，无会话记忆，不自动重试")
        for index, batch in enumerate(batches, 1):
            emit(f"正在清洗第 {index}/{len(batches)} 批，输入CSV已保留")
            entry = {"index": index, "status": "started", "block_ids": [b["block_id"] for b in batch]}
            report["batches"].append(entry)
            write_json(paths.file("llm_cleaning"), report)
            try:
                reply = engine.clean_records(question, batch, metadata,
                                             max_tokens=config.llm_cleaning.max_output_tokens)
                entry.update(content=reply["content"], usage=reply.get("usage"))
                facts, invalid = validate_rows(parse_response(reply["content"]), batch, metadata,
                                               reattach_blocks=blocks)
                report["facts"].extend(facts)
                report["invalid_rows"].extend(invalid)
                entry.update(status="completed", source_validated_rows=len(facts), invalid_rows=len(invalid))
            except (ServiceError, ValueError, TypeError, KeyError) as exc:
                message = exc.error.message if isinstance(exc, ServiceError) else "返回JSON或数据结构无效"
                entry.update(status="failed", reason=message)
                report["invalid_rows"].extend({"record_id": b["record_id"], "url": b["url"],
                    "evidence_excerpt": b["text"], "review_status": "pending",
                    "cleaning_reasons": [f"LLM清洗未完成：{message}"], "block_id": b["block_id"]} for b in batch)
                emit(f"第 {index} 批未完成，保留原CSV并记录原因，不自动重发")
                write_json(paths.file("llm_cleaning"), report)
                break  # 网络/余额/截断等失败不向后续批次连续付费请求。
            write_json(paths.file("llm_cleaning"), report)
        complete = sum(b["status"] == "completed" for b in report["batches"])
        processed = {key for batch in report["batches"] for key in batch["block_ids"]}
        report["invalid_rows"].extend({"record_id": b["record_id"], "url": b["url"],
            "evidence_excerpt": b["text"], "review_status": "pending", "block_id": b["block_id"],
            "cleaning_reasons": ["清洗预算或前序批次失败，本块未交给LLM"]}
            for b in blocks if b["block_id"] not in processed)
        report["status"] = ("no_input" if not batches else "completed" if complete == len(batches)
                            and not omitted and not report["input_block_limit_reached"] else "partial")
        report["unprocessed_blocks"] = len(blocks) - sum(len(b["block_ids"]) for b in report["batches"])
        report["reason"] = ("只接纳通过本地原文校验的记录；疑问数据见review.csv。"
                            if report["status"] == "completed" else "清洗未覆盖全部内容，未处理材料仍保留在清洗前CSV。")
        write_json(paths.file("llm_cleaning"), report)
        emit(f"状态 {report['status']}；原文验证记录 {len(report['facts'])} 条；开始本地口径检查")
        return _finalize(report, config, settings, paths, workspace)
    write_json(paths.file("llm_cleaning"), report)
    emit(report["reason"])
    analysis["llm_cleaning"] = {key: report[key] for key in ("status", "model", "reason")}
    if analysis.get("data_quality"):
        analysis["data_quality"]["llm_status"] = report["status"]
        write_json(paths.file("data_quality"), analysis["data_quality"])
    write_json(paths.file("analysis"), analysis)
    return analysis


def _finalize(report, config, settings, paths, workspace):
    """把已有清洗审计交回无密钥子进程重新校验并组序列；不再调用LLM。"""
    if report.get("status") == "running":
        report = {**report, "status": "interrupted", "reason": "上次API结果未知，未重新请求；请核对清洗记录"}
        write_json(paths.file("llm_cleaning"), report)
    return run_component({"action": "cli-finalize", "output": str(paths.root),
        "output_layout": paths.organized, "config": config.model_dump(mode="json")},
        workspace, settings.data_dir, config.laptop.timeout_s, stream=True)
