# 产业信息研究平台

项目的目标很大，甚至可以说很宏大：把散落的公开信息转化为有来源、有口径、可质疑、可复核的产业判断。当前先把后端采集、研究分析和建议助手连接起来；React 页面与 Next.js 网站层留待后续。

2026-10-21：三个 Python 模块已接线，统一使用根目录 .venv。验收范围与未完成项见 [集成验收记录](docs/集成验收记录.md)，接线细节见 [集成说明](docs/集成说明.md)。

当前新增优先使用的 [CLI 版本](docs/CLI使用说明.md)：双击 `启动CLI.cmd`，先输入网址 TXT 地址（每行一个网站），再输入一句检索需求，之后自动采集、整理、判断数据是否可建模。仅必要时询问 WGAN 的 PyTorch/TensorFlow 选择，普通流程参数在 config/cli.json，GAN参数直接取原仓库副本 config/wgan.json。CLI 不使用 SQL，结果覆盖写入 output；后面的 HTTP 服务架构继续保留。

除最后的信息分析助手使用 API，其余运行在本机，GAN设备按原JSON选择。默认 BGE-small-zh-v1.5 INT8、浅树 XGBoost；GAN按原JSON训练，不做性能降档；缺失、清洗排除和采集失败原因都会在终端及产物中记录。自动建模保持未复核候选的探索性质，不把生成样本当成新事实。

## 职责与调用关系

```text
未来 React / Next.js（尚未开发）
           │ HTTP + JSON
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
                     └─ IntegratedAdvisor → import advisor_bot
                                           │
                       数据库 + 不可变材料 + 项目 JSON 会话
```

采集、研究与建议模型均在后台任务中执行。probe 保留原合同同步接口，适合少量 URL。Redis 只负责投递，数据库保存正式状态；待执行行也是补投依据，消息丢失或 Redis 重启后由 Beat 再扫描，重复消息通过条件领取过滤。

## Windows 安装与启动

推荐双击根目录 **一键启动.bat**：调用 [快速部署](快速部署/README.md) 中的脚本，创建或复用根 `.venv`、安装 Python 依赖、有前端 package.json 时安装 npm 依赖，再启动 FastAPI 与 Worker。依赖未变时复用；支持在本机配置指定 Conda 解释器。根 `requirement.txt` 保留手动安装入口。当前无前端工程，npm 明确跳过。

启动前先显示 CPU、内存、显卡及 NVIDIA 显存/驱动，提供 CPU、NVIDIA GPU、按硬件自动选择和高级选项。选择保存在本机配置；GPU 安装后验证实际 CUDA 运算。CPU/GPU 目前对应 WGAN 的 PyTorch 后端，原训练 JSON 不随硬件降档。

Quick start: run `一键启动.bat` to prepare the root environment and start API/Worker. Manual Conda interpreters are supported through local deployment config; npm dependencies are installed only when a frontend project exists. See the deployment guide for prerequisites and configuration.

Hardware is shown before an interactive CPU/CUDA selection. Choices are remembered locally, CUDA is verified with a real operation, and the original training parameters remain unchanged.

使用 Python 3.12，在项目根目录运行：

```powershell
# 首次安装时可指定解释器；已有根 .venv 时省略 -PythonPath
./scripts/Setup.ps1 -PythonPath <你的Python3.12解释器>
./scripts/Setup.ps1
```

脚本安装根锁定依赖、主服务和助手包，创建缺失的根 .env 并迁移数据库。已有配置保留。源码和持久数据不绑定原电脑目录；虚拟环境自身不随目录搬移复用，移动或克隆后重建。

双击 启动API.cmd 和 启动Worker.cmd。API 默认仅监听 127.0.0.1:8000：

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

启动建议助手.cmd 和子目录原按钮均使用根环境。手动运行：

```powershell
./.venv/Scripts/python.exe -m app.advisor_cli --project kaili --demo chat
./.venv/Scripts/python.exe -m app.advisor_cli --project kaili --session default chat
./.venv/Scripts/python.exe -m app.advisor_cli --project kaili memory list
```

终端与接口共用同项目 JSON 会话及人工记忆；终端不自动读平台材料，带证据建议使用任务接口。独立 advisor_bot 仍可单独安装，包本身不依赖 FastAPI。

## 相对路径与目录

配置和持久引用相对项目根或逻辑 data 根。程序执行时解析真实路径用于操作与边界检查，不把本机绝对路径写进 API 或研究导出。

```text
./
  .venv/                         唯一 Python 环境，不入库
  .env                           唯一本机服务配置，不入库
  一键启动.bat                    自动部署并监管本机 API/Worker
  requirement.txt                手动 pip/Conda 安装入口
  快速部署/                       部署入口、配置模板与使用说明
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

运行 运行检查.cmd 或 ./scripts/Test.ps1，分别执行服务、独立助手、爬虫离线测试并检查依赖一致性。实际 CPU 模型计算用 scripts/verify_environment.py，结果在 work/environment-check.json。

当前为单可信调用方服务，project_id 是材料归属检查，不等于用户授权。多用户登录与项目授权、React/Next.js、审核管理 HTTP 界面、训练服务入口、分布式文件存储和多主机限速未实施。文件锁限速要求共享同一文件系统。

原合同在 docs/source/，正式定义缺项在 docs/接口缺项与实现边界.md；历史“未接线”描述以本页当前状态为准。
