"""根配置驱动的助手终端显式入口。

结构：main → 项目/演示隔离配置 → 独立 advisor_bot.cli.main。
调用：启动脚本或 python -m app.advisor_cli；数据流：根 .env + 项目 ID → 原有 JSON 会话/人工记忆。
"""

import argparse
import sys

from advisor_bot.cli import main as advisor_main

from app.advisor.integrated import IntegratedAdvisor
from app.core.config import Settings


def main() -> int:
    """解析平台项目参数并复用独立助手 CLI；由终端启动脚本调用。"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project", default="terminal")
    known, remaining = parser.parse_known_args()
    demo = "--demo" in remaining
    adapter = IntegratedAdvisor(Settings())
    root = adapter._root(known.project, demo)
    settings = adapter._bot_settings(root, demo)
    sys.argv = [sys.argv[0], *remaining]
    return advisor_main(settings)


if __name__ == "__main__":
    raise SystemExit(main())
