"""功能层（依赖共用层，不横向导入功能模块）。
结构导航：
├─ iso
├─ Claim
│  └─ 契约字段
├─ Jobs
│  ├─ __init__
│  ├─ submit
│  ├─ get
│  ├─ retry
│  ├─ _ready
│  ├─ claim
│  ├─ heartbeat
│  ├─ complete
│  ├─ _refresh_job
│  ├─ save_profiles
│  ├─ export_materials
调用关系：
├─ submit → self.db.session
├─ get → self.db.session, iso
├─ retry → self.db.session
├─ claim → self.db.session, self._ready
├─ heartbeat → self.db.session
├─ complete → self.db.session, self._refresh_job, self.export_materials
├─ save_profiles → self.storage.paths.path, self.db.session, self.storage.write_json
├─ export_materials → self.storage.paths.path, self.db.session, self.storage.write_json。
数据流：任务请求 → 同事务待执行项 → 租约 → 结果与统计 → JSON 快照。
"""

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from filelock import FileLock
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.common.storage import Storage
from app.contracts.crawler import Counts, CrawlRequest, CrawlResult, Job, record_id_for
from app.core.errors import ServiceError
from app.db.models import ItemRow, JobRow, MaterialRow, ProfileRow
from app.db.session import Database

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3  # First attempt plus at most two automatic retries.


def iso(timestamp: float | None) -> str | None:
    """将持久时间戳转换成 UTC ISO 文本；只由任务查询调用。"""
    return datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp is not None else None


@dataclass(frozen=True)
class Claim:
    record_id: str
    job_id: str
    url: str
    token: str
    attempts: int
    request: CrawlRequest


class Jobs:
    """The persisted item is the queue entry: there is no save/enqueue dual write."""

    def __init__(self, db: Database, storage: Storage, lease_seconds: int = 120):
        """绑定当前对象的配置、依赖和实例状态；由所属入口构造流程调用。"""
        self.db, self.storage, self.lease_seconds = db, storage, lease_seconds

    def submit(self, request: CrawlRequest, key: str | None = None) -> str:
        """在同一事务保存幂等任务和待执行项；由提交入口调用。"""
        payload = request.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with self.db.session() as session:
            existing = session.get(JobRow, request.job_id)
            keyed = (
                session.scalar(select(JobRow).where(JobRow.idempotency_key == key)) if key else None
            )
            if existing or keyed:
                row = keyed or existing
                if row.request_hash != digest:
                    raise ServiceError(
                        "E_IDEMPOTENCY_CONFLICT", "相同任务或幂等键的请求内容不一致。", 409
                    )
                return row.job_id
            session.add(
                JobRow(
                    job_id=request.job_id,
                    project_id=request.project_id,
                    request_data=payload,
                    request_hash=digest,
                    idempotency_key=key,
                    state="queued",
                    created_at=time.time(),
                )
            )
            try:
                session.flush()
                session.add_all(
                    [
                        ItemRow(
                            record_id=record_id_for(request.job_id, url),
                            job_id=request.job_id,
                            url=url,
                            state="queued",
                            attempts=0,
                            next_run=0,
                        )
                        for url in request.urls
                    ]
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                row = session.get(JobRow, request.job_id)
                if row is None and key:
                    row = session.scalar(select(JobRow).where(JobRow.idempotency_key == key))
                if row and row.request_hash == digest:
                    return row.job_id
                raise ServiceError(
                    "E_IDEMPOTENCY_CONFLICT", "并发提交发生幂等冲突。", 409
                ) from None
        return request.job_id

    def get(self, job_id: str) -> Job:
        """按主键读取所属功能的固定返回契约；由对应业务入口调用。"""
        with self.db.session() as session:
            job = session.get(JobRow, job_id)
            if job is None:
                raise ServiceError("E_JOB_NOT_FOUND", "未找到任务。", 404)
            items = session.scalars(select(ItemRow).where(ItemRow.job_id == job_id)).all()
            counts = Counts()
            for item in items:
                if item.outcome:
                    setattr(counts, item.outcome, getattr(counts, item.outcome) + 1)
            errors = [
                item.error_data for item in items if item.error_data and item.state == "failed"
            ]
            return Job(
                job_id=job_id,
                project_id=job.project_id,
                state=job.state,
                total=len(items),
                finished=sum(x.state in {"done", "failed", "skipped"} for x in items),
                counts=counts,
                started_at=iso(job.started_at),
                finished_at=iso(job.finished_at),
                error=errors[0] if job.state == "failed" and errors else None,
            )

    def retry(self, job_id: str) -> str:
        """显式重排可重试失败项并保留主键；由任务重试入口调用。"""
        with self.db.session() as session:
            job = session.get(JobRow, job_id)
            if job is None:
                raise ServiceError("E_JOB_NOT_FOUND", "未找到任务。", 404)
            if job.state in {"queued", "running"}:
                return job_id
            items = session.scalars(
                select(ItemRow).where(ItemRow.job_id == job_id, ItemRow.state == "failed")
            ).all()
            eligible = [
                x.record_id for x in items if x.error_data and x.error_data.get("retryable")
            ]
            if not eligible:
                raise ServiceError("E_NO_RETRYABLE_ITEMS", "没有可重试的失败项。", 409)
            # CAS prevents simultaneous retry requests from resetting an active job.
            claimed = session.execute(
                update(JobRow)
                .where(JobRow.job_id == job_id, JobRow.state.in_(["done", "failed", "partial"]))
                .values(state="queued", finished_at=None)
            ).rowcount
            if not claimed:
                session.rollback()
                return job_id
            session.execute(
                update(ItemRow)
                .where(ItemRow.record_id.in_(eligible))
                .values(
                    state="queued",
                    outcome=None,
                    error_data=None,
                    attempts=0,
                    next_run=0,
                    lease_until=None,
                    lease_token=None,
                )
            )
            session.commit()
        return job_id

    @staticmethod
    def _ready(now):
        """构造可执行或过期租约条件；由任务领取调用。"""
        return or_(
            and_(ItemRow.state == "queued", ItemRow.next_run <= now),
            and_(ItemRow.state == "running", ItemRow.lease_until < now),
        )

    def claim(self, record_id: str | None = None) -> Claim | None:
        """通过条件更新领取一个持久工作项；由 Worker 单步执行调用。"""
        now = time.time()
        with self.db.session() as session:
            query = select(ItemRow).where(self._ready(now))
            if record_id is not None:
                query = query.where(ItemRow.record_id == record_id)
            candidate = session.scalar(query.order_by(ItemRow.next_run, ItemRow.record_id).limit(1))
            if candidate is None:
                return None
            record_id, job_id, url = candidate.record_id, candidate.job_id, candidate.url
            token = str(uuid4())
            claimed = session.execute(
                update(ItemRow)
                .where(ItemRow.record_id == record_id, self._ready(now))
                .values(
                    state="running",
                    lease_token=token,
                    lease_until=now + self.lease_seconds,
                    attempts=ItemRow.attempts + 1,
                    outcome=None,
                )
            ).rowcount
            if not claimed:
                session.rollback()
                return None
            session.execute(
                update(JobRow)
                .where(JobRow.job_id == job_id)
                .values(
                    state="running",
                    finished_at=None,
                )
            )
            session.execute(
                update(JobRow)
                .where(JobRow.job_id == job_id, JobRow.started_at.is_(None))
                .values(started_at=now)
            )
            session.flush()
            session.expire_all()
            item = session.get(ItemRow, record_id)
            request = CrawlRequest.model_validate(session.get(JobRow, job_id).request_data)
            attempts = item.attempts
            session.commit()
        return Claim(record_id, job_id, url, token, attempts, request)

    def heartbeat(self, claim: Claim) -> bool:
        """仅为仍持有令牌的执行者续租；由 Worker 心跳调用。"""
        with self.db.session() as session:
            changed = session.execute(
                update(ItemRow)
                .where(
                    ItemRow.record_id == claim.record_id,
                    ItemRow.lease_token == claim.token,
                    ItemRow.state == "running",
                )
                .values(lease_until=time.time() + self.lease_seconds)
            ).rowcount
            session.commit()
            return bool(changed)

    def complete(self, claim: Claim, result: CrawlResult, outcome: str) -> bool:
        """隔离过期执行者并持久化结果或重试意图；由 Worker 收尾调用。"""
        error = result.error
        retry = bool(
            error and error.retryable and outcome == "failed" and claim.attempts < MAX_ATTEMPTS
        )
        with self.db.session() as session:
            # This update also fences stale workers after a lease is reassigned.
            changed = session.execute(
                update(ItemRow)
                .where(
                    ItemRow.record_id == claim.record_id,
                    ItemRow.lease_token == claim.token,
                    ItemRow.state == "running",
                )
                .values(
                    state="queued"
                    if retry
                    else (
                        "failed"
                        if outcome == "failed"
                        else "skipped"
                        if outcome == "skipped"
                        else "done"
                    ),
                    outcome=None if retry else outcome,
                    error_data=error.model_dump() if error else None,
                    next_run=time.time() + min(2**claim.attempts, 30) + random.uniform(0, 1)
                    if retry
                    else 0,
                    lease_until=None,
                    lease_token=None,
                )
            ).rowcount
            if not changed:
                session.rollback()
                return False
            material = session.get(MaterialRow, claim.record_id)
            if material:
                material.payload = result.model_dump(mode="json")
            else:
                session.add(
                    MaterialRow(
                        record_id=claim.record_id,
                        job_id=claim.job_id,
                        payload=result.model_dump(mode="json"),
                    )
                )
            session.flush()
            self._refresh_job(session, claim.job_id)
            session.commit()
        try:
            self.export_materials()
        except OSError:
            logger.exception("Material snapshot failed; database committed; run industry export")
        return True

    def _refresh_job(self, session, job_id: str):
        # A row lock serializes aggregate refreshes on PostgreSQL; SQLite serializes writers.
        """串行刷新终态和聚合状态；由任务完成事务调用。"""
        job = session.scalar(select(JobRow).where(JobRow.job_id == job_id).with_for_update())
        session.expire_all()
        items = session.scalars(select(ItemRow).where(ItemRow.job_id == job_id)).all()
        if any(x.state in {"queued", "running"} for x in items):
            job.state = "running"
            return
        failed = sum(x.state == "failed" for x in items)
        succeeded = sum(x.outcome in {"ok", "ok_pdf"} for x in items)
        job.state = "partial" if failed and succeeded else "failed" if failed else "done"
        job.finished_at = time.time()

    def save_profiles(self, profiles):
        """保存最新画像并原子输出兼容快照；由 probe 入口调用。"""
        with FileLock(str(self.storage.paths.path("profiles_lock"))):
            with self.db.session() as session:
                for profile in profiles:
                    session.merge(
                        ProfileRow(url=profile.url, payload=profile.model_dump(mode="json"))
                    )
                session.commit()
                data = session.scalars(select(ProfileRow).order_by(ProfileRow.url)).all()
                self.storage.write_json(
                    self.storage.paths.path("profiles_file"), [x.payload for x in data]
                )

    def export_materials(self):
        """从数据库重建材料 JSON 快照；由任务完成或维护命令调用。"""
        with FileLock(str(self.storage.paths.path("materials_lock"))):
            with self.db.session() as session:
                materials = session.scalars(
                    select(MaterialRow).order_by(MaterialRow.record_id)
                ).all()
                self.storage.write_json(
                    self.storage.paths.path("materials_file"), [x.payload for x in materials]
                )
