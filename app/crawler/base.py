"""共用契约/基础设施层。
结构导航：
├─ CrawlContext
│  └─ 契约字段
├─ CrawlerAdapter
│  ├─ probe
│  ├─ crawl_one
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：共享协议定义 → 外部采集实现；本文件不执行网络请求。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.contracts.crawler import CrawlRequest, CrawlResult, ProbeRequest, Profile, SitePolicy


@dataclass(frozen=True)
class CrawlContext:
    data_dir: Path
    policy: SitePolicy
    code_version: str


class CrawlerAdapter(Protocol):
    """Implement this in the crawler package; no FastAPI or database dependency is required.

    Adapter must enforce robots, host rate limits, network timeouts, redirect/DNS safety,
    write byte-preserving raw files and normalized outputs, and return v1.2 records.
    probe must apply the same network policy. No built-in adapter performs network I/O.
    """

    def probe(self, request: ProbeRequest, policies: list[SitePolicy]) -> list[Profile]:
        """调用画像适配器并按请求保存最新画像；由 probe HTTP 路由调用。"""
        ...

    def crawl_one(self, request: CrawlRequest, url: str, context: CrawlContext) -> CrawlResult:
        """执行单 URL 采集接口；由 Worker 调用，实现方遵守输入输出契约。"""
        ...
