# -*- coding: utf-8 -*-
"""路径地址簿（Store 模式）—— 全项目文件与目录位置的**唯一登记处**。

《工程架构规范》§02：全项目路径集中登记在一个纯常量模块里；
**地址簿只被读、不调用任何人**，也不做任何读写/建目录动作。
新增产物（快照、索引、导出文件）先在这里登记，再在别处使用。

本文件位于 `src\\Path_Store\\Path_Store.py`：从文件所在目录往上两级是 `src`，
再往上一级是仓库根，全部常量都由仓库根推算，不硬编码任何绝对路径。

写法统一用 `os.path`：全部常量都是**字符串绝对路径**；
需要 `Path` 对象的地方由调用方自己 `Path(路径)` 转换（本模块不引入 pathlib）。

================================ 结构树 ================================
仓库根 PROJECT_ROOT
├─ src/                        SRC_DIR
│   ├─ main.py                 ENTRY_MAIN          （入口层：唯一调用源，调子命令）
│   ├─ Path_Store/             SRC_PATH_STORE_DIR  （本文件所在层：路径地址簿）
│   │   └─ Path_Store.py       本文件
│   ├─ webcrawl/               SRC_WEBCRAWL_DIR    （工具包：网页爬取）
│   │   ├─ net.py                                  HTTP 单一出口 + 限速
│   │   ├─ io_utils.py                             落盘单一出口
│   │   ├─ html_utils.py                           HTML → 可见文本
│   │   ├─ web_check.py                            robots 合规 + 静态/动态
│   │   ├─ page.py                                 抓正文存快照
│   │   ├─ console.py                              终端 UTF-8
│   │   └─ url_norm.py                             URL 归一化（建了不调用）
│   └─ extract/                SRC_EXTRACT_DIR     （业务：抽正文 / 抽事实）
├─ tests/                      TESTS_DIR
├─ sites/                      SITES_DIR           （种子 URL 清单，爬虫只读输入）
│   ├─ 市场.md                  SEED_MARKET
│   ├─ 上游.md                  SEED_UPSTREAM
│   └─ 其他数据.md              SEED_OTHER
├─ data/                       DATA_DIR           （采集产物，永不入库）
│   ├─ profile/                PROFILE_DIR        （判定层产物：每个 URL 一份策略画像）
│   │   └─ site_profile.json    SITE_PROFILE_JSON  （probe 落盘结果，抓取前查表复用）
│   ├─ raw/                    RAW_DIR
│   │   ├─ html/               RAW_HTML_DIR       （原始 HTML 快照，字节保真）
│   │   ├─ files/              RAW_FILES_DIR      （非 HTML 原始文件，如 PDF 快照）
│   │   └─ pages.json          PAGES_INDEX_JSON   （抓取清单：URL / 状态 / 指纹）
│   ├─ jobs/                   JOBS_DIR           （异步任务状态 + 断点，一 job 一 json）
│   ├─ discover/               DISCOVER_DIR       （链接发现候选池）
│   │   └─ url_pool.json       URL_POOL_JSON
│   ├─ normalized/             NORMALIZED_DIR     （给下游消费的规范化产物）
│   │   ├─ text|para|pdf/                         正文 / 段落锚点 / PDF 文本层
│   │   ├─ tables/                                表格三件套（html/json/csv）
│   │   └─ wide/               WIDE_DIR           （长表转宽表：指标 × 年份）
│   └─ parsed/                 PARSED_DIR
│       ├─ articles.json       ARTICLES_JSON      （标题 / 正文 / 字数 / 重复标记）
│       ├─ articles.csv        ARTICLES_CSV
│       ├─ facts.json          FACTS_JSON         （数字事实，带原句证据）
│       └─ facts.csv           FACTS_CSV
├─ reports/                    REPORTS_DIR        （指标报告与结论）
├─ README.md                   README
├─ AGENTS.md                   AGENTS
├─ requirement.txt             REQUIREMENTS
└─ 凯里酸汤鱼_市场与产业链数据源.md   SOURCE_INDEX_DOC

============================== 使用约定 ==============================
    from Path_Store import Path_Store as ps
    for seed in ps.SEED_FILES:          # 只读遍历，不要自己拼 "sites\\市场.md"
        ...

禁止在别处用 `os.path.join(PROJECT_ROOT, "...")` 自己拼路径。
"""

from __future__ import annotations

import os
from pathlib import Path

# ------------------------------------------------------------ 仓库根与源码
# 本文件在 <仓库根>\src\Path_Store\Path_Store.py：
# 往上两级是 src，再往上一级是仓库根
_HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
PROJECT_ROOT = os.path.abspath(os.environ.get("INDUSTRY_COMPONENT_WORKSPACE", SOURCE_ROOT))

SRC_DIR = os.path.join(SOURCE_ROOT, "src")
SRC_WEBCRAWL_DIR = os.path.join(SRC_DIR, "webcrawl")
SRC_PATH_STORE_DIR = os.path.join(SRC_DIR, "Path_Store")
SRC_EXTRACT_DIR = os.path.join(SRC_DIR, "extract")
ENTRY_MAIN = os.path.join(SRC_DIR, "main.py")
TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")

# ------------------------------------------------------------ 种子数据（只读输入）
SITES_DIR = os.path.join(SOURCE_ROOT, "sites")
SEED_MARKET = os.path.join(SITES_DIR, "市场.md")
SEED_UPSTREAM = os.path.join(SITES_DIR, "上游.md")
SEED_OTHER = os.path.join(SITES_DIR, "其他数据.md")
SEED_CATERING = os.path.join(SITES_DIR, "贵阳餐饮_数据源.md")
SEED_FILES = (SEED_MARKET, SEED_UPSTREAM, SEED_OTHER, SEED_CATERING)
SOURCE_INDEX_DOC = os.path.join(PROJECT_ROOT, "凯里酸汤鱼_市场与产业链数据源.md")

# ------------------------------------------------------------ 采集产物（可再生成，永不入库）
# data\ 分四层：profile 判定 / policy 站点策略 / raw 原始字节 / normalized 给下游消费 / parsed 抽取
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROFILE_DIR = os.path.join(DATA_DIR, "profile")
SITE_PROFILE_JSON = os.path.join(PROFILE_DIR, "site_profile.json")
POLICY_DIR = os.path.join(DATA_DIR, "policy")
SITE_POLICY_JSON = os.path.join(POLICY_DIR, "site_policy.json")
RAW_DIR = os.path.join(DATA_DIR, "raw")
RAW_HTML_DIR = os.path.join(RAW_DIR, "html")
RAW_FILES_DIR = os.path.join(RAW_DIR, "files")
PAGES_INDEX_JSON = os.path.join(RAW_DIR, "pages.json")

# 任务状态：异步任务的进度与断点（一个 job 一个 json，增量/续抓靠它）
JOBS_DIR = os.path.join(DATA_DIR, "jobs")

# 链接发现：种子页 → 站内链接 / 翻页 / 附件 / sitemap 的候选池
DISCOVER_DIR = os.path.join(DATA_DIR, "discover")
URL_POOL_JSON = os.path.join(DISCOVER_DIR, "url_pool.json")

# 规范化产物：向量层/校验层只读这里，不许自己去解析 raw\
NORMALIZED_DIR = os.path.join(DATA_DIR, "normalized")
TEXT_DIR = os.path.join(NORMALIZED_DIR, "text")
PARA_DIR = os.path.join(NORMALIZED_DIR, "para")
PDF_TEXT_DIR = os.path.join(NORMALIZED_DIR, "pdf")
TABLES_DIR = os.path.join(NORMALIZED_DIR, "tables")
WIDE_DIR = os.path.join(NORMALIZED_DIR, "wide")     # 长表 → 宽表（指标 × 年份）导出

PARSED_DIR = os.path.join(DATA_DIR, "parsed")
ARTICLES_JSON = os.path.join(PARSED_DIR, "articles.json")
ARTICLES_CSV = os.path.join(PARSED_DIR, "articles.csv")
FACTS_JSON = os.path.join(PARSED_DIR, "facts.json")
FACTS_CSV = os.path.join(PARSED_DIR, "facts.csv")

# ------------------------------------------------------------ 交付与文档
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
README = os.path.join(PROJECT_ROOT, "README.md")
AGENTS = os.path.join(PROJECT_ROOT, "AGENTS.md")
REQUIREMENTS = os.path.join(PROJECT_ROOT, "requirement.txt")

# 研究分析独立于采集；模型缓存和运行数据不进 Git。
RESEARCH_CONFIG = os.path.join(SOURCE_ROOT, "config", "research.json")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
RESEARCH_DIR = os.path.join(DATA_DIR, "research")
MODELS_DIR = os.path.join(DATA_DIR, "models")
EMBED_MODEL_DIR = os.environ.get("INDUSTRY_COMPONENT_MODEL_DIR", os.path.join(
    MODELS_DIR, "bge-small-zh-v1.5-int8"))
EMBED_MODEL_REFERENCE = os.environ.get("INDUSTRY_COMPONENT_MODEL_REFERENCE",
                                      "data/models/bge-small-zh-v1.5-int8")
EMBED_MODEL_PATH = os.path.join(EMBED_MODEL_DIR, "model_quantized.onnx")
EMBED_TOKENIZER_PATH = os.path.join(EMBED_MODEL_DIR, "tokenizer.json")
EMBED_MANIFEST_PATH = os.path.join(EMBED_MODEL_DIR, "manifest.json")
RESEARCH_EXPORT_FILES = {
    name: name + suffix for name, suffix in (
        ("documents", ".csv"), ("passages", ".csv"), ("facts", ".csv"),
        ("series", ".csv"), ("issues", ".json"), ("manifest", ".json"),
        ("review_template", ".csv"), ("analysis", ".json"), ("report", ".md"),
        ("trend", ".json"), ("trend_model", ".json"),
        ("llm_suggestions", ".json"),
    )
}


def research_output(directory: str | Path, name: str) -> str:
    """只计算运行产物地址；无 I/O，地址构造仍集中在地址簿。"""
    return os.path.join(str(directory), RESEARCH_EXPORT_FILES[name])
