"""入口/显式聚合层（负责横向编排）。
结构导航：
├─ create_app
调用关系：
├─ lifespan → runtime.db.close
├─ ready → runtime.db.engine.connect。
数据流：配置 → 生命周期装配 → HTTP 路由/中间件 → 脱敏响应。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException

from app.api.integration import router as integration_router
from app.api.pipeline import router as pipeline_router
from app.api.routes import router
from app.core.config import Settings
from app.core.errors import ServiceError
from app.runtime import create_runtime

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, adapter=None) -> FastAPI:
    """装配 FastAPI 生命周期、错误处理与路由；由 Uvicorn 工厂入口调用。"""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """初始化并释放本进程运行资源；由 FastAPI 生命周期调度。"""
        runtime = create_runtime(settings, adapter)
        application.state.runtime = runtime
        yield
        runtime.db.close()

    application = FastAPI(
        title="产业信息研究平台 · 服务接口",
        version="0.1.0",
        lifespan=lifespan,
        description="L1 爬虫接口 v1.2 服务框架。真实爬虫通过适配器接入。",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Crawler-API-Version"],
        expose_headers=["X-Crawler-API-Version"],
    )

    @application.middleware("http")
    async def version_and_cache(request: Request, call_next):
        """标注接口版本并禁止业务响应缓存；由 HTTP 中间件调度。"""
        response = await call_next(request)
        response.headers["X-Crawler-API-Version"] = "1.2"
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(ServiceError)
    async def service_error(_, exc: ServiceError):
        """将已知业务错误转换成统一错误体；由 FastAPI 异常处理调度。"""
        return JSONResponse(
            {"error": exc.error.model_dump()},
            status_code=exc.status,
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_, exc):
        # Do not echo input values, credentials or raw Pydantic context objects.
        """返回字段位置而不回显敏感输入；由请求校验错误调度。"""
        locations = [".".join(map(str, error["loc"])) for error in exc.errors()]
        return JSONResponse(
            {
                "error": {
                    "code": "E_VALIDATION",
                    "message": "参数校验失败：" + ", ".join(locations),
                    "retryable": False,
                }
            },
            status_code=422,
        )

    @application.exception_handler(HTTPException)
    async def http_error(_, exc):
        """把 HTTP 错误转换成统一错误体；由 FastAPI 异常调度。"""
        return JSONResponse(
            {
                "error": {
                    "code": "E_HTTP",
                    "message": str(exc.detail),
                    "retryable": False,
                }
            },
            status_code=exc.status_code,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(_, exc):
        """记录内部异常并返回脱敏错误；由框架兜底处理调用。"""
        logger.error("Unhandled API exception", exc_info=exc)
        return JSONResponse(
            {
                "error": {
                    "code": "E_INTERNAL",
                    "message": "服务内部错误，详见服务日志。",
                    "retryable": False,
                }
            },
            status_code=500,
            headers={"X-Crawler-API-Version": "1.2"},
        )

    @application.get("/health/live", tags=["health"])
    def live():
        """返回进程存活状态；由存活检查路由调用。"""
        return {"status": "ok"}

    @application.get("/health/ready", tags=["health"])
    def ready(request: Request):
        """检查数据库与适配器就绪状态；由就绪检查路由调用。"""
        runtime = request.app.state.runtime
        try:
            with runtime.db.engine.connect() as connection:
                connection.execute(text("SELECT job_id FROM jobs LIMIT 1"))
                connection.execute(text("SELECT operation_id FROM operations LIMIT 1"))
        except Exception:
            return JSONResponse({"status": "not_ready", "database": False}, status_code=503)
        configured = runtime.adapter is not None
        return JSONResponse(
            {
                "status": "ready" if configured else "waiting_for_crawler",
                "database": True,
                "crawler_configured": configured,
            },
            status_code=200 if configured else 503,
        )

    application.include_router(router)
    application.include_router(integration_router)
    application.include_router(pipeline_router)
    return application
