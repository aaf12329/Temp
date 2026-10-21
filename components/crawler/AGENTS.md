> 2026-10-21 集成更新：本副本现由主平台统一环境 .venv 调用，隔离工作区与模型位置可注入。以下独立项目旧路径与“不归并”描述保留作历史；当前接线、授权与执行边界以根 AGENTS.md 和 README.md 为准，历史子代理授权不延续。

# AI 工作手册 / AI Operating Manual —— 凯里酸汤鱼产业调查（Crawler）

> **本文档写给在本项目工作的 AI 助手**（任何 AI）。
> 目标：任何 AI 读完即可按同样的流程与规矩接手工作，**不重新探索、不重复踩坑、不越权**。
> 人类所有者：项目发起人。更新：2026-10-20
>
> **标准来源**：骨架按 `CoMind\01_AI项目协作参考\多Agent合作项目及项目接手手册\项目接手手册_模板与写法.md`
> **v2.1 §2** 的六节填空；条文引用同目录 `代码及路径的规范\` 下的规范文件。
> 云端同一套规范：<https://github.com/aaf12329/CoMind>（2026-10-19 核对：四份关键规范内容一致）。
> 本文件只写**项目落地口径**，规范原文不复述——冲突时**以规范原文为准**。

---

## 2026-10-20 当前研究分析交付

追加范围：所有者提出 WGAN_Trainer 后，已克隆并独立验证 CPU 增强；详见 `docs/WGAN数据增强评估.md`。合成夹具与研究数据隔离，当前不把生成样本作为真实证据；不修改研究 Python API 合同。

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

## 0. 项目事实（不要重新探索，直接采信）

| 项 | 值 |
|---|---|
| 仓库 | `../Crawler`；分支 `main`，remote `origin` → `github.com/aaf12329/Crawler.git`；🔴 **push 只由所有者执行** |
| 资料类型与规范 | **工程项目**（非学习资料，故不套用《学习资料代码规范》）；规范根目录 `../CoMind/01_AI项目协作参考`，条文见 §5 |
| 项目性质 | **不是"一个爬虫"**：最终目标是在**没有官方统计口径**的产业上，造出可复现、可被质疑、可被复核的产业判断力。爬虫与模型只是手段。四层野心见 `README.md` §0 |
| 最硬的事实 | 🔴 **官方统计里没有"酸汤鱼"这个类目**（GDP 行业分类最细到餐饮业，无菜系/单品）。所以"爬得更多"不是瓶颈，**自建口径**才是。别把精力全押在多加种子 |
| 服务对象 | 独立项目，仅供所有者本人的调研与比赛/课程用途；无上游依赖，也无下游项目依赖它 |
| 环境 | `myvenv\Scripts\python.exe` = **Python 3.10.0**；依赖见 `requirement.txt`（**已不是零依赖**：`curl_cffi` + `patchright`）。**不在**系统 PATH 里——裸 `python` 是 Microsoft Store 空壳。🔴 装包前必须 `$env:NO_PROXY="*"`，否则系统代理会让 pip 报 `check_hostname requires server_hostname` |
| 代码结构 | 工具包 `src\webcrawl\`：**对外 API `api.py`** + 判定 `probe` + 选档 `route` + 三档抓取 `net`/`fetch_tls`/`fetch_browser` + 并发限速 `ratelimit` + 任务账本 `job` + 链接发现 `discover` + 跨 URL 去重 `dedupe` + 规范化 `normalize`/`boilerplate`/`table_ops`/`pdf` + 记录 `record`/`store`/`source_meta`/`policy`/`errors` + 共用工具 `web_check`/`io_utils`/`html_utils`/`console`/`url_norm` + 旧抓取路径 `page`（待退休）；路径地址簿 `src\Path_Store\` + 业务层 `src\extract\`（待移出）+ 入口层 `src\main.py`（**CLI 调用入口；研究由 research_api 编排**：16 个子命令）；测试 `tests\` 14 个文件 / 522 项；**仓库根目录不放 `.py`** |
| 数据目录 | `sites\`（**输入**：141 条纯 URL）；`data\profile\`（判定画像）/ `data\policy\`（站点策略）/ `data\raw\`（**原始字节**：`html\` + `files\` + `pages.json`）/ `data\normalized\`（**给下游消费**：`text\` 正文 + `para\` 段落锚点 + `pdf\` + `tables\` 三件套 + `wide\` 宽表）/ `data\jobs\`（任务账本，一 job 一 JSON）/ `data\discover\`（链接候选池 `url_pool.json`）/ `data\parsed\`（旧抽取产物）；整个 `data\` 已 gitignore |
| 硬件 | 无 GPU 需求（阶段一爬虫 + 阶段二树模型，CPU 足够） |
| 交付物 | 结构化产业数据表 + 特征表 + 模型脚本 + 指标报告；README 同步 |
| 详细文档 | `README.md`（总览、目录树、进度表，**唯一权威来源**）；本文件（工作手册）；根目录 `凯里酸汤鱼_市场与产业链数据源.md`（带说明的数据源索引） |
| 当前禁区 | 🔴 **代码按需增量**：已实现 判定 / 选档 / 三档抓取 / 规范化（正文+段落+表格+PDF）/ 正文容器定位 / 版式表过滤 / 宽表 / 并发限速 / 任务账本+增量+断点续跑 / 链接发现 / 跨 URL 去重 / 站点策略；**本轮已批准研究分析；人工审核待做，数据库/三个项目归并不做**；`CoMind` 两份参考均**只读** |

**代码地图**（`src\` 下 37 个非空 `.py` 文件 12734 行 = 地址簿 1 + 工具层 25 + 旧业务层 2 + 研究包 7 + 入口层 2；测试 `tests\` 14 个文件 5678 行 / 522 项，全部零网络）

| 文件 | 行数 | 层 | 干什么 |
|---|---:|---|---|
| `src\Path_Store\Path_Store.py` | 155 | 地址簿 | 全项目地址声明与纯路径推导，不读写文件、不 import 功能模块（新增 `JOBS_DIR` / `DISCOVER_DIR` / `URL_POOL_JSON` / `WIDE_DIR`） |
| `src\webcrawl\net.py` | 211 | 工具 | 唯一 HTTP 出口 `fetch()`：解 gzip、按 charset 解码、只重试暂时性错误 + 限速基元；**`proxies` 参数**支持「环境代理 / 不走代理 / 指定代理」 |
| `src\webcrawl\io_utils.py` | 163 | 工具 | 唯一落盘出口：文本 / 字节 / JSON / CSV / URL 清单，原子写 + 文件名生成 + 哈希 |
| `src\webcrawl\html_utils.py` | 106 | 工具 | HTML → 可见文本（跳过 script/style/noscript/template/**head**）+ `<title>` |
| `src\webcrawl\web_check.py` | 506 | 工具 | 合规判定（robots 解析 + `check_robots` + 禁止清单）与页面类型（静态 / JS 动态 / 未知） |
| `src\webcrawl\console.py` | 56 | 工具 | 终端 UTF-8、对齐打印 |
| `src\webcrawl\url_norm.py` | 61 | 工具 | URL 归一化（砍 `#`、协议统一 https、域名小写）——**现被 `discover` 用作去重键**，不再"建了不调用" |
| `src\webcrawl\errors.py` | 176 | 工具 | 20 个封闭错误码 + 可重试表，错误映射唯一出处 |
| `src\webcrawl\page.py` | 228 | 旧路径 | 旧抓取路径（已被 `api.crawl` 取代，保留待退休） |
| `src\webcrawl\probe.py` | 755 | 判定 | 逐 URL 探测 → 8 选 1 `verdict`，落 `site_profile.json` |
| `src\webcrawl\route.py` | 332 | 选档 | `verdict` → 抓取工具（7 档）+ 不可用降级链 |
| `src\webcrawl\fetch_tls.py` | 182 | 抓取档 | `curl_cffi` 的 TLS 指纹伪装（治 403 / CF） |
| `src\webcrawl\fetch_browser.py` | 208 | 抓取档 | `patchright` 真浏览器（治 JS 动态站） |
| `src\webcrawl\ratelimit.py` | 352 | 抓取档 | 并发限速：同 host 串行、不同 host 并行；robots `crawl-delay` **取最大不截断** |
| `src\webcrawl\job.py` | 591 | 抓取档 | 任务状态机（7 态）+ 断点落盘 + 增量计划（fresh / 过期重抓） |
| `src\webcrawl\boilerplate.py` | 625 | 规范化 | **正文容器定位**：删导航/页脚/侧栏/推荐位/评论/分享，候选打分；找不到就回退原文 |
| `src\webcrawl\normalize.py` | 369 | 规范化 | 正文 + 段落锚点 + 表格三件套；接 `boilerplate` 正文容器、`table_ops` 版式表过滤与宽表 |
| `src\webcrawl\table_ops.py` | 886 | 规范化 | 表格算子：**版式表过滤** / 多行表头拼平 / 指标×年份长表转宽表 / CSV 转义 |
| `src\webcrawl\pdf.py` | 186 | 规范化 | PDF 文本层 + 页码映射（纯标准库；扫描件如实报 `E_PDF_NO_TEXT`） |
| `src\webcrawl\discover.py` | 676 | 发现 | 链接发现：详情页 / 翻页 / 附件 / sitemap / robots → BFS 候选池（去重 / 限深 / 限数） |
| `src\webcrawl\dedupe.py` | 619 | 去重 | 跨 URL 去重：`content_sha256` 直接判重 + 正文 shingle Jaccard / simhash；只打 `duplicate_of`，不删记录 |
| `src\webcrawl\api.py` | 910 | **对外 API** | 契约唯一实现入口：`probe` / `plan` / `crawl` / `expand` / `resume_job` / `job_progress` / `read_material` / `site_policy` / `write_site_policy` |
| `src\webcrawl\record.py` | 286 | 记录 | `CrawlResult` 构造（30 字段，uuid5 主键；`meta` 扩展键原样保留） |
| `src\webcrawl\store.py` | 224 | 落盘 | 原始材料 + 规范化产物（**含宽表**）落盘；`pages.json` 按 (job_id,url) upsert |
| `src\webcrawl\source_meta.py` | 470 | 来源 | 发布主体 / 发布时间 / canonical / 转载判定（抽不到即 null，不猜） |
| `src\webcrawl\policy.py` | 190 | 策略 | 站点访问策略（人工值不被自动覆盖）+ 从画像批量写策略 |
| `src\extract\Extract_Article.py` | 275 | 业务 | 快照 → 标题 / 正文 / 字数 / 指纹去重 → 文章表（旧链路，待退休） |
| `src\extract\Extract_Facts.py` | 311 | 业务 | 文章 → 数字事实（指标/数值/单位/年份/地域 + 原句证据；旧链路） |
| `src\main.py` | 826 | **入口** | **CLI 调用入口；研究由 research_api 编排**：`probe` / `plan` / `check` / `crawl` / `expand` / `job` / `resume` / `material` / `policy` / `parse` / `status` 十一个子命令 + 全部终端显示 |

以上行数为 2026-10-20 实测；**29 个文件带 `_self_test()`**，`main.py --self-test` 一次串起其中 **25 个模块**（`fetch_tls` / `fetch_browser` / `errors` 的自测未纳入这 25 个）。新增研究模块行数见上方当前交付表。改代码请同步本表。

---

## 1. 核心工作循环 ★（每个任务都必须完整走一遍）

**① 先说明 → ② 执行 → ③ 同步文档 → ④ git commit → ⑤ 停（等所有者 push）**

1. **先说明你要干什么**：目标、要改/建哪些文件、是否触发 §3 报备条款。
   🔴 禁止不打招呼就开始大动作。
2. **执行**：过程中发现新情况（报错、方案要改、要下载大文件）随时插播说明。
3. **同步文档**：按 §5.5 的提交前检查清单更新 `README.md` 的**目录树 / 进度表 / 坑表**——
   **代码先行、文档滞后是返工的最大来源**。
4. **git commit**：中英双语（**中文在前**），一个逻辑单元一次提交；格式见 §6。
5. **停**：🔴 **push 只由所有者执行**，AI 需要 push 必须先征得同意。

---

## 2. 数据/素材查找路径（需要新数据时按此顺序找）

1. **先看 `sites\`**：已有 **141 条**种子 URL（市场 36 / 上游 20 / 其他数据 35 / 贵阳餐饮 50），
   已按产业链环节分好，**不要重复去找已知的站**；
2. **再看根目录 `凯里酸汤鱼_市场与产业链数据源.md`**：带说明的完整版索引，
   含每个站能拿什么、以及已知的**数据缺口**（木姜子 / 线上销售额 / 客单价 / 成本占比）；
3. 需要新来源时优先级：
   **官方统计（省/州统计局）> 政府产业专栏 > 行业报告库 > 电商数据工具 > 新闻稿 > 百科**；
4. 🔴 **百科类只能搭框架，不能当数据源引用**；引用前必须回溯到它引用的原始出处；
5. 二手数字一律标注「**口径 + 地域 + 年度**」——产值 ≠ 销售额 ≠ 零售额。

---

## 3. 硬盘/大文件规则 ★

- **需要报备的操作**：单次写入或删除 **≥1GB**、跨盘移动、环境安装。报备 = 先说明再执行。
- 大动作**前后查系统盘剩余容量**：报备带数字，执行后复测记差额。
- 爬取的原始 HTML / 截图属**易膨胀数据**：落盘前先定目录与命名规则，登记在 README 目录树里；
  大数据放**非系统盘**专用目录，变更登记台账（时间 / 盘 / 路径 / 大小 / 用途）。
- 不把大文件塞进仓库；必要时加 `.gitignore` 规则并说明（本项目现有规则：`myvenv/`、`__pycache__/`、`*.pyc`、`data/`）。
- 🔴 **删除**：项目文件夹之外的任何东西，先报备再说；删除一律**送回收站**，
  永不直接 `rm`、**永不清空回收站**（规范 §10）。

---

## 4. 领域 SOP

### 阶段一 · 采集（基础能力已实现；本轮新增独立研究分析，人工审核仍待做）

```
⚠️ 全部阶段由入口层 `src\main.py` 串起来（唯一允许横向调用的层）：
   probe / plan / check / crawl / expand / job / resume / material / policy / parse / status

① 合规 + 页面类型（`src\webcrawl\web_check.py` 的 check_robots）→ 不允许就放弃
   → 同一趟顺带判页面类型：`render.mode` = static / dynamic / unknown
② 采集主链路（`src\webcrawl\api.py` 的 crawl，契约 v1.2/v1.3）
   → 种子 + robots 闸门 → 并发抓取（ratelimit：同 host 串行、不同 host 并行，
     robots crawl-delay 取最大、不截断）
   → 可选 BFS 链接发现（discover：详情页 / 翻页 / 附件 / sitemap，depth 0..3、max_urls 封顶）
   → net.fetch 抓 HTML（自动解 gzip、按 charset 解码）；PDF 等非网页存 files\ 并标 ok_pdf
   → 原始字节存 data\raw\html\<站点>_<URL哈希10>.html（同一 URL 覆盖同一文件）
   → 正文容器定位（boilerplate.find_main：去导航 / 页脚 / 侧栏 / 推荐位）
   → 规范化（normalize）：正文 + 段落锚点 + 表格；table_ops 挡版式表、长表转宽表
   → 跨 URL 去重（dedupe）：content_sha256 相同直接判重，其余正文 simhash + Jaccard；
     只打 meta.duplicate_of，**不删任何记录**
   → 任务账本（job）：状态机 queued/running/paused/done/partial/failed/cancelled，
     每条落状态；可增量跳过、可 resume 断点续跑
   → 记录写进 data\raw\pages.json（按 (job_id,url) upsert）
③ 解析旧链路（`src\extract\Extract_Article.py` + `Extract_Facts.py`，待退休）
   → 快照 → 标题 / 正文 / 字数 / 指纹 → data\parsed\articles.*
   → 文章 → 指标 + 数值 + 单位 + 年份 + 地域 + 原句 → data\parsed\facts.*
④ 人工校验事实（数字可用性、口径）→ ⑤ 清洗入库
```

🔴 **`sites\` 是输入，`data\` 是产物**：`sites\` 放纯 URL（人/AI 找来的，进 git）；
`data\` 分几层——`raw\`（爬下来的：`html\` / `files\` / `pages.json`）、
`normalized\`（规范化产物：`text\` / `para\` / `pdf\` / `tables\` / `wide\`）、
`jobs\`（任务账本）、`discover\`（链接候选池）、`parsed\`（旧链路的 `articles.*` / `facts.*`），
整个 `data\` 已 gitignore。路径一律从 `src\Path_Store\Path_Store.py` 取，**不许在别处拼路径**。

🔴 **同一 URL 直接覆盖**：快照文件名只由 URL 决定，重抓覆盖同一文件、清单里同一 URL 只留一条。

🔴 **事实抽取仍需人工过一遍**：规则是关键词+正则，会抽到广告/页脚里的数字；
每条都带原句，核对时看 `sentence` 列。

🔴 **去重已升级、Cross-URL 仍未命中过真实转载**：`dedupe` 先看 `content_sha256`（相同直接判重），
再看**正文容器**的 shingle Jaccard / simhash（阈值 0.85）。本轮拿 13 条真实记录（11 个 URL）实测：
只有 **2 组重复，且两组都是同 URL 重抓（sha256 相同）**；跨站最高相似度仅 **0.0544**
（qdn.gov.cn vs guizhou.gov.cn）——也就是说跨站转载**还没有一次真正被测到**，别拿"有去重模块"当"跨站已解决"。
旧 `source_meta.is_reprint` 在这 13 条里标了 **5 条 true，逐条核对全是误报**；去重已不依赖它，
它只在 `choose_keeper` 里当排序参考。

🔴 **增量 / 断点靠账本，不靠猜**：`--incremental` 只跳过 fresh 记录（默认 30 天，
`--recheck-days` 可调），`--force` 全部重抓；`resume <job_id>` 只补账本里还 pending 的 URL。
本轮实测：`--incremental` fresh 1 条、**零正文请求**；`resume j_resume` 只补 pending 1 条后 done 2/2。

🔴 **`src\main.py` 是唯一调用源**：原先 `Robots_Detect.py` / `Crawl_Page.py` 各自带一套 CLI，
现已全部并入 `main.py`（16 个子命令）；工具层 `webcrawl\` 里**零 `print`**，只返回数据，
进度用回调交给入口层。

🔴 **范围纪律**：本阶段只做被明确要求的步骤。按本轮明确授权实现独立分析；人工复核、数据库与网页接线仍依当前范围推进（曾因擅自加"静态/动态判别 + 分流"被退回）。

### 阶段二 · 建模（数据到位后）

- 模型：**XGBoost**（基线 + 特征重要性）+ **CatBoost**（类别特征友好，适合地区/品类）
- 🔴 **划分维度 = 防泄漏维度**：按时间 / 按地区 / 按站点切分，**不是按行切**；
  同一篇内容的多处转载必须先合并，否则训练集与测试集互相泄漏。
- 🔴 **标签体系在数据到位后再定**，不为凑模型而扭曲调研结论。
- 评估要留**时间外样本**，避免用未来数据预测过去。

---

## 5. 代码标准 ★（动手前扫一眼）

> **规范原文不在这里复述**，只写本项目落地口径。
> 权威来源：<https://github.com/aaf12329/CoMind/tree/main/01_AI项目协作参考>
> （本地只读副本 `../CoMind/01_AI项目协作参考\`；冲突时**以规范原文为准**）

### 5.1 缩进 / 命名（机器可查，改完自己核一遍）

| 项 | 规则 | 现状 |
|---|---|---|
| 缩进 | **4 空格**，🔴 禁 Tab | 0 个 Tab ✅ |
| 行宽 | 代码行 **≤ 100** 字符（中文注释可略长） | 🟡 1 处历史遗留：`source_meta.py:204`（105 字符）；本轮已清掉 `api.py` 5 处 |
| 空行 | 顶层函数间 2 空行，类方法间 1 空行 | — |
| 编码 | 首行 `# -*- coding: utf-8 -*-` | 37/37 ✅ |
| 文件 / 函数 / 变量 | `大驼峰_直白名.py` / `snake_case` / `snake_case` | — |
| 私有函数 | 下划线开头 | `_decompress` / `_is_pdf` |
| 常量 / 类 | `全大写` / `大驼峰` | `DEFAULT_TIMEOUT` / `_VisibleTextParser` |
| 类型注解 | **必写**，文件头 `from __future__ import annotations`，用 `str \| None` | 36/37（仅研究包声明 `__init__.py` 无需注解） |
| 字符串 | 统一**双引号** | — |

### 5.2 文件结构（AI 最常写错的地方）

🔴 **文件头 docstring 四样齐全**（规范 §12）：函数结构树 → 调用关系总览 →
数据流单链路 → 对外返回契约。照抄任一现有模块即可（如 `webcrawl\page.py`）。

🔴 **正文三段式**：`① 纯逻辑（不碰网络/文件）→ ② 网络·落盘 → ③ 主流程`，
用 `# ===== ① ... =====` 横幅分区；新增研究模块分别注明纯逻辑、I/O 或模型适配与主流程。

🔴 **禁止巨型 `main()`**：只做编排；说不清"吃什么、吐什么"就拆函数。

🟡 **零网络自测**：**29 个文件**底部带 `_self_test()`（只吃内置字符串 / 临时目录 +
`if __name__ == "__main__"`）；入口层 `main.py --self-test` 一次串 **25 个模块**。
新增模块请补 `_self_test()`，但**不要**因此碰真实网络或真实 `data\`。

### 5.3 import（分层铁律的具体化）

| 层 | 可以 import |
|---|---|
| 入口 `main.py` | 多个功能层 + 共用层（🔴 **唯一有权横向调用**） |
| 工具 `webcrawl\` | 🔴 不 import `extract\`；内部单向互调 + 读地址簿 |
| 业务 `extract\` | 🔴 只能用 `webcrawl\` 工具 + 地址簿 |
| 入口 `main.py` | 🔴 **唯一可以同时 import 工具层和业务层的地方** |
| 地址簿 `Path_Store\` | 🔴 只 import 标准库 |

统一用这段头（`try/except` 是必要的，支持 `python src\webcrawl\page.py` 直接跑）：
```python
try:
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils, net
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils, net
```

### 5.4 错误处理

🔴 只重试**暂时性**错误（429 / 5xx / 超时 / `IncompleteRead`），4xx 与逻辑错误立刻暴露；
批量流程单条失败不中断整批；错误信息要写"下一步该干什么"。

### 5.5 文档同步清单（漏了就是返工）

代码一改，下面这些**数字**必须同时核对：

| 位置 | 同步什么 |
|---|---|
| `README.md` §1 事实表 | 文件数 / 总行数 / 测试项数 / 种子条数 / 依赖 |
| `README.md` §3 目录树 | 新增 / 删除 / 改名的文件 |
| `README.md` §9 进度表 | 完成项打 ✅ + 日期 |
| 本文件 §0 代码地图 | 每个文件的行数（**最容易过期**） |
| 本文件 §5.1 / §5.2 | 编码 / 行宽 / 横幅数 / `_self_test` 覆盖数 |
| 本文件 §7 坑表 | 新踩的坑（一行一条） |



## 6. Git 分工准则 ★

> 权威来源同 §5：<https://github.com/aaf12329/CoMind/tree/main/01_AI项目协作参考>
> 下的 `通用规范\提交与协作规范.md`（v2.2）。下面只留本项目口径。

### 6.1 分工红线（任何 AI 都必须遵守）

| 动作 | 谁做 |
|---|---|
| 改代码 / 改文档 | 🟢 AI（按 §5 标准） |
| `git add` / `git commit` | 🟢 AI（按逻辑单元挑文件，🔴 别 `git add -A` 无脑全加） |
| `git commit --amend` | 🟡 AI 可做，但只限**未 push** 的提交，且要说明改了什么 |
| `git push` | 🔴 **只由所有者执行**；AI 想 push 必须**先问** |
| `--force` / `reset --hard` / `checkout --` | 🔴 **AI 不许**（会覆盖远端历史、丢并发代理的工作） |
| 改 remote / 建分支 / PR / merge | 🔴 需所有者同意；本项目只在 `main` 直推 |
| 动 `CoMind\01_AI项目协作参考` | 🔴 **绝对只读** |

### 6.2 提交格式

```text
功能：一句话标题（≤72 字符，前缀：文档/规范/功能/修复/整理/重构）

- 中文要点（改了什么、为什么）
- 验证方式 / 同步了哪些文档

Feature: one-line title

- EN bullets
- How verified / which docs updated
```

🔴 **提交信息里每个"已删除 / 已修复"都要能指到具体文件行** —— 写不准就 `--amend` 修正，别留假记录。

### 6.3 多代理并发纪律（真踩过：两代理同写一文件，测试互踩挂）

1. 派子代理前**先划分不相交的写盘范围**；
2. 子代理**不许** `git add` / `commit` / `push`，只交代码，主代理统一收口；
3. 收口时**逐个 `git diff` 看真身**，不采信"我做完了"的自述；
4. 同一时刻**只允许一个进程写 `data\`**。

### 6.4 其余约定（照规范原文）

| 事项 | 规则 |
|---|---|
| 分支 | 只在 `main` 直推，不开 feature 分支、不用 PR/merge |
| 永不入库 | 密钥 `.env`、虚拟环境、`__pycache__`、**数据文件** |
| 🔴 身份信息 | 提交信息与文件内容**不得写入**真实姓名 / 单位 / 地理位置 / 联系方式 |
| remote | `origin` = `github.com/aaf12329/Crawler.git`，未经同意不改 |
| 密钥 | 🔴 只进 `.env`，严禁硬编码 |
| 自测 | 🔴 不碰真实数据；涉网络用**假响应**，零花费 |

### 6.5 工程架构铁律（`通用规范\工程架构规范.md`）

| 铁律 | 口径 |
|---|---|
| 分层 | 入口 → 功能 → 共用 → 地址簿 → 标准库，**单向向下** |
| 🔴 解耦 | 功能层模块之间零 import；共享逻辑下沉共用层，协作由入口层编排 |
| 🔴 地址簿 | 全项目路径集中在一个纯常量模块，只被读、不调任何人 |
| 🔴 重复消除 | 同一逻辑 ≥2 处即算重复：抽共用层 → 删本地副本 → 改 import |
| 🔴 I/O 单一入口 | 文本一个口子、结构化数据一个口子，**不开第三处** |

---

## 7. 已知坑速查表（一行一条，动手前扫一眼）

| 坑 | 一句话对策 |
|---|---|
| 裸 `python` 指向 Microsoft Store 空壳（报 9009） | 一律用 `myvenv\Scripts\python.exe` |
| robots.txt 被当摆设 | 先跑 `src\main.py check <URL>`；Disallow 的直接放弃，不绕行 |
| 选择器散落在循环里，站点一改版全废 | 每站选择器集中在**一处**常量区 |
| 抓取结果不可复现 | 每次记录：时间戳 / URL / HTTP 状态 / 原始 HTML 快照 |
| 训练集混入测试集的转载副本 | 先按内容去重，再划分 |
| 按行切分数据，指标虚高 | **划分维度 = 防泄漏维度**（时间/地区/站点） |
| 二手数据口径混用（产值 ≠ 销售额 ≠ 零售额） | 每个数字标注「口径 + 地域 + 年度」 |
| 百科当数据源引用 | 只搭框架，必须回溯原始出处 |
| 后台命令管道收尾吞报错（exit 0） | 少用管道收尾，跑完**验证产物存在**再报成功 |
| `.gitignore` 白名单顺序写反 | 后行覆盖前行，顺序不能颠倒 |
| 判定层"登录墙"误伤整站（媒体页普遍内嵌隐藏登录弹窗） | 登录表单只是**弱证据**：正文够长仍判 `static`；只有被重定向到登录页才算墙。契约见 `docs\抓取策略表.md` §2 |
| 判定层判完就丢，抓一次重判一次 | 判定结果必须落 `data\profile\site_profile.json`，抓取层查表复用 |
| 判定枚举和工具档位各写各的，最后对不上 | **先改契约再改代码**——`docs\抓取策略表.md` 与 `爬虫标准API接口.md` 是唯一合同 |
| 为了抓一个站就引入 Playwright / curl_cffi | 先跑 `main.py probe` 看实测分布；零依赖档覆盖不到再申请装依赖 |
| **pip 报 `check_hostname requires server_hostname`** | 系统代理 `127.0.0.1:7897` + 老版 pip。装包前 `$env:NO_PROXY="*"`，并加 `--proxy ""` |
| 版式表格（导航条）被当成数据表抽出 | 已由 `table_ops.is_layout_table` 在落盘前挡掉（分隔符单元格占比 / 版式词 / 有效列 ≤1 / 单格超长）；上一批 7 张「表」**7/7 判版式表（0 张真数据表）**，全量重跑又挡掉 13 张。别再手写一套过滤 |
| 段落粒度太粗，整页 1 段 1485 字 | 正文无句末标点时按分隔符硬切到 ≤300 字，否则"第几段"不能当证据坐标 |
| 无头浏览器比静态抓慢约 20 倍 | 实测 3.3 s vs 0.16 s；浏览器档默认关，只对 `dynamic` 站开 |
| 扫描版 PDF 抽不出字却被当成功 | `pdf.parse` 文本层为空 → `E_PDF_NO_TEXT`，绝不假装成功 |
| 本地已提交但忘了 push，只有本机一份 | 每次收尾提醒所有者 `git push`；🔴 AI 不自行 push |
| 提交信息里写了真实姓名/单位/位置 | 🔴 规范 §07 硬约束，提交前逐条过滤 |
| 动了 `CoMind\01_AI项目协作参考` | 🔴 该目录**只读参考**，任何情况下不改 |
| 未打招呼就大改目录结构 | ①先说明 ②再动手；结构变更同步 README 目录树 |
| 模块目录名与文件名单复数不一致 | 目录用全小写（`webcrawl\` `extract\`），Python 文件用大驼峰或小写（`page.py` `Extract_Facts.py`），同一目录内保持一致 |
| 控制台 GBK：Python print 中文正常但 `✓`/emoji 抛 `UnicodeEncodeError`，经管道还乱码 | `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，输出只用 GBK 也能编的字符 |
| 凭"页面脚本多"判动态站，把 SSR 站误判成要上浏览器 | 将来做判别时：看**内嵌状态**（`__NEXT_DATA__` / `__INITIAL_STATE__`）+ 正文占比，别数 `<script>` 个数 |
| 种子 URL 静默失效（404），抓半天没内容 | 逐个抓 robots.txt 时顺带核对，死链与取不到的**单独列出**及时替换（2026-10-19 已换 8 条） |
| 中文站是 GB2312/GBK，按 UTF-8 解码成乱码（正文抓到了但全是`���`） | 按响应头 `charset` → `<meta charset>` → utf-8 → gb18030 解码；已踩：`gs.foodjx.com`、`stock.finance.sina.com.cn` |
| 平台页看着能抓（企查查 / 天眼查），实为反爬登录墙 | 正文短（约千字）且无数据 → 判定无效；改用新闻报道里的企业数量口径 |
| robots.txt 返回 404 被当成"抓取失败" | 404/410 = **站点没有 robots.txt** → 视为无限制；只有超时/5xx 才算失败（真踩过，一批站被误放弃） |
| 页面类型判别把正常文章页判成 dynamic | **正文长度优先于脚本特征**：正文 ≥ 500 字 = 静态直出，页内出现 `document.cookie` / `window.location=` 不算数（新浪财经、腾讯新闻正文 2500~6400 字曾被误判）；脚本占比分子分母都用字节，否则中文页会假性放大 |
| 判 static 就以为万无一失 | 20 份真实快照全判 static 不代表分类器有效——必须拿**已知动态站**验证 dynamic 分支真的会触发（`ymt.com` 正文 0 字 → dynamic 0.9 已验证） |
| 站点无视请求头照样返回 gzip，正文被当文本解码成乱码 | `net.fetch` 先按 `content-encoding` 解 gzip/deflate，**再**按 charset 解码（`ymt.com` 已踩） |
| 快照存"解码后的文本"会丢信息 | 快照一律存**解压后的原始字节**（`io_utils.write_bytes`），文本解码可能有损 |
| 正文抽取把导航/页脚一起算进字数 | 已接 `boilerplate.find_main`：正文与表格都只从正文容器取；容器找不到或正文 <40 字时回退原文，不删空。实测 11 份真实快照：gzstv 2212→1334（压掉 39.7%）、guizhou.gov.cn 2700→595（压掉 78.0%） |
| 用整页文字指纹判重，跨站转载漏判 | 已改为对**正文容器**做 simhash + shingle Jaccard（阈值 0.85）。本轮 13 条真实记录仍只有 2 组重复（都是同 URL 重抓、sha256 相同），**跨站最高相似度仅 0.0544** → 跨站转载尚未真正命中过，别当成已解决 |
| 旧 `is_reprint` 判转载偏松 | 13 条真实记录里标了 **5 条 true，逐条核对全是误报**；**已修**：全量 141 条（131 条成功）实测 47.3% → **14.5%**，判不准一律 `null`（`clean_declaration` / `org_core` / `same_org` 三件套，26 项单测） |
| robots `crawl-delay` 被 `min(wait, per_host_delay*2)` 截断 | 旧 `api.crawl` 把站点声明的大间隔截成 3 秒；改由 `ratelimit.effective_interval` 取 max，**不截断**（`test_api_pipeline` 用 crawl_delay=10 钉死） |
| 并发抓取把同一主机打出多个请求 | `ratelimit.run_parallel`：同 host 加锁 + 限速、不同 host 并行；结果按输入顺序回填，单条异常收成 `_error`，不拖垮整批 |
| 增量 / 续跑靠"跑没跑过"猜 | 认 `data\jobs\` 账本：`--incremental` 只跳 fresh（默认 30 天，`--force` 全重抓），`resume` 只补 pending。实测 fresh 1 条零请求、`resume j_resume` 补 pending 1 条后 done 2/2 |
| 未经要求就多加功能（如自动判站点类型、加入口层编排） | 🔴 **只做被要求的步骤**；越界做了也会被退回，白干一遍 |
| 合并两份同类实现时**丢掉其中一份的语义** | 合并前先把两份差异列成表（本次漏了 `head`：旧抽取层跳过、旧 robots 不跳）；合并后用**同一批样例逐字对照**新旧输出，8/8 相同才算等价 |
| 删模块前没全库 grep 旧名 | 删 `paths.py` 前 grep 到 5 处 `paths.` 仍在用，才没删出连锁故障 |
| 测试 monkeypatch 打到了**兼容别名**，没打到真身 | `web_check.check_robots` 调的是**自己模块里**的 `fetch_robots`，patch 别处无效——**patch 必须打在实际调用方所在模块上**（规范 §08 的"注册表持有旧函数引用"） |
| `http.client.IncompleteRead` 不继承 `OSError`，抓大文件时穿透上层 | `fetch()` 的 except 必须显式接 `http.client.HTTPException`（真踩过：3.5 MB 的 PDF 传输中断） |
| 测试跑出真实 `data\` 文件（自测污染） | 涉落盘的测试一律显式传 `out_dir=tmp`，并断言 `os.listdir(tmp)`；跑完再核 `data\` 仍为空 |
| 合并 CSV 写入时丢了 BOM，Excel 打开中文乱码 | `write_csv` 必须自己拼 `"\ufeff"`；断言前 3 字节是 `ef bb bf` |
| README 自称"唯一权威"但数字全是旧的 | 改结构必须同步 §5.5 那张表的**所有数字**（本次漏了，被所有者当场抓到） |
| 提交信息里写"去掉了 4 处 X"但实际一处没删 | 🔴 **提交信息里每个"已删除/已修复"都要能指到具体文件行**；宁可 `--amend` 修正，也别留假记录 |
| 子代理与主代理同写一个文件，测试互相踩挂 | 派活前划分不相交写盘范围；收口时主代理自己跑全量测试，不采信子代理的"已全绿" |
| 子代理"按规格严格执行"但**规格本身错**（如 `js_redirect` 无条件短路） | 规格里的判定顺序要拿**真实数据反向验证**：本次 20 份真实快照全判 static 才发现 dynamic 分支从没触发过 |
| `record.py` 的 `meta` 只挑 3 个键，调用方传的 `depth` / `duplicate_of` / `tables_dropped` 被**静默丢弃** | 改 `_build_meta`：骨架字段 + 扩展键**原样保留**，并加断言；证据链字段必须能落进 `CrawlResult` |
| `incremental=True` 且 `save_job=False` 时增量**静默失效**（照旧重抓） | 增量判断改读 `pages.json`，与账本开关解耦；`tests\test_api_pipeline.py` 用打桩集成测试钉住 |

---

## 8. 附录：接手已有工作的 AI 怎么用本文件

1. 先读 `README.md` 与 `git log`，对照本文件找**过期信息**；
2. 信息过期时**先请所有者同步**，确认后再动手，不要凭记忆补全；
3. 发现本文件没写的新坑，**干完活顺手补进 §7**——手册是活的，每次踩坑都该让它变厚一点。

---

<div align="center">

**AI 工作手册 v2.1 · 2026-10-20**（对齐《项目接手手册模板》v2.1 骨架；已与云端 CoMind 规范核对；本轮补 v1.3 采集能力）

**事实前置 · 循环最小化 · 坑表一行一条**

</div>
