"""入口/显式聚合层（负责横向编排）。
结构导航：
├─ Runtime
│  └─ 契约字段
├─ create_runtime
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：配置 → 共用基础设施 → 业务功能对象 → Runtime。
"""

from dataclasses import dataclass

from app.advisor.integrated import IntegratedAdvisor
from app.common.storage import Storage
from app.core.config import Settings
from app.crawler.base import CrawlerAdapter
from app.crawler.integrated import IntegratedCrawler
from app.crawler.loader import load_adapter
from app.db.session import Database
from app.research import IntegratedResearch
from app.services.jobs import Jobs
from app.services.materials import Materials
from app.services.operations import Operations
from app.services.policies import Policies


@dataclass
class Runtime:
    settings: Settings
    db: Database
    storage: Storage
    jobs: Jobs
    policies: Policies
    materials: Materials
    adapter: CrawlerAdapter | None
    operations: Operations
    advisor: IntegratedAdvisor
    research: IntegratedResearch


def create_runtime(settings: Settings, adapter=None) -> Runtime:
    """按单向依赖装配数据库、共用存储和业务功能；由各入口调用。"""
    storage = Storage(settings.data_dir)
    storage.initialize()
    db = Database(settings)
    return Runtime(
        settings,
        db,
        storage,
        Jobs(db, storage, settings.lease_seconds),
        Policies(storage),
        Materials(db, storage),
        adapter if adapter is not None else (
            IntegratedCrawler(settings.data_dir, settings.bridge_timeout_s)
            if settings.crawler_adapter == "integrated" else load_adapter(settings.crawler_adapter)),
        Operations(db, settings.lease_seconds),
        IntegratedAdvisor(settings),
        IntegratedResearch(settings),
    )
