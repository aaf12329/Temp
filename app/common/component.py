"""隔离组件调用基础设施。

结构：run_component → 同一解释器的 bridge 子进程；调用：采集/研究适配器 → 本模块。
数据流：可信 JSON 载荷 → 最小环境子进程 → 已登记 JSON 响应；密钥不传给爬虫。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.common.file_io import read_json
from app.common.paths import PROJECT_ROOT, DataPaths
from app.core.errors import ServiceError


def _streamed_run(command: list[str], *, timeout: float | None, input: str, **options):
    """CLI 流式打印并在超时杀掉解释器子树，避免 Windows venv 启动器遗留训练。"""
    import psutil

    process = subprocess.Popen(command, stdin=subprocess.PIPE, **options)
    try:
        process.communicate(input=input, timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            descendants = []
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode)


def run_component(payload: dict, workspace: Path, data_root: Path, timeout: float | None = 600,
                  *, stream: bool = False) -> dict:
    """在统一 Python 环境隔离旧模块全局状态；由显式适配层调用。"""
    paths = DataPaths(data_root)
    workspace.mkdir(parents=True, exist_ok=True)
    result_file = paths.bridge_file(workspace, "result")
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP",
                                      "LANG", "LC_ALL", "HOME", "USERPROFILE"}}
    if payload["action"] != "cli-model":
        environment.update({"CUDA_VISIBLE_DEVICES": "-1", "OMP_NUM_THREADS": "2",
                            "MKL_NUM_THREADS": "2", "TF_NUM_INTRAOP_THREADS": "2",
                            "TF_NUM_INTEROP_THREADS": "1"})
    elif "CUDA_VISIBLE_DEVICES" in os.environ:
        environment["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
    environment.update({"PYTHONUTF8": "1",
                        "INDUSTRY_COMPONENT_WORKSPACE": str(workspace),
                        "INDUSTRY_COMPONENT_MODEL_DIR": str(paths.model_directory()),
                        "INDUSTRY_COMPONENT_MODEL_REFERENCE": "data/models/bge-small-zh-v1.5-int8"})
    message = {**payload, "workspace": str(workspace), "data_root": str(data_root),
               "result_file": str(result_file)}
    try:
        runner = _streamed_run if stream else subprocess.run
        options = {} if stream else {"capture_output": True}
        completed = runner(
            [sys.executable, "-m", "app.crawler.bridge"], cwd=PROJECT_ROOT,
            env=environment, input=json.dumps(message, ensure_ascii=False),
            **options, text=True, encoding="utf-8", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ServiceError("E_COMPONENT_TIMEOUT", "组件超时，请缩小材料批次后重试。", 504, True) from None
    if completed.returncode or not result_file.is_file():
        raise ServiceError("E_COMPONENT_FAILED", "组件运行失败，请运行项目检查并核对依赖。", 500)
    result = read_json(result_file)
    if result.get("error"):
        error = result["error"]
        raise ServiceError(error["code"], error["message"], 502, bool(error.get("retryable")))
    return result["data"]
