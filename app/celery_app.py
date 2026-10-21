"""Celery 显式调度入口：数据库为任务事实源，Redis 只承载投递。

结构：build_celery → dispatch_pending → execute_item/execute_operation。
调用：Beat → dispatch_pending → Redis → Celery Worker → 既有执行器。
数据流：数据库待执行/过期行 → 仅含 ID 的消息 → CAS 领取/心跳 → 数据库结果。
同一持久待执行行是 outbox；每轮补投，无需数据库提交和 Redis 发布原子双写。
"""

import time

from celery import Celery
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import ItemRow, OperationRow
from app.operation_worker import run_operation
from app.runtime import create_runtime
from app.services.jobs import Jobs
from app.services.operations import Operations
from app.worker import run_one


def build_celery(settings) -> Celery:
    """构造 JSON 消息队列配置；由本模块入口或隔离测试调用。"""
    instance = Celery("industry", broker=settings.redis_url.get_secret_value())
    instance.conf.update(
        task_serializer="json", accept_content=["json"], result_serializer="json",
        task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": 9000}, timezone="UTC",
        task_routes={"industry.dispatch": {"queue": "control"},
                     "industry.crawl": {"queue": "crawl"},
                     "industry.operation": {"queue": "operations"}},
        beat_schedule={"recover-persisted-work": {"task": "industry.dispatch", "schedule": 5.0,
                                                  "options": {"expires": 5}}},
    )
    return instance


celery = build_celery(Settings())


@celery.task(name="industry.dispatch")
def dispatch_pending():
    """补投数据库待执行/过期任务；由单实例 Beat 每五秒触发，失败下轮再扫描。"""
    runtime = create_runtime(Settings())
    try:
        now = time.time()
        with runtime.db.session() as session:
            items = session.scalars(select(ItemRow.record_id).where(Jobs._ready(now)).order_by(
                ItemRow.next_run, ItemRow.record_id).limit(100)).all()
            operations = session.scalars(select(OperationRow.operation_id).where(
                Operations.ready(now)).order_by(OperationRow.created_at).limit(100)).all()
        for record_id in items:
            execute_item.apply_async(args=[record_id], expires=10)
        for operation_id in operations:
            execute_operation.apply_async(args=[operation_id], expires=10)
        return {"crawl_messages": len(items), "operation_messages": len(operations)}
    finally:
        runtime.db.close()


@celery.task(name="industry.crawl")
def execute_item(record_id: str):
    """按 ID 条件领取一条采集，重复消息无副作用；由 Celery Worker 调用。"""
    runtime = create_runtime(Settings())
    try:
        return run_one(runtime, record_id)
    finally:
        runtime.db.close()


@celery.task(name="industry.operation")
def execute_operation(operation_id: str):
    """按 ID 条件执行研究或建议；由 Celery Worker 调用，不使用 Celery 自动重试。"""
    runtime = create_runtime(Settings())
    try:
        return run_operation(runtime, operation_id)
    finally:
        runtime.db.close()
