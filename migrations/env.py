"""框架/维护/隔离验收入口。
结构导航：
└─ 包标识，无执行函数
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

from logging.config import fileConfig

from alembic import context

from app.core.config import Settings
from app.db.models import Base
from app.db.session import Database

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
settings = config.attributes.get("settings") or Settings()
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=settings.absolute_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    database = Database(settings)
    with database.engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
    database.close()
