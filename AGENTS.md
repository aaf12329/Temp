# 项目入口

本轮最终检查更新：完整主项目pytest为111通过/1跳过（98.89秒），前端10测试及类型检查通过，15个受影响Python文件AST和Ruff通过。首次全量发现tests/test_deployment.py的中文路径探针输出不是UTF-8，已给该探针加-X utf8和退出码断言，完整重跑通过；正式Start.ps1本来已设置PYTHONUTF8=1。36通过/1跳过是先执行的针对性子集，不与完整数量相加。本轮未做生产构建、浏览器视觉或新的真实LLM请求；真实26网址仍为DNS阻断而非建模成功。当前授权仅用于本轮提交并推送一次。

2026-10-22当前授权及验证：用户已明确“你去跑测试”，随后要求桌面测试集并授权本轮commit/push一次，覆盖以下旧暂停要求；仅用于包含本记录的当前提交，后续不得延用。桌面数学建模测试集已经整理，公开链接及无密钥配置归档examples/research_sources。针对修复回归36通过/1跳过（Windows符号链接权限），前端类型与10项交互测试通过，修改Python Ruff通过。真实26公报任务在output/real-source-tests/20261022-grain-104504，81.383秒、26项全部被非公网DNS闸门阻断，正文/年度行/模型均0；www.cee.edu.cn解析198.18.0.22且Clash进程运行。未调用真实LLM或降低门槛，未改代理；不能声称真实建模成功。详见docs/验证记录_20261022.md。桌面资料及本机产物不作为自动化夹具，自动化继续使用临时合成数据。

先读 README.md、docs/集成说明.md 和 docs/集成验收记录.md。2026-10-21 所有者已恢复开发，授权连接主服务、爬虫研究模块与建议助手，并统一 Python 环境。旧暂停交接保留为历史记录，暂停要求已经解除。

规范唯一入口为相邻目录 ../CoMind/01_AI项目协作参考/README.md。本文只记录项目事实。子项目历史文档中旧环境和“不归并”描述，以本入口为准；历史子代理授权不延续。

唯一环境为根 .venv（Python 3.12）。主服务和助手安装进同一环境；爬虫/研究在同一解释器的隔离子进程中运行，避免旧模块全局状态污染服务。不新建组件环境，不修改原始独立 Crawler 工程。

2026-10-21 所有者部署入口更新：启动脚本/01_平台/一键启动.bat 调用快速部署/Start.ps1，默认仍用根 .venv；所有者可在不入库的 config.local.json 明确选择已有 Conda/Python 3.12 解释器。此时安装与 API/Worker 均使用所选解释器，不创建嵌套环境。requirement.txt 是根锁依赖及两个可编辑包的手动安装入口。入口按依赖指纹安装，不自动切换已安装的 GAN 后端。npm 识别已有前端，标准frontend随服务启动。

所有者随后要求启动时先展示硬件并让用户选择 CPU/GPU：快速部署/Hardware.ps1 负责本机配置展示与交互，GPU 入口限现有 NVIDIA CUDA 方案，安装或复用后验证真实小型 CUDA 运算。用户选择存入本机配置，回车沿用；高级选项可选 TensorFlow CPU、保留框架、跳过安装。SetupOnly/Plan 默认无交互，ChooseBackend 可显式打开菜单，NonInteractive 禁止交互。设备选择仅改变安装后端，不修改原 WGAN JSON。

配置、文档与持久引用使用相对项目根/逻辑 data 根的路径。绝对路径仅在运行时解析用于文件操作与边界检查，不硬编码开发机路径。平台地址簿为 app/common/paths.py；组件地址簿由适配器注入隔离工作区，运行数据不写源码目录。

2026-10-21 本机目录已改为与 GitHub 同名的 industry-evidence-platform。业务源码与默认配置不含机器绝对文件路径；启动位置与目录名不能成为运行条件。外部 TXT/Conda 用户输入可显式指向项目外；Docker 容器内部固定挂载路径及工具生成的 .venv 元数据不作字符串替换。tests/test_conventions.py 负责源码地址检查及项目外导入/默认路径验证。

runtime、api、worker、operation_worker、execution、research、crawler/bridge、crawler/integrated、advisor/integrated 和 CLI 是入口/显式聚合层，可横向编排；services 功能层不横向导入。共用 I/O 在 app/common/file_io.py。

Windows 使用数据库持久队列；Linux 容器入口为 Celery + Redis + PostgreSQL。执行器共用任务表和 CAS 租约；Celery 不叠加自动重试。没有实际部署验收，不声称 Redis/PostgreSQL 已通过联调。

.env 只留本地，禁止输出凭据。自动化测试使用临时数据、假 HTTP 与假模型；本地 ONNX/XGBoost 验收只用合成输入。真实模型调用须有当次授权。记忆、历史、摘要继续采用 JSON 键值对；正式/演示数据按项目分别隔离。

当前仍为单可信调用方 Bearer 鉴权，project_id 只校验材料归属，不等于多用户授权。主项目前端已获所有者授权接线，Bearer留在Node服务器；不做登录、注册或用户中心。桌面设计包继续独立未接线。服务只开放静态HTML与文本PDF；浏览器/TLS档未适配网络闸门，不能绕过。

2026-10-21 所有者先要求补齐九个独立演示视图，随后明确修改数据流程并总装。ResearchWorkspace、LiveResearch、ResearchPrototype、Node代理、pipeline_service和api/pipeline是显式聚合层。实际与演示状态分开；GAN数量固定真实训练集×5，先选是否训练，生成后再确认采用，同批产物哈希校验，不重训。原n_generate仅来源兼容，训练/图形参数不降档。网页管线复用Operations持久队列，每次领取独立output/web目录，最终未知LLM结果不自动重试。用户暂停测试前已有隔离三进程记录，见docs/网页总装说明.md。

一键启动准备依赖后可同时监管API、Worker和标准frontend。代码路径、默认配置与产物引用仍相对根/逻辑data根。桌面设计包是独立纯前端快照，不能将主项目Node代理或Python源码直接覆盖过去。人类负责最终视觉设计。

当前所有者最新要求“暂时不做测试就先搭建起来”。停止测试、冒烟与训练验收，仅进行代码、配置和文档收口。此前通过记录不能当成最后版本重新验收；未获后续恢复指令不继续运行这些检查。

2026-10-22 数据漏检修复：所有者授权解决output无可建模数据的问题。本轮0子代理，未重爬、训练、调用模型或运行测试。workflow编排新增selection/context/diagnostics三个纯规则叶子；空口径使用组件默认词表及餐饮别名，混合召回不降低BGE阈值，预算按文档轮流且数值优先。统计标题可关联唯一年度/地域，指标名可提供餐饮主体；标题主体、限额/企业/非年度/计划及补全后的冲突仍保护。关联证据保留在候选及匹配CSV并传给最终无记忆LLM。PipelinePaths登记diagnostics；网页和运行消息展示实际瓶颈，gan_gate区分未启动与失败。20年度/XGB时间划分、64真实训练行GAN门槛、原GAN JSON和5倍生成均未改；月度季度与通用表格抽取没有本轮适配。新增tests/test_data_recovery.py尚未执行；旧output未改，需正常重启后提交新任务。说明见docs/数据整理与建模门槛.md；备份在忽略目录work/before-extraction-repair-20261022。本轮没有push授权。

2026-10-22最新需求：研究最终LLM一次性输入本轮数据，不搞记忆，只说发展趋势。summarize改调用IntegratedAdvisor.analyze_trend，直接ChatModel.complete(max_retries=0)，不走execute/build_bot/History/Memory，不创建会话或读写聊天摘要；结果仍保存llm.json但不作为后续上下文。网页最终只显示llm.reply.content，完整report.md继续下载。旧独立聊天与旧建议API保持原契约。提示词禁止建议、行动计划和追问；证据不足明确无法判断趋势。此修改及新增隔离用例尚未运行验收，不追加真实模型请求。

2026-10-22后续授权：曾明确要求两轮真实来源测试，已完成19/48网址，均因Clash Fake-IP被公网地址闸门阻断，不能据此判定来源数据量不足。随后所有者改为自行测试。当前新增实际网页消息框与接通最终LLM的授权；LLM密钥只留根.env，已通过正式IntegratedAdvisor完成最小连接检查，使用deepseek-flash。不将其推广为全流程验收，不运行新的真实网站批量测试。ResearchProgress状态机组织真实事件，collect以可选回调上报；Operations复用内部result_data._progress保存受租约令牌保护的快照，对外查询把progress和最终result分开，无需新迁移。完成/失败后保留消息，重新领取重置旧快照。ResearchMessages为展示叶子，现有轮询携带真实进度。新增隔离回归用例尚未执行。上一轮push授权已消费，本轮没有push授权。

随后所有者要求统一结果目录、分类BAT入口，并明确“做完了push一下”：允许本轮完成后提交并推送当前仓库main一次；不解除暂停测试要求。所有新完整管线结果使用output/cli或output/web/执行令牌；CSV/PNG/模型/JSON由PipelinePaths组织到data/charts/models/metadata，各口径在series。新网页result.storage_layout=output-v1，旧无标记任务仍读data/pipeline；旧结果不自动迁移。底层PipelinePaths默认平铺供历史接口兼容，生产新入口明确organized=True，并通过output_layout传给离线进程。默认data的输出为同级output；非data名称的自定义根内隔离output。旧research接口和助手JSON会话仍在data。用户按钮集中启动脚本/01_平台、02_命令行、03_开发维护，由scripts/Launch.ps1复用所选解释器；桌面独立设计交接包未改动。本轮目录调整尚未运行验收。

2026-10-21 CLI 范围更新：pipeline_cli、pipeline/workflow、pipeline/training、pipeline/gan 为显式聚合层。CLI 不构造 runtime、不使用 SQL；输入为用户指定的每行网址 TXT 与终端一句需求。用户提供的单页路径视作本轮来源声明，已有 site_policy 人工禁令优先，robots、登录和网络限制继续强制执行。CLI 自动年度建模保持 machine_candidate_unreviewed，仅作探索；服务原有研究仍默认要求人工批准。output 只覆盖地址簿登记的上轮产物，不递归删除用户文件。

只有最终助手可调用 API；其余模型在本地子进程运行，不接收 API 凭据。GAN遵循原JSON的device=auto，当前测试环境为CUDA版PyTorch；可选TensorFlow，均安装于同一个根 .venv。GAN 参数在 config/cli.json，真实训练集分割在 GAN/训练异常模型之前，验证和测试不包含合成数据。原始独立 WGAN_Trainer/WGAN_Boost_Xgboost 不修改，选用算法及适配范围见 components/wgan_trainer/PROVENANCE.md。

后续所有者明确变更：GAN不再做轻薄本性能兼容，原config.json逐字节保存为config/wgan.json，训练与charts参数直接生效（5000轮、原网络、device=auto）。GAN正式阶段默认不限时、不强制CPU/线程；其他本地检索/XGBoost预算保持。100轮小档仅作为此前接线测试历史，不能当作正式默认。
