"""LLM清洗的结构化返回合同，拒绝额外字段与隐式数字转换。

结构：QuotedValue → ExtractedRow；调用：本地验证器逐条解析LLM JSON。
数据流：不可信JSON记录 → 带原文引用的候选；本合同不执行网络、文件或模型。
所有字段均为未人工审核候选，数值和单位由本地重新计算。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QuotedValue(BaseModel):
    """来源字段必须附带原文引句，缺失则显式null。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    value: str | None = Field(max_length=120)
    quote: str | None = Field(max_length=400)


class ExtractedRow(BaseModel):
    """LLM只报告原始数字及证据，不接受其计算的value或审核通过状态。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    block_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=600)
    raw_value: str = Field(min_length=1, max_length=60)
    unit: QuotedValue
    metric: str = Field(min_length=1, max_length=80)
    metric_quote: str = Field(min_length=1, max_length=400)
    subject: QuotedValue
    region: QuotedValue
    year: QuotedValue
    publication_date: QuotedValue
    period_type: Literal["year", "month", "quarter", "event", "unknown"]
    scope: Literal["total", "qualified", "enterprise", "unknown"]
    scope_quote: str | None = Field(max_length=400)
    claim_type: Literal["observation", "target", "forecast", "unknown"]
    needs_review: bool
    review_reason: str = Field(default="", max_length=600)
