"""部署入口验收：隔离配置、依赖缓存和真实 API/Worker 生命周期。

结构：部署沙箱 → PowerShell 入口检查；独立临时数据库 → 本地服务集成。
调用：pytest → 部署脚本/serve，不访问真实网站或付费模型。
数据流：临时 JSON、假安装器与离线 npm 包 → 安装行为、任务完成与进程回收断言。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psutil
import pytest

from app import local_service
from app.common.paths import PROJECT_ROOT

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows deployment entry")


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """复制部署脚本并以假安装器记录动作；不向当前环境安装测试包。"""
    root = tmp_path / "部署 空格"
    folder = root / "快速部署"
    folder.mkdir(parents=True)
    for name in ("Start.ps1", "Hardware.ps1", "config.example.json"):
        shutil.copyfile(PROJECT_ROOT / "快速部署" / name, folder / name)
    buttons = root / "启动脚本/01_平台"
    buttons.mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "启动脚本/01_平台/一键启动.bat", buttons / "一键启动.bat")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "Setup.ps1").write_text(
        "param($PythonPath, $GANBackend, [switch]$UseExistingEnvironment)\n"
        "if (-not $UseExistingEnvironment) { exit 7 }\n"
        "[IO.File]::AppendAllText((Join-Path $PSScriptRoot 'calls.txt'), 'setup;')\n"
        "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'backend.txt'), $GANBackend)\n"
        "exit 0\n", encoding="utf-8-sig")
    (scripts / "init_config.py").write_text("print('isolated init')", encoding="utf-8")
    (root / "requirement.txt").write_text("# fixture", encoding="utf-8")
    for name, value in {
        "INDUSTRY_API_TOKEN": "deployment-test-token-0000000000000000",
        "INDUSTRY_DATABASE_URL": "sqlite:///" + (root / "db.sqlite").as_posix(),
        "INDUSTRY_DATA_DIR": str(root / "data"),
        "INDUSTRY_TASK_BACKEND": "database",
        "INDUSTRY_ADVISOR_API_KEY": "",
    }.items():
        monkeypatch.setenv(name, value)
    config = folder / "config.local.json"
    config.write_text(json.dumps({"environment_mode": "existing", "python_path": sys.executable,
                                 "prepare_vector_model": False}), encoding="utf-8")
    return root, config


def run_deploy(root: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    """运行真实 PowerShell 5.1 入口并收集结果；由部署用例调用。"""
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                           "-File", str(root / "快速部署/Start.ps1"), *args],
                          cwd=root, capture_output=True, encoding="utf-8", errors="replace",
                          input=input_text, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)


def test_existing_interpreter_reuse_and_dependency_change(deployment):
    """验证中文路径、明确解释器、缓存复用及依赖变更重新安装。"""
    root, _ = deployment
    first = run_deploy(root, "-SetupOnly")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "skipping npm" in first.stdout
    assert not (root / ".venv").exists()
    second = run_deploy(root, "-SetupOnly")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "reusing environment" in second.stdout
    assert (root / "scripts/calls.txt").read_text() == "setup;"
    (root / "requirement.txt").write_text("# fixture changed", encoding="utf-8")
    third = run_deploy(root, "-SetupOnly")
    assert third.returncode == 0, third.stdout + third.stderr
    assert (root / "scripts/calls.txt").read_text() == "setup;setup;"


def test_bat_handles_chinese_space_path_and_propagates_failure(deployment):
    """真实 CMD 调用中文空格路径，计划不安装，错误退出码不丢失。"""
    root, config = deployment
    command = ["cmd.exe", "/d", "/c", str(root / "启动脚本/01_平台/一键启动.bat"), "-Plan"]
    good = subprocess.run(command, input="", capture_output=True, encoding="utf-8", errors="replace",
                          timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert good.returncode == 0, good.stdout + good.stderr
    assert "[PLAN]" in good.stdout
    assert not (root / "scripts/calls.txt").exists()
    config.write_text('{"environment_mode":"wrong"}', encoding="utf-8")
    bad = subprocess.run(command, input="", capture_output=True, encoding="utf-8", errors="replace",
                         timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert bad.returncode == 1


@pytest.mark.parametrize("answer, backend", [("1\n", "pytorch"), ("4\n1\n", "tensorflow"),
                                           ("4\n2\n", "auto"), ("4\n3\n", "none")])
def test_terminal_backend_choice_is_used_and_remembered(deployment, answer, backend):
    """真实菜单输入贯穿安装器参数与本地配置，不安装真实框架。"""
    root, config = deployment
    result = run_deploy(root, "-ChooseBackend", "-SetupOnly", input_text=answer)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.index("当前电脑配置") < result.stdout.index("请选择本次部署")
    assert (root / "scripts/backend.txt").read_text() == backend
    assert json.loads(config.read_text())["gan_backend"] == backend


def test_cancel_menu_does_not_install(deployment):
    """用户取消时不运行安装器。"""
    root, _ = deployment
    result = run_deploy(root, "-ChooseBackend", "-SetupOnly", input_text="0\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "scripts/calls.txt").exists()


@pytest.mark.parametrize("gpu, answer, expected", [
    (False, "2\n1\n", "pytorch"),
    (False, "3\n", "pytorch"),
    (True, "3\n", "pytorch-cuda"),
    (True, "2\n", "pytorch-cuda"),
])
def test_menu_hardware_branches(deployment, gpu, answer, expected):
    """合成硬件验证无 GPU 拒绝、自动分支与显式 GPU 选择。"""
    root, _ = deployment
    entry = root / "menu.ps1"
    entry.write_text(
        ". (Join-Path $PSScriptRoot '快速部署/Hardware.ps1')\n"
        f"$hardware = [pscustomobject]@{{ NvidiaReady = ${str(gpu).lower()} }}\n"
        "$selected = Select-DeploymentBackend $hardware 'auto'\n"
        "Write-Host ('SELECTED=' + $selected)\n", encoding="utf-8-sig")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(entry)],
                            input=answer, capture_output=True, encoding="utf-8", errors="replace",
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"SELECTED={expected}" in result.stdout


@pytest.mark.parametrize("change, message", [
    ({"python_path": ""}, "Set python_path"),
    ({"environment_mode": "mystery"}, "environment_mode"),
    ({"frontend_dir": "../outside"}, "project-relative"),
    ({"port": "8000"}, "port must"),
    ({"install_npm": "false"}, "true or false"),
])
def test_invalid_config_stops_before_install(deployment, change, message):
    """错误配置不创建环境、不执行安装；由参数化用例调用。"""
    root, config = deployment
    settings = json.loads(config.read_text()) | change
    config.write_text(json.dumps(settings), encoding="utf-8")
    result = run_deploy(root, "-SetupOnly")
    assert result.returncode != 0
    assert message in result.stdout
    assert not (root / "scripts/calls.txt").exists()


def test_failed_setup_is_not_cached(deployment):
    """安装失败不登记成功指纹，修复后可重试。"""
    root, _ = deployment
    script = root / "scripts/Setup.ps1"
    text = script.read_text("utf-8-sig")
    assert run_deploy(root, "-SetupOnly").returncode == 0
    script.write_text(text.replace("exit 0", "exit 23"), encoding="utf-8-sig")
    failed = run_deploy(root, "-SetupOnly")
    assert failed.returncode == 1
    assert not (root / "work/quick-deploy/python.sha256").exists()
    script.write_text(text, encoding="utf-8-sig")
    assert run_deploy(root, "-SetupOnly").returncode == 0


def test_create_root_venv_with_selected_base_python(deployment):
    """在隔离副本真实创建根 venv，不复用或修改生产环境目录。"""
    root, config = deployment
    settings = json.loads(config.read_text()) | {"environment_mode": "venv"}
    config.write_text(json.dumps(settings), encoding="utf-8")
    result = run_deploy(root, "-SetupOnly")
    assert result.returncode == 0, result.stdout + result.stderr
    executable = root / ".venv/Scripts/python.exe"
    assert executable.is_file()
    check = subprocess.run([str(executable), "-X", "utf8", "-c", "import sys; print(sys.prefix)"],
                           capture_output=True, encoding="utf-8", timeout=10)
    assert check.returncode == 0, check.stderr
    assert Path(check.stdout.strip()) == root / ".venv"


def test_npm_install_lock_reuse_and_failure(deployment):
    """离线本地包验证 npm install、npm ci、缓存与失败阻断；不访问包仓库。"""
    if not shutil.which("npm.cmd"):
        pytest.skip("Node/npm not installed")
    root, _ = deployment
    frontend = root / "frontend"
    package = frontend / "fixture"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "local-fixture", "version": "1.0.0"}))
    (frontend / "package.json").write_text(json.dumps({"name": "deployment-fixture", "version": "1.0.0",
                        "private": True, "dependencies": {"local-fixture": "file:./fixture"}}))
    first = run_deploy(root, "-SetupOnly")
    assert first.returncode == 0, first.stdout + first.stderr
    assert (frontend / "package-lock.json").is_file()
    assert (frontend / "node_modules/local-fixture").exists()
    second = run_deploy(root, "-SetupOnly")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "reusing node_modules" in second.stdout
    stamp = root / "work/quick-deploy/npm.sha256"
    stamp.unlink()
    locked = run_deploy(root, "-SetupOnly")
    assert locked.returncode == 0, locked.stdout + locked.stderr
    # 注入失败安装脚本，改变 manifest 迫使重装，旧成功指纹不能替代这轮失败。
    manifest = json.loads((frontend / "package.json").read_text())
    manifest["scripts"] = {"preinstall": "node -e process.exit(19)"}
    (frontend / "package.json").write_text(json.dumps(manifest))
    failed = run_deploy(root, "-SetupOnly")
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert not stamp.exists()


def test_supervisor_runs_task_and_stops_both_children(tmp_path, monkeypatch):
    """真实 HTTP 提交演示任务，后台自动完成，正常退出后无子进程残留。"""
    for name, value in {
        "INDUSTRY_API_TOKEN": "deployment-test-token-0000000000000000",
        "INDUSTRY_DATABASE_URL": "sqlite:///" + (tmp_path / "platform.db").as_posix(),
        "INDUSTRY_DATA_DIR": str(tmp_path / "data"),
        "INDUSTRY_TASK_BACKEND": "database",
        "INDUSTRY_CRAWLER_ADAPTER": "integrated",
        "INDUSTRY_ADVISOR_API_KEY": "",
    }.items():
        monkeypatch.setenv(name, value)
    subprocess.run([sys.executable, "-m", "app.cli", "init"], cwd=PROJECT_ROOT, check=True,
                   capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    for name, filename in (("LOCAL_SERVICE_LOCK", "service.lock"), ("LOCAL_API_LOG", "api.log"),
                           ("LOCAL_WORKER_LOG", "worker.log")):
        monkeypatch.setattr(local_service, name, tmp_path / filename)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    stop = threading.Event()
    children_before = {p.pid for p in psutil.Process().children(recursive=True)}
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(local_service.serve, port, stop)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 25
                while True:
                    assert not future.done(), future.exception() if future.done() else ""
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                assert client.get("/v1/operations/check", params={"project_id": "smoke"}).status_code == 401
                client.headers["Authorization"] = "Bearer " + os.environ["INDUSTRY_API_TOKEN"]
                response = client.post("/v1/advisor/jobs", json={"operation_id": "deploy-advice",
                    "project_id": "smoke", "question": "离线验收", "demo": True})
                assert response.status_code == 202
                while True:
                    result = client.get("/v1/operations/deploy-advice", params={"project_id": "smoke"}).json()
                    if result["state"] == "done":
                        break
                    assert time.monotonic() < deadline, result
                    time.sleep(0.1)
                assert result["result"]["mode"] == "demo"
                started = [p for p in psutil.Process().children(recursive=True) if p.pid not in children_before]
                assert len(started) >= 2
        finally:
            stop.set()
        future.result(timeout=15)
    assert all(not p.is_running() for p in started)
    assert "Uvicorn running" in (tmp_path / "api.log").read_text("utf-8")


def test_occupied_port_is_rejected():
    """端口已有服务时不启动新 API/Worker。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(OSError):
            local_service.check_port(listener.getsockname()[1])
