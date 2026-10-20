"""共用契约/基础设施层。
结构导航：
├─ load_adapter
├─ require_adapter
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：显式模块工厂配置 → 采集适配器或配置错误。
"""

from importlib import import_module

from app.core.errors import ServiceError
from app.crawler.base import CrawlerAdapter


def load_adapter(spec: str) -> CrawlerAdapter | None:
    """按 module:factory 显式加载采集适配器；由资源编排调用。"""
    if not spec:
        return None
    module_name, separator, factory_name = spec.partition(":")
    if not separator:
        raise ValueError("INDUSTRY_CRAWLER_ADAPTER must be module:factory")
    adapter = getattr(import_module(module_name), factory_name)()
    for method in ("probe", "crawl_one"):
        if not callable(getattr(adapter, method, None)):
            raise TypeError(f"Crawler adapter is missing {method}")
    return adapter


def require_adapter(adapter: CrawlerAdapter | None) -> CrawlerAdapter:
    """缺少真实采集器时返回可操作配置提示；由 API/Worker 调用。"""
    if adapter is None:
        raise ServiceError(
            "E_CRAWLER_NOT_CONFIGURED",
            "尚未接入真实爬虫；请配置 INDUSTRY_CRAWLER_ADAPTER=模块:工厂函数。",
            status=503,
        )
    return adapter
