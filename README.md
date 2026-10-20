# 产业信息研究平台 · 后端服务框架

## 仓库快照与克隆说明

本仓库包含后端服务框架与独立的 `产业研究建议助手/` 子项目。助手仅随目录迁入，尚未接入 FastAPI、Worker 或研究数据库；前端暂未开发。

上传内容为源码、测试、依赖清单和说明文档。开发机密钥、虚拟环境、聊天/记忆数据、数据库、日志和临时产物均未包含。文档中的本机路径已改为通用写法；历史测试记录描述迁移前的开发环境，不代表克隆后无需安装。

使用 Python 3.12+。Windows 下可先按现有 `scripts/Setup.ps1` 建立主服务环境；该脚本将创建本地 .env 和随机服务凭据。真实爬虫仍未提供，主服务就绪检查会提示等待采集器。

独立助手单独安装与演示：

```powershell
cd 产业研究建议助手
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m advisor_bot --demo chat
```

真实模型模式须在该子目录自行创建 .env，参考 .env.example 填写自己的密钥。不要把本地密钥提交到仓库。

Source snapshot: backend framework and a standalone terminal research advisor. The advisor is not integrated. Recreate local environments after cloning; credentials, runtime data and generated artifacts are excluded.

---

这是按 `docs/source/` 两份原始文档搭建的第一版服务工程。前端样式由项目发起人后续设计；当前没有创建 React/Next.js 应用，也没有实现产业推断或真实抓取算法。

## 当前完成内容

- FastAPI 模块化接口、OpenAPI 描述、Bearer 鉴权、v1.2 版本响应头和统一错误体。
- 任务创建、进度查询、幂等提交、失败重试与材料读取接口。
- 数据库持久任务队列和独立 Worker：租约、心跳、过期回收及过期执行者写入隔离。
- 每个 URL 最多两次自动重试，仅处理 `retryable=true`；显式人工重试可开启新一轮尝试，仍复用原材料主键。
- SQLAlchemy 数据访问、Alembic 初始迁移、SQLite 本地模式和 PostgreSQL 连接配置。
- v1.2 数据目录、原子 JSON 快照、人工站点策略读取、规范化产物目录边界校验。
- 可插拔 Python 爬虫适配器；测试适配器只用于自动化测试。
- 项目根目录 `.venv`、依赖锁定文件、启动脚本与自动化检查。

## 运行环境与根目录

- 本机已创建 Python 3.12 虚拟环境，依赖安装在 `.venv/Lib/site-packages/`，不依赖全局 pip 包。
- `.venv`、`.env`、依赖声明与锁定文件均位于项目根目录。
- 原生 Windows 开发采用 SQLite + 数据库持久队列。API 与 Worker 必须使用相同数据库、数据目录及适配器配置。
- 绑定地址默认为 `127.0.0.1`，用于本地开发。
- PostgreSQL 驱动和可选 Compose 配置已准备；本机没有安装 Docker，未启动 PostgreSQL。
- Celery + Redis 仍是未来候选方案，当前未安装和接入。Celery 官方不支持原生 Windows，后续采用时在 Linux/WSL/容器环境单独验证。

## 启动

双击根目录 `启动API.cmd`，服务默认监听 `http://127.0.0.1:8000`。

- 接口文档：`http://127.0.0.1:8000/docs`
- 存活检查：`http://127.0.0.1:8000/health/live`
- 就绪检查：`http://127.0.0.1:8000/health/ready`

**未配置真实爬虫时，存活检查为 200，就绪检查为 503，状态为 `waiting_for_crawler`。** `/v1/probe` 和任务提交返回 `E_CRAWLER_NOT_CONFIGURED`；Worker 明确退出。服务不会伪造抓取结果。

接入爬虫后，在另一个终端双击 `启动Worker.cmd`。API 只接收和查询任务，Worker 独立运行耗时处理。

PowerShell 手动启动方式：

```powershell
.\.venv\Scripts\python.exe -m app.cli init
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
# 另一个终端，接入爬虫后执行：
.\.venv\Scripts\python.exe -m app.cli worker
```

配置文件 `.env` 已生成随机 API 密钥，未写入本文或示例。打开 `.env` 查看 `INDUSTRY_API_TOKEN`，在 `/docs` 的 Authorize 中填入密钥。当前密钥代表单一可信调用方；多用户登录、项目级权限与租户隔离尚未实现。

重新安装环境时运行 `scripts/Setup.ps1`；也可传入 `-PythonPath` 指定 Python 3.12+。脚本优先使用现有 `.venv`，保持已有 `.env` 不变。

## 爬虫接入

1. 按 `app/crawler/base.py` 实现 `probe` 和 `crawl_one`。
2. 在自己的模块中提供无参数工厂 `create_adapter()`，返回适配器实例。
3. 在 `.env` 配置 `INDUSTRY_CRAWLER_ADAPTER=你的模块:create_adapter`。
4. 人工编辑 `data/policy/site_policy.json`，声明来源和允许路径。没有站点策略时，任务提交会返回 `E_POLICY_NOT_FOUND`。
5. 重启 API 与 Worker，执行真实来源的验收。详细约定见 `docs/爬虫接入说明.md`。

框架没有执行网络请求。robots、主机限速、网络超时、重定向与 DNS 安全、字节保真抓取、PDF/表格解析等由接入的采集模块实现和验收。真实发布者与发布时间缺失时保持 `null`。

## HTTP 接口

所有 `/v1` 接口需要 `Authorization: Bearer <token>`。可传 `X-Crawler-API-Version: 1.2`，不同版本会被拒绝；响应会标明版本。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/v1/probe` | URL 画像，返回 `{profiles}` |
| POST | `/v1/jobs` | 创建任务，返回 202 和 `{job_id}`；支持 `Idempotency-Key` |
| GET | `/v1/jobs/{job_id}` | 任务进度与统计 |
| POST | `/v1/jobs/{job_id}/retry` | 推荐的显式失败重试入口 |
| GET | `/v1/jobs/{job_id}/retry` | 兼容原契约；已标记弃用并禁止缓存 |
| GET | `/v1/materials/{record_id}` | 材料记录 |
| GET | `/v1/materials/{record_id}/text` | UTF-8 正文 |
| GET | `/v1/materials/{record_id}/paragraphs` | 段落 NDJSON |
| GET | `/v1/materials/{record_id}/tables/{index}?format=json` | 结构化表格，支持 `csv` |
| GET | `/v1/site-policy/{host}` | 人工声明的站点策略 |

原契约未定义 `plan` 的 HTTP 路由，当前保持未实现；同步 crawl、read_material 的完整 Material 封装，以及跨层 ResearchParams/Fact/Answer 等结构等待完整合同。

## 数据和任务

```text
data/
  platform.db                  # 本地数据库：任务及材料记录的持久事实来源
  profile/site_profile.json    # 最新 Profile 的兼容快照（首次保存后生成）
  policy/site_policy.json      # 人工维护；初始化不覆盖已有文件
  raw/pages.json               # CrawlResult 兼容快照（首次完成后生成）
  raw/html/                    # 由真实爬虫写入
  raw/files/                   # 由真实爬虫写入
  normalized/text/
  normalized/para/
  normalized/pdf/
  normalized/tables/
```

任务和待执行项在同一事务内保存，持久化即入队，避免“任务已保存、消息未发出”的双写问题。Worker 以条件更新领取工作项，执行期间续租；进程中断后，租约到期即可被其他 Worker 领取。执行结果用租约令牌检查归属，防止旧执行者覆盖新结果。

任务采用至少一次执行语义，外部抓取与文件写入仍须由适配器保证重入安全。材料主键固定为 `uuid5(NAMESPACE_URL, f"{job_id}|{url}")`，相同 URL 字符串保持原样。

数据库事务提交后生成 `raw/pages.json`。如果快照写入失败，数据库记录仍保留，可用以下命令恢复快照：

```powershell
.\.venv\Scripts\python.exe -m app.cli export
```

## PostgreSQL 迁移入口

准备好可访问的 PostgreSQL 数据库后，修改 `.env` 的 `INDUSTRY_DATABASE_URL`，再运行 `app.cli init`。如果使用 `compose.yaml`，先在 `.env` 添加自己的 `POSTGRES_PASSWORD`；连接 URL 中的特殊字符需要编码。

这会在目标数据库建立表结构，**不会搬运已有 SQLite 数据**。数据迁移和 PostgreSQL 并发验收属于后续步骤。

## 检查与后续接入

双击 `运行检查.cmd`，或运行：

```powershell
.\.venv\Scripts\python.exe -m ruff check app tests scripts\init_config.py
.\.venv\Scripts\python.exe -m pytest -q
```

测试包含鉴权、错误与参数校验、重复提交、并发领取、租约恢复、自动重试上限、部分成功、人工策略保护、中文段落偏移、材料读取和真实进程间的持久任务运行。测试不访问真实网站。

接口描述可从 `/openapi.json` 获取，根目录 `docs/openapi.json` 也保存了当前版本，供后续 Next.js/TypeScript 接入。前端接入时使用服务端保存密钥。

完整用户系统、项目/口径管理、产业抽取与推断、审核历史和研究报告属于后续业务开发；当前主要交付 L1 合同对应的服务底座，`project_id` 与 `rule_version` 已作为关联字段保留。

文档缺项与待确认事项见 `docs/接口缺项与实现边界.md`。
