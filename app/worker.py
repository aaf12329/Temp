"""入口/显式聚合层（负责横向编排）。
结构导航：
├─ failed_result
├─ check_result
├─ run_one
├─ run_forever
调用关系：
├─ run_one → runtime.jobs.claim, runtime.policies.for_url, check_result, failed_result, runtime.jobs.complete
├─ run_forever → run_one
├─ maintain_lease → runtime.jobs.heartbeat。
数据流：持久待执行项 → 租约/采集/校验 → 结果或重试状态。
"""

import logging
import signal
import threading
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.common.leases import maintain_lease
from app.contracts.crawler import CrawlResult, Meta
from app.core.errors import ServiceError
from app.crawler.base import CrawlContext
from app.crawler.loader import require_adapter
from app.operation_worker import run_operation
from app.runtime import Runtime
from app.services.jobs import MAX_ATTEMPTS, Claim

logger = logging.getLogger(__name__)


def failed_result(runtime: Runtime, claim: Claim, error) -> CrawlResult:
    """将明确错误封装成可追溯失败材料；由 Worker 异常分支调用。"""
    return CrawlResult(
        record_id=claim.record_id,
        job_id=claim.job_id,
        project_id=claim.request.project_id,
        url=claim.url,
        site=urlsplit(claim.url).hostname or "",
        fetched_at=datetime.now(UTC).isoformat(),
        elapsed_ms=0,
        meta=Meta(
            requested_by=claim.request.requested_by,
            rule_version=claim.request.rule_version,
            code_version=runtime.settings.code_version,
        ),
        verdict="error",
        status="failed",
        error=error,
    )


def check_result(claim: Claim, result: CrawlResult) -> str:
    """核对采集身份并映射临时统计状态；由 Worker 调用。"""
    if (
        result.record_id != claim.record_id
        or result.job_id != claim.job_id
        or result.url != claim.url
        or result.project_id != claim.request.project_id
    ):
        raise ServiceError("E_ADAPTER_CONTRACT", "爬虫返回的材料身份与任务不一致。", 500)
    if result.verdict in {"robots_disallow", "robots_unknown", "login_wall", "blocked", "dynamic"}:
        return "skipped"
    if result.error:
        return "failed"
    if result.verdict == "error":
        raise ServiceError("E_ADAPTER_CONTRACT", "失败材料必须包含 error。", 500)
    return "ok_pdf" if result.verdict == "pdf" else "ok"


def run_one(runtime: Runtime, record_id: str | None = None) -> bool:
    """领取并处理一个持久工作项；由 Worker 循环或单步命令调用。"""
    adapter = require_adapter(runtime.adapter)
    claim = runtime.jobs.claim(record_id)
    if claim is None:
        return False
    with maintain_lease(lambda: runtime.jobs.heartbeat(claim),
                        runtime.settings.lease_seconds) as lease_lost:
        try:
            if claim.attempts > MAX_ATTEMPTS:
                raise ServiceError("E_WORKER_LOST", "任务多次因进程中断失去执行租约。", 500, True)
            policy = runtime.policies.for_url(claim.url)
            result = CrawlResult.model_validate(
                adapter.crawl_one(
                    claim.request,
                    claim.url,
                    CrawlContext(runtime.storage.root, policy, runtime.settings.code_version),
                )
            )
            outcome = check_result(claim, result)
        except ServiceError as exc:
            result, outcome = failed_result(runtime, claim, exc.error), "failed"
        except Exception:
            logger.exception("Crawler adapter failed for %s", claim.record_id)
            error = ServiceError("E_ADAPTER_ERROR", "爬虫适配器执行异常，详见 Worker 日志。", 500)
            result, outcome = failed_result(runtime, claim, error.error), "failed"
    if not lease_lost.is_set():
        runtime.jobs.complete(claim, result, outcome)
    return True


def run_forever(runtime: Runtime):
    """维护独立 Worker 循环和退出信号；由 CLI worker 调用。"""
    if runtime.settings.task_backend != "database":
        raise ServiceError("E_WORKER_MODE", "当前为 Celery 模式，请启动 Celery Worker 和 Beat。", 409)
    require_adapter(runtime.adapter)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    logger.info("Worker started; durable queue; adapter=%s", runtime.settings.crawler_adapter)
    while not stop.is_set():
        try:
            worked = run_operation(runtime)
            crawled = run_one(runtime)
            if not (worked or crawled):
                stop.wait(runtime.settings.poll_interval_s)
        except Exception:
            logger.exception(
                "Worker iteration failed; persisted leases will recover unfinished work"
            )
            time.sleep(runtime.settings.poll_interval_s)
