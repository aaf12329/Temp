"""研究任务共用契约。

结构：ResearchConfig → ResearchRequest；调用：API/Worker → Pydantic。
数据流：有界口径配置与材料 ID → 可信离线分析参数，无任意文件路径或脚本。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contracts.advisor import SafeIdentifier


class ResearchConfig(BaseModel):
    """每个研究任务显式声明口径，不从网页指令推断。"""

    model_config = ConfigDict(extra="forbid")
    rule_version: str = Field(default="research-v1", max_length=128)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    queries: list[str] = Field(default_factory=list, max_length=20)
    subjects: list[str] | dict[str, list[str]] = Field(default_factory=list)
    regions: list[str] | dict[str, list[str]] = Field(default_factory=list)
    semantic_threshold: float = Field(default=0.64, ge=0, le=1)
    semantic_exclude_keywords: list[str] = Field(default_factory=list, max_length=100)
    forecast_at: datetime
    min_periods: int = Field(default=20, ge=20, le=200)
    min_train_samples: int = Field(default=10, ge=10, le=200)
    min_test: int = Field(default=4, ge=4, le=100)

    @field_validator("keywords", "queries", "semantic_exclude_keywords", "subjects", "regions")
    @classmethod
    def bound_terms(cls, value):
        """限制词条与别名规模；由 Pydantic 请求校验调用。"""
        terms = list(value) if isinstance(value, list) else list(value) + [
            term for aliases in value.values() for term in aliases]
        if len(terms) > 300 or any(len(term) > 1000 or not term.strip() for term in terms):
            raise ValueError("词条过多、过长或为空白")
        return value


class ResearchRequest(BaseModel):
    """只接受数据库中属于当前项目的成功材料；由研究任务路由使用。"""

    model_config = ConfigDict(extra="forbid")
    operation_id: SafeIdentifier
    project_id: SafeIdentifier
    material_ids: list[SafeIdentifier] = Field(min_length=1, max_length=200)
    mode: Literal["lexical", "semantic"] = "lexical"
    config: ResearchConfig

    @field_validator("material_ids")
    @classmethod
    def unique_materials(cls, value):
        """拒绝重复材料以保持幂等与计数含义；由 Pydantic 调用。"""
        if len(value) != len(set(value)):
            raise ValueError("材料 ID 重复")
        return value
