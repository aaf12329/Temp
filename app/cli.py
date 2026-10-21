"""入口/显式聚合层（负责横向编排）。
结构导航：
├─ migrate
├─ main
调用关系：
├─ main → migrate, runtime.jobs.export_materials, runtime.db.close。
数据流：命令参数 → 资源装配 → 初始化/执行/导出 → 退出码。
"""

import argparse
import json
import logging

from alembic import command
from alembic.config import Config

from app.common.paths import ALEMBIC_CONFIG, MIGRATIONS_DIR
from app.core.config import Settings
from app.core.errors import ServiceError
from app.runtime import create_runtime


def migrate(settings: Settings):
    """执行目标数据库的版本迁移；由初始化命令和隔离测试调用。"""
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", settings.absolute_database_url().replace("%", "%%"))
    config.attributes["settings"] = settings
    command.upgrade(config, "head")


def main():
    """解析命令并按步骤组织功能；由模块入口调用。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="产业信息研究平台服务工具")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="创建数据目录并执行数据库迁移")
    worker = sub.add_parser("worker", help="运行独立持久任务 Worker")
    worker.add_argument("--once", action="store_true")
    sub.add_parser("export", help="从数据库重建 pages.json 材料快照")
    sub.add_parser("openapi", help="输出接口描述，供后续 TypeScript 接入")
    sub.add_parser("model-setup", help="显式准备固定版本 CPU 向量模型，不调用付费模型")
    research = sub.add_parser("research", help="无需数据库的交互式 CLI：网址文件 + 一句需求")
    research.add_argument("--config", default="config/cli.json")
    research.add_argument("--urls-file")
    research.add_argument("--query")
    args = parser.parse_args()
    if args.action == "research":
        from app.pipeline_cli import main as research_main

        arguments = ["--config", args.config]
        for name, value in [("--urls-file", args.urls_file), ("--query", args.query)]:
            if value:
                arguments.extend([name, value])
        research_main(arguments)
        return
    settings = Settings()
    if args.action == "openapi":
        from app.main import create_app

        print(json.dumps(create_app(settings).openapi(), ensure_ascii=False, indent=2))
        return
    runtime = create_runtime(settings)
    try:
        if args.action == "init":
            migrate(settings)
            print("数据目录和数据库迁移已完成。")
        elif args.action == "model-setup":
            from uuid import uuid4

            from app.common.component import run_component

            paths = runtime.storage.paths
            result = run_component({"action": "model-setup"},
                                   paths.research_workspace(uuid4().hex), settings.data_dir, 600)
            print(json.dumps({"model_id": result["model_id"], "revision": result["revision"]}))
        elif args.action == "export":
            runtime.jobs.export_materials()
            print("材料快照已导出。")
        else:
            from app.operation_worker import run_operation
            from app.worker import run_forever, run_one

            if args.once:
                run_operation(runtime)
                run_one(runtime)
            else:
                run_forever(runtime)
    except ServiceError as exc:
        raise SystemExit(f"{exc.error.code}: {exc.error.message}") from None
    finally:
        runtime.db.close()


if __name__ == "__main__":
    main()
