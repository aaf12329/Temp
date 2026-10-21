"""CLI 配置契约，无模型/网络副作用。

结构：Laptop → GANParameters → GAN → Cleaning → CLIConfig。
调用：CLI/桥接入口 → Pydantic；数据流：JSON → 有界 CPU 配置 → 模型聚合层。
GAN 参数来自 WGAN_Trainer 原 JSON，不套用轻薄本性能上限；未知字段直接报错。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    """拒绝拼错配置，避免用户误以为参数已生效。"""

    model_config = ConfigDict(extra="forbid")


class Laptop(Strict):
    """面向 16 GB CPU 笔记本的工程预算，不代表硬内存沙箱。"""

    threads: int = Field(default=2, ge=1, le=4)
    max_rows: int = Field(default=10000, ge=32, le=50000)
    max_columns: int = Field(default=32, ge=1, le=64)
    timeout_s: int = Field(default=1200, ge=30, le=7200)


class GANParameters(Strict):
    """WGAN-GP 原配置参数；只校验数学有效性，不限制训练规模。"""

    epochs: int = Field(default=5000, ge=1)
    batch_size: int = Field(default=64, ge=2)
    latent_dim: int = Field(default=100, ge=1)
    n_critic: int = Field(default=5, ge=1)
    learning_rate: float = Field(default=0.0002, gt=0)
    lambda_gp: float = Field(default=10, ge=0)
    beta1: float = Field(default=0.5, ge=0, lt=1)
    beta2: float = Field(default=0.9, ge=0, lt=1)
    generator_layers: tuple[int, ...] = (128, 256, 512)
    critic_layers: tuple[int, ...] = (256, 128, 64)
    device: str = Field(default="auto", pattern=r"^(auto|cpu|cuda:\d+)$")
    gpu_memory_mb: int = Field(default=8192, ge=0)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    log_every: int = Field(default=1, ge=1)

    @field_validator("generator_layers", "critic_layers")
    @classmethod
    def valid_layers(cls, values):
        """沿用原算法的有效网络结构检查，不截断层宽。"""
        if not 1 <= len(values) <= 8 or any(n < 1 for n in values):
            raise ValueError("网络需要 1..8 层，每层为正整数。")
        return values


class GAN(Strict):
    """训练开关和门槛独立于原训练器的超参数。"""

    backend: Literal["pytorch", "tensorflow"] = "pytorch"
    config_file: str | None = "config/wgan.json"
    mode: Literal["auto", "off"] = "auto"
    min_real_samples: int = Field(default=64, ge=32, le=10000)
    n_generate: int = Field(default=2000, ge=1)
    generation_batch_size: int = Field(default=256, ge=1)
    precision: Literal["float32"] = "float32"
    tsne_max_points: int = Field(default=2000, ge=2)
    tsne_max_iter: int = Field(default=1000, ge=250)
    tsne_perplexity: float | None = Field(default=None, gt=0)
    timeout_s: float | None = Field(default=None, gt=0)
    params: GANParameters = Field(default_factory=GANParameters)


class Cleaning(Strict):
    """规则删除非法输入；IsolationForest 默认只标记训练异常供审核。"""

    model: Literal["isolation_forest", "rules"] = "isolation_forest"
    min_samples: int = Field(default=64, ge=32, le=10000)
    contamination: float = Field(default=0.02, gt=0, le=0.1)
    remove_training_outliers: bool = False


class CLIConfig(Strict):
    """两次输入的 CLI 高级配置；URL、问题和输出位置不隐藏在 JSON 中。"""

    retrieval: Literal["bge-small-zh-v1.5-int8", "keywords"] = "bge-small-zh-v1.5-int8"
    semantic_threshold: float = Field(default=0.64, ge=0, le=1)
    subjects: list[str] | dict[str, list[str]] = Field(default_factory=list)
    regions: list[str] | dict[str, list[str]] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    cleaning: Cleaning = Field(default_factory=Cleaning)
    gan: GAN = Field(default_factory=GAN)
    laptop: Laptop = Field(default_factory=Laptop)
    llm: Literal["api", "off", "demo"] = "api"
    max_urls: int = Field(default=100, ge=1, le=200)
    max_text_bytes: int = Field(default=16000000, ge=1024, le=32000000)
    max_passages: int = Field(default=2000, ge=1, le=10000)
    min_periods: int = Field(default=20, ge=20, le=200)
    min_train_samples: int = Field(default=10, ge=10, le=200)
    validation_size: int = Field(default=4, ge=4, le=50)
    test_size: int = Field(default=4, ge=4, le=50)
    max_series: int = Field(default=3, ge=1, le=10)

    @field_validator("subjects", "regions", "keywords")
    @classmethod
    def bounded_terms(cls, value):
        """限制本地规则词条规模，防止配置让检索和抽取超出 CPU 预算。"""
        terms = value if isinstance(value, list) else list(value) + [
            word for aliases in value.values() for word in aliases]
        if len(terms) > 300 or any(not term.strip() or len(term) > 1000 for term in terms):
            raise ValueError("词条需非空、每条不超过 1000 字符，总数不超过 300。")
        return value
