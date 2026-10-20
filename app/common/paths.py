"""路径地址簿（纯路径计算，无读写）。

结构：项目常量 → DataPaths 数据地址；独立机器人有自己的工程地址簿。
调用关系：配置/入口构造地址簿 → 功能模块只读取路径；本模块不调用项目模块。
数据流：项目根目录或测试临时根目录 → 已登记的文件/目录 Path。
"""

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
ENV_LOCK = PROJECT_ROOT / ".env.lock"
ENV_TEMPLATE = PROJECT_ROOT / ".env.example"
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
DEPENDENCY_LOCK = PROJECT_ROOT / "requirements.lock.txt"
OPENAPI_FILE = PROJECT_ROOT / "docs/openapi.json"
DATA_DIRECTORY_NAME = "data"
DATABASE_FILENAME = "platform.db"


@dataclass(frozen=True)
class DataPaths:
    """登记采集服务数据位置；由 Storage 创建并提供给服务读取。"""

    root: Path

    def path(self, name: str) -> Path:
        """按登记名称返回路径；由存储、策略和快照服务调用。"""
        registered = {
            "profile": "profile",
            "policy": "policy",
            "raw_html": "raw/html",
            "raw_files": "raw/files",
            "text": "normalized/text",
            "para": "normalized/para",
            "pdf": "normalized/pdf",
            "tables": "normalized/tables",
            "normalized": "normalized",
            "policy_file": "policy/site_policy.json",
            "policy_lock": "policy/site_policy.json.lock",
            "profiles_file": "profile/site_profile.json",
            "profiles_lock": "profile/snapshot.lock",
            "materials_file": "raw/pages.json",
            "materials_lock": "raw/pages.snapshot.lock",
        }
        return self.root / registered[name]

    def directories(self) -> tuple[Path, ...]:
        """返回初始化所需目录；只由 Storage.initialize 调用。"""
        return tuple(
            self.path(name)
            for name in (
                "profile",
                "policy",
                "raw_html",
                "raw_files",
                "text",
                "para",
                "pdf",
                "tables",
            )
        )

    def normalized_reference(self, reference: str) -> Path:
        """计算契约文件引用的位置；由 Storage 路径校验调用，不在此读文件。"""
        path = Path(reference)
        if path.is_absolute():
            return path.resolve()
        if path.parts and path.parts[0] == DATA_DIRECTORY_NAME:
            path = Path(*path.parts[1:])
        return (self.root / path).resolve()
