"""研究/建议持久任务功能层，不横向导入其他服务。

结构：OperationClaim → Operations.submit/get/claim/heartbeat/complete。
调用：API/Worker → Operations → 数据库；数据流：有界请求 → 条件领取 → 令牌校验后的结果。
建议任务结果不明确时停止，研究任务崩溃最多恢复两次；业务失败不自动重复模型付费调用。
"""

import hashlib
import json
import time
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ServiceError
from app.db.models import OperationRow


@dataclass(frozen=True)
class OperationClaim:
    """一次操作的不可变执行令牌；供 Worker 使用。"""

    operation_id: str
    project_id: str
    kind: str
    request: dict
    token: str
    attempts: int


class Operations:
    """数据库记录同时承担持久提交与消息恢复依据。"""

    def __init__(self, db, lease_seconds=120):
        """注入数据库与租约；由 runtime 调用。"""
        self.db, self.lease_seconds = db, lease_seconds

    def submit(self, kind: str, request: dict) -> str:
        """保存幂等操作，冲突时拒绝；由任务提交路由调用。"""
        digest = hashlib.sha256(json.dumps({"kind": kind, "request": request},
                                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        operation_id = request["operation_id"]
        with self.db.session() as session:
            row = session.get(OperationRow, operation_id)
            if row is None:
                session.add(OperationRow(operation_id=operation_id,
                            project_id=request["project_id"], kind=kind, request_data=request,
                            request_hash=digest, state="queued", attempts=0, created_at=time.time()))
                try:
                    session.commit()
                    return operation_id
                except IntegrityError:
                    session.rollback()
                    row = session.get(OperationRow, operation_id)
            if row is None or row.request_hash != digest:
                raise ServiceError("E_IDEMPOTENCY_CONFLICT", "操作 ID 已用于其他请求。", 409)
        return operation_id

    def get(self, operation_id: str, project_id: str) -> dict:
        """按项目查询操作，返回脱敏结果；由查询和编排入口调用。"""
        with self.db.session() as session:
            row = session.get(OperationRow, operation_id)
            if row is None or row.project_id != project_id:
                raise ServiceError("E_OPERATION_NOT_FOUND", "当前项目没有该操作。", 404)
            result = row.result_data
            progress = result.get("_progress") if isinstance(result, dict) else None
            if isinstance(result, dict) and "_progress" in result:
                result = {key: value for key, value in result.items() if key != "_progress"} or None
            return {"operation_id": row.operation_id, "project_id": row.project_id,
                    "kind": row.kind, "state": row.state, "attempts": row.attempts,
                    "result": result, "error": row.error_data, "progress": progress}

    def update_progress(self, claim, progress: dict) -> bool:
        """按当前令牌保存有界中间快照；由管线回调调用，无需数据库迁移。"""
        with self.db.session() as session:
            count = session.execute(update(OperationRow).where(
                OperationRow.operation_id == claim.operation_id, OperationRow.state == "running",
                OperationRow.lease_token == claim.token).values(
                result_data={"_progress": progress})).rowcount
            session.commit()
            return bool(count)

    @staticmethod
    def ready(now):
        """构造待执行或过期条件；由领取与 Celery 补投扫描调用。"""
        return or_(OperationRow.state == "queued", and_(OperationRow.state == "running",
                                                       OperationRow.lease_until < now))

    def claim(self, operation_id: str | None = None) -> OperationClaim | None:
        """CAS 领取任务，重复消息或并发竞争返回空；由 Worker 调用。"""
        now, token = time.time(), str(uuid4())
        with self.db.session() as session:
            query = select(OperationRow).where(self.ready(now)).order_by(OperationRow.created_at)
            if operation_id is not None:
                query = query.where(OperationRow.operation_id == operation_id)
            row = session.scalar(query.limit(1))
            if row is None:
                return None
            changed = session.execute(update(OperationRow).where(
                OperationRow.operation_id == row.operation_id, self.ready(now)).values(
                state="running", lease_token=token, lease_until=now + self.lease_seconds,
                attempts=OperationRow.attempts + 1, result_data=None, error_data=None)).rowcount
            if not changed:
                session.rollback()
                return None
            session.flush()
            session.refresh(row)
            claim = OperationClaim(row.operation_id, row.project_id, row.kind, row.request_data,
                                   token, row.attempts)
            session.commit()
            return claim

    def heartbeat(self, claim) -> bool:
        """仅续租当前令牌；由 Worker 心跳调用。"""
        with self.db.session() as session:
            count = session.execute(update(OperationRow).where(
                OperationRow.operation_id == claim.operation_id, OperationRow.state == "running",
                OperationRow.lease_token == claim.token).values(
                lease_until=time.time() + self.lease_seconds)).rowcount
            session.commit()
            return bool(count)

    def complete(self, claim, result=None, error=None) -> bool:
        """防止旧执行者覆盖结果；由 Worker 收尾调用。"""
        with self.db.session() as session:
            row = session.get(OperationRow, claim.operation_id)
            if row is None or row.lease_token != claim.token or row.state != "running":
                return False
            snapshot = row.result_data.get("_progress") if isinstance(row.result_data, dict) else None
            if snapshot is not None:
                result = {**(result or {}), "_progress": snapshot}
            count = session.execute(update(OperationRow).where(
                OperationRow.operation_id == claim.operation_id, OperationRow.state == "running",
                OperationRow.lease_token == claim.token).values(
                state="failed" if error else "done", result_data=result, error_data=error,
                lease_token=None, lease_until=None)).rowcount
            session.commit()
            return bool(count)
