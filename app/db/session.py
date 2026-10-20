"""共用契约/基础设施层。
结构导航：
├─ Database
│  ├─ __init__
│  ├─ close
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：数据库配置 → 引擎/会话工厂 → 独立事务 → 连接释放。
"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings


class Database:
    def __init__(self, settings: Settings):
        """绑定当前对象的配置、依赖和实例状态；由所属入口构造流程调用。"""
        url = settings.absolute_database_url()
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite:"):

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                """为连接设置外键、WAL 和忙等待；由 SQLAlchemy 连接事件调用。"""
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def close(self):
        """释放数据库连接池；由资源入口收尾调用。"""
        self.engine.dispose()
