# 产业信息研究平台

项目的目标很大，甚至可以说很宏大：把散落的公开信息转化为有来源、有口径、可质疑、可复核的产业判断。当前已完成本机网页流程总装：React/Next.js → FastAPI → 持久Worker → Python采集/分析/可选GAN/XGBoost → 最终建议；另保留独立设计预览与CLI。

## 使用的技术与模型

| 技术或模型 | 职责 | 运行位置 |
| --- | --- | --- |
| React + TypeScript | 网页组件、表单、用户阶段选择与结果展示 | 浏览器 |
| Next.js / Node.js | 页面与同源接口转发，服务密钥仅留服务器端 | 本机Node进程 |
| FastAPI / Uvicorn | 参数与权限校验、阶段顺序、任务提交与产物下载 | 根Python环境 |
| BGE-small-zh-v1.5（INT8 ONNX） | 用户需求与网页内容的向量匹配 | 本地CPU |
| IsolationForest | 条件满足时标记训练异常；默认不自动删除 | 本地CPU |
| WGAN-GP（PyTorch / TensorFlow） | 用户先选择才训练，生成数量=真实训练集×5，生成后再次确认采用 | 本地，设备沿用JSON |
| XGBoost | 同口径年度数据的回归预测与独立真实测试评估 | 本地CPU |
| 规则、pandas / SciPy / pyampute | 数值抽取、单位检查、去重、清洗及缺失诊断 | 本地 |
| t-SNE / XGBoost原生TreeSHAP | 分布可视化与模型解释；不等于质量或因果证明 | 本地 |
| 外部LLM API | 只在最后解读证据、指标、限制与建议；缺少密钥仍导出数据报告 | 远程API |

Windows复用既有SQLite持久队列；Linux Celery/Redis/PostgreSQL配置仍保留，尚未做真实部署验收。只有CLI不使用SQL；网页研究产物仍是CSV/JSON/PNG，助手会话仍是JSON。

网页入口见 [frontend/README.md](frontend/README.md)：打开即用，没有登录、注册或用户中心。“实际研究”连接本机服务，“设计预览”保留九个合成演示视图。桌面的“产业研究平台_前端设计交接”及同名ZIP保持独立、未接后端，供人类设计师工作。实际接线说明与验收见 [网页总装说明](docs/网页总装说明.md)。

Local assembly: React/Next.js forwards to FastAPI and a durable Python worker. Users opt in before GAN training and confirm generated data after review; generation is five times the real training count. Real validation/test data remain isolated. A separate nine-view design demo and standalone frontend handoff remain available. BGE, IsolationForest, WGAN-GP and XGBoost run locally; only final language analysis uses an external API.

2026-10-21：三个 Python 模块已接线，统一使用根目录 .venv。验收范围与未完成项见 [集成验收记录](docs/集成验收记录.md)，接线细节见 [集成说明](docs/集成说明.md)。

当前新增优先使用的 [CLI 版本](docs/CLI使用说明.md)：双击 `启动脚本/02_命令行/启动CLI.bat`，先输入网址 TXT 地址（每行一个网站），再输入一句检索需求，之后自动采集、整理、判断数据是否可建模。仅必要时询问 WGAN 的 PyTorch/TensorFlow 选择，普通流程参数在 config/cli.json，GAN参数直接取原仓库副本 config/wgan.json。CLI 不使用 SQL，结果覆盖写入 output/cli；后面的 HTTP 服务架构继续保留。

除最后的信息分析助手使用 API，其余运行在本机，GAN设备按原JSON选择。默认 BGE-small-zh-v1.5 INT8、浅树 XGBoost；GAN按原JSON训练，不做性能降档；缺失、清洗排除和采集失败原因都会在终端及产物中记录。自动建模保持未复核候选的探索性质，不把生成样本当成新事实。

## 职责与调用关系

```text
React / Next.js（实际研究 + 独立设计预览）
           │ 同源Node转发：HTTP + JSON，Bearer留在服务器
           ▼
FastAPI：校验、单调用方鉴权、任务提交、材料与结果查询
  ├─ 短查询 → services / 数据库
  └─ 长任务 → 持久任务表
                 ├─ Windows：数据库队列 Worker
                 └─ Linux：Beat 补投 → Redis → Celery Worker
                                           │
                     共用执行器 + CAS 租约/心跳/结果隔离
                     ├─ IntegratedCrawler → 同解释器子进程 → webcrawl.api
                     ├─ IntegratedResearch → 同解释器子进程 → research_api
                     ├─ IntegratedAdvisor → import advisor_bot
                     └─ pipeline_service → 分阶段CLI核心 → GAN确认 / XGBoost / 最终助手
                                           │
                       数据库 + 不可变材料 + 项目 JSON 会话
```

采集、研究与建议模型均在后台任务中执行。probe 保留原合同同步接口，适合少量 URL。Redis 只负责投递，数据库保存正式状态；待执行行也是补投依据，消息丢失或 Redis 重启后由 Beat 再扫描，重复消息通过条件领取过滤。

## Windows 安装与启动

推荐双击 **[启动脚本/01_平台/一键启动.bat](启动脚本/01_平台/一键启动.bat)**：调用 [快速部署](快速部署/README.md) 中的脚本，创建或复用根 `.venv`、安装 Python 依赖、识别 frontend/package.json 安装 npm 依赖，再启动 FastAPI、Worker和Next.js。默认网页 http://127.0.0.1:3000 ，API文档 http://127.0.0.1:8000/docs 。依赖未变时复用；支持明确指定已有Conda环境。根 `requirement.txt` 保留手动安装入口。已有3000/8000进程时需先正常关闭，入口不会接管其他服务。

启动前先显示 CPU、内存、显卡及 NVIDIA 显存/驱动，提供 CPU、NVIDIA GPU、按硬件自动选择和高级选项。选择保存在本机配置；GPU 安装后验证实际 CUDA 运算。CPU/GPU 目前对应 WGAN 的 PyTorch 后端，原训练 JSON 不随硬件降档。

Quick start: run `启动脚本/01_平台/一键启动.bat` to prepare the root environment and start API, worker and the standard Next.js frontend. Manual Conda interpreters remain supported. See the deployment guide for prerequisites and configuration. Final acceptance is paused at the owner's request.

Hardware is shown before an interactive CPU/CUDA selection. Choices are remembered locally, CUDA is verified with a real operation, and the original training parameters remain unchanged.

使用 Python 3.12，在项目根目录运行：

```powershell
# 首次安装时可指定解释器；已有根 .venv 时省略 -PythonPath
./scripts/Setup.ps1 -PythonPath <你的Python3.12解释器>
./scripts/Setup.ps1
```

脚本安装根锁定依赖、主服务和助手包，创建缺失的根 .env 并迁移数据库。已有配置保留。源码和持久数据不绑定原电脑目录；虚拟环境自身不随目录搬移复用，移动或克隆后重建。

单独调试时，双击 `启动脚本/03_开发维护/启动API.bat` 和 `启动Worker.bat`。API 默认仅监听 127.0.0.1:8000：

- /docs：交互接口文档。
- /health/live：进程存活。
- /health/ready：数据库迁移与采集适配器就绪，不代表外部站点或模型均可用。

根 .env 的 INDUSTRY_CRAWLER_ADAPTER=integrated 启用真实组件；空值保留等待爬虫兼容模式。API 与 Worker 使用相同根配置、数据库和 data 目录。

采集前人工编辑 data/policy/site_policy.json；没有策略拒绝采集。配置形状示例：

```json
[{"host":"example.org","allowed_paths":["/articles/"],"disallowed_paths":[],
  "min_interval_s":1.5,"requires_login":false,"note":"先人工核对来源和允许范围"}]
```

服务对每次请求和重定向检查主机、路径、协议、端口、实际连接 DNS 与 robots，并跨进程限速、限制响应大小。当前只开放静态 HTML 与文本 PDF；渲染/TLS 档尚未适配，明确拒绝对应参数。扫描 PDF 没有 OCR。

## 采集 → 研究 → 建议

所有 /v1 接口需要根服务 Bearer 凭据。在本机 .env 查看后填入 /docs 的 Authorize，不把密钥写入浏览器代码。

1. POST /v1/jobs：提交 job_id、project_id、urls，返回 202。
2. GET /v1/jobs/{job_id}：查进度。
3. GET /v1/jobs/{job_id}/materials?project_id=...：列出材料 ID。
4. POST /v1/research/jobs：提交项目材料和明确口径，后台导出 CSV/JSON。
5. GET /v1/operations/{operation_id}?project_id=...：查研究或建议结果。
6. GET /v1/operations/{operation_id}/files/{name}?project_id=...：下载登记产物。
7. POST /v1/advisor/jobs：引用同项目材料和可选研究 ID，后台生成建议。
8. GET /v1/projects/{project_id}/advisor/sessions/{session_id}?demo=true：查演示会话；省略 demo 查正式会话。

研究请求示例，替换材料 ID：

```json
{
  "operation_id":"research-001","project_id":"kaili",
  "material_ids":["材料record_id"],"mode":"lexical",
  "config":{
    "rule_version":"kaili-v1","keywords":["酸汤"],"queries":[],
    "subjects":["酸汤产业"],"regions":["凯里"],
    "forecast_at":"2026-10-21T00:00:00+08:00"
  }
}
```

lexical 显式使用关键词，不伪造向量分数。semantic 使用本地 CPU ONNX，首次准备与计算验收：

```powershell
./.venv/Scripts/python.exe -m app.cli model-setup
./.venv/Scripts/python.exe scripts/verify_environment.py
```

模型固定 revision 并记录哈希，不隐式下载。语义阈值未经独立标注集校准。机器事实候选保持 pending；没有人工审核的同口径连续时间序列时，趋势返回 insufficient_data。

建议请求示例：

```json
{
  "operation_id":"advice-001","project_id":"kaili","session_id":"default",
  "question":"这些材料还缺哪些统计口径？","material_ids":["材料record_id"],
  "research_id":"research-001","demo":true
}
```

demo 使用标注的假模型。真实模式在根 .env 设置 INDUSTRY_ADVISOR_API_KEY；接口不接受模型密钥、模型地址、任意路径或工具。建议不自动写人工事实。同操作 ID 与请求只执行一次，变更请求返回冲突；模型执行中断或结果未知时停止，核对会话后决定是否使用新 ID。

## 助手终端

`启动脚本/02_命令行/启动建议助手.bat` 使用部署配置所选的统一环境。原助手子目录重复按钮已归并。手动运行：

```powershell
./.venv/Scripts/python.exe -m app.advisor_cli --project kaili --demo chat
./.venv/Scripts/python.exe -m app.advisor_cli --project kaili --session default chat
./.venv/Scripts/python.exe -m app.advisor_cli --project kaili memory list
```

终端与接口共用同项目 JSON 会话及人工记忆；终端不自动读平台材料，带证据建议使用任务接口。独立 advisor_bot 仍可单独安装，包本身不依赖 FastAPI。

## 相对路径与目录

所有新完整管线结果统一放在 [output](output/README.md)：CLI 为 output/cli，网页为 output/web/<执行令牌>。每轮包含 report.md，以及分类的 data/ CSV、charts/ PNG、models/ 模型和 metadata/ JSON；各统计口径分放 series/短哈希。旧结果保留原位置，不自动搬动。启动入口分类见 [启动脚本说明](启动脚本/README.md)。

New pipeline results use output/cli or output/web/<run-token>, with separate CSV, chart, model and metadata directories. Windows launchers are grouped under 启动脚本. Historical artifacts remain readable. These storage and launcher changes have not been runtime-tested at the owner's request.

配置和持久引用相对项目根或逻辑 data 根。程序执行时解析真实路径用于操作与边界检查，不把本机绝对路径写进 API 或研究导出。

本地项目文件夹现与仓库同名 industry-evidence-platform。源码通过文件自身位置定位根目录，启动脚本通过自身目录定位，不依赖文件夹名称或启动终端的位置。默认配置使用 data、output、config 等相对引用；Conda 的 python_path 也支持相对项目根目录填写。

外部 TXT/Conda 的用户输入、运行时解析结果、虚拟环境生成文件及容器内固定挂载点可以包含绝对路径。它们不属于写死开发机目录；虚拟环境仍应在新电脑重新创建，不能保证复制 .venv 后可用。

Project-owned paths are rooted at the source/script location, independently of the checkout name or current working directory. Defaults and stored artifact references are relative; external user selections and tool-managed environment metadata are resolved locally.

```text
./
  .venv/                         唯一 Python 环境，不入库
  .env                           唯一本机服务配置，不入库
  启动脚本/                       按平台、命令行、开发维护分类的 BAT 入口
  requirement.txt                手动 pip/Conda 安装入口
  快速部署/                       部署入口、配置模板与使用说明
  frontend/                       实际研究网页与独立设计预览
  app/                           服务、适配、任务、调度
  components/crawler/            爬虫研究源码，不写运行数据
  产业研究建议助手/               独立助手源码，不另建环境
  data/
    platform.db                  Windows 任务、材料登记与操作结果
    policy/site_policy.json      人工策略
    raw/ normalized/             内容哈希寻址材料，重抓不覆盖旧字节
    research/<run_id>/            CSV、JSON、报告
    models/                      固定版本 CPU 模型
    advisor/<project_id>/        正式 JSON 会话、记忆、操作缓存
    advisor_demo/<project_id>/   隔离的演示数据
    workspaces/                  隔离运行副本，不是事实源
  output/
    cli/                         最新 CLI 结果；登记文件覆盖
    web/<执行令牌>/              网页每次阶段结果；保留父结果
  work/                          源码备份、检查记录，不入库
```

原有正文、段落、表格读取和 POST/兼容 GET retry 保留。数据库是状态事实源，raw/pages.json 是兼容快照，可通过 python -m app.cli export 重建。

## Linux 容器入口

Dockerfile 与 compose.yaml 提供 API、Celery Worker、单实例 Beat、Redis 和 PostgreSQL。先准备根 .env，额外设置仅含 URL 安全字符的 POSTGRES_PASSWORD，然后：

```text
docker compose up --build -d
```

Compose 先迁移数据库再启动服务。Python 服务共用同一镜像和数据卷。Linux 根据根 pyproject.toml 安装，采用 XGBoost CPU 包；Windows 验证版本保存在根锁文件。Redis 不暴露主机端口。容器卷与 Windows data 分开，已有 SQLite 和模型缓存不会自动迁移。

本机未安装 Docker/WSL，容器构建及真实 Redis/PostgreSQL 联调待验证。Celery 的 Windows solo 测试不代表正式部署支持。配置依据 [Celery 配置](https://docs.celeryq.dev/en/stable/userguide/configuration.html)、[周期任务](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html) 和 [XGBoost 安装说明](https://xgboost.readthedocs.io/en/stable/install.html)。

## 检查与边界

恢复验收后，可手动运行 `启动脚本/03_开发维护/运行全部检查.bat` 或 ./scripts/Test.ps1，分别执行服务、独立助手、爬虫离线测试并检查依赖一致性。实际 CPU 模型计算用 scripts/verify_environment.py，结果在 work/environment-check.json。当前仍遵守所有者暂停测试的要求，本次目录与入口调整未运行验收。

当前为单可信调用方服务，project_id 是材料归属检查，不等于用户授权。React/Next.js 已连接实际研究流程且不做登录；多用户授权、审核管理 HTTP 界面、分布式文件存储和多主机限速未实施。文件锁限速要求共享同一文件系统。

原合同在 docs/source/，正式定义缺项在 docs/接口缺项与实现边界.md；历史“未接线”描述以本页当前状态为准。
