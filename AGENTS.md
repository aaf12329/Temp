# 项目入口

先读 README.md、docs/集成说明.md 和 docs/集成验收记录.md。2026-10-21 所有者已恢复开发，授权连接主服务、爬虫研究模块与建议助手，并统一 Python 环境。旧暂停交接保留为历史记录，暂停要求已经解除。

规范唯一入口为相邻目录 ../CoMind/01_AI项目协作参考/README.md。本文只记录项目事实。子项目历史文档中旧环境和“不归并”描述，以本入口为准；历史子代理授权不延续。

唯一环境为根 .venv（Python 3.12）。主服务和助手安装进同一环境；爬虫/研究在同一解释器的隔离子进程中运行，避免旧模块全局状态污染服务。不新建组件环境，不修改原始独立 Crawler 工程。

2026-10-21 所有者部署入口更新：一键启动.bat 调用快速部署/Start.ps1，默认仍用根 .venv；所有者可在不入库的 config.local.json 明确选择已有 Conda/Python 3.12 解释器。此时安装与 API/Worker 均使用所选解释器，不创建嵌套环境。requirement.txt 是根锁依赖及两个可编辑包的手动安装入口。旧按钮仍固定 .venv；新入口按依赖指纹安装，不自动切换已安装的 GAN 后端。npm 只在已有前端 package.json 时安装，尚未增加前端服务。

配置、文档与持久引用使用相对项目根/逻辑 data 根的路径。绝对路径仅在运行时解析用于文件操作与边界检查，不硬编码开发机路径。平台地址簿为 app/common/paths.py；组件地址簿由适配器注入隔离工作区，运行数据不写源码目录。

runtime、api、worker、operation_worker、execution、research、crawler/bridge、crawler/integrated、advisor/integrated 和 CLI 是入口/显式聚合层，可横向编排；services 功能层不横向导入。共用 I/O 在 app/common/file_io.py。

Windows 使用数据库持久队列；Linux 容器入口为 Celery + Redis + PostgreSQL。执行器共用任务表和 CAS 租约；Celery 不叠加自动重试。没有实际部署验收，不声称 Redis/PostgreSQL 已通过联调。

.env 只留本地，禁止输出凭据。自动化测试使用临时数据、假 HTTP 与假模型；本地 ONNX/XGBoost 验收只用合成输入。真实模型调用须有当次授权。记忆、历史、摘要继续采用 JSON 键值对；正式/演示数据按项目分别隔离。

当前仍为单可信调用方 Bearer 鉴权，project_id 只校验材料归属，不等于用户授权。前端、多用户登录和用户项目权限未实施。服务只开放静态 HTML 与文本 PDF；浏览器/TLS 档未适配服务网络闸门，不能绕过。

2026-10-21 CLI 范围更新：pipeline_cli、pipeline/workflow、pipeline/training、pipeline/gan 为显式聚合层。CLI 不构造 runtime、不使用 SQL；输入为用户指定的每行网址 TXT 与终端一句需求。用户提供的单页路径视作本轮来源声明，已有 site_policy 人工禁令优先，robots、登录和网络限制继续强制执行。CLI 自动年度建模保持 machine_candidate_unreviewed，仅作探索；服务原有研究仍默认要求人工批准。output 只覆盖地址簿登记的上轮产物，不递归删除用户文件。

只有最终助手可调用 API；其余模型在本地子进程运行，不接收 API 凭据。GAN遵循原JSON的device=auto，当前测试环境为CUDA版PyTorch；可选TensorFlow，均安装于同一个根 .venv。GAN 参数在 config/cli.json，真实训练集分割在 GAN/训练异常模型之前，验证和测试不包含合成数据。原始独立 WGAN_Trainer/WGAN_Boost_Xgboost 不修改，选用算法及适配范围见 components/wgan_trainer/PROVENANCE.md。

后续所有者明确变更：GAN不再做轻薄本性能兼容，原config.json逐字节保存为config/wgan.json，训练与charts参数直接生效（5000轮、原网络、device=auto）。GAN正式阶段默认不限时、不强制CPU/线程；其他本地检索/XGBoost预算保持。100轮小档仅作为此前接线测试历史，不能当作正式默认。
