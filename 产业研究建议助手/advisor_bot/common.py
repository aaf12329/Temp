"""机器人共用契约与配置，无功能模块反向依赖。

结构：BotSettings 配置 → ModelReply 统一模型结果 → AdviceResponse 对外回复。
调用关系：入口构造 BotSettings；模型适配层构造 ModelReply；服务编排 AdviceResponse。
数据流：根目录 .env / 显式配置 → 参数；模型响应 → content/reasoning/usage/tool_calls。

函数结构树（AST 核对）：
├─ BotSettings
│  ├─ validate_endpoint
│  ├─ root
├─ ModelReply
│  └─ 契约字段
├─ AdviceResponse
│  └─ 契约字段
项目内调用关系：
├─ root → default_advisor_root
"""

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from advisor_bot.shared.errors import BotError
from advisor_bot.shared.paths import ENV_FILENAME, default_advisor_root

__all__ = ["BotError", "BotSettings", "ModelReply", "AdviceResponse"]


class BotSettings(BaseSettings):
    """独立配置；由 build_bot 或 CLI 构造，导入模块时不读取 .env。"""

    model_config = SettingsConfigDict(env_prefix="ADVISOR_", env_file=ENV_FILENAME, extra="ignore")
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    data_dir: Path | None = None
    timeout_s: float = Field(default=60, gt=0, le=300)
    max_retries: int = Field(default=2, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=128, le=32768)
    max_input_chars: int = Field(default=6000, ge=1)
    max_context_chars: int = Field(default=32000, ge=2000)
    max_memory_chars: int = Field(default=8000, ge=1)
    recent_turns: int = Field(default=6, ge=1, le=100)
    summary_max_chars: int = Field(default=4000, ge=100)
    demo: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """限制凭据发送到有效 HTTPS 或本地端点；由 Pydantic 配置校验调用。"""
        parsed = urlsplit(value)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError("模型地址不得包含凭据、查询参数或片段。")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("模型地址必须使用 HTTPS；本地服务允许回环地址 HTTP。")
        return value.rstrip("/")

    def root(self) -> Path:
        """返回配置的数据根目录；由服务入口构建地址簿时调用。"""
        return (self.data_dir or default_advisor_root(self.demo)).resolve()


class ModelReply(BaseModel):
    """统一模型返回契约；raw_assistant 仅供原样落盘，不进入公共序列化。"""

    content: str
    reasoning: str = ""
    usage: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = []
    raw_assistant: dict[str, Any] = Field(default_factory=dict, exclude=True)


class AdviceResponse(BaseModel):
    """一次成功建议的固定封装；由 Advisor.ask 返回给 CLI 或未来调用方。"""

    session_id: str
    turn: int
    reply: ModelReply
    mode: str
