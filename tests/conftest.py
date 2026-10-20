"""框架/维护/隔离验收入口。
结构导航：
├─ FixtureAdapter
│  ├─ __init__
│  ├─ probe
│  ├─ crawl_one
├─ settings
├─ adapter
├─ client
├─ transient_error
调用关系：
├─ client → runtime.storage.write_json。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.cli import migrate
from app.contracts.crawler import CrawlResult, Meta, Profile, record_id_for
from app.core.config import Settings
from app.core.errors import ServiceError
from app.main import create_app
from app.services.storage import Storage

TOKEN = "test-only-token-012345678901234567890123456789"


class FixtureAdapter:
    """Local test fixture only: never connects to a website or produces real research."""

    def __init__(self):
        """绑定当前对象的配置、依赖和实例状态；由所属入口构造流程调用。"""
        self.calls = []
        self.behaviors = {}

    def probe(self, request, policies):
        """调用画像适配器并按请求保存最新画像；由 probe HTTP 路由调用。"""
        return [
            Profile(url=url, verdict="static", checked_at=datetime.now(UTC).isoformat())
            for url in request.urls
        ]

    def crawl_one(self, request, url, context):
        """执行单 URL 采集接口；由 Worker 调用，实现方遵守输入输出契约。"""
        self.calls.append(url)
        if url in self.behaviors:
            raise self.behaviors[url]
        text = "这是测试材料。\n第二段：2025年示例数量为12。"
        h10 = hashlib.sha256(url.encode()).hexdigest()[:10]
        stem = f"example.test_{h10}"
        text_ref = f"data/normalized/text/{stem}.txt"
        para_ref = f"data/normalized/para/{stem}.jsonl"
        (context.data_dir / "normalized/text" / f"{stem}.txt").write_text(
            text, "utf-8", newline="\n"
        )
        paragraphs = []
        offset = 0
        for index, paragraph in enumerate(text.split("\n")):
            paragraphs.append(
                {
                    "index": index,
                    "char_start": offset,
                    "char_end": offset + len(paragraph),
                    "page": None,
                    "text": paragraph,
                }
            )
            offset += len(paragraph) + 1
        (context.data_dir / "normalized/para" / f"{stem}.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in paragraphs) + "\n", "utf-8"
        )
        json_ref = f"data/normalized/tables/{stem}_t00.json"
        csv_ref = f"data/normalized/tables/{stem}_t00.csv"
        (context.data_dir / "normalized/tables" / f"{stem}_t00.json").write_text(
            '{"cells":[{"r":0,"c":0,"rowspan":1,"colspan":1,"text":"12"}]}', "utf-8"
        )
        (context.data_dir / "normalized/tables" / f"{stem}_t00.csv").write_text("12\n", "utf-8")
        return CrawlResult(
            record_id=record_id_for(request.job_id, url),
            job_id=request.job_id,
            project_id=request.project_id,
            url=url,
            site=urlsplit(url).hostname,
            final_url=url,
            fetched_at=datetime.now(UTC).isoformat(),
            elapsed_ms=1,
            meta=Meta(
                requested_by=request.requested_by,
                rule_version=request.rule_version,
                code_version=context.code_version,
            ),
            verdict="static",
            status="ok",
            storage={"text_path": text_ref},
            paragraph_file=para_ref,
            paragraph_count=2,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            tables=[{"index": 0, "rows": 1, "cols": 1, "json_path": json_ref, "csv_path": csv_ref}],
        )


@pytest.fixture
def settings(tmp_path):
    """构造临时数据库测试配置；由 pytest fixture 调度。"""
    value = Settings(
        _env_file=None,
        api_token=TOKEN,
        data_dir=tmp_path / "data",
        database_url="sqlite:///" + (tmp_path / "data/platform.db").as_posix(),
        lease_seconds=6,
    )
    Storage(value.data_dir).initialize()
    migrate(value)
    return value


@pytest.fixture
def adapter():
    """构造只使用合成材料的假采集器；由 pytest fixture 调度。"""
    return FixtureAdapter()


@pytest.fixture
def client(settings, adapter):
    """装配隔离测试服务和身份头；由 pytest fixture 调度。"""
    app = create_app(settings, adapter)
    with TestClient(app) as client:
        runtime = app.state.runtime
        runtime.storage.write_json(
            runtime.policies.path,
            [
                {
                    "host": "example.test",
                    "allowed_paths": ["/"],
                    "note": "TEST FIXTURE ONLY",
                }
            ],
        )
        client.headers.update({"Authorization": f"Bearer {TOKEN}", "X-Crawler-API-Version": "1.2"})
        yield client


@pytest.fixture
def transient_error():
    """提供可重试合成错误；由 pytest fixture 调度。"""
    return ServiceError("E_FIXTURE_TRANSIENT", "fixture temporary failure", 502, True)
