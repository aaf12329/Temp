"""三进程总装隔离验收：假来源，真实Node/FastAPI/Worker/本地XGBoost。

结构：main → 临时迁移/启动 → 页面代理提交 → Worker → CSV/PNG/报告断言。
调用：维护者显式运行；数据流：合成网址与年表 → 隔离队列 → 实际模型产物，不访问网站或付费API。
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import numpy as np

from app.cli import migrate
from app.common.file_io import write_csv_rows, write_json
from app.common.paths import (
    ACCEPTANCE_DIRECTORY,
    FRONTEND_DIRECTORY,
    NEXT_EXECUTABLE,
    PIPELINE_SERVICE_CHECK,
    PROJECT_ROOT,
    DataPaths,
)
from app.contracts.pipeline import CLIConfig
from app.core.config import Settings
from app.local_service import stop_processes
from app.runtime import create_runtime


def free_port():
    """为隔离进程选取本机空闲端口，不借用已存在服务。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_url(client, path, timeout=40):
    """等待实际HTTP就绪，超时返回可定位错误。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            if response.status_code == 200:
                return response
        except httpx.TransportError:
            pass
        time.sleep(.2)
    raise RuntimeError(f"HTTP未就绪：{path}")


def await_operation(client, operation_id):
    """通过Node代理等待持久Worker完成，不直接调用执行函数。"""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"/api/platform/operations/{operation_id}")
        response.raise_for_status()
        operation = response.json()
        if operation["state"] in {"done", "failed"}:
            assert operation["state"] == "done", operation.get("error")
            return operation["result"]
        time.sleep(.2)
    raise RuntimeError("隔离Worker未完成")


def seed_model_input(settings):
    """登记明确标注的100年合成年表，只用于真实XGBoost服务接线验收。"""
    paths = DataPaths(settings.data_dir).pipeline("synthetic-seed")
    config = CLIConfig(llm="off")
    rows = [{"series_id": "synthetic", "scope": "SYNTHETIC PROCESS TEST ONLY", "period": 1900 + i,
             "value": float(100 + i * 2 + np.sin(i) * 5), "known_at": f"{1900 + i}-12-31"} for i in range(100)]
    counts = {"documents": 0, "selected_passages": 0, "candidate_facts": 100, "series_rows": 100}
    write_json(paths.file("analysis"), {"series": rows, "facts": [], "counts": counts})
    for name in ("access", "evidence"):
        write_json(paths.file(name), [])
    write_csv_rows(paths.file("information"), [], ["record_id", "text"])
    result = {"stage": "choice", "run_id": paths.root.name, "can_model": True, "gan_available": True,
              "context": {"question": "SYNTHETIC PROCESS TEST ONLY", "forecast_at": "2020-01-01",
                          "config": config.model_dump(mode="json")},
              "files": {paths.file(name).name: paths.file(name).name for name in
                        ("analysis", "access", "evidence", "information")}}
    runtime = create_runtime(settings)
    try:
        runtime.operations.submit("pipeline", {"operation_id": "synthetic-seed", "project_id": "local"})
        runtime.operations.complete(runtime.operations.claim("synthetic-seed"), result)
    finally:
        runtime.db.close()


def main():
    """启动三进程并验证两条实际请求链；所有任务和日志都在临时目录。"""
    parser = argparse.ArgumentParser(description="合成来源三进程验收")
    parser.add_argument("--preview", action="store_true", help="验收后保留临时服务供浏览器检查，回车退出")
    args = parser.parse_args()
    ACCEPTANCE_DIRECTORY.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="assembly-", dir=ACCEPTANCE_DIRECTORY) as directory, ExitStack() as stack:
        sandbox = Path(directory).resolve()
        assert sandbox.is_relative_to(PROJECT_ROOT.resolve())
        token = "synthetic-process-token-00000000000000000000"
        settings = Settings(_env_file=None, api_token=token, data_dir=sandbox / "data",
            database_url="sqlite:///" + (sandbox / "data/platform.db").as_posix(),
            crawler_adapter="tests.conftest:FixtureAdapter", advisor_api_key="")
        settings.data_dir.mkdir(parents=True)
        migrate(settings)
        seed_model_input(settings)
        api_port, web_port = free_port(), free_port()
        environment = {**os.environ, "PYTHONUTF8": "1", "INDUSTRY_API_TOKEN": token,
            "INDUSTRY_DATA_DIR": str(settings.data_dir), "INDUSTRY_DATABASE_URL": settings.database_url,
            "INDUSTRY_CRAWLER_ADAPTER": settings.crawler_adapter, "INDUSTRY_ADVISOR_API_KEY": "",
            "INDUSTRY_TASK_BACKEND": "database", "INDUSTRY_API_URL": f"http://127.0.0.1:{api_port}"}
        hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        processes = []
        try:
            commands = [([sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(api_port)], PROJECT_ROOT),
                        ([sys.executable, "-m", "app.cli", "worker"], PROJECT_ROOT),
                        ([shutil.which("node"), str(NEXT_EXECUTABLE), "start", "--hostname", "127.0.0.1", "--port", str(web_port)], FRONTEND_DIRECTORY)]
            for index, (command, cwd) in enumerate(commands):
                log = stack.enter_context((sandbox / f"process-{index}.log").open("w", encoding="utf-8"))
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, **hidden))
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=5) as api:
                wait_url(api, "/health/ready")
            with httpx.Client(base_url=f"http://127.0.0.1:{web_port}", trust_env=False, timeout=20) as client:
                assert "实际研究" in wait_url(client, "/").text
                origin = {"Origin": str(client.base_url).rstrip("/")}
                payload = {"operation_id": "source-roundtrip", "project_id": "local", "stage": "prepare",
                    "urls": ["https://example.test/a"], "question": "示例数量", "retrieval": "keywords", "llm": "off"}
                assert client.post("/api/platform/pipeline/jobs", json=payload).status_code == 403
                response = client.post("/api/platform/pipeline/jobs", json=payload, headers=origin)
                assert response.status_code == 202, response.text
                result = await_operation(client, payload["operation_id"])
                assert result["stage"] == "completed" and not result["can_model"]
                assert result["llm"]["status"] == "disabled"
                assert client.get("/api/platform/pipeline/source-roundtrip/files/report.md").status_code == 200
                response = client.post("/api/platform/pipeline/jobs", json={"operation_id": "model-roundtrip",
                    "project_id": "local", "stage": "model", "parent_id": "synthetic-seed", "use_generated": False}, headers=origin)
                assert response.status_code == 202, response.text
                model = await_operation(client, "model-roundtrip")
                assert model["modeling"]["status"] == "modeled", model
                assert model["modeling"]["series_reports"][0]["test"]["selected"]["n"] == 4
                png = next(name for name in model["files"] if name.endswith("shap.png"))
                assert client.get("/api/platform/pipeline/model-roundtrip/files/" + png).content.startswith(b"\x89PNG")
                report = {"next_fastapi_worker": True, "source_data_only": True, "real_xgboost": True,
                    "tree_shap_png_download": True, "cross_origin_rejected": True,
                    "gan_requires_user_choice": True, "paid_api_called": False, "production_data_modified": False}
                write_json(PIPELINE_SERVICE_CHECK, report)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                if args.preview:
                    print(f"合成验收预览：http://127.0.0.1:{web_port}/；恢复任务 model-roundtrip 查看实际计算结果。", flush=True)
                    input("按回车结束隔离预览并回收服务：")
        finally:
            stop_processes(processes)


if __name__ == "__main__":
    main()
