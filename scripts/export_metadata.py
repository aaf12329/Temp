"""Refresh reproducible local dependency pins and the frontend-facing API description.


框架/维护/隔离验收入口。
结构导航：
├─ main
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

import subprocess
import sys

from app.common.file_io import write_json, write_text
from app.common.paths import DEPENDENCY_LOCK, OPENAPI_FILE


def main() -> None:
    """导出依赖和接口元数据；由显式维护命令调用，导入不会写文件。"""
    from app.main import create_app

    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude", "industry-research-platform"],
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    write_text(
        DEPENDENCY_LOCK,
        "# Verified local environment: Windows x64, Python 3.12.14\n"
        "# Install these pins, then: python -m pip install --no-deps -e .\n" + freeze.stdout,
    )
    write_json(OPENAPI_FILE, create_app().openapi())
    print("Updated dependency pins and OpenAPI; no credentials exported.")


if __name__ == "__main__":
    main()
