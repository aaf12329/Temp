"""v1.2 explicit fields; omitted v1.1 structures stay extensible pending the full contract.


共用契约/基础设施层。
结构导航：
├─ Extensible
│  └─ 契约字段
├─ validate_urls
├─ Profile
│  └─ 契约字段
├─ ProbeRequest
│  └─ 契约字段
├─ ProbeResponse
│  └─ 契约字段
├─ JobSubmitted
│  └─ 契约字段
├─ CrawlRequest
│  ├─ unique_urls
├─ Source
│  └─ 契约字段
├─ Meta
│  └─ 契约字段
├─ TextMeta
│  └─ 契约字段
├─ Paragraph
│  └─ 契约字段
├─ PageMap
│  └─ 契约字段
├─ PdfInfo
│  └─ 契约字段
├─ Table
│  └─ 契约字段
├─ CrawlResult
│  └─ 契约字段
├─ Counts
│  └─ 契约字段
├─ Job
│  └─ 契约字段
├─ SitePolicy
│  └─ 契约字段
├─ record_id_for
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：JSON 请求/材料 → Pydantic 校验 → v1.2 契约对象。
"""

from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import ErrorInfo

Verdict = Literal[
    "static",
    "thin_static",
    "pdf",
    "other_file",
    "robots_disallow",
    "robots_unknown",
    "login_wall",
    "dynamic",
    "blocked",
    "error",
]
JobState = Literal["queued", "running", "done", "failed", "partial"]


class Extensible(BaseModel):
    model_config = ConfigDict(extra="allow")


def validate_urls(values: list[str]) -> list[str]:
    """校验 HTTP(S) 地址并保留原字符串；由请求模型校验器调用。"""
    for value in values:
        try:
            parts = urlsplit(value)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
                or any(c.isspace() for c in value)
            ):
                raise ValueError
            _ = parts.port
        except ValueError as exc:
            raise ValueError("URLs must be HTTP(S), without credentials or whitespace") from exc
    return values  # Preserve the exact URL string used in the UUID5 contract.


class Profile(Extensible):
    url: str
    site: str | None = None
    checked_at: str | None = None
    http_status: int | None = None
    final_url: str | None = None
    robots: Any = None
    content_type: str | None = None
    encoding: str | None = None
    bytes: int = 0
    text_chars: int = 0
    verdict: Verdict = "error"
    reason: str | None = None
    signals: Any = None
    error: ErrorInfo | None = None


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urls: list[str] = Field(min_length=1, max_length=100)
    timeout_s: float = Field(default=8, gt=0, le=300)
    sample_bytes: int = Field(default=200000, gt=0, le=10000000)
    user_agent: str | None = None
    save: bool = True
    _urls = field_validator("urls")(validate_urls)


class ProbeResponse(BaseModel):
    profiles: list[Profile]


class JobSubmitted(BaseModel):
    job_id: str


class CrawlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[\w.-]+$")
    project_id: str | None = Field(default=None, max_length=128)
    urls: list[str] = Field(min_length=1, max_length=1000)
    profiles: list[Profile] | None = None
    timeout_s: float = Field(default=15, gt=0, le=300)
    per_host_delay_s: float = Field(default=1.5, ge=0)
    respect_robots: Literal[True] = True
    allow_render: bool = False
    allow_tls_impersonate: bool = False
    save_raw: bool = True
    normalize: bool = True
    parse_pdf: bool = True
    dedupe_by_hash: bool = True
    requested_by: str | None = None
    rule_version: str | None = None
    _urls = field_validator("urls")(validate_urls)

    @field_validator("urls")
    @classmethod
    def unique_urls(cls, values: list[str]) -> list[str]:
        """拒绝同任务内重复 URL；由 CrawlRequest 校验器调用。"""
        if len(set(values)) != len(values):
            raise ValueError("Duplicate URLs are not allowed within one job")
        return values


class Source(BaseModel):
    publisher: str | None = None
    published_at: str | None = None
    canonical_url: str | None = None
    author: str | None = None
    declaration: str | None = None
    is_reprint: bool | None = None


class Meta(Extensible):
    requested_by: str | None = None
    rule_version: str | None = None
    code_version: str


class TextMeta(Extensible):
    paragraphs: int = 0
    chars: int = 0
    has_table: bool = False
    table_count: int = 0


class Paragraph(BaseModel):
    index: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    page: int | None = Field(default=None, ge=1)
    text: str


class PageMap(BaseModel):
    page: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)


class PdfInfo(BaseModel):
    pages: int = Field(ge=0)
    text_path: str
    page_map: list[PageMap] = []


class Table(BaseModel):
    index: int = Field(ge=0)
    rows: int = Field(ge=0)
    cols: int = Field(ge=0)
    page: int | None = Field(default=None, ge=1)
    html_path: str | None = None
    json_path: str | None = None
    csv_path: str | None = None


class CrawlResult(Extensible):
    record_id: str
    job_id: str
    project_id: str | None = None
    url: str
    final_url: str | None = None
    site: str
    fetched_at: str
    elapsed_ms: int = Field(ge=0)
    meta: Meta
    source: Source = Field(default_factory=Source)
    robots: Any = None
    verdict: Verdict
    tool_used: str | None = None
    tool_fallback: Any = None
    status: str  # The four official values are omitted from the supplied v1.2 document.
    http_status: int | None = None
    content_type: str | None = None
    encoding: str | None = None
    bytes: int = Field(default=0, ge=0)
    title: str | None = None
    content_sha256: str | None = None
    storage: dict[str, Any] = Field(default_factory=dict)
    text_chars: int = 0
    text_sha256: str | None = None
    text_meta: TextMeta = Field(default_factory=TextMeta)
    paragraph_file: str | None = None
    paragraph_count: int = 0
    pdf: PdfInfo | None = None
    tables: list[Table] = []
    error: ErrorInfo | None = None


class Counts(BaseModel):
    ok: int = 0
    ok_pdf: int = 0
    skipped: int = 0
    failed: int = 0


class Job(BaseModel):
    job_id: str
    project_id: str | None = None
    state: JobState
    total: int
    finished: int
    counts: Counts
    started_at: str | None = None
    finished_at: str | None = None
    error: ErrorInfo | None = None


class SitePolicy(BaseModel):
    model_config = ConfigDict(extra="allow")
    host: str
    allowed_paths: list[str] = []
    disallowed_paths: list[str] = []
    min_interval_s: float = Field(default=1.5, ge=0)
    preferred_tool: str | None = None
    requires_login: bool = False
    has_pdf: bool = False
    note: str = ""


def record_id_for(job_id: str, url: str) -> str:
    """按任务号和原始 URL 生成 UUID5；由任务创建及采集适配器调用。"""
    return str(uuid5(NAMESPACE_URL, f"{job_id}|{url}"))
