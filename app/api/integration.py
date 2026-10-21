"""集成业务 HTTP 显式编排入口。

结构：研究/建议提交 → 操作查询 → 安全产物下载与会话读取。
调用：FastAPI → 既有鉴权/运行时 → 显式校验和功能模块。
数据流：有界 JSON → 持久操作 ID → 状态/带来源建议/鉴权文件，无任意路径参数。
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.routes import RT, authorize
from app.contracts.advisor import AdvisorRequest, SafeIdentifier
from app.contracts.research import ResearchRequest
from app.core.errors import ServiceError
from app.db.models import MaterialRow
from app.execution import validate_operation

router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)], tags=["integration"])


@router.post("/research/jobs", status_code=202)
def submit_research(body: ResearchRequest, runtime: RT):
    """检查材料归属并持久提交研究任务；由研究提交路由调用。"""
    payload = body.model_dump(mode="json")
    validate_operation(runtime, "research", payload)
    return {"operation_id": runtime.operations.submit("research", payload)}


@router.post("/advisor/jobs", status_code=202)
def submit_advice(body: AdvisorRequest, runtime: RT):
    """只排队建议，不在 HTTP 请求期间调用付费模型；由建议提交路由调用。"""
    payload = body.model_dump(mode="json")
    validate_operation(runtime, "advisor", payload)
    return {"operation_id": runtime.operations.submit("advisor", payload)}


@router.get("/operations/{operation_id}")
def get_operation(operation_id: SafeIdentifier, runtime: RT, project_id: SafeIdentifier = Query()):
    """按项目查询任务及结果；由轮询接口调用。"""
    return runtime.operations.get(operation_id, project_id)


@router.get("/operations/{operation_id}/files/{name}", response_class=FileResponse)
def download(operation_id: SafeIdentifier, name: str, runtime: RT,
             project_id: SafeIdentifier = Query()):
    """仅下载已完成研究登记的白名单文件；由下载路由调用。"""
    operation = runtime.operations.get(operation_id, project_id)
    if operation["kind"] != "research" or operation["state"] != "done":
        raise ServiceError("E_RESEARCH_NOT_READY", "研究任务未完成。", 409)
    result = operation["result"]
    if name not in result["files"]:
        raise ServiceError("E_ARTIFACT_NOT_FOUND", "未登记该研究文件。", 404)
    path = runtime.storage.paths.research_file(result["run_id"], name)
    if not path.resolve().is_relative_to(runtime.storage.root.resolve()) or not path.is_file():
        raise ServiceError("E_ARTIFACT_NOT_FOUND", "文件不可用，请检查研究任务产物。", 404)
    return FileResponse(path, filename=path.name)


@router.get("/projects/{project_id}/advisor/sessions/{session_id}")
def history(project_id: SafeIdentifier, session_id: SafeIdentifier, runtime: RT, demo: bool = False):
    """读取隔离会话，不暴露模型原始响应与密钥；由会话查询调用。"""
    return runtime.advisor.history(project_id, session_id, demo)


@router.get("/jobs/{job_id}/materials")
def job_materials(job_id: str, runtime: RT, project_id: SafeIdentifier = Query()):
    """列出同项目采集任务的材料 ID，供下一步研究选取；由材料列表路由调用。"""
    job = runtime.jobs.get(job_id)
    if job.project_id != project_id:
        raise ServiceError("E_JOB_NOT_FOUND", "当前项目没有该采集任务。", 404)
    with runtime.db.session() as session:
        rows = session.scalars(select(MaterialRow).where(MaterialRow.job_id == job_id)).all()
        return {"materials": [{"record_id": row.record_id, "url": row.payload["url"],
                               "title": row.payload.get("title"), "status": row.payload["status"],
                               "error": row.payload.get("error")} for row in rows]}
