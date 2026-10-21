"""研究与建议操作的显式执行入口。

结构：run_operation → 心跳 → execute_operation → 条件完成。
调用：原生 Worker/Celery → run_operation；数据流：持久操作 → 租约执行 → 成功或明确失败。
"""

import logging

from app.common.leases import maintain_lease
from app.core.errors import ServiceError
from app.execution import execute_operation

logger = logging.getLogger(__name__)


def run_operation(runtime, operation_id: str | None = None) -> bool:
    """领取一个操作并续租，只由持有令牌者落库；由 Worker 或 Celery 调用。"""
    claim = runtime.operations.claim(operation_id)
    if claim is None:
        return False
    with maintain_lease(lambda: runtime.operations.heartbeat(claim),
                        runtime.settings.lease_seconds) as lost:
        result, error = None, None
        try:
            result = execute_operation(runtime, claim)
        except ServiceError as exc:
            error = exc.error.model_dump()
        except Exception:
            logger.exception("Operation failed: %s", claim.operation_id)
            error = ServiceError("E_OPERATION_FAILED", "任务执行异常，请查看服务日志并运行检查。", 500).error.model_dump()
    if not lost.is_set():
        runtime.operations.complete(claim, result, error)
    return True
