"""操作任务显式编排入口，允许横向调用材料、研究和建议功能。

结构：select_materials → validate_operation → execute_operation。
调用：API → validate_operation；Worker → execute_operation → 研究/建议适配器。
数据流：项目与材料 ID → 数据库归属、正文哈希与文件边界 → 离线研究或带证据建议。
"""

import hashlib

from app.common.file_io import read_bytes
from app.core.errors import ServiceError
from app.pipeline_service import execute as execute_pipeline

MAX_EVIDENCE_BYTES = 32 * 1024 * 1024


def select_materials(runtime, project_id: str, material_ids: list[str]) -> tuple[list, list, list]:
    """查验项目归属与正文哈希，仅选数据库登记材料；由提交和执行入口调用。"""
    records, evidence, files, total = [], [], [], 0
    for record_id in material_ids:
        material = runtime.materials.get(record_id)
        if material.project_id != project_id:
            raise ServiceError("E_MATERIAL_PROJECT", "材料不属于当前项目。", 403)
        if material.error or material.status not in {"ok", "ok_pdf"}:
            raise ServiceError("E_MATERIAL_NOT_READY", "材料未成功采集，请先检查采集任务。", 409)
        source = runtime.materials.text(record_id)
        total += source.stat().st_size
        if total > MAX_EVIDENCE_BYTES:
            raise ServiceError("E_EVIDENCE_LIMIT", "材料超过 32 MB，请拆分研究任务。", 422)
        raw = read_bytes(source)
        text = raw.decode("utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != material.text_sha256:
            raise ServiceError("E_EVIDENCE_HASH", "材料正文与登记哈希不一致，请重新采集。", 409)
        record = material.model_dump(mode="json")
        reference = material.storage.get("text_path") or material.pdf.text_path
        files.append((reference, source))
        if material.paragraph_file:
            files.append((material.paragraph_file, runtime.materials.paragraphs(record_id)))
        records.append(record)
        evidence.append({"record_id": record_id, "url": material.url, "title": material.title,
                         "text_sha256": digest, "text": text,
                         "published_at": material.source.published_at})
    return records, evidence, files


def research_summary(runtime, project_id: str, research_id: str | None):
    """只读取同项目已完成研究摘要；由建议提交及执行入口调用。"""
    if not research_id:
        return None
    operation = runtime.operations.get(research_id, project_id)
    if operation["kind"] != "research" or operation["state"] != "done":
        raise ServiceError("E_RESEARCH_NOT_READY", "研究任务尚未完成或类型不符。", 409)
    result = operation["result"]
    return {key: result[key] for key in ("counts", "trend_status", "human_review_required", "mode")}


def validate_operation(runtime, kind: str, request: dict) -> None:
    """提交前检查依赖和归属，不执行模型或研究计算；由 API 调用。"""
    select_materials(runtime, request["project_id"], request["material_ids"])
    if kind == "advisor":
        research_summary(runtime, request["project_id"], request.get("research_id"))
        if not request["demo"] and not runtime.settings.advisor_api_key.get_secret_value():
            raise ServiceError("E_ADVISOR_KEY", "请在根 .env 配置 INDUSTRY_ADVISOR_API_KEY。", 503)
    elif request["mode"] == "semantic" and not runtime.storage.paths.model_directory().is_dir():
        raise ServiceError("E_MODEL_NOT_READY", "请先运行模型准备命令，或显式选择 lexical 模式。", 503)


def execute_operation(runtime, claim) -> dict:
    """在后台执行有界任务，崩溃恢复不得重复未知模型调用；由操作 Worker 调用。"""
    if claim.attempts > 3:
        raise ServiceError("E_WORKER_LOST", "研究任务连续中断，请检查环境后使用新操作 ID。", 500)
    if claim.kind == "pipeline":
        return execute_pipeline(runtime, claim)
    if claim.kind == "advisor" and claim.attempts > 1:
        raise ServiceError("E_ADVISOR_OUTCOME_UNKNOWN", "建议执行曾中断，请核对会话后使用新操作 ID。", 409)
    records, evidence, files = select_materials(runtime, claim.project_id, claim.request["material_ids"])
    if claim.kind == "advisor":
        summary = research_summary(runtime, claim.project_id, claim.request.get("research_id"))
        return runtime.advisor.execute(claim.request, evidence, summary)
    if claim.kind != "research":
        raise ServiceError("E_OPERATION_KIND", "未知任务类型，请检查任务记录。", 500)
    return runtime.research.execute(claim.request, records, files,
                                    claim.token.replace("-", ""))
