"""Initial durable jobs, material registry, latest profiles.


框架/维护/隔离验收入口。
结构导航：
├─ upgrade
├─ downgrade
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """建立本版本服务表和索引；由 Alembic 升级调度。"""
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column("request_data", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=True, unique=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=True),
        sa.Column("finished_at", sa.Float(), nullable=True),
    )
    op.create_index("ix_jobs_state", "jobs", ["state"])
    op.create_table(
        "job_items",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(128), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("url", sa.String(4096), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_run", sa.Float(), nullable=False),
        sa.Column("lease_until", sa.Float(), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("error_data", sa.JSON(), nullable=True),
        sa.UniqueConstraint("job_id", "url", name="uq_job_url"),
    )
    for column in ("job_id", "state", "next_run", "lease_until"):
        op.create_index(f"ix_job_items_{column}", "job_items", [column])
    op.create_table(
        "materials",
        sa.Column(
            "record_id", sa.String(36), sa.ForeignKey("job_items.record_id"), primary_key=True
        ),
        sa.Column("job_id", sa.String(128), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_materials_job_id", "materials", ["job_id"])
    op.create_table(
        "profiles",
        sa.Column("url", sa.String(4096), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade():
    """撤销本版本服务表；仅由显式 Alembic 降级调度。"""
    op.drop_table("profiles")
    op.drop_table("materials")
    op.drop_table("job_items")
    op.drop_table("jobs")
