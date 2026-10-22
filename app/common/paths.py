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
PIPELINE_SERVICE_CHECK = PROJECT_ROOT / "work/pipeline-service-check.json"
ACCEPTANCE_DIRECTORY = PROJECT_ROOT / "work"
CRAWLER_COMPONENT = PROJECT_ROOT / "components/crawler"
CRAWLER_SOURCE = CRAWLER_COMPONENT / "src"
WGAN_SOURCE = PROJECT_ROOT / "components/wgan_trainer"
CLI_CONFIG = PROJECT_ROOT / "config/cli.json"
OUTPUT_ROOT = PROJECT_ROOT / "output"
CLI_OUTPUT_ROOT = OUTPUT_ROOT / "cli"
CLI_LOCK = PROJECT_ROOT / "work/cli.lock"
CLI_CHECK_ROOT = PROJECT_ROOT / "work/cli-check"
LOCAL_SERVICE_LOCK = PROJECT_ROOT / "work/quick-deploy/service.lock"
LOCAL_API_LOG = PROJECT_ROOT / "work/quick-deploy/api.log"
LOCAL_WORKER_LOG = PROJECT_ROOT / "work/quick-deploy/worker.log"
LOCAL_FRONTEND_LOG = PROJECT_ROOT / "work/quick-deploy/frontend.log"
FRONTEND_DIRECTORY = PROJECT_ROOT / "frontend"
NEXT_EXECUTABLE = FRONTEND_DIRECTORY / "node_modules/next/dist/bin/next"
RESEARCH_TEMPLATE = CRAWLER_COMPONENT / "config/research.json"
CRAWLER_BRIDGE = PROJECT_ROOT / "app/crawler/bridge.py"
ADVISOR_COMPONENT = PROJECT_ROOT / "产业研究建议助手"
DATA_DIRECTORY_NAME = "data"
DATABASE_FILENAME = "platform.db"


def project_input(reference: str) -> Path:
    """解析 CLI 的项目相对输入，拒绝环境、凭据目录和越界链接。"""
    candidate = Path(reference)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise ValueError("请输入相对项目根目录的路径，不能使用 .. 或绝对路径。")
    if any(part.startswith(".env") or part in {".git", ".venv"} for part in candidate.parts):
        raise ValueError("配置与训练输入不能来自凭据、Git 或虚拟环境目录。")
    target = (PROJECT_ROOT / candidate).resolve()
    if not target.is_relative_to(PROJECT_ROOT):
        raise ValueError("输入路径越过项目边界。")
    return target


def url_list_input(reference: str) -> Path:
    """用户明确提供的网址 TXT 可位于项目外；只允许文本清单，不接受凭据文件。"""
    path = Path(reference.strip().strip('"'))
    path = (PROJECT_ROOT / path).resolve()
    if path.suffix.lower() not in {".txt", ".list"} or path.name.startswith(".env"):
        raise ValueError("网址清单应为 .txt 或 .list 文件，每行一个 http(s) 网址。")
    return path


@dataclass(frozen=True)
class PipelinePaths:
    """CLI 管线地址簿；聚合入口注入每轮独立目录，不接受网页指定的路径。"""

    root: Path
    organized: bool = False

    def series_parent(self) -> Path:
        """集中登记多序列产物父目录。"""
        return self.root / "series"

    def file(self, name: str) -> Path:
        """返回登记的 CSV、报告、模型或图表位置；由离线管线调用。"""
        names = {
            "config": "config.json", "result": "result.json", "report": "report.md",
            "llm": "llm.json", "access": "access.json", "raw": "raw.csv",
            "candidates": "candidates.csv", "cleaned": "cleaned.csv",
            "rejected": "rejected.csv", "missing": "missing.json", "cleaning": "cleaning.json",
            "reviewed": "reviewed.json", "series": "series.csv", "train": "train.csv",
            "validation": "validation.csv", "test": "test.csv", "generated": "generated.csv",
            "quality": "quality.json", "model": "xgboost.json", "predictions": "predictions.csv",
            "evaluation": "evaluation.png", "importance": "importance.png",
            "trend": "trend.png", "missing_plot": "missing.png", "gan_plot": "gan.png",
            "runtime": "runtime.json", "run_lock": "run.lock",
            "materials": "materials.json", "passages": "passages.csv", "analysis": "analysis.json",
            "manifest": "manifest.json", "status": "status.json", "diagnostics": "diagnostics.json",
            "information": "information.csv",
            "llm_input": "llm_input.csv", "llm_cleaning": "llm_cleaning.json",
            "extraction_context": "extraction_context.json", "review": "review.csv",
            "data_quality": "data_quality.json", "data_quality_fields": "data_quality_fields.csv",
            "data_quality_plot": "data_quality.png",
            "sources": "sources.zip", "collection": "collection.json",
            "forecast": "forecast.csv",
            "tsne": "gan_tsne.png", "tsne_points": "gan_tsne.csv", "tsne_meta": "gan_tsne.json",
            "gan_review": "gan_review.json", "evidence": "evidence.json",
            "comparison": "comparison.png", "shap": "shap.png",
        }
        filename = names[name]
        if not self.organized or name == "report":
            return self.root / filename
        category = ("charts" if filename.endswith(".png") else
                    "models" if name == "model" else
                    "data" if filename.endswith((".csv", ".zip")) else "metadata")
        return self.root / category / filename

    def series(self, digest: str) -> "PipelinePaths":
        """短哈希隔离不同口径，避免中文名称和 Windows 长路径。"""
        return PipelinePaths(self.series_parent() / digest, organized=self.organized)

    def gan_directory(self) -> Path:
        """WGAN 原算法产物目录；文件名由内嵌组件自己的地址簿登记。"""
        return self.root / "gan"

    def registered_files(self) -> list[Path]:
        """登记 CLI 可覆盖的文件集合，包含内嵌训练器固定产物，不使用用户清单删除。"""
        names = ["config", "result", "report", "llm", "access", "raw", "candidates", "cleaned",
                 "rejected", "missing", "cleaning", "reviewed", "series", "train", "validation",
                 "test", "generated", "quality", "model", "predictions", "evaluation",
                 "importance", "trend", "missing_plot", "gan_plot", "runtime", "materials",
                 "passages", "analysis", "manifest", "status", "diagnostics", "information", "forecast",
                 "llm_input", "llm_cleaning", "extraction_context", "review",
                 "data_quality", "data_quality_fields", "data_quality_plot",
                 "sources", "collection",
                 "tsne", "tsne_points", "tsne_meta", "gan_review", "evidence", "comparison", "shap"]
        vendor = ["model.npz", "model.json", "normalizer.json", "training_history.csv",
                  "generated_data.csv", "generated_encoded.csv", "training_data.csv",
                  "processing_report.json", "training_losses.png", "tsne.png", "run_status.json"]
        return [self.file(name) for name in names] + [self.gan_directory() / name for name in vendor]


@dataclass(frozen=True)
class DataPaths:
    """登记采集服务数据位置；由 Storage 创建并提供给服务读取。"""

    root: Path

    def pipeline(self, run_id: str, *, output: bool = False) -> PipelinePaths:
        """新网页结果集中到output；未标记的历史任务仍解析原data/pipeline。"""
        if output:
            return PipelinePaths(self.output_root() / "web" / run_id, organized=True)
        return PipelinePaths(self.root / "pipeline" / run_id)

    def output_root(self) -> Path:
        """默认data的同级output；自定义数据根内隔离输出，避免沙箱污染主项目。"""
        return self.root.parent / "output" if self.root.name == DATA_DIRECTORY_NAME else self.root / "output"

    def heavy_lock(self) -> Path:
        """单机重计算互斥锁，避免同时运行两组训练。"""
        return self.root / "workspaces/heavy.lock"

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
