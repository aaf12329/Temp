"""共用契约/基础设施层。
结构导航：
├─ Base
│  └─ 契约字段
├─ JobRow
│  └─ 契约字段
├─ ItemRow
│  └─ 契约字段
├─ MaterialRow
│  └─ 契约字段
├─ ProfileRow
│  └─ 契约字段
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：声明式表定义 → SQLAlchemy 元数据；实际表由迁移建立。
"""

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128))
    request_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    created_at: Mapped[float] = mapped_column(Float)
    started_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)


class ItemRow(Base):
    __tablename__ = "job_items"
    __table_args__ = (UniqueConstraint("job_id", "url", name="uq_job_url"),)
    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), index=True)
    url: Mapped[str] = mapped_column(String(4096))
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_run: Mapped[float] = mapped_column(Float, default=0, index=True)
    lease_until: Mapped[float | None] = mapped_column(Float, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    outcome: Mapped[str | None] = mapped_column(String(20))
    error_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class MaterialRow(Base):
    __tablename__ = "materials"
    record_id: Mapped[str] = mapped_column(ForeignKey("job_items.record_id"), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ProfileRow(Base):
    __tablename__ = "profiles"
    url: Mapped[str] = mapped_column(String(4096), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class OperationRow(Base):
    """研究与建议的持久任务；令牌隔离过期执行者。"""

    __tablename__ = "operations"
    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    request_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[float | None] = mapped_column(Float, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    result_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
