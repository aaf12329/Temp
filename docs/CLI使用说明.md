# CLI 使用说明

此CLI入口不启网页或数据库；主项目另有已接线的网页入口。最新顺序是“先采集→用户选择分支”：教材分支只保存材料并停止，原流程继续扩大匹配、CSV、LLM清洗与既有缺失/建模。检索与XGBoost使用本地CPU；GAN保持原训练/图形参数，生成量固定真实训练集×5。详见[采集后分支](采集后分支.md)。原流程中间清洗与最终趋势可调用API，其余模型本地运行。此前26来源两种抽取路径已进入探索性XGBoost，但未优于基线，历史测试情况见[双路径交接](交接_20261022_双抽取路径.md)。

## 启动与输入

双击 `启动脚本/02_命令行/启动CLI.bat`，先输入两项，爬完后再选择分支：

```text
网址清单文件地址（每行一个网站）：<你的 TXT 地址>
要检索什么信息（一句话）：贵阳餐饮业的收入、消费规模与发展变化
采集完成后：1=继续原流程，2=教材分支（保存材料并停止），回车默认2
```

清单可放桌面或其他位置，支持中文、空格、带引号的 Windows 绝对地址。每行一个 http(s) 网页或文本 PDF 地址；空行和 # 开头注释忽略。示例为 config/urls.example.txt。普通流程参数读取 config/cli.json，GAN 的训练及图形参数直接读取 config/wgan.json。

也可以在项目根目录运行：

```powershell
./.venv/Scripts/python.exe -m app.cli research
# 或直接运行相同入口，可显式提供参数以便重复操作
./.venv/Scripts/python.exe -m app.pipeline_cli --urls-file <清单TXT> --query "检索需求"
```

首次新建环境：

```powershell
./scripts/Setup.ps1 -GANBackend pytorch
# 当前NVIDIA测试机可选CUDA版，仍使用同一个根环境
./scripts/Setup.ps1 -GANBackend pytorch-cuda
# 可选 TensorFlow，仍安装在相同根 .venv
./scripts/Setup.ps1 -GANBackend tensorflow
./.venv/Scripts/python.exe -m app.cli model-setup
```

Setup 的 init 步骤是保留的主服务安装过程，会准备服务数据库；CLI 自身不打开或使用它。纯 CLI 已有根环境时，可安装根依赖/主包/助手包及选定的 requirements/gan-*.txt，无需执行服务 init。model-setup 只下载固定版本向量权重；日常检索不联网请求向量服务。

根.env配置INDUSTRY_ADVISOR_API_KEY、INDUSTRY_ADVISOR_BASE_URL、INDUSTRY_ADVISOR_MODEL。中间CSV清洗与最后趋势分析共用这些设置，分别独立请求，终端打印模型名称，不打印key。默认llm=api；llm_cleaning.mode=off可单独关闭中间API，llm=off关闭两阶段API；demo只演示最终输出，中间仍用本地规则。缺密钥或失败保留原CSV和明确状态，不冒充清洗成功。

最终LLM走analyze_trend单次入口：本轮证据和模型摘要一次提交，禁用重试，不读取或保存会话、人工记忆和聊天摘要。只输出发展趋势；不足以判断时直说，避免行动建议与追问。metadata/llm.json保留本轮结果及memory_enabled=false，这属于结果存档，不用于下一次模型上下文。

## 实际流程与终端提示

```text
网址TXT的有效行 U[N] + 用户一句需求 q
  → collect：按网址顺序检查策略/robots/HTTP/类型 → 材料 R[K]
  → 保存sources.zip → 用户选择：教材分支到此停止；原流程继续以下步骤
  → stage_records：隔离只读副本 + 原始相对引用映射
  → workflow.analyze
       load_corpus → Encoder/score_passages → extract_facts
       → information.csv / raw.csv / llm_input.csv先落盘
  → clean_csv → 分批独立LLM清洗 → 本地原文/数字/单位检查
  → workflow.finalize → cleaned.csv / review.csv → 原有缺失诊断与同口径年度行
  → 信息整理完成
  → workflow.model(plan)：时间切分 + 门槛检查，不提前拟合XGBoost或GAN
       数据不够 → data_only → API助手
       数据够但GAN门槛不够 → 仅真实数据XGBoost
       GAN真实训练样本够 → 用户选1=PyTorch、2=TensorFlow、0=不训练（默认0）
  → 如选择GAN：仅训练集拟合 → 生成真实训练数×5 → 质量检查+t-SNE
       → 用户再选是否采用（默认0）；不采用则仅真实数据继续
  → XGBoost对比真实/所选增强方案，真实验证未改善则回退；不重新生成GAN数据
  → 真实测试评估 + 可选未来探索预测 + PNG
  → summarize：匹配原文摘录 + 实际指标/缺口 → API助手 → output/cli/<路径>/report.md
```

终端逐网址打印 HTTP、类型、成功/跳过及原因；例如 robots 禁止、登录墙、拦截、未支持的动态网页或扫描 PDF。已有 data/policy/site_policy.json 限制优先；无该主机记录时，用户输入的单页路径作为本轮允许声明，仍不绕过任何 robots、登录和访问限制。程序不能凭自动检查证明法律许可。

清洗逐项原因和完整来源另写 CSV/JSON。控制台显示前十条候选排除理由，其他见 rejected.csv。只支持当前静态 HTML 与文本 PDF；不存在“任何网站都能解析”的承诺。

## 两条抽取路径

配置中的 `extraction.mode` 决定 CSV 落盘后的整理方式，两条路径共用后续的缺失诊断、年度序列、门槛与 XGBoost：

| 路径 | 实现 | 适用 | 结果目录 |
| --- | --- | --- | --- |
| `deterministic`（手动/规则） | 按 `extraction.rules` 的正则逐份正文取唯一数值，本地核对年份、发布时间与单位；不调用清洗 LLM | 版式固定的年度公报，数字位置稳定 | `output/cli/deterministic` |
| `llm`（自动，原路径） | 本地规则召回 → 落盘 CSV → LLM 分批清洗 → 本地原文/数字校验 | 版式不固定、表述有歧义的材料 | `output/cli/llm` |

示例规则（见 `config/research-grain.json`）：

```json
"extraction": {
  "mode": "deterministic",
  "rules": [{"subject": "粮食", "metric": "production", "region": "全国",
             "pattern": "粮食产量\\s*(\\d{4,6})\\s*万吨", "unit": "万吨"}]
}
```

规则正则必须且只能有一个捕获组；同一正文命中多个不同数值时不自动选取。未配置规则时，`deterministic` 仍使用本地规则且不调用 LLM 清洗。CLI 可用 `--extraction llm|deterministic` 临时覆盖配置。网页在提交前选择“自动：LLM 清洗”或“手动：配置规则确定性抽取”，任务目录按 `output/web/<mode>/<run_id>` 分层。

LLM 路径的字段归一、同文档 block_id 重挂、年度公报标题地域回填与变动量排除由本地校验完成；模型返回的 scope 对象和空 review_reason 会先归一，但原文数字和单位仍严格核对。实现细节、实测数据和遗留问题见[交接文档](交接_20261022_双抽取路径.md)。

## 模型与预算

| 用途 | 实际模型/实现 | 默认运行方式 |
| --- | --- | --- |
| 中文向量检索 | BGE-small-zh-v1.5 的固定版本 INT8 ONNX，24 MB 左右权重 | CPU，2线程，batch 8，最长256 tokens |
| 初步提取与清洗 | 先本地规则提取写CSV，再LLM整理口径、本地原文校验 | 根.env选择的API；关闭时保留本地规则 |
| 训练异常标记 | IsolationForest，100棵树 | 至少64条真实训练样本才拟合；默认只标记，不把异常等同错误 |
| 缺失诊断 | WGAN_Trainer 的 pandas/pyampute/SciPy | 只诊断，不伪造缺年/目标标签 |
| 可选增强 | WGAN-GP，PyTorch 或 TensorFlow | config/wgan.json 原参数：5000轮、原网络、device=auto |
| 年度预测 | XGBoost 3.1.1，120棵树，深度2 | CPU，2线程，无自动超参搜索 |
| 最终发展趋势 | 根.env选择的API语言模型 | 最后单独无记忆请求，只说趋势 |

默认最多100网址、16 MB规范化正文、2000段落、10000数值候选、3条年度序列。截断和跳过会记录。普通本地分析阶段默认最长1200秒；GAN正式建模阶段默认不设超时（gan.timeout_s=null），不强制屏蔽GPU或限定线程。Ctrl+C会停止解释器子树。预算是工程限制，不能保证任意网页/任意数据都在固定耗时内完成。

GAN达到真实训练门槛后，先询问是否训练及框架；生成后再显示t-SNE并确认是否采用。训练与绘图直接读取 config/wgan.json，该文件为 WGAN_Trainer 原 config.json 的逐字节副本，不再套用100轮或小网络。默认training：epochs=5000、batch_size=64、latent_dim=100、n_critic=5、learning_rate=0.0002、lambda_gp=10、G=[128,256,512]、D=[256,128,64]、device=auto、gpu_memory_mb=8192、n_generate=2000（仅原仓库来源兼容，不作为实际数量）、generation_batch_size=256、precision=float32、seed=42、log_every=1。实际设备由原算法与所安装框架决定，不暗中改为CPU；本机测试阶段改用CUDA版PyTorch，auto可选择GPU；CPU版部署仍可选requirements/gan-pytorch.txt。原配置中的Reference/Pollutant是实验专用列名，年度管线输入已数值化，不套用这些列名或填造标签；此边界记录在配置来源报告中。框架缺失或明确GPU不可用会报告失败，不擅自换框架。

charts沿用原JSON：standard、tsne_perplexity=null（原默认30，小样本时调整）、每组最多1000点、1000次迭代。不再限为600点/500次。两组使用真实训练表拟合的MinMaxScaler(-1,1)并联合降维。均值漂移、跨列相关性、范围、多样性和近重复采用独立的数值初筛；通过初筛不等于GAN可靠，最后还须真实验证集比较。
## 自动建模边界

自动提取保持 pending，不假冒人工批准；CLI 允许结构检查通过的候选做探索，标记 machine_candidate_unreviewed，原 HTTP 服务仍默认要求人工审核。

当前建模对象是同口径年度指标，不是任意文本或任意 CSV 的自动回归。至少20个连续真实年度、10个真实训练标签，另留4个验证年和4个测试年；真实发布时间还必须允许历史可见，实际需要的年度数可能更多。config 的 subjects/regions 可声明主体和地域别名；默认空时照常输出匹配信息，但口径不明的候选不会强行建模。

清洗异常模型、归一化和 GAN 只接触训练集。用户确认采用后，再由真实验证集决定增强方案是否进入最终模型，然后才计算测试指标。生成数据单独标记，不计入真实样本门槛。即使模型跑通，未优于末值基线、样本未复核等限制会交给助手说明；不编造置信区间或因果结论。

## 输出与覆盖

每轮覆盖 output/cli/<llm|deterministic> 中程序登记的上轮文件；旧模型/旧图在新一轮没有模型时也会清掉，避免误认。不同抽取路径互不覆盖。用户自建文件保留，不递归删除目录，也不影响 output/web 的网页结果。输入网址 TXT 不会被改写。旧版根 output 平铺文件保留，未自动搬动；新结构见 [结果说明](../output/README.md)。

下表路径均相对于 `output/cli/<llm|deterministic>/`；确定性路径的 `llm_input.csv` 只有表头，`metadata/analysis.json` 的 `extraction` 记录规则数量与未采纳项。

| 文件 | 内容 |
| --- | --- |
| report.md / metadata/llm.json | 最终报告、API状态和建议 |
| data/information.csv | 与需求匹配的原文片段、来源、坐标、向量分数 |
| data/raw.csv / data/candidates.csv | 原始数值候选，未人工批准 |
| data/llm_input.csv / data/review.csv | 已落盘清洗输入、有疑问/无效/未处理记录 |
| metadata/llm_cleaning.json / metadata/extraction_context.json | 清洗批次审计及本地字段/单位定义 |
| data/cleaned.csv / data/rejected.csv | 可比候选与完整排除理由 |
| metadata/access.json / metadata/materials.json | 各网址处理状态与来源文件引用 |
| metadata/missing.json / charts/missing.png | 缺失诊断与图表；没有数值时不伪造图表 |
| metadata/analysis.json / data/series.csv | 结构化整理、口径序列与截断信息 |
| metadata/config.json / metadata/result.json / metadata/status.json | 本轮实际配置、建模结果、完成状态 |
| series/短哈希/ | 各口径独立模型产物 |

成功建模子目录的 data/ 含train/validation/test.csv、predictions.csv、可预测时forecast.csv；models/含xgboost.json；charts/含evaluation.png、importance.png、trend.png、comparison.png与原生TreeSHAP的shap.png。实际训练GAN时增加data/generated.csv、metadata/quality.json、metadata/gan_review.json、charts/gan.png及gan/权重与损失记录。generated.csv初筛通过也不自动标为已采用；是否进入最终模型另行记录。

GAN 训练后还会输出 charts/gan_tsne.png（真实训练样本/生成样本联合 t-SNE）、data/gan_tsne.csv（二维坐标、来源行号、生成标记）和 metadata/gan_tsne.json（抽样、随机种子、尺度拟合范围及参数）。点数与迭代次数直接取 config/wgan.json 的 charts；默认每类最多1000点、1000次迭代。MinMaxScaler(-1,1)只拟合真实训练表，验证/测试不参与投影。质量被拒的生成样本仍可展示，但图例注明 rejected；常量或样本不足会明确说明不适用。点云重叠不作为质量通过依据。

缓存、来源快照和助手 JSON 会话仍在 data，便于审计和重用；不进入 Git。output 会覆盖，重要报告请使用者自行另存。配置和导出引用使用相对路径，运行时才解析本机绝对位置。

