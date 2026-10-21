"""研究与建议任务迁移。

结构：upgrade/downgrade；调用：Alembic → 本模块；数据流：版本 0001 ↔ 0002 表结构。
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    """创建独立操作任务表；由 Alembic 升级调用。"""
    op.create_table("operations",
                    sa.Column("operation_id", sa.String(64), primary_key=True),
                    sa.Column("project_id", sa.String(64), nullable=False),
                    sa.Column("kind", sa.String(20), nullable=False),
                    sa.Column("request_data", sa.JSON(), nullable=False),
                    sa.Column("request_hash", sa.String(64), nullable=False),
                    sa.Column("state", sa.String(20), nullable=False),
                    sa.Column("created_at", sa.Float(), nullable=False),
                    sa.Column("attempts", sa.Integer(), nullable=False),
                    sa.Column("lease_until", sa.Float(), nullable=True),
                    sa.Column("lease_token", sa.String(36), nullable=True),
                    sa.Column("result_data", sa.JSON(), nullable=True),
                    sa.Column("error_data", sa.JSON(), nullable=True))
    for column in ("project_id", "state", "lease_until"):
        op.create_index(f"ix_operations_{column}", "operations", [column])


def downgrade():
    """只撤销本版本任务表；由显式降级调用。"""
    op.drop_table("operations")
