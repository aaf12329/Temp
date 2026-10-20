"""Actual process boundary: stop API, consume persisted work, restart API and read results.


框架/维护/隔离验收入口。
结构导航：
├─ test_api_and_worker_are_independent_processes
调用关系：
├─ test_api_and_worker_are_independent_processes → start, request
├─ start → request。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.core.config import PROJECT_ROOT
from app.services.storage import Storage
from tests.conftest import TOKEN


def test_api_and_worker_are_independent_processes(settings, tmp_path):
    """验证 api and worker are independent processes；由 pytest 调度。"""
    environment = os.environ.copy()
    environment.update(
        {
            "INDUSTRY_DATABASE_URL": settings.database_url,
            "INDUSTRY_DATA_DIR": str(settings.data_dir),
            "INDUSTRY_API_TOKEN": TOKEN,
            "INDUSTRY_CRAWLER_ADAPTER": "tests.process_adapter:create_adapter",
        }
    )
    Storage.write_json(
        settings.data_dir / "policy/site_policy.json",
        [
            {
                "host": "example.test",
                "allowed_paths": ["/"],
                "note": "TEST FIXTURE ONLY",
            }
        ],
    )
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]

    def request(path, body=None):
        """向临时测试端口发送请求；由进程边界验收调用。"""
        value = Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode() if body else None,
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        with urlopen(value, timeout=2) as response:
            return json.load(response)

    def start(log):
        """启动临时 API 并等待具体就绪状态；由进程边界验收调用。"""
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if request("/health/ready")["status"] == "ready":
                    return process
            except (URLError, TimeoutError):
                pass
            if process.poll() is not None:
                break
            time.sleep(0.1)
        process.terminate()
        process.wait(timeout=5)
        raise AssertionError("API failed to start; see process log")

    with (tmp_path / "api.log").open("w", encoding="utf-8") as log:
        process = start(log)
        try:
            assert request(
                "/v1/jobs",
                {
                    "job_id": "process-test",
                    "urls": ["https://example.test/process"],
                },
            ) == {"job_id": "process-test"}
            assert request("/v1/jobs/process-test")["state"] == "queued"
        finally:
            process.terminate()
            process.wait(timeout=5)
        worker = subprocess.run(
            [sys.executable, "-m", "app.cli", "worker", "--once"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            timeout=20,
        )
        assert worker.returncode == 0, worker.stderr.decode("utf-8", errors="replace")
        process = start(log)
        try:
            assert request("/v1/jobs/process-test")["state"] == "done"
        finally:
            process.terminate()
            process.wait(timeout=5)
