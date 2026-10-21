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
ENVIRONMENT_CHECK = PROJECT_ROOT / "work/environment-check.json"
SERVICE_CHECK = PROJECT_ROOT / "work/service-check.json"
ACCEPTANCE_DIRECTORY = PROJECT_ROOT / "work"
CRAWLER_COMPONENT = PROJECT_ROOT / "components/crawler"
CRAWLER_SOURCE = CRAWLER_COMPONENT / "src"
RESEARCH_TEMPLATE = CRAWLER_COMPONENT / "config/research.json"
CRAWLER_BRIDGE = PROJECT_ROOT / "app/crawler/bridge.py"
ADVISOR_COMPONENT = PROJECT_ROOT / "产业研究建议助手"
DATA_DIRECTORY_NAME = "data"
DATABASE_FILENAME = "platform.db"


@dataclass(frozen=True)
class DataPaths:
    """登记采集服务数据位置；由 Storage 创建并提供给服务读取。"""

    root: Path

    def crawler_workspace(self, token: str) -> Path:
        """返回隔离采集工作区；由集成适配器调用。"""
        return self.root / "workspaces/crawler" / token

    def network_directory(self) -> Path:
        """跨进程网络限速账本目录；由采集守卫调用。"""
        return self.root / "workspaces/network"

    def network_host(self, host_digest: str, suffix: str) -> Path:
        """返回主机限速锁或时间戳位置；由网络守卫调用。"""
        return self.network_directory() / (host_digest + suffix)

    def artifact_path(self, category: str, record_id: str, digest: str, suffix: str) -> Path:
        """返回按内容寻址的不可变材料位置；由采集晋升流程调用。"""
        return self.root / category / digest[:2] / (digest + suffix)

    def research_input_reference(self, category: str, stem: str, suffix: str) -> str:
        """登记短文件名研究副本，避免 Windows 临时路径超长；由研究入口调用。"""
        return f"data/normalized/{category}/{stem}{suffix}"

    def advisor_root(self, project_id: str, demo: bool = False) -> Path:
        """项目隔离的建议会话根；由建议适配器调用。"""
        return self.root / ("advisor_demo" if demo else "advisor") / project_id

    def advisor_operation(self, project_id: str, operation_id: str, name: str,
                          demo: bool = False) -> Path:
        """返回建议调用的缓存、锁与开始标记；由幂等适配器调用。"""
        names = {"result": "result.json", "lock": "operation.lock", "started": "started.json"}
        return self.advisor_root(project_id, demo) / "operations" / operation_id / names[name]

    def operation_directory(self, operation_id: str) -> Path:
        """研究结果目录；由操作任务入口调用。"""
        return self.root / "research" / operation_id

    def research_workspace(self, operation_id: str) -> Path:
        """研究子进程的隔离输入根；由研究入口调用。"""
        return self.root / "workspaces/research" / operation_id

    def bridge_file(self, workspace: Path, name: str) -> Path:
        """登记隔离桥接的配置和响应文件；由适配入口调用。"""
        return workspace / {"config": "research-config.json", "result": "bridge-result.json"}[name]

    def research_file(self, operation_id: str, name: str) -> Path:
        """登记允许下载的研究产物；由研究入口和下载路由调用。"""
        exports = {"documents": ".csv", "passages": ".csv", "facts": ".csv",
                   "review_template": ".csv", "series": ".csv", "issues": ".json",
                   "manifest": ".json", "analysis": ".json", "trend": ".json", "report": ".md"}
        return self.operation_directory(operation_id) / (name + exports[name])

    def model_directory(self) -> Path:
        """统一环境的向量模型缓存；由研究入口调用。"""
        return self.root / "models/bge-small-zh-v1.5-int8"

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
