"""网页管线契约：PipelineRequest → 有界阶段动作；由API/Worker共同校验。

结构：prepare输入 / gan框架 / model采用确认；调用：API/Worker → 契约。
数据流：JSON → 持久操作，不接收输出路径或模型密钥。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.advisor import SafeIdentifier
from app.contracts.crawler import CrawlRequest


class PipelineRequest(BaseModel):
    """一次人工阶段对应一次持久任务；父操作必须来自同项目且已经完成。"""

    model_config = ConfigDict(extra="forbid")
    operation_id: SafeIdentifier
    project_id: SafeIdentifier = "local"
    stage: Literal["prepare", "gan", "model"]
    parent_id: SafeIdentifier | None = None
    urls: list[str] = Field(default_factory=list, max_length=100)
    question: str = Field(default="", max_length=4000)
    framework: Literal["pytorch", "tensorflow"] = "pytorch"
    use_generated: bool = False
    llm: Literal["api", "off", "demo"] = "api"
    retrieval: Literal["bge-small-zh-v1.5-int8", "keywords"] = "bge-small-zh-v1.5-int8"
    extraction: Literal["llm", "deterministic"] = "llm"

    @model_validator(mode="after")
    def valid_stage(self):
        """拒绝不完整输入与绕过GAN生成的采用动作；服务再校验父操作。"""
        if self.stage == "prepare":
            if not self.urls or not self.question.strip() or self.parent_id or self.use_generated:
                raise ValueError("整理阶段需要网址与需求，不接受父任务或GAN采用标记")
            CrawlRequest(job_id=self.operation_id, urls=self.urls)
            self.question = self.question.strip()
        elif not self.parent_id or self.urls or self.question:
            raise ValueError("训练阶段只引用已完成父任务，不重新提交网址或需求")
        return self
