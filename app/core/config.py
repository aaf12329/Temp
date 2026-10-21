"""共用契约/基础设施层。
结构导航：
├─ Settings
│  ├─ resolve_data_dir
│  ├─ absolute_database_url
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：根目录环境配置 → 校验后的运行参数与数据库地址。
"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.common.paths import DATA_DIRECTORY_NAME, DATABASE_FILENAME, ENV_FILE, PROJECT_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INDUSTRY_", env_file=ENV_FILE, extra="ignore")
    environment: str = "local"
    api_token: SecretStr = Field(min_length=32)
    database_url: str = f"sqlite:///{DATA_DIRECTORY_NAME}/{DATABASE_FILENAME}"
    data_dir: Path = Path(DATA_DIRECTORY_NAME)
    crawler_adapter: str = ""
    cors_origins: list[str] = []
    poll_interval_s: float = Field(default=1, ge=0.05)
    lease_seconds: int = Field(default=120, ge=6)
    code_version: str = "0.1.0"
    advisor_api_key: SecretStr = SecretStr("")
    advisor_base_url: str = "https://api.deepseek.com"
    advisor_model: str = "deepseek-flash"
    advisor_timeout_s: float = Field(default=60, gt=0, le=300)
    advisor_max_tokens: int = Field(default=2000, ge=128, le=8192)
    bridge_timeout_s: float = Field(default=600, ge=30, le=7200)
    task_backend: str = Field(default="database", pattern="^(database|celery)$")
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        """将配置数据位置相对项目根目录解析；由 Pydantic 调用。"""
        return (PROJECT_ROOT / value).resolve()

    def absolute_database_url(self) -> str:
        """构造可跨 API/Worker 共享的数据库地址；由数据库初始化与迁移调用。"""
        if self.database_url.startswith("sqlite:///"):
            path = Path(self.database_url.removeprefix("sqlite:///"))
            if str(path) == ":memory:":
                raise ValueError("Use a file database: API and Worker must share durable state")
            return "sqlite:///" + (PROJECT_ROOT / path).resolve().as_posix()
        return self.database_url
