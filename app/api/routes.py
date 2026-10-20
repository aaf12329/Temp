"""入口/显式聚合层（负责横向编排）。
结构导航：
├─ get_runtime
├─ authorize
├─ probe
├─ submit_job
├─ get_job
├─ retry_job
├─ retry_job_compat
├─ get_material
├─ material_text
├─ material_paragraphs
├─ material_table
├─ get_site_policy
调用关系：
├─ authorize → runtime.settings.api_token.get_secret_value
├─ probe → runtime.policies.for_url, runtime.jobs.save_profiles
├─ submit_job → runtime.policies.get, runtime.jobs.submit
├─ get_job → runtime.jobs.get
├─ retry_job → runtime.jobs.retry
├─ retry_job_compat → retry_job
├─ get_material → runtime.materials.get
├─ material_text → runtime.materials.text
├─ material_paragraphs → runtime.materials.paragraphs
├─ material_table → runtime.materials.table
├─ get_site_policy → runtime.policies.get。
数据流：HTTP 请求 → 身份/参数校验 → 注入业务功能 → 固定响应或规范化文件。
"""

import secrets
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.contracts.crawler import (
    CrawlRequest,
    CrawlResult,
    Job,
    JobSubmitted,
    ProbeRequest,
    ProbeResponse,
    SitePolicy,
)
from app.core.errors import ErrorEnvelope, ServiceError
from app.crawler.loader import require_adapter
from app.runtime import Runtime

security = HTTPBearer(auto_error=False)


def get_runtime(request: Request) -> Runtime:
    """提取当前服务运行资源；由 FastAPI 依赖注入调用。"""
    return request.app.state.runtime


def authorize(
    runtime: Annotated[Runtime, Depends(get_runtime)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    version: Annotated[str | None, Header(alias="X-Crawler-API-Version")] = None,
):
    """校验 Bearer 与接口版本；由所有 v1 路由依赖调用。"""
    if (
        not credentials
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(
            credentials.credentials, runtime.settings.api_token.get_secret_value()
        )
    ):
        raise ServiceError("E_UNAUTHORIZED", "需要有效的 Bearer 凭据。", 401)
    if version is not None and version != "1.2":
        raise ServiceError("E_API_VERSION", "当前仅支持爬虫接口版本 1.2。", 409)


router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(authorize)],
    responses={
        status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 422, 500, 503)
    },
)
RT = Annotated[Runtime, Depends(get_runtime)]


@router.post("/probe", tags=["crawler"], response_model=ProbeResponse)
def probe(body: ProbeRequest, runtime: RT):
    """调用画像适配器并按请求保存最新画像；由 probe HTTP 路由调用。"""
    adapter = require_adapter(runtime.adapter)
    policies = [runtime.policies.for_url(url) for url in body.urls]
    from app.contracts.crawler import Profile

    profiles = [Profile.model_validate(x) for x in adapter.probe(body, policies)]
    if [x.url for x in profiles] != body.urls:
        raise ServiceError("E_ADAPTER_CONTRACT", "probe 结果与输入 URL 列表不一致。", 500)
    if body.save:
        runtime.jobs.save_profiles(profiles)
    return {"profiles": profiles}


@router.post("/jobs", tags=["jobs"], status_code=202, response_model=JobSubmitted)
def submit_job(
    body: CrawlRequest,
    runtime: RT,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=200)] = None,
) -> dict[str, str]:
    """校验适配器和来源策略后创建幂等任务；由 POST jobs 调用。"""
    require_adapter(runtime.adapter)
    for url in body.urls:
        runtime.policies.get(urlsplit(url).hostname or "")
    return {"job_id": runtime.jobs.submit(body, idempotency_key)}


@router.get("/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: str, runtime: RT) -> Job:
    """返回持久任务的状态和统计；由 GET jobs 调用。"""
    return runtime.jobs.get(job_id)


@router.post("/jobs/{job_id}/retry", tags=["jobs"], status_code=202, response_model=JobSubmitted)
def retry_job(job_id: str, runtime: RT) -> dict[str, str]:
    """重新安排可重试的失败项；由 POST retry 调用。"""
    require_adapter(runtime.adapter)
    return {"job_id": runtime.jobs.retry(job_id)}


@router.get(
    "/jobs/{job_id}/retry",
    tags=["jobs"],
    deprecated=True,
    status_code=202,
    response_model=JobSubmitted,
)
def retry_job_compat(job_id: str, runtime: RT, response: Response) -> dict[str, str]:
    """v1.2 compatibility. Prefer POST for this state-changing action."""
    response.headers["Cache-Control"] = "no-store"
    return retry_job(job_id, runtime)


@router.get("/materials/{record_id}", tags=["materials"])
def get_material(record_id: str, runtime: RT) -> CrawlResult:
    """读取材料元数据；由材料 HTTP 路由调用。"""
    return runtime.materials.get(record_id)


@router.get("/materials/{record_id}/text", tags=["materials"], response_class=FileResponse)
def material_text(record_id: str, runtime: RT):
    """返回规范化正文文件；由正文 HTTP 路由调用。"""
    return FileResponse(runtime.materials.text(record_id), media_type="text/plain; charset=utf-8")


@router.get("/materials/{record_id}/paragraphs", tags=["materials"], response_class=FileResponse)
def material_paragraphs(record_id: str, runtime: RT):
    """返回段落锚点 NDJSON；由段落 HTTP 路由调用。"""
    return FileResponse(runtime.materials.paragraphs(record_id), media_type="application/x-ndjson")


@router.get(
    "/materials/{record_id}/tables/{index}", tags=["materials"], response_class=FileResponse
)
def material_table(
    record_id: str, index: int, runtime: RT, format: Literal["json", "csv"] = "json"
):
    """返回选定 JSON/CSV 表格；由表格 HTTP 路由调用。"""
    path = runtime.materials.table(record_id, index, format)
    return FileResponse(path, media_type="application/json" if format == "json" else "text/csv")


@router.get("/site-policy/{host}", tags=["policies"])
def get_site_policy(host: str, runtime: RT) -> SitePolicy:
    """读取人工站点策略；由站点策略路由调用。"""
    return runtime.policies.get(host)
