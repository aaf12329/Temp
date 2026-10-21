"""本地 API/Worker 的显式启动聚合入口，不提交采集或付费任务。

结构：main → serve → check_port / wait_ready / stop_processes。
调用：快速部署/Start.ps1 → 同一解释器的 Uvicorn 与数据库队列 Worker。
数据流：端口、根配置 → 两个受监管进程、就绪状态与相对日志位置。
约束：只用于本机数据库队列；不冒充 Linux Celery/Redis 部署。
"""

import argparse
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from threading import Event
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import psutil
from filelock import FileLock, Timeout

from app.common.paths import LOCAL_API_LOG, LOCAL_SERVICE_LOCK, LOCAL_WORKER_LOG, PROJECT_ROOT
from app.core.config import Settings


def check_port(port: int) -> None:
    """在启动前检查本机端口，拒绝借用其他服务的健康响应；由 serve 调用。"""
    with socket.socket() as probe:
        if sys.platform == "win32":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))


def stop_processes(processes: list[subprocess.Popen]) -> None:
    """终止本入口创建的子树，包含 Windows venv 启动器；由退出清理调用。"""
    targets = []
    for process in processes:
        try:
            parent = psutil.Process(process.pid)
            targets.extend(parent.children(recursive=True))
            targets.append(parent)
        except psutil.NoSuchProcess:
            continue
    for target in reversed(targets):
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(targets, timeout=5)
    for target in alive:
        try:
            target.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)
    for process in processes:
        process.wait(timeout=5)


def check_children(processes: list[subprocess.Popen]) -> None:
    """发现 API 或 Worker 异常退出就中止整组；由就绪和运行循环调用。"""
    for name, process in zip(("API", "Worker"), processes, strict=True):
        if process.poll() is not None:
            raise RuntimeError(f"{name} 已退出（{process.returncode}），请查看 work/quick-deploy 日志。")


def wait_ready(port: int, processes: list[subprocess.Popen], timeout: float = 30) -> None:
    """轮询真实 API 就绪状态并检查两进程存活；由 serve 调用。"""
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_children(processes)
        try:
            with opener.open(f"http://127.0.0.1:{port}/health/ready", timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.2)
    raise RuntimeError("API 未在 30 秒内就绪，请查看 work/quick-deploy/api.log。")


def serve(port: int, stop_event: Event | None = None) -> None:
    """启动并监管两进程，退出时回收；由命令入口或隔离验收调用。"""
    if Settings().task_backend != "database":
        raise RuntimeError("此入口用于本机 database 队列；Celery 模式请使用 compose.yaml。")
    check_port(port)
    LOCAL_SERVICE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    processes = []
    with FileLock(LOCAL_SERVICE_LOCK, timeout=0), ExitStack() as stack:
        try:
            for command, log in (
                (["-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1",
                  "--port", str(port)], LOCAL_API_LOG),
                (["-m", "app.cli", "worker"], LOCAL_WORKER_LOG),
            ):
                output = stack.enter_context(log.open("w", encoding="utf-8"))
                processes.append(subprocess.Popen([sys.executable, *command], cwd=PROJECT_ROOT,
                                 stdout=output, stderr=subprocess.STDOUT, **hidden))
            wait_ready(port, processes)
            print(f"后端已就绪：http://127.0.0.1:{port}/docs", flush=True)
            print("API 与 Worker 已启动；日志：work/quick-deploy/。按 Ctrl+C 停止。", flush=True)
            while stop_event is None or not stop_event.is_set():
                check_children(processes)
                time.sleep(0.5)
        finally:
            stop_processes(processes)


def interrupt(signum, frame) -> None:
    """把终止信号转为可清理退出；由系统信号调度。"""
    raise KeyboardInterrupt


def main() -> int:
    """校验端口并注册退出处理；由快速部署脚本调用。"""
    parser = argparse.ArgumentParser(description="启动本地 API 与 Worker")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口应为 1～65535。")
    signal.signal(signal.SIGTERM, interrupt)
    try:
        serve(args.port)
    except KeyboardInterrupt:
        print("API 与 Worker 已停止。", flush=True)
    except Timeout:
        print("另一组本地服务正在运行，请先关闭它。", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
