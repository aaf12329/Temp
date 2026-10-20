"""独立终端验收：用子进程验证无需原平台，数据只进入临时目录。

结构/调用：pytest → 独立 Python 进程 → advisor_bot CLI。
数据流：--demo/假配置 → JSON 回复与会话文件；禁用真实模型调用。

函数结构树（AST 核对）：
├─ test_terminal_demo_works_outside_project_without_main_platform
├─ test_chat_continues_after_invalid_input
项目内调用关系：
└─ 无项目内函数调用；入口由 Python/pytest 调度
"""

import json
import os
import subprocess
import sys


def test_terminal_demo_works_outside_project_without_main_platform(tmp_path):
    """验证从任意目录运行已安装模块，且环境不依赖 FastAPI/SQLAlchemy；由 pytest 调用。"""
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "ADVISOR_DATA_DIR": str(tmp_path / "demo")})
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "advisor_bot",
            "--demo",
            "--json",
            "ask",
            "凯里酸汤鱼先查什么？",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["mode"] == "demo" and "离线演示" in value["reply"]["content"]
    assert value["turn"] == 1
    assert (tmp_path / "demo/sessions/default/chat_history.json").is_file()
    check = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys, advisor_bot.service; assert not any(x in sys.modules for x in ['app','fastapi','sqlalchemy']); print('independent')",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert check.returncode == 0 and check.stdout.strip() == "independent"


def test_chat_continues_after_invalid_input(tmp_path):
    """验证终端单轮参数错误不会终止对话主循环；由 pytest 调用。"""
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONUTF8": "1",
            "ADVISOR_DATA_DIR": str(tmp_path / "chat"),
            "ADVISOR_MAX_INPUT_CHARS": "8",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "advisor_bot", "--demo", "chat"],
        cwd=tmp_path,
        env=environment,
        input="这个问题长度明显超出限制\n查什么\n/exit\n",
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0
    assert "E_ADVISOR_INPUT" in result.stdout and "离线演示" in result.stdout
