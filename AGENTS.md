# 项目入口

先读 README.md、docs/集成说明.md 和 docs/集成验收记录.md。2026-10-21 所有者已恢复开发，授权连接主服务、爬虫研究模块与建议助手，并统一 Python 环境。旧暂停交接保留为历史记录，暂停要求已经解除。

规范唯一入口为相邻目录 ../CoMind/01_AI项目协作参考/README.md。本文只记录项目事实。子项目历史文档中旧环境和“不归并”描述，以本入口为准；历史子代理授权不延续。

唯一环境为根 .venv（Python 3.12）。主服务和助手安装进同一环境；爬虫/研究在同一解释器的隔离子进程中运行，避免旧模块全局状态污染服务。不新建组件环境，不修改原始独立 Crawler 工程。

配置、文档与持久引用使用相对项目根/逻辑 data 根的路径。绝对路径仅在运行时解析用于文件操作与边界检查，不硬编码开发机路径。平台地址簿为 app/common/paths.py；组件地址簿由适配器注入隔离工作区，运行数据不写源码目录。

runtime、api、worker、operation_worker、execution、research、crawler/bridge、crawler/integrated、advisor/integrated 和 CLI 是入口/显式聚合层，可横向编排；services 功能层不横向导入。共用 I/O 在 app/common/file_io.py。

Windows 使用数据库持久队列；Linux 容器入口为 Celery + Redis + PostgreSQL。执行器共用任务表和 CAS 租约；Celery 不叠加自动重试。没有实际部署验收，不声称 Redis/PostgreSQL 已通过联调。

.env 只留本地，禁止输出凭据。自动化测试使用临时数据、假 HTTP 与假模型；本地 ONNX/XGBoost 验收只用合成输入。真实模型调用须有当次授权。记忆、历史、摘要继续采用 JSON 键值对；正式/演示数据按项目分别隔离。

当前仍为单可信调用方 Bearer 鉴权，project_id 只校验材料归属，不等于用户授权。前端、多用户登录和用户项目权限未实施。服务只开放静态 HTML 与文本 PDF；浏览器/TLS 档未适配服务网络闸门，不能绕过。
