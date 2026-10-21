"""建议接口共用契约，不包含模型配置或任意文件入口。

结构：SafeIdentifier → AdvisorRequest；调用：API/Worker 校验任务输入。
数据流：JSON 问题、项目与材料 ID → 有界的持久任务请求。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SafeIdentifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$", min_length=1, max_length=64)
]


class AdvisorRequest(BaseModel):
    """仅引用服务端材料与研究结果；由路由及执行器共同验证。"""

    model_config = ConfigDict(extra="forbid")
    operation_id: SafeIdentifier
    project_id: SafeIdentifier
    session_id: SafeIdentifier = "default"
    question: str = Field(min_length=1, max_length=4000)
    material_ids: list[SafeIdentifier] = Field(default_factory=list, max_length=10)
    research_id: SafeIdentifier | None = None
    demo: bool = False

    @field_validator("question")
    @classmethod
    def require_question(cls, value: str) -> str:
        """拒绝空白问题且保持原问题文本；由 Pydantic 调用。"""
        if not value.strip():
            raise ValueError("问题不能为空白。")
        return value

    @field_validator("material_ids")
    @classmethod
    def unique_materials(cls, value: list[str]) -> list[str]:
        """拒绝重复引用以使幂等请求语义稳定；由 Pydantic 调用。"""
        if len(set(value)) != len(value):
            raise ValueError("材料 ID 不得重复。")
        return value
