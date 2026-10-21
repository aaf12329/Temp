"""管线HTTP聚合：submit → 持久任务；download → 同项目已登记文件。

由FastAPI装配，复用既有鉴权和Worker；请求不执行采集/训练，不接受任意磁盘路径。
结构：submit/download；调用：FastAPI → 持久队列/白名单下载；数据流：JSON → 操作ID/文件。
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.api.routes import RT, authorize
from app.common.paths import DataPaths
from app.contracts.advisor import SafeIdentifier
from app.contracts.pipeline_web import PipelineRequest
from app.core.errors import ServiceError
from app.pipeline_service import validate

router = APIRouter(prefix="/v1/pipeline", dependencies=[Depends(authorize)], tags=["pipeline"])


@router.post("/jobs", status_code=202)
def submit(body: PipelineRequest, runtime: RT):
    """校验人工阶段与父任务，提交后立即返回；由浏览器服务转发层调用。"""
    payload = body.model_dump(mode="json")
    validate(runtime, payload)
    return {"operation_id": runtime.operations.submit("pipeline", payload)}


@router.get("/{operation_id}/files/{name:path}")
def download(operation_id: SafeIdentifier, name: str, runtime: RT, project_id: SafeIdentifier = Query()):
    """只下载同项目已完成操作登记的文件，拒绝目录遍历及未登记文件。"""
    operation = runtime.operations.get(operation_id, project_id)
    result = operation["result"]
    if operation["kind"] != "pipeline" or operation["state"] != "done" or name not in result["files"]:
        raise ServiceError("E_ARTIFACT_NOT_FOUND", "任务未完成或文件未登记。", 404)
    root = DataPaths(runtime.settings.data_dir).pipeline(
        result["run_id"], output=result.get("storage_layout") == "output-v1").root.resolve()
    path = (root / result["files"][name]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ServiceError("E_ARTIFACT_NOT_FOUND", "文件不可用。", 404)
    return FileResponse(path, filename=path.name)
