# 爬虫标准 API 接口（输入 / 输出）

版本：**v1.2（按《项目初步技术规划与目标》逐条对齐）**
日期：2026-10-20
适用：参与本项目的全部 AI（Lead / GLM / GPT）与人类开发者

> **一句话**：这份文件是「爬虫」与「外部世界」之间唯一的合同。
> 改行为先改这里；代码与它不一致时，**以本文为准**。
>
> 🔵 **定位**：本文是《产业分析平台_全链路API.md》里 **L1 采集层**的字段细则。
> 跨层共享结构（`ResearchParams` / `SearchHit` / `Fact` / `Answer` 等）以那份为准；
> 本文只管 L1 自己的输入输出。

**v1.2 的由来**：v1.1 把"材料"做对了，但拿《目标》逐条比对后发现 8 处不达标——
其中 2 处（发布主体/发布时间、证据定位）是**直接违反 §五.1 明文要求**，
1 处（PDF 只存不解析）会让最重要的数据源变成黑盒。详见 §14 对照表。

---

## 1. 边界：什么是"初步解析"

依据《目标》§三.2：爬虫负责**获取和初步解析**材料，抽取/主体识别/去重/口径归核心模块。

| 做 | 不做 |
|---|---|
| 判定能不能抓、该怎么抓 | 从正文里抽「指标 + 数值 + 口径」 |
| 三档抓取 + 字节保真落盘 | 主体识别、公司/门店去重 |
| **规范化**：正文 → 干净文本 + **段落锚点** | 判断「这家企业该不该纳入研究范围」 |
| **表格抽取**：保留行列/表头/合并结构 | 口径计算、量纲归一 |
| **PDF**：页 → 文本块 + 页码映射 | 向量化、数值合理性判断、建模 |
| 记录来源元数据（发布主体 / 时间 / canonical） | 估算与推断 |

**验收标准（一句话）**：下游必须能指着一个数字说
**「它出自哪份材料、谁发布的、哪一年、第几段/第几页」**。做不到就是材料不完整。

---

## 2. 接口清单

| # | 接口 | 输入 | 输出 | 落盘 |
|:-:|---|---|---|:-:|
| 1 | `probe` | `ProbeRequest` | `Profile[]` | 可选 |
| 2 | `crawl` | `CrawlRequest` | `Job`（异步）/ `CrawlResult[]`（同步） | 是 |
| 3 | `plan` | `Profile[]` | `Plan[]` | 否 |
| 4 | `read_material` | `record_id` | `Material` | 否 |
| 5 | `site_policy` | `host` | `SitePolicy` | 是（`data/policy/`） |

第 5 个是 v1.2 新增，对应《目标》§七.3「各来源允许的访问方式、频率和技术适配需求」。

---

## 3. 输入契约

### 3.1 `ProbeRequest`

| 字段 | 类型 | 必填 | 默认 |
|---|---|:-:|---|
| `urls` | `string[]` | ✅ | — |
| `timeout_s` | `number` | | `8.0` |
| `sample_bytes` | `int` | | `200000` |
| `user_agent` | `string` | | 项目 UA（含联系方式） |
| `save` | `bool` | | `true` |

### 3.2 `CrawlRequest`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:-:|---|---|
| `job_id` | `string` | ✅ | — | 参与主键计算 |
| `project_id` | `string` | | `null` | 🔵 v1.2：所属研究项目（《目标》§五.5） |
| `urls` | `string[]` | ✅ | — | |
| `profiles` | `Profile[] \| null` | | `null` | |
| `timeout_s` | `number` | | `15.0` | |
| `per_host_delay_s` | `number` | | `1.5` | |
| `respect_robots` | `bool` | | `true` | 🔴 恒 true |
| `allow_render` | `bool` | | `false` | |
| `allow_tls_impersonate` | `bool` | | `false` | |
| `save_raw` | `bool` | | `true` | |
| `normalize` | `bool` | | `true` | 产出正文 + 段落锚点 + 表格 |
| `parse_pdf` | `bool` | | `true` | 🔵 v1.2：PDF → 文本 + 页码映射 |
| `dedupe_by_hash` | `bool` | | `true` | |
| `requested_by` | `string` | | — | |
| `rule_version` | `string` | | — | 规则版本（《目标》§五.3） |

---

## 4. 输出契约

### 4.1 `Profile`（判定画像）

`url / site / checked_at / http_status / final_url / robots / content_type / encoding /
bytes / text_chars / verdict / reason / signals / error`（同 v1.1，略）

### 4.2 `CrawlResult`（材料记录，写库主对象）

**身份与追溯**

| 字段 | 类型 | 规则 |
|---|---|---|
| `record_id` | `string` | `uuid5(uuid.NAMESPACE_URL, f"{job_id}\|{url}")` |
| `job_id` / `project_id` | `string` | `project_id` 可空 |
| `url` / `final_url` / `site` | `string` | |
| `fetched_at` | `string` | 我**采集**的时间 |
| `elapsed_ms` | `int` | |
| `meta` | `Meta` | `{requested_by, rule_version, code_version}` 🔵 加 `code_version` |

**来源陈述元数据（🔵 v1.2 新增，对应《目标》§五.1 / §五.2）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `source.publisher` | `string \| null` | **发布主体**：谁说的。取自 meta/署名/站点声明，取不到为 `null`，**不许猜** |
| `source.published_at` | `string \| null` | **发布时长**：原文时间，ISO8601；取不到 `null`，与 `fetched_at` 严格区分 |
| `source.canonical_url` | `string \| null` | 原文规范地址（`<link rel=canonical>` / meta），用于**转载归并** |
| `source.author` | `string \| null` | 署名作者 / 记者 |
| `source.declaration` | `string \| null` | 页面自述的来源声明原文，如「来源：贵阳市统计局」 |
| `source.is_reprint` | `bool \| null` | 是否识别为转载（三方判断，`null` = 判不了） |

> 🔴 **`publisher` 与 `published_at` 是"来源陈述"的骨架**。
> 缺了它们，下游无法回答「这条说法是谁在什么时候说的」，整条证据链降级为"网上看到过"。

**合规与判定**

`robots / verdict / tool_used / tool_fallback / status`（同 v1.1）

**原始材料**

`http_status / content_type / encoding / bytes / title / content_sha256 / storage`

**规范化产物**

| 字段 | 类型 | 说明 |
|---|---|---|
| `text_chars` | `int` | CJK 字数 |
| `text_sha256` | `string \| null` | 正文指纹，判断要不要重建向量索引 |
| `text_meta` | `TextMeta` | `{paragraphs, chars, has_table, table_count}` |
| **`paragraph_file`** | `string \| null` | 🔵 v1.2：段落锚点文件路径 |
| **`paragraph_count`** | `int` | 🔵 v1.2：段落数 |
| **`pdf`** | `PdfInfo \| null` | 🔵 v1.2：PDF 才有的页映射 |
| `tables` | `Table[]` | 见 §4.5 |

**错误**：`error: Error \| null`

### 4.3 `Paragraph`（段落锚点，🔵 v1.2，对应《目标》§五.1「关联到正文片段」）

落盘为 **JSON Lines**（`data/normalized/para/<site>_<hash10>.jsonl`），每行：

```json
{"index": 37, "char_start": 2841, "char_end": 2967, "page": null,
 "text": "2025年全市餐饮收入412.68亿元，同比增长7.9%。"}
```

- `index`：段号，从 **0** 起，**稳定不变**（同一正文重跑得到同样段号）
- `char_start/char_end`：在 `normalized/text/*.txt` 中的**字符偏移**（UTF-8 码点，不是字节）
- `page`：PDF 才有，其它为 `null`
- 下游引用一个数字时，必须能定位到 `(文件, 段index)` 或 `(文件, page)`

### 4.4 `PdfInfo`（🔵 v1.2）

```json
{"pages": 42, "text_path": "data/normalized/pdf/tjj_1f0c9d8a3b.txt",
 "page_map": [{"page": 1, "char_start": 0, "char_end": 612}, …]}
```

- PDF 的正文合并进 `text_path`，**页码边界用 `page_map` 标出**
- 扫描版 PDF（无文本层）→ `pages>0` 但 `page_map` 为空 + `E_PDF_NO_TEXT`，如实标记，不假装解析成功

### 4.5 `Table` 与表格结构保真（🔵 v1.2 改）

统计年鉴的表有**合并单元格、多行表头、跨页续表**。CSV 表达不了，会直接导致下游误读指标。

```json
{"index": 0, "rows": 42, "cols": 6, "page": 7,
 "html_path": "data/normalized/tables/tjj_1f0c9d8a3b_t00.html",
 "json_path": "data/normalized/tables/tjj_1f0c9d8a3b_t00.json",
 "csv_path":  "data/normalized/tables/tjj_1f0c9d8a3b_t00.csv"}
```

| 产物 | 用途 |
|---|---|
| `html_path` | **原始表格片段**，字节保真，是最终依据 |
| `json_path` | 结构化的 `{cells:[{r,c,rowspan,colspan,text}]}`，保住合并与表头层级 |
| `csv_path` | 展平导出，给人和快速查看；**不许作为机器读取的唯一来源** |

- `page`：PDF 表格才有页码，HTML 为 `null`
- 没抽到表格 → `tables: []`

### 4.6 `Verdict` / `Status`（10 / 4，同 v1.1）

`static / thin_static / pdf / other_file / robots_disallow / robots_unknown /
login_wall / dynamic / blocked / error`

### 4.7 错误码（**20 个**）

v1.1 的 18 个，加：

| 码 | 可重试 | 含义 |
|---|:-:|---|
| `E_PDF_PARSE` | ❌ | PDF 解析失败（损坏 / 加密） |
| `E_PDF_NO_TEXT` | ❌ | PDF 无文本层（扫描件），需 OCR，本期不做 |

其余 18 个同 v1.1（含补回的 `E_LOGIN_WALL`）。

---

## 5. 存储契约

```
data/
├── profile/site_profile.json        # Profile[]
├── policy/site_policy.json          # 🔵 SitePolicy[]
├── raw/{html,files}/                # 字节保真，不许改动
├── normalized/
│   ├── text/<site>_<h10>.txt        # 规范化正文
│   ├── para/<site>_<h10>.jsonl      # 🔵 段落锚点
│   ├── pdf/<site>_<h10>.txt         # 🔵 PDF 合并文本
│   └── tables/<site>_<h10>_t<NN>.{html,json,csv}
└── raw/pages.json                   # CrawlResult[]
```

🔴 文件名只由 **URL 哈希**决定；下游只读 `normalized/`，不许自己解析 `raw/`。

---

## 6. `SitePolicy`（🔵 v1.2，对应《目标》§七.3）

「首批公开来源，以及各来源允许的访问方式、频率和技术适配需求」——这是需要**声明**的，不是探测出来的。

| 字段 | 类型 | 说明 |
|---|---|---|
| `host` | `string` | |
| `allowed_paths` | `string[]` | 允许抓的路径前缀 |
| `disallowed_paths` | `string[]` | 明确禁止 |
| `min_interval_s` | `number` | 最小请求间隔（robots 的 crawl-delay 与人工设定取最大） |
| `preferred_tool` | `Tool` | 人工指定档位，覆盖自动判定 |
| `requires_login` | `bool` | 需登录 → 一律不抓 |
| `has_pdf` | `bool` | 是否有 PDF 资源 |
| `note` | `string` | 人工备注 |

持久化在 `data/policy/site_policy.json`，**人工可改、AI 不得自动覆盖已有条目**。

---

## 7. 任务与进度（🔵 v1.2，对应《目标》§四）

长任务在后台执行，接口先返回任务号；网页关闭后仍继续。

`Job`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | `string` | |
| `project_id` | `string \| null` | |
| `state` | `enum` | `queued / running / done / failed / partial` |
| `total` / `finished` | `int` | 进度条依据 |
| `counts` | `object` | `{ok, ok_pdf, skipped, failed}` |
| `started_at` / `finished_at` | `string \| null` | |
| `error` | `Error \| null` | 整体失败原因 |

> `partial` = 部分成功部分失败，**不是** `failed`。三条约束：
> ① 任务已保存但未成功入队要能重投；② 重跑不得产生重复记录（幂等键）；
> ③ 失败任务要能定位原因并单独重跑。

---

## 8. 幂等与重试（同 v1.1）

幂等键 `(job_id, url)`；`record_id = uuid5(uuid.NAMESPACE_URL, …)`；
只重试 `retryable=true`，最多 2 次；失败必须保留 `error.message`。

---

## 9. 版本与变更规则（同 v1.1）

封闭枚举只许新增；字段只许新增；破坏性变更升 minor 并写迁移方式。

---

## 10. 仍需三家确认

| # | 议题 | 暂定 |
|:-:|---|---|
| 1 | 正文进不进接口响应 | 不进，给 `text_path` + `paragraph_file` |
| 2 | 种子文件 vs URL 列表 | 只要 URL 列表 |
| 3 | 并发与限速位置 | 接口内 `per_host_delay_s` |
| 4 | `Profile` 入不入库 | 入，只存最新 + `checked_at` |
| 5 | 人工覆盖档位 | 走 `SitePolicy.preferred_tool` |
| 6 | PDF 解析归谁 | **爬虫组**（页码只有解析方知道） |
| 7 | 表格机器读取用哪个 | `json_path`；CSV 只给人看 |
| 8 | 转载识别到哪一步 | 只给 `canonical_url` / `is_reprint` 线索，归并结论归下游 |

---

## 11. v1.1 → v1.2 变更记录（全部由《目标》倒推）

| # | 目标条款 | v1.1 的问题 | v1.2 |
|:-:|---|---|---|
| 1 | §五.1 | 缺发布主体、缺发布时间 | 新增 `source.publisher / published_at` |
| 2 | §五.1 | 无法关联正文片段 | 新增 `Paragraph[]` 段落锚点 |
| 3 | §五.1 | PDF 只存不解析 | 新增 `PdfInfo` + 页映射；`parse_pdf` 默认 true |
| 4 | §五.2 | 只能识别完全相同的转载 | 新增 `canonical_url / is_reprint` |
| 5 | §五.3 | 缺处理程序版本 | `meta.code_version` |
| 6 | §七.3 | 没有站点策略声明 | 新增 `SitePolicy` 接口与落盘 |
| 7 | §四 | 进度/失败/重试无结构 | 新增 `Job` 状态机（含 `partial`） |
| 8 | §五.5 | 材料挂不到研究项目 | 新增 `project_id` |
| 9 | — | 表格扁平化会丢合并单元格 | 改为 html + json + csv 三件套 |
| 10 | — | 扫描件 PDF 会被误当成功 | 新增 `E_PDF_NO_TEXT` |

---

## 12. 下游消费契约

| 下游 | 读什么 | 关键约束 |
|---|---|---|
| 向量层 | `normalized/text/*.txt` + `text_sha256` | 指纹没变就不重建索引 |
| 向量层（细粒度） | `normalized/para/*.jsonl` | 每条 chunk 必须带 `(record_id, index)` |
| 数值校验层 | `normalized/tables/*.json` + 正文段落 | 用 `json_path`，**不要用 csv** |
| 建模层 | 下游自己的表 | 靠 `record_id / fetched_at / content_sha256` 回溯 |
| 展示层 | `read_material(record_id)` | 任一条结论都能回读原文并定位到段/页 |

---

## 13. HTTP 映射（交给 GPT 实现）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/v1/probe` | → `{profiles}` |
| `POST` | `/v1/jobs` | `CrawlRequest` → `{job_id}`（异步） |
| `GET` | `/v1/jobs/{job_id}` | `Job` |
| `GET` | `/v1/materials/{record_id}` | `CrawlResult` |
| `GET` | `/v1/materials/{record_id}/text` | `text/plain` |
| `GET` | `/v1/materials/{record_id}/paragraphs` | `application/x-ndjson` |
| `GET` | `/v1/materials/{record_id}/tables/{index}` | `?format=json\|csv` |
| `GET` | `/v1/site-policy/{host}` | `SitePolicy` |
| `GET` | `/v1/jobs/{job_id}/retry` | 重跑失败项 |

**约定**：`Authorization: Bearer`；`X-Crawler-API-Version: 1.2`；
`POST /v1/jobs` 支持 `Idempotency-Key`；错误体统一 `{error:{code,message,retryable}}`，
HTTP 状态码只做粗分类，**判据看 `code`**。

---

## 14. 目标条款对照表（本文档的验收依据）

| 《目标》条款 | 要求 | v1.1 | v1.2 |
|---|---|:-:|:-:|
| §一.4 | 核心能力脱离网页独立运行 | ✅ | ✅ 五接口均为纯函数 |
| §二 | 能表达「来源陈述」 | ❌ | ✅ `source.*` |
| §四 | 任务/进度/重试/关页继续 | 🟡 | ✅ `Job` |
| §五.1 | 发布主体、发布时间 | ❌ | ✅ |
| §五.1 | 关联正文片段 / 表格位置 / PDF 页码 | ❌ | ✅ `Paragraph` / `Table.page` / `PdfInfo` |
| §五.2 | 转载与引用关系识别 | ❌ | 🟡 只给线索，归并归下游 |
| §五.3 | 关联规则与处理程序版本 | 🟡 | ✅ `rule_version` + `code_version` |
| §五.5 | 材料挂到研究项目 | ❌ | ✅ `project_id` |
| §七.3 | 声明来源的访问方式/频率/适配 | ❌ | ✅ `SitePolicy` |

**遗留**：§五.2 的转载归并到底归谁，仍需三家确认（本文档暂定只给线索）。
