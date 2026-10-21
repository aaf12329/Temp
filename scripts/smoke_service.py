"""实际 API/Worker 进程的隔离验收入口，不调用付费模型。

结构：main → 临时数据库迁移 → Uvicorn → HTTP 提交 → 独立 Worker → 状态验证。
调用：维护者显式运行；数据流：临时目录和演示请求 → 持久任务 → 成功状态与相对报告。
不读取或改写正式研究数据，子进程收尾全部退出。
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from app.common.file_io import write_json
from app.common.paths import ACCEPTANCE_DIRECTORY, PROJECT_ROOT, SERVICE_CHECK
from app.core.config import Settings


def main() -> None:
    """在受限临时根目录验证真实 API 和 Worker；由显式验收入口调用。"""
    ACCEPTANCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    with TemporaryDirectory(prefix="service-", dir=ACCEPTANCE_DIRECTORY) as directory:
        sandbox = Path(directory).resolve()
        assert sandbox.is_relative_to(PROJECT_ROOT.resolve())
        relative = sandbox.relative_to(PROJECT_ROOT).as_posix()
        environment = {**os.environ, "PYTHONUTF8": "1", "INDUSTRY_DATA_DIR": relative + "/data",
                       "INDUSTRY_DATABASE_URL": "sqlite:///" + relative + "/data/platform.db",
                       "INDUSTRY_TASK_BACKEND": "database", "INDUSTRY_CRAWLER_ADAPTER": "integrated"}
        subprocess.run([sys.executable, "-m", "app.cli", "init"], cwd=PROJECT_ROOT,
                       env=environment, check=True, capture_output=True, **hidden)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:create_app",
                                   "--factory", "--host", "127.0.0.1", "--port", str(port)],
                                  cwd=PROJECT_ROOT, env=environment,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        ready = client.get("/health/ready")
                        if ready.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError("API did not become ready")
                    time.sleep(0.1)
                assert client.get("/v1/operations/check", params={"project_id": "smoke"}).status_code == 401
                client.headers["Authorization"] = "Bearer " + Settings().api_token.get_secret_value()
                response = client.post("/v1/advisor/jobs", json={"operation_id": "smoke-advice",
                    "project_id": "smoke", "question": "离线演示：如何先核对研究口径？", "demo": True})
                assert response.status_code == 202, response.text
                subprocess.run([sys.executable, "-m", "app.cli", "worker", "--once"],
                               cwd=PROJECT_ROOT, env=environment, check=True, capture_output=True, **hidden)
                result = client.get("/v1/operations/smoke-advice", params={"project_id": "smoke"}).json()
                assert result["state"] == "done" and result["result"]["mode"] == "demo"
                history = client.get("/v1/projects/smoke/advisor/sessions/default",
                                     params={"demo": True}).json()
                assert history["total_turns"] == 1
                report = {"api_ready": True, "unauthenticated_rejected": True,
                          "submit_status": response.status_code, "worker_separate_process": True,
                          "operation_state": result["state"], "demo_turns": history["total_turns"],
                          "paid_model_called": False, "production_data_modified": False,
                          "report": SERVICE_CHECK.relative_to(PROJECT_ROOT).as_posix()}
                write_json(SERVICE_CHECK, report)
                print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
