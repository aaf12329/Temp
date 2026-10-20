"""共用存储设施：目录初始化与材料读取边界。

结构：Storage.__init__ → initialize / resolve_normalized；write_json 为兼容导出。
调用关系：runtime 入口 → initialize → DataPaths + file_io；材料服务 → resolve_normalized。
数据流：数据根目录/材料引用 → 受限 Path；共享 JSON 写入 → 统一 I/O。
"""

from pathlib import Path

from filelock import FileLock

from app.common.file_io import write_json
from app.common.paths import DataPaths
from app.core.errors import ServiceError


class Storage:
    """提供共用文件存储；由入口构建后注入功能模块。"""

    def __init__(self, root: Path):
        """绑定数据根目录和地址簿；由 runtime 调用，不创建文件。"""
        self.root = root.resolve()
        self.paths = DataPaths(self.root)

    def initialize(self):
        """初始化目录并保留人工策略；由 runtime 创建流程调用。"""
        for directory in self.paths.directories():
            directory.mkdir(parents=True, exist_ok=True)
        policy = self.paths.path("policy_file")
        with FileLock(str(self.paths.path("policy_lock"))):
            if not policy.exists():
                write_json(policy, [])

    def resolve_normalized(self, reference: str) -> Path:
        """解析规范化文件并拒绝敏感或越界路径；由材料服务调用。"""
        resolved = self.paths.normalized_reference(reference)
        sensitive = {".env", ".env.example", "id_rsa", "id_ed25519"}
        if (
            not resolved.is_relative_to(self.paths.path("normalized"))
            or resolved.name.lower() in sensitive
            or resolved.suffix.lower() in {".pem", ".key"}
        ):
            raise ServiceError("E_STORAGE_PATH", "材料路径越界或指向敏感文件。", 422)
        if not resolved.is_file():
            raise ServiceError(
                "E_ARTIFACT_NOT_FOUND", "规范化材料不存在，请检查爬虫落盘结果。", 404
            )
        return resolved

    write_json = staticmethod(write_json)
