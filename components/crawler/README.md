> 2026-10-21：本副本已接入平台采集与研究任务。统一使用平台根 .venv，运行数据在平台 data 目录；本页下文是独立爬虫能力与历史验收，当前服务范围见根 README.md。

# 凯里酸汤鱼产业调查（贵阳）

**Crawler — Kaili Sour-Soup Fish Industry Survey (Guiyang)**

## 2026-10-20 当前研究分析交付

追加评估：[WGAN 数据增强](docs/WGAN数据增强评估.md)已完成独立 CPU 夹具训练/采样/下游对照；本次短程增强效果变差，主研究不启用生成样本。真实观测数量保持原样。

本节为最新状态；下文旧实验数字保留为历史记录。本次所有者已授权研究分析与子 Agent 并行，三个项目暂不归并。

- 代码：37 个非空 Python 文件 / 12734 行；测试 14 文件 / 5678 行 / 522 项。`main.py --self-test` 覆盖 25 个模块；29 个文件含自测。
- 新链路：已采 HTML/PDF → 验证正文指纹/坐标 → CPU INT8 BGE 与关键词联合召回 → 待审核事实 CSV → 同口径序列 → XGBoost 回测。
- 真实验收：新抓 2/2 成功；313 条历史记录整理为 144 份文档（含 3 份 PDF）、5709 个片段；841 个候选片段（含 4 个纯语义补查），116 条 pending 事实，零指纹/坐标异常；完整分析约 36.37 秒。
- 趋势：实际审核序列 0 条，明确返回 insufficient_data。真实 XGBoost 计算使用单独合成夹具验收，四折未优于基线，正确保持 exploratory_model；测试夹具不写入研究数据。
- AI：DeepSeek 官方接口真实验证通过，3 段测试产生 3 条有原文依据的待审核建议、2 条不支持的候选被拦截；用量 1078 tokens。密钥仅本地 `.env`，未入库。
- 接口：采集 Python v1.3；独立研究 API v1.0，统一返回壳；Temp/建议助手未修改、未接入。

新增目录与文件：

```text
config/research.json              研究规则与初始召回配置
src/research/                    六个独立功能模块（见下表）
src/research_api.py               显式研究入口编排；供未来适配器调用
data/models/                     固定 revision 的 ONNX/tokenizer/manifest（忽略）
data/research/final-20261020/      本轮完整 JSON、CSV、报告（忽略）
requirements.lock.txt            本机已验证环境快照
.env.example                     无密钥的 AI 设置示例
docs/研究分析使用说明.md          命令、输入/输出形状、调用树与限制
docs/三个项目接口准备.md          字段合同、未来适配和未完成的接线前提
docs/本轮验证记录.md              测试、磁盘和实测台账
```

| 新模块 | 行数 | 职责 |
|---|---:|---|
| `src\research\__init__.py` | 2 | 独立研究包声明 |
| `src\research\assets.py` | 77 | 固定版本模型下载与缓存校验 |
| `src\research\evidence.py` | 518 | HTML/PDF 证据与可比序列 |
| `src\research\llm.py` | 338 | 可选 DeepSeek 待审核建议 |
| `src\research\retrieval.py` | 274 | CPU 关键词/语义召回 |
| `src\research\review.py` | 53 | 独立审核决定 |
| `src\research\trend.py` | 306 | 按发布时间的 XGBoost 滚动回测 |
| `src\research_api.py` | 229 | 研究 API 显式编排入口 |

常用命令：

```powershell
.\myvenv\Scripts\python.exe src\main.py model-setup
.\myvenv\Scripts\python.exe src\main.py analyze --out data\research\新运行目录
.\myvenv\Scripts\python.exe src\main.py research-api
.\myvenv\Scripts\python.exe src\main.py ai
.\myvenv\Scripts\python.exe -m pytest -q
```

当前范围与剩余工作：

1. 事实校验规则与审核通道已实现，人工口径复核尚未完成；CSV 不冒充数据库入库。
2. 年度单指标特征与 XGBoost 回测已实现；真实产业预测待可比观测和明确业务目标。CatBoost 尚未实现。
3. 0.64 阈值仅经高分样本 Agent 抽查，尚无独立标注集精确率/召回率；酸菜鱼为纯语义排除词，所有片段/分数保留。
4. `parse` 为旧格式兼容命令；新记录使用 `analyze`。扫描 PDF 无 OCR。
5. 暂不归并：详见 [三个项目接口准备](docs/三个项目接口准备.md)；HTTP 适配、可注入数据根、跨进程限速、快照不可变版本等尚待实现。

---

## 0. 这个项目想干什么（别把它当成"一个爬虫"）

表面上它是「爬虫 + 建模」，但那只是手段。**野心是做出可复现的产业判断力**：

| 层次 | 内容 | 当前进度 |
|---|---|---|
| 1 | 把散在公开信息里的产业数字**采下来、结构化** | 链路已通（并发 / BFS 发现 / 增量 / 去重 / 账本已接） |
| 2 | 判断这些数字**能不能用**（口径 / 量纲 / 年份 / 地域） | 自动校验与审核门槛完成，人工复核待做 |
| 3 | 在**没有官方统计**的产业上，自己造出可比的指标口径 | 已实现口径隔离；业务口径仍待审核 |
| 4 | 用模型给出**可被质疑、可被复核**的判断 | 回测程序完成；真实数据不足 |

关键现实：**官方统计里没有"酸汤鱼"这个类目**（GDP 行业分类最细到餐饮业，没有菜系/单品）。
所以这个项目真正要解决的问题不是"爬得更多"，而是**在没有官方口径的情况下，
自己造一套站得住的口径** —— 这也是第 2、3 层比第 1 层难的地方。

> ⚠️ 第 2~4 层已于本轮获授权实现基础流程，真实产业结论仍取决于数据与审核。

---

## 1. 项目事实

| 项 | 值 |
|---|---|
| 本地路径 | `../Crawler` |
| Git | 分支 `main`，remote `origin` → `github.com/aaf12329/Crawler.git`；**push 只由本人执行** |
| 虚拟环境 | `myvenv\Scripts\python.exe` → **Python 3.10.0** |
| 依赖清单 | requirement.txt 与 requirements.lock.txt；新增 CPU ONNX、tokenizers、scikit-learn、XGBoost；无 PyTorch/CUDA |
| 种子数据 | `sites\` 下 4 个文件，共 **141 条 URL**（含贵阳餐饮方向新增的 50 条） |
| 代码 | 37 个非空 Python 文件 / 12734 行；测试 14 文件 / 5678 行 / 522 项 |
| 数据产物 | 跑完是 `data\raw\`（原始字节 + `pages.json`）/ `data\normalized\`（正文 / 段落锚点 / 表格三件套 / **宽表**）/ `data\jobs\`（任务账本）/ `data\discover\`（链接候选池）/ `data\profile\`（判定画像）/ `data\policy\`（站点策略）/ `data\parsed\`（旧链路产物）—— 已 gitignore，可再生成 |
| 忽略规则 | myvenv/、__pycache__/、*.pyc、data/、.env、work/、reports/、.pytest_cache/；.env.example 可入库 |
| 协作规范 | 本地 `../CoMind/01_AI项目协作参考`；云端 [aaf12329/CoMind](https://github.com/aaf12329/CoMind)（详见 §7） |

---

## 2. 研究目标

### 阶段一 · 采集与整理（当前）

用爬虫把凯里酸汤鱼在北京/贵阳市场的**公开信息**采集下来并结构化：

- 产业规模、产值、政策目标
- 上游：原料种养（稻花鱼、辣椒、木姜子、番茄）、发酵加工
- 下游：餐饮门店、预制菜、电商销售、文旅联动
- 企业与资本：注册企业数、注册资本、成立年份

### 阶段二 · 建模预测（后续）

基于阶段一整理出的结构化数据，用 **XGBoost** 与 **CatBoost** 做产业预测，
并参照本仓库既定规范（提交标准 / 文档标准，见 §7）产出可复现的结论：

| 方向 | 说明 |
|---|---|
| 预测目标 | 产业规模走势、品类热度、区域扩张潜力（具体标签待数据到位后确定） |
| 模型 | XGBoost（基线 + 特征重要性） / CatBoost（类别特征友好，适合地区/品类） |
| 评估 | 时间序列留出 + 交叉验证；**划分维度必须与防泄漏维度一致**（见 §8 坑表） |
| 交付 | 特征表 + 训练脚本 + 指标报告 + README 进度同步 |

> ⚠️ 阶段二的标签体系**尚未确定**。数据到手前不预设标签，避免为凑模型而扭曲调研。

---

## 3. 目录结构

```text
Crawler/
├─ README.md                             ← 本文件：项目总览与进度（唯一权威）
├─ AGENTS.md                             ← AI 接手手册：事实、工作循环、坑表
├─ requirement.txt                        ← 依赖清单（含清华源）
├─ .gitignore                             ← 忽略 myvenv/ __pycache__/ data/
├─ src/                                   ← 代码根目录（所有源码都放这里）
│   ├─ main.py                             ← 入口层：唯一调用源（16 个子命令 + 全部显示）
│   ├─ Path_Store/
│   │   └─ Path_Store.py                   ← 路径地址簿（纯常量；JOBS_DIR / DISCOVER_DIR / WIDE_DIR 都登记在这）
│   ├─ webcrawl/                           ← 工具包：网页爬取（换项目能原样复用）
│   │   ├─ api.py                          ← 【对外 API】probe/plan/crawl/expand/resume_job/job_progress/read_material/site_policy/write_site_policy
│   │   ├─ probe.py                        ← 【判定层】逐 URL 探测 → profile（8 选 1 verdict）
│   │   ├─ route.py                        ← 【选档层】profile.verdict → 用哪个抓取工具
│   │   ├─ fetch_tls.py                    ← 【抓取档】curl_cffi 的 TLS 指纹伪装（治 403 / CF）
│   │   ├─ fetch_browser.py                ← 【抓取档】patchright 真浏览器（治 JS 动态站）
│   │   ├─ ratelimit.py                    ← 【抓取档】并发限速：同 host 串行、robots crawl-delay 不截断
│   │   ├─ job.py                          ← 【抓取档】任务状态机 + 断点落盘 + 增量计划
│   │   ├─ discover.py                     ← 【发现】链接 / 详情页 / 翻页 / 附件 / sitemap → 候选池
│   │   ├─ dedupe.py                       ← 【去重】跨 URL 去重（content_sha256 + 正文 simhash / Jaccard）
│   │   ├─ boilerplate.py                  ← 【规范化】正文容器定位（去导航 / 页脚 / 侧栏 / 推荐位）
│   │   ├─ normalize.py                    ← 【规范化】正文 + 段落锚点 + 表格三件套（版式表过滤 / 宽表）
│   │   ├─ table_ops.py                    ← 【规范化】表格算子：版式表过滤 / 多行表头 / 长表转宽
│   │   ├─ pdf.py                          ← 【规范化】PDF 文本层 + 页码映射（纯标准库）
│   │   ├─ source_meta.py                  ← 发布主体 / 发布时间 / canonical / 转载判定
│   │   ├─ record.py                       ← CrawlResult 构造（30 字段，uuid5 主键；meta 扩展键保留）
│   │   ├─ store.py                        ← 落盘 + pages.json 按 (job_id,url) upsert（含宽表目录）
│   │   ├─ policy.py                       ← 站点访问策略（人工值不被自动覆盖）+ 从画像批量写策略
│   │   ├─ errors.py                       ← 20 个封闭错误码 + 可重试表
│   │   ├─ net.py                          ← HTTP 单一入口（只重试暂时性错误 + proxies）+ 限速基元
│   │   ├─ io_utils.py                     ← I-O 单一入口（文本 / 字节 / JSON / CSV / 文件名 / 哈希）
│   │   ├─ html_utils.py                   ← HTML → 可见文本 + <title>
│   │   ├─ web_check.py                    ← 合规判定（robots）+ 页面类型（静态 / JS 动态）
│   │   ├─ page.py                         ← 旧抓取路径（已被 api.py 取代，保留待退休）
│   │   ├─ console.py                      ← 终端 UTF-8 与对齐打印
│   │   └─ url_norm.py                     ← URL 归一化（现被 discover 用作去重键）
│   └─ extract/                            ← 业务层（旧链路，待退休；含产业名词，按新架构应移出爬虫组）
│       ├─ Extract_Article.py              ← 抽标题/正文 + 标转载重复 → 落成表
│       └─ Extract_Facts.py                ← 抽数字事实（指标/数值/单位/年份/地域）
├─ tests/
│   ├─ test_logic.py                      ← 纯逻辑测试（67 项，零网络、零真实数据写入）
│   ├─ test_probe_route.py                ← 判定层 / 选档层（18 项）
│   ├─ test_boilerplate.py                ← 正文容器定位（29 项）
│   ├─ test_discover.py                   ← 链接发现（64 项）
│   ├─ test_job.py                        ← 并发限速 + 任务状态机（71 项）
│   ├─ test_dedupe.py                     ← 跨 URL 去重（73 项）
│   ├─ test_table_ops.py                  ← 表格算子（48 项）
│   ├─ test_api_pipeline.py               ← api 编排集成测试（打桩网络，12 项）
│   └─ test_source_meta.py                ← 转载三态判定（26 项）
├─ docs/
│   └─ 抓取策略表.md                       ← 【旧契约】8 个 verdict + 7 个工具档位
├─ sites/                                  ← 【输入】种子数据，纯 URL，一行一条
│   ├─ 市场.md                            36 条 URL
│   ├─ 上游.md                            20 条 URL
│   ├─ 其他数据.md                         35 条 URL
│   └─ 贵阳餐饮_数据源.md                   50 条 URL（逐条实测 HTTP 200 + robots 允许 + 正文≥300）
├─ data/                                   ← 【产物】已 gitignore，删了能重爬
│   ├─ profile/                            ← 判定画像（抓取前查表，不必重判）
│   ├─ policy/                             ← 站点访问策略（人工可改）
│   ├─ raw/                                ← 原始字节，不许改动
│   │   ├─ html/                           ← <站点>_<URL哈希10>.html（同 URL 覆盖）
│   │   ├─ files/                          ← PDF 等非网页原件
│   │   └─ pages.json                      ← CrawlResult[]（30 字段，按 (job_id,url) upsert）
│   ├─ normalized/                         ← 给下游消费的干净产物
│   │   ├─ text/                           ← 规范化正文
│   │   ├─ para/                           ← 段落锚点（JSON Lines：段号 / 偏移 / 原文）
│   │   ├─ pdf/                            ← PDF 合并文本
│   │   ├─ tables/                         ← 每表三件套 .html / .json / .csv
│   │   └─ wide/                           ← 长表 → 宽表（指标 × 年份）
│   ├─ jobs/                               ← 任务账本（一 job 一 JSON：状态 / 进度 / 每条结果）
│   ├─ discover/                           ← 链接候选池 url_pool.json
│   └─ parsed/                             ← 旧链路产物（articles / facts，待退役）
├─ 凯里酸汤鱼_市场与产业链数据源.md          ← 带说明的完整版索引（人工阅读用）
└─ myvenv/                                ← 虚拟环境（已 gitignore）
```

**分层与约定**（《工程架构规范》§01/§02/§04 落地）：

```
入口层  src\main.py              ← 唯一调用源：横向编排，也是唯一的终端显示处
   │                              （工具层一个 print 都没有，进度靠回调交给这里）
工具层  webcrawl\                ← 网页爬取工具包：换项目能原样用
   │                               ├─ probe.py       判定：这个 URL 属于哪种情况（8 选 1）
   │                               ├─ route.py       选档：这种情况该用哪个抓取工具
   │                               ├─ boilerplate.py 正文容器定位（纯逻辑）
   │                               ├─ normalize.py   正文 / 段落 / 表格（纯逻辑 + 落盘）
   │                               ├─ table_ops.py   表格算子：版式表过滤 / 宽表（纯逻辑）
   │                               ├─ discover.py    链接发现（纯逻辑）
   │                               ├─ dedupe.py      跨 URL 去重（纯逻辑）
   │                               ├─ ratelimit.py   并发限速（同 host 串行 / 不同 host 并行）
   │                               └─ job.py         任务账本：状态机 / 断点 / 增量
业务层  extract\                 ← 含产业名词（指标词典），换项目必须改；**旧链路待退休**
   │
地址簿  Path_Store\              ← 最底层，连工具层也要读它
```

**判定 → 选档骨架**（契约见 `docs\抓取策略表.md`）：

```
URL ──probe──► profile{verdict, signals, robots…} ──保存──► data\profile\site_profile.json
                                    │
                                    └──route──► {tool, available, fallback}
                                                   │
                    ┌──────────────────────────────┴───────────────────┐
               零依赖档（现在就能跑）                        需装依赖档（只留接口）
        skip / pdf / static / static_retry / skip_retry        impersonate(curl_cffi) / browser(patchright)
```

- 🔴 **`src\main.py` 是唯一调用源**：工具层与业务层都被它调，谁也进不去对方的门。
- 🔴 **工具层准入规则**：换一个调查项目（比如查云南菌子）能原样用 → 进 `webcrawl\`；
  出现任何产业名词（酸汤鱼/贵州/产值/门店）→ 留在 `extract\`。
- 🔴 **`webcrawl\` 里的模块零 `print`**：工具只返回数据，显示统一由入口层做。
- 🔴 **所有文件路径从 `src\Path_Store\Path_Store.py` 取**，禁止在别处拼 `"sites\\市场.md"`。
- 🔴 **HTTP 只走 `src\webcrawl\net.py` 的 `fetch()`**；I-O 只走 `src\webcrawl\io_utils.py`，不开第三处。
- `src\` 是**代码根目录**，**不在仓库根目录散放 `.py` 文件**。
- `sites\` 只放**纯 URL 列表，一行一条，不含任何说明文字**——
  它是爬虫的输入，不是给人看的文档。需要解释性内容请写在根目录那份完整版索引里。
- `data\` 是**可再生成的采集产物**，永不入库（规范 §07），按用途分层：
  `data\raw\` = 爬下来的原始数据（快照 + 抓取清单）、`data\normalized\` = 规范化产物
  （正文 / 段落 / 表格 / 宽表 / PDF）、`data\jobs\` = 任务账本、`data\discover\` = 链接候选池、
  `data\parsed\` = 旧链路的文章表与数字事实。规范化规则改了只需重跑 `normalized\`，不用再爬一遍。

**采集主链路**（`api.crawl`，契约 v1.3；下面每一步都能用 CLI 关掉）：

```
种子/URL ──expand?──► 候选池(discover) ──crawl──► 并发限速(ratelimit)
   ──► 正文容器定位(boilerplate) ──► 规范化(normalize: 正文/段落/表格/宽表)
   ──► 跨 URL 去重(dedupe: 只打 duplicate_of，不删记录)
   ──► 任务账本(job: 增量跳过 / 断点续跑) ──► data\raw\pages.json
```

常用开关：`crawl --workers 4 --discover --depth 1 --max-urls 12 --incremental
--recheck-days 30 --sitemap --no-dedupe --no-job --no-proxy --force`。

---

## 4. 种子数据说明

`sites\` 四个文件（**纯 URL，一行一条，不含任何说明文字**）：

| 文件 | 条数 | 覆盖什么 |
|---|---:|---|
| `市场.md` | 36 | 产业规模、产值、政策目标、下游消费与电商 |
| `上游.md` | 20 | 种养（稻花鱼）、原料工艺、发酵科研、农产品行情站 |
| `其他数据.md` | 35 | 统计局、统计公报（含 PDF）、行业报告库、电商数据工具、餐饮、企业查询、学术 |
| `贵阳餐饮_数据源.md` | 50 | **贵阳餐饮方向**：官方统计 15、政府政策 14、行业协会 9、平台 2、本地媒体 9、宏观 1 |

**演进**：首轮 34 条 → 2026-10-19 替换 8 条失效/禁止链接 → 同日扩充到 **91 条**
（新增 42 条，逐条实测 HTTP 200 + 正文 ≥800 字符 + robots 允许）；
2026-10-20 新增贵阳餐饮方向 **50 条**（逐条实测 HTTP 200 + robots 允许 + 正文 ≥300 字符），合计 **141 条**。

| 原链接的问题 | 数量 | 处理 |
|---|---:|---|
| 死链（HTTP 404） | 2 | 已换新源（统计年鉴聚合页 / 餐饮业报道） |
| robots.txt `Disallow: /` | 3 | 已换新源，**原站不绕行** |
| 纯 SPA，静态拿不到正文 | 1 | 已换成静态可抓的报告页 |
| robots.txt 无响应 | 1 | 已换成研报页 |
| 源码 GB2312，按 UTF-8 解码乱码 | 1 | 换成同站 UTF-8 的 detail 页 |

**替换后的核对结论**：34 条中 **33 条直抓即得正文**（≥300 字），
剩 `qcc.com` / `tianyancha.com` 两条是**反爬登录墙**（正文 1 千余字但拿不到企业数据），
需改用新闻报道里的企业数量口径，或后续上浏览器方案。

> 注意三件事：① `tjnjw.com` 是第三方聚合，正式引用须回溯官方年鉴；
> ② 多个政府站 robots.txt 返回 404（视为无限制），**页面本身可以正常抓**；
> ③ 采集前仍要先过 robots（本模块就是干这个的）。

**上一轮全量实测**（2026-10-19，34 条种子基线，产物已清空、可重跑）：

| 环节 | 结果 |
|---|---|
| 抓取 | 34/34 成功，快照 34 个（4.63 MB），编码 utf-8×32 / gbk×1 / gb18030×1 |
| 抽取 | 34 篇全部抽出标题与正文；正文字数 最短 1047 / 中位 2988 / 最长 12596 |
| 去重 | 转载重复 **0 篇**（全文指纹相同才判重；跨站导航不同会漏，见 §8 坑表） |
| 数字事实 | **295 条**（指标 产值/营收 60、企业/门店数 45、种养面积 43、增速 36…），每条带原句 |
| 缺正文 | **1 篇**：`ymt.com` 是反爬 JS 页（返回 setCookie 脚本），静态抓不到正文 |
| 可用率 | 295 条里人工初筛仅 **约 14% 可用**（量纲错、指标归错、无年份、非酸汤主题）—— 这就是第 2 层要解决的 |

**上一轮冒烟实测**（2026-10-19，3 条种子）：抓取 → 3 篇文章 9551 字 → 38 条事实，
`render.mode` 全部判 `static`，`data\` 测试零污染。

**本轮实测**（2026-10-20，v1.3 六个新模块接入后；`data\` 可重跑）：

| 环节 | 结果 |
|---|---|
| 链接发现 | `crawl https://tjj.guiyang.gov.cn/ --discover --depth 1 --max-urls 12` → 1 个种子页发现 **11 条详情页**，**12/12 ok**，账本 done 12/12 |
| 增量 | `--incremental` → **fresh 1 条、零正文请求**（账本 demo_incr：skipped 1 / total 1） |
| 断点续跑 | `resume j_resume` → 只补账本里 pending 的 1 条，收口 done 2/2 |
| 站点策略 | `policy --from-profile --write` → 落 **3 条**（cinic.org.cn / finance.sina.com.cn / gzstv.com） |
| 正文容器定位 | 13 条真实记录去重后的 11 份快照：`gzstv.com` 2212→**1334** 字（压掉 39.7%）、`www.guizhou.gov.cn` 2700→**595** 字（压掉 78.0%）；10 篇判 article、1 篇首页判 listing，正文都在 |
| 版式表过滤 | 上一批 `data\normalized\tables` 里的 **7 张表逐张复核：7/7 判版式表（0 张真数据表）**（tjnjw 5 + 杭州网 2）；全量重跑又挡掉 **13 张**版式表 |
| 跨 URL 去重 | 13 条真实记录（11 个 URL）→ **只有 2 组重复**，两组都是同 URL 重抓、`content_sha256` 相同；跨站最高相似度 **0.0544**（qdn.gov.cn vs guizhou.gov.cn）；旧 `is_reprint` 标了 5 条 true，**逐条核对全是误报** |
| 宽表 | 全量重跑**首次产出 1 张宽表**（`www.nianjiandata.com_c731b114da_t00.wide.csv`）；上一批 7 张版式表没有可转宽表 |
| 全量重跑 | 141 条种子跑完（账本 `full141`）：**131 ok / 3 failed / 7 skipped**；规范化正文 143 份，版式表过滤累计挡掉 13 张 |
| sitemap | `--sitemap` 只做小样：账本 demo_sitemap total 51 / 已跑 3、48 pending → **未全量实跑**，可 `resume` 续跑 |
| 测试 | **522 项 / 14 个测试文件全绿**；`main.py --self-test` **25 个模块全绿** |

### 已知数据缺口（调研的独特性所在）

这几块公开数据最弱，是阶段一最该自己补的：

1. **木姜子（山苍子）** 的种植面积与收购价 —— 酸汤风味的核心原料
2. **预包装酸汤鱼**的线上销售额 —— 目前只有"SKU 增速"这类间接指标
3. **酸汤鱼客单价 / 复购率** —— 需美团数据或实地调研
4. **上游原料在终端成本中的占比** —— 决定利润分配，通常只能靠企业访谈

---

## 5. 环境

```powershell
# 激活虚拟环境
.\myvenv\Scripts\Activate.ps1

# 🔴 本机装包必须先绕开系统代理（否则 pip 报 check_hostname requires server_hostname）
$env:NO_PROXY="*"; $env:no_proxy="*"
.\myvenv\Scripts\python.exe -m pip install --proxy "" -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirement.txt
# 浏览器档还要下二进制（约 150 MB，网络抖动就重试）
.\myvenv\Scripts\python.exe -m patchright install chromium

# 纯逻辑测试（零网络、零真实数据写入；14 个文件 522 项）
.\myvenv\Scripts\python.exe -m unittest discover -s tests -v

# 全部模块自检（零网络，25 个模块）
.\myvenv\Scripts\python.exe src\main.py --self-test

# ─────────────── 主路径：入口层一条命令串起来 ───────────────
# 判定层：逐个 URL 探测 → 落 data\profile\site_profile.json → 汇总该用哪个工具
.\myvenv\Scripts\python.exe src\main.py probe                 # 全量种子
.\myvenv\Scripts\python.exe src\main.py probe --limit 3        # 先试水 3 条

# 看画像会被分到哪个档（可用 / 缺哪个包）
.\myvenv\Scripts\python.exe src\main.py plan

# 只查合规 + 页面类型（不落盘，默认 3 条试水）
.\myvenv\Scripts\python.exe src\main.py check --limit 3

# ① 采集（契约主路径）：判定 → 选档 → 并发抓取 → 正文定位 → 规范化 → 去重 → 账本
.\myvenv\Scripts\python.exe src\main.py crawl --limit 3              # 试水 3 条
.\myvenv\Scripts\python.exe src\main.py crawl                        # 全量 141 条
.\myvenv\Scripts\python.exe src\main.py crawl --limit 3 --allow-tls  # 允许 TLS 伪装档
.\myvenv\Scripts\python.exe src\main.py crawl --limit 3 --allow-render --allow-tls

# v1.3 采集开关：并发 / BFS 发现 / 增量 / 断点 / 去重 / 账本
.\myvenv\Scripts\python.exe src\main.py crawl "https://tjj.guiyang.gov.cn/" --discover --depth 1 --max-urls 12
.\myvenv\Scripts\python.exe src\main.py crawl --limit 20 --incremental --recheck-days 30
.\myvenv\Scripts\python.exe src\main.py crawl --limit 3 --sitemap
.\myvenv\Scripts\python.exe src\main.py crawl --limit 3 --no-dedupe --no-job --no-proxy
.\myvenv\Scripts\python.exe src\main.py crawl --force                # 忽略增量，全部重抓

# 只做链接发现：种子页 → 候选池（不抓正文）
.\myvenv\Scripts\python.exe src\main.py expand --limit 5
.\myvenv\Scripts\python.exe src\main.py expand "https://tjj.guiyang.gov.cn/" --max-urls 20

# 任务账本（状态 / 进度 / 各条结果）与断点续跑
.\myvenv\Scripts\python.exe src\main.py job                          # 列出全部任务
.\myvenv\Scripts\python.exe src\main.py job j_resume                 # 看单个任务
.\myvenv\Scripts\python.exe src\main.py resume j_resume --workers 4  # 只补 pending

# 回读一条材料（正文 + 表格），record_id 从 pages.json 或 crawl 输出里拿
.\myvenv\Scripts\python.exe src\main.py material <record_id>

# 站点访问策略（人工声明，AI 不覆盖）
.\myvenv\Scripts\python.exe src\main.py policy
.\myvenv\Scripts\python.exe src\main.py policy tjj.guiyang.gov.cn
.\myvenv\Scripts\python.exe src\main.py policy --from-profile            # 只预览建议
.\myvenv\Scripts\python.exe src\main.py policy --from-profile --write    # 写入（人工值不覆盖）
.\myvenv\Scripts\python.exe src\main.py policy --from-profile --write --overwrite  # 连人工值也覆盖（危险）

# ②③ 解析（旧链路，待退休）：清单 → 文章 → 数字事实（全离线）
.\myvenv\Scripts\python.exe src\main.py parse

# 体检：现在手里有什么、各环节卡在哪
.\myvenv\Scripts\python.exe src\main.py status

# ─────────────── 单模块（调试时用，一般不需要） ───────────────
.\myvenv\Scripts\python.exe src\webcrawl\page.py                    # 工具自检（零网络）
.\myvenv\Scripts\python.exe src\extract\Extract_Article.py           # 只重跑文章抽取
.\myvenv\Scripts\python.exe src\extract\Extract_Facts.py             # 只重跑事实抽取
```

> 🔴 **`src\main.py` 是唯一调用源**（16 个子命令：`probe` / `plan` / `check` / `crawl` /
> `expand` / `job` / `resume` / `material` / `policy` / `parse` / `status`）。原先
> `Robots_Detect.py` / `Crawl_Page.py` 各自带一套命令行，现已全部并入 `main.py`：
> `check` 子命令管 robots 与页面类型（`--render-only` 只看静态/动态，`--no-render` 不探测页面），
> `crawl` 子命令管抓取与账本，`expand` 只做链接发现，`job` / `resume` 管账本与续跑。
> 工具层 `webcrawl\` 里不打印任何东西，所以它们只保留自检入口，没有 CLI。

> ⚠️ 本机 `PATH` 里的 `python` 是 Microsoft Store 空壳（执行报 9009）。
> **必须用 `myvenv` 里的解释器**或写全路径，不要依赖裸 `python`。

---

## 6. 工作流程与提交规范

**核心循环**（每个任务都走一遍）：

```
① 先说明 ──▶ ② 执行 ──▶ ③ 同步文档 ──▶ ④ git commit ──▶ ⑤ 停
```

| 步骤 | 要求 |
|---|---|
| ① 先说明 | 说清目标、要改哪些文件、是否触发报备条款；**禁止不打招呼就大动作** |
| ② 执行 | 发现新情况随时插播 |
| ③ 同步文档 | 先改代码后补文档 = 返工最大来源；README 进度表必须同步 |
| ④ commit | **中英双语，中文在前**，一个逻辑单元一次提交（格式见下） |
| ⑤ 停 | 🔴 **push 只由本人执行**，AI 需要 push 必须先征得同意 |

**提交信息格式**（推荐段落式，标题 ≤72 字符）：

```text
功能：一句话标题

- 中文要点一
- 中文要点二
- 验证方式 / 同步了哪些文档

Feature: one-line title

- EN bullet 1
- EN bullet 2
- How verified / which docs updated
```

**类型前缀**：`文档：` `规范：` `功能：` `修复：` `整理：` `重构：`

---

## 7. 协作规范索引

本仓库遵循以下规范。**本地目录为权威副本**，云端 [`aaf12329/CoMind`](https://github.com/aaf12329/CoMind)
是同一套规范的托管（2026-10-19 核对：`项目接手手册模板`、`工程架构规范`、`提交与协作规范`、
`Python 代码规范` 四份**内容与云端 main 一致**，仅换行符不同）：

| 规范 | 路径 |
|---|---|
| 工程架构（分层 / 解耦 / 路径地址簿） | `CoMind\01_AI项目协作参考\代码及路径的规范\通用规范\工程架构规范.md` |
| 提交与协作（提交格式 / 检查清单 / Git 分工） | `…\通用规范\提交与协作规范.md` |
| Python 语言规范 | `…\语言规范\Python\代码规范.md` |
| 项目接手手册模板 | `CoMind\…\多Agent合作项目及项目接手手册\项目接手手册_模板与写法.md` |

**本仓库的实现约定**：

- 分层：入口层（`main` / 命令行）→ 功能层（采集、清洗、建模模块）→ 共用层（路径地址簿、I/O、日志）
- 🔴 **功能层模块之间零 import**；共享逻辑下沉到共用层
- 🔴 所有文件路径从**路径地址簿**取，禁止各文件自己拼路径
- 🔴 I/O 单一入口：文本读写一个函数、结构化数据一个函数，不开第三处
- 🔴 密钥只进 `.env`，严禁硬编码
- 🔴 自测不碰真实数据；涉网络的自测用假响应，零花费

---

## 8. 已固化的坑

| 坑 | 对策 |
|---|---|
| 爬虫把 robots.txt 的 Disallow 当摆设 | 先跑 `src\main.py check <URL>` 再决定采不采；不确定的直接放弃 |
| 站点改版导致选择器全废 | 每站的选择器集中在一处，别散落在循环里 |
| 采集结果不可复现 | 每次抓取记录：时间戳、URL、HTTP 状态、原始 HTML 快照 |
| `python` 指向 Store 空壳 | 一律用 `myvenv\Scripts\python.exe` |
| 数据划分按"行"切，指标虚高 | **划分维度 = 防泄漏维度**（按时间/按地区/按站点，不是按行） |
| 训练集混入测试集同一条信息的转载 | 先去重（同源转载、镜像站），再做划分 |
| 二手数据口径混用 | 每个数字标注「口径 + 地域 + 年度」；产值 ≠ 销售额 ≠ 零售额 |
| 管道收尾吞报错 | 后台命令少用管道收尾，跑完**验证产物存在**再报成功 |
| 控制台 GBK：`print` 中文正常，但 `✓`/emoji 直接抛 `UnicodeEncodeError`，经管道还乱码 | `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，输出只用 GBK 可编字符 |
| 种子 URL 静默失效 | 逐个抓 robots.txt 时顺带核对，404 与取不到的**单独列出来**及时换链接（首轮就抓到 2 条死链） |
| 中文站是 GB2312/GBK，按 UTF-8 解码成乱码 | 按响应头 `charset` → `<meta charset>` → utf-8 → gb18030 顺序解码；`net.fetch` 已内置，返回 `encoding` 字段 |
| robots.txt 返回 404 被当成"抓取失败" | 404/410 = **站点没有 robots.txt**，按 RFC 9309 视为无限制；真失败（超时/5xx）才算 `error` —— 这个 bug 真踩过，一批站被误放弃 |
| 正文抽取把导航 / 页脚 / 推荐位一起抓进来 | 已接 `boilerplate.find_main`：只取正文容器，找不到或正文 <40 字时回退原文；实测 11 份真实快照 gzstv 2212→1334（-39.7%）、guizhou.gov.cn 2700→595（-78.0%） |
| 跨站转载用全文指纹会漏判 | 已改为**正文容器**的 simhash + shingle Jaccard；本轮 13 条真实记录仍只有 2 组重复（都是同 URL 重抓、sha256 相同），跨站最高相似度 0.0544 → **跨站转载尚未命中，别当成已解决** |
| 站点无视请求头照样返回 gzip，正文被当文本解码成乱码 | `net.fetch` 先按 `content-encoding` 解 gzip/deflate，**再**按 charset 解码（`ymt.com` 踩过） |
| 快照存"解码后的文本"会丢信息 | 快照一律存**解压后的原始字节**（`io_utils.write_bytes`），文本解码可能是有损的 |
| 用 `\| Select-Object -First N` 截断输出，子进程被提前掐断，产物没写进去 | 跑「会落盘」的脚本时**不要截断管道**；先重定向到文件，再看文件 |
| argparse 的 `--xxx-dir` 默认值改了函数默认却没改，产物落错目录 | 默认值只写一处（从地址簿取常量）；用测试断言产物路径必须在对应层 |
| 导航栏文字被当数字事实 | 抽事实前按「短词占比」过滤导航行（`首页 新闻 产经要闻…` 这类） |
| **版式表格被当成数据表**（`首页,|,原创,|,杭州新闻,…`） | `table_ops.is_layout_table` 在落盘前挡掉；上一批 7 张「表」**7/7 判版式表（0 张真数据表）**，全量重跑又挡掉 13 张，别再手写第二套过滤 |
| 段落粒度太粗（整页只有 1 段 1485 字） | 正文无句末标点时按分隔符**硬切**到 ≤300 字，否则"第几段"没法当证据坐标 |
| robots `crawl-delay` 被 `min(wait, per_host_delay*2)` 截断 | 旧 `api.crawl` 会把站点声明的大间隔截成 3 秒；改由 `ratelimit.effective_interval` 取 max、**不截断**（集成测试用 `crawl_delay=10` 钉住） |
| 并发抓取把同一主机打出多个请求 | `ratelimit.run_parallel`：同 host 加锁 + 限速、不同 host 并行；结果按输入顺序回填，单条异常不拖垮整批 |
| 增量 / 续跑靠"跑没跑过"猜 | 认 `data\jobs\` 账本：`--incremental` 只跳 fresh（30 天），`resume` 只补 pending；实测 fresh 1 条零请求、resume 补 1 条 |
| `record.py` 只留 3 个 meta 键，`depth` / `duplicate_of` / `tables_dropped` 被静默丢弃 | `_build_meta` 改为**骨架 + 扩展键原样保留**，证据链字段必须能落进 `CrawlResult` |
| `incremental=True` 且 `save_job=False` 时增量静默失效（照旧重抓） | 增量判断改读 `pages.json`，与账本开关解耦；`test_api_pipeline` 用打桩集成测试钉住 |
| `main.py status` 的"数据表 N 张"数的是 `tables\` 目录文件数，不代表判定为数据表 | 以 `table_ops.is_layout_table` 为准；`tables_dropped` 才是挡掉的张数（全量重跑已挡 13 张） |
| **pip 装包报 `check_hostname requires server_hostname`** | 系统代理 `127.0.0.1:7897` + 老版 pip 的坑。装包前 `$env:NO_PROXY="*"`，并给 pip 加 `--proxy ""` |
| 无头浏览器比静态抓慢一个数量级 | 实测 3.3 s vs 0.16 s（约 20 倍）。浏览器档**默认关**，只对判定为 `dynamic` 的站开 |
| 扫描版 PDF 抽不出字却被当成功 | `pdf.parse` 检测文本层为空 → 报 `E_PDF_NO_TEXT`，绝不假装解析成功 |

---

## 9. 进度表

| 阶段 | 任务 | 状态 |
|---|---|:-:|
| 准备 | 产业资料检索与索引（34 条） | ✅ 2026-10-18 |
| 准备 | README / AGENTS.md 建立 | ✅ 2026-10-19 |
| 准备 | 分层架构 + 路径地址簿 | ✅ 2026-10-19 |
| 准备 | 种子扩充 34 → **91 条**（逐条实测可抓） | ✅ 2026-10-19 |
| 准备 | 种子扩充 91 → **141 条**（新增贵阳餐饮方向 50 条，全部实测） | ✅ 2026-10-20 |
| 准备 | **代码体系重构**（共用层下沉、特批废除、加入口层） | ✅ 2026-10-19 |
| 准备 | 纯逻辑测试（`tests\`，**67 项**，零网络） | ✅ 2026-10-19 |
| 判定 | 判定层 / 选档层测试（**+18 项**，当时共 85 项，零网络） | ✅ 2026-10-20 |
| 采集 | robots.txt 抓取 + 禁止清单判定 | ✅ 2026-10-19 |
| 采集 | 抓正文 + 原始快照落盘 + 抓取清单 | ✅ 2026-10-19 |
| **采集** | **全链路跑通**（robots → 抓取 → 抽文本 → 抽事实） | ✅ 2026-10-19 |
| **采集** | **全量 141 条重跑**（账本 full141：131 ok / 3 failed / 7 skipped） | ✅ 2026-10-20 |
| **采集** | **v1.3 六模块接入**：boilerplate / discover / ratelimit / job / dedupe / table_ops | ✅ 2026-10-20 |
| **采集** | **并发限速**（ratelimit：同 host 串行 / 不同 host 并行 / crawl-delay 不截断） | ✅ 2026-10-20 |
| **采集** | **链接发现**（discover：1 个种子页 → 11 条详情页，12/12 ok） | ✅ 2026-10-20 |
| **采集** | **任务账本 + 增量 + 断点续跑**（job / resume：fresh 1 零请求、补 pending 1） | ✅ 2026-10-20 |
| **采集** | **跨 URL 去重**（dedupe：13 条真实记录 2 组同 URL 重复；跨站最高 0.0544） | ✅ 2026-10-20 |
| **采集** | **proxies 透传**（`--no-proxy` → `proxies={}` 直到抓取档） | ✅ 2026-10-20 |
| **修复** | `record.meta` 扩展键被静默丢弃；`incremental + save_job=False` 增量失效 | ✅ 2026-10-20 |
| 采集 | 判断静态站 / 动态站（`render.mode`，robots 判定同趟完成） | ✅ 2026-10-19 |
| 采集 | PDF 分流（存 `data\raw\files\`，本阶段不解析） | ✅ 2026-10-19 |
| 采集 | 去重（同源转载、镜像站） | ✅ 2026-10-19（旧全文指纹；2026-10-20 由 `dedupe` 取代） |
| **判定** | **判定层**：robots + 内容探测 → 8 选 1 verdict，落 `site_profile.json` | ✅ 2026-10-20 |
| **判定** | **选档层**：verdict → 工具（7 档）+ 降级链 | ✅ 2026-10-20 |
| **判定** | 需渲染 / 需伪装的档位真正落地（`patchright` / `curl_cffi` 已装并实测） | ✅ 2026-10-20 |
| **采集** | **按画像执行抓取**（`api.crawl` 主链路：判定 → 选档 → 抓取 → 规范化 → 落盘） | ✅ 2026-10-20 |
| **规范化** | 正文 + **段落锚点**（段号 / 字符偏移 / 原文，JSON Lines） | ✅ 2026-10-20 |
| **规范化** | 表格三件套（原始 html / 结构化 json / 展平 csv，保合并单元格） | ✅ 2026-10-20 |
| **规范化** | PDF 文本层 + **页码映射**（纯标准库；扫描件如实报 `E_PDF_NO_TEXT`） | ✅ 2026-10-20 |
| **来源** | 发布主体 / 发布时间 / canonical / 转载判定（抽不到即 null，不猜） | ✅ 2026-10-20 |
| **接口** | 契约 v1.2 / v1.3 对外函数（probe / plan / crawl / expand / resume_job / job_progress / read_material / site_policy / write_site_policy） | ✅ 2026-10-20 |
| **规范化** | **正文容器定位**（boilerplate；11 份真实快照实测） | ✅ 2026-10-20 |
| **规范化** | **版式表过滤**（上一批 7 张「表」7/7 判版式表、0 张真数据表；全量重跑又挡 13 张） | ✅ 2026-10-20 |
| **规范化** | **长表 → 宽表**（指标 × 年份；全量重跑首次产出 1 张真实宽表） | ✅ 2026-10-20 |
| **测试** | **522 项 / 14 个测试文件**（旧采集 408 项 + 新研究链 114 项），全绿 | ✅ 2026-10-20 |
| **测试** | 自测：29 个文件带 `_self_test`，`main.py --self-test` 覆盖 25 个模块 | ✅ 2026-10-20 |
| 规范化 | `is_reprint` 判定偏松，需抽样核 | ✅ 2026-10-20 已修（141 条实测 47.3% → 14.5%，判不准给 `null`；去重本就不依赖它） |
| 采集 | 旧 `page.py` / `extract/` 退休，职责并入新链路 | ⬜ |
| 采集 | `--sitemap` 全量实跑（目前 demo_sitemap 51 条只跑 3 条） | ⬜ 小样 |
| 整理 | 基础字段抽取（站点/标题/正文/字数/重复标记） | ✅ 2026-10-19 |
| 整理 | 数字事实抽取（295 条，带原句证据） | ✅ 2026-10-19 |
| 整理 | 事实校验规则、冲突标记、独立审核通道 | ✅ 2026-10-20；人工口径复核待做 |
| 整理 | 清洗与入库 | CSV 规范化完成；数据库未接 |
| 整理 | 结构化指标表（指标 × 主体 × 地域 × 年份 × 单位 × 来源） | ✅ 代码完成；审核后才填入 |
| 建模 | 标签体系确定 | 年度单指标条件预测已定义；实际业务序列待数据 |
| 建模 | 历史可见滞后值、变化、观测滞后特征 | ✅ 2026-10-20 |
| 建模 | XGBoost 训练与滚动回测、最后值基线比较 | ✅ 程序及合成验收；真实数据不足；CatBoost 未实现 |
| 交付 | 指标报告与结论 | ✅ 运行与缺口报告；未输出真实趋势结论 |

---

## 10. 当前不做的事

- **代码按需增量**：当前做到「判定 → 选档 → 三档抓取 → 并发限速 → 链接发现 → 正文定位 →
  规范化（正文/段落/表格/宽表/PDF）→ 去重 → 账本（增量/续跑）→ 落盘」；
  本次已授权独立研究分析；数据库入库、网页接线与项目归并尚未实施
- 不做未被要求的扩功能（曾因擅自加"静态/动态判别 + 分流"被退回过一次）
- 不预设阶段二的标签与评估指标
- 不采集需要登录、付费或明确禁止爬取的内容
- 本阶段不做**定时调度 / 分布式 worker**：增量与断点续跑只靠本地 `data\jobs\` JSON 账本
- **不做 OCR** —— 扫描版 PDF 只如实标记 `E_PDF_NO_TEXT`，不假装解析
- **不在落盘路径上做 URL 归一化**：`url_norm` 目前只当 `discover` 的候选去重键；
  快照文件名仍由**原始 URL 哈希**决定（同一 URL 覆盖同一文件）

---

*最后更新：2026-10-20*
