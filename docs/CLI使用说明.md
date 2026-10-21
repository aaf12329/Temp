# CLI 使用说明

当前实现优先服务 i5-12450H、16 GB 内存的 CPU 轻薄本：不做前端，不启动数据库；除了最后的信息分析助手，所有处理均在本机运行。

## 启动与输入

双击根目录 `启动CLI.cmd`，只输入两项：

```text
网址清单文件地址（每行一个网站）：<你的 TXT 地址>
要检索什么信息（一句话）：贵阳餐饮业的收入、消费规模与发展变化
```

清单可放桌面或其他位置，支持中文、空格、带引号的 Windows 绝对地址。每行一个 http(s) 网页或文本 PDF 地址；空行和 # 开头注释忽略。示例为 config/urls.example.txt。其余模型参数读取 config/cli.json。

也可以在项目根目录运行：

```powershell
./.venv/Scripts/python.exe -m app.cli research
# 或直接运行相同入口，可显式提供参数以便重复操作
./.venv/Scripts/python.exe -m app.pipeline_cli --urls-file <清单TXT> --query "检索需求"
```

首次新建环境：

```powershell
./scripts/Setup.ps1 -GANBackend pytorch
# 可选 TensorFlow，仍安装在相同根 .venv
./scripts/Setup.ps1 -GANBackend tensorflow
./.venv/Scripts/python.exe -m app.cli model-setup
```

Setup 的 init 步骤是保留的主服务安装过程，会准备服务数据库；CLI 自身不打开或使用它。纯 CLI 已有根环境时，可安装根依赖/主包/助手包及选定的 requirements/gan-*.txt，无需执行服务 init。model-setup 只下载固定版本向量权重；日常检索不联网请求向量服务。

根 .env 配置 INDUSTRY_ADVISOR_API_KEY、INDUSTRY_ADVISOR_BASE_URL、INDUSTRY_ADVISOR_MODEL。程序只在最后一步使用这些设置，终端打印模型名称，不打印 key。默认 llm=api；未配置或 API 失败时保留 CSV、指标和本地状态说明，明确标记没有获得 LLM 总结，不冒充成功。llm=off 可只导出本地结果，demo 是显式演示输出。

## 实际流程与终端提示

```text
网址TXT的有效行 U[N] + 用户一句需求 q
  → collect：按网址顺序检查策略/robots/HTTP/类型 → 材料 R[K]
  → stage_records：隔离只读副本 + 原始相对引用映射
  → workflow.analyze
       load_corpus → Encoder/score_passages → extract_facts
       → 规则排除及逐条理由 → 缺失诊断 → 自动同口径年度行
  → 信息整理完成
  → workflow.model(plan)：时间切分 + 固定浅树验证
       数据不够 → data_only → API助手
       真实模型已经优于末值基线 → 不用GAN → XGBoost
       真实训练样本够且验证未优于末值 → 询问框架 1/2/0
  → WGAN-GP仅训练集增强 → 质量初筛 → 验证集选择真实/增强模型
  → 真实测试评估 + 可选未来探索预测 + PNG
  → summarize：匹配原文摘录 + 实际指标/缺口 → API助手 → output/report.md
```

终端逐网址打印 HTTP、类型、成功/跳过及原因；例如 robots 禁止、登录墙、拦截、未支持的动态网页或扫描 PDF。已有 data/policy/site_policy.json 限制优先；无该主机记录时，用户输入的单页路径作为本轮允许声明，仍不绕过任何 robots、登录和访问限制。程序不能凭自动检查证明法律许可。

清洗逐项原因和完整来源另写 CSV/JSON。控制台显示前十条候选排除理由，其他见 rejected.csv。只支持当前静态 HTML 与文本 PDF；不存在“任何网站都能解析”的承诺。

## 模型与预算

| 用途 | 实际模型/实现 | 默认运行方式 |
| --- | --- | --- |
| 中文向量检索 | BGE-small-zh-v1.5 的固定版本 INT8 ONNX，24 MB 左右权重 | CPU，2线程，batch 8，最长256 tokens |
| 数字抽取与基本清洗 | 本地证据规则，保留原文坐标与单位 | 不调用语言模型 |
| 训练异常标记 | IsolationForest，100棵树 | 至少64条真实训练样本才拟合；默认只标记，不把异常等同错误 |
| 缺失诊断 | WGAN_Trainer 的 pandas/pyampute/SciPy | 只诊断，不伪造缺年/目标标签 |
| 可选增强 | WGAN-GP，PyTorch 2.9.1+cpu 或 TensorFlow CPU 2.21.0 | 小网络，100轮，CPU，单任务 |
| 年度预测 | XGBoost 3.1.1，120棵树，深度2 | CPU，2线程，无自动超参搜索 |
| 文字结论与建议 | 根 .env 选择的 API 语言模型 | 只在最后调用 |

默认最多100网址、16 MB规范化正文、2000段落、10000数值候选、3条年度序列。截断和跳过会记录。单个本地分析/建模阶段最长1200秒，可在 JSON 中调整；超时停止解释器子树并回退已有数据。预算是工程限制，不能保证任意网页/任意数据都在固定耗时内完成。

GAN 只有达到真实训练门槛且有尝试必要时才询问框架。终端输入1或pytorch；2或tf/tensorflow；0跳过。其余参数在 JSON 的 gan.params：epochs、batch_size、latent_dim、n_critic、learning_rate、lambda_gp、beta1/beta2、generator_layers、critic_layers、seed、log_every。CPU 档固定 device=cpu、gpu_memory_mb=0。框架缺失时提示依赖文件，本轮保留真实数据模型。

## 自动建模边界

自动提取保持 pending，不假冒人工批准；CLI 允许结构检查通过的候选做探索，标记 machine_candidate_unreviewed，原 HTTP 服务仍默认要求人工审核。

当前建模对象是同口径年度指标，不是任意文本或任意 CSV 的自动回归。至少20个连续真实年度、10个真实训练标签，另留4个验证年和4个测试年；真实发布时间还必须允许历史可见，实际需要的年度数可能更多。config 的 subjects/regions 可声明主体和地域别名；默认空时照常输出匹配信息，但口径不明的候选不会强行建模。

清洗异常模型、归一化和 GAN 只接触训练集。验证集决定增强是否采用，然后才计算测试指标。生成数据单独标记，不计入真实样本门槛。即使模型跑通，未优于末值基线、样本未复核等限制会交给助手说明；不编造置信区间或因果结论。

## 输出与覆盖

每轮覆盖根 output 中程序登记的上轮文件；旧模型/旧图在新一轮没有模型时也会清掉，避免误认。用户自建文件保留，不递归删除 output。输入网址 TXT 不会被改写。

| 文件 | 内容 |
| --- | --- |
| report.md / llm.json | 最终报告、API状态和建议 |
| information.csv | 与需求匹配的原文片段、来源、坐标、向量分数 |
| raw.csv / candidates.csv | 原始数值候选，未人工批准 |
| cleaned.csv / rejected.csv | 可比候选与完整排除理由 |
| access.json / materials.json | 各网址处理状态与来源文件引用 |
| missing.json / missing.png | 缺失诊断与图表；没有数值时不伪造图表 |
| analysis.json / series.csv | 结构化整理、口径序列与截断信息 |
| config.json / result.json / status.json | 本轮实际配置、建模结果、完成状态 |
| series/短哈希/ | 各口径独立模型产物 |

成功建模的子目录含 train/validation/test.csv、predictions.csv、可预测时的forecast.csv、xgboost.json、evaluation.png（实际/预测、残差、直方图）、importance.png、trend.png。实际训练 GAN 时再增加 generated.csv、quality.json、gan.png 和 gan/ 下数值权重/归一化/损失记录。

GAN 训练后还会输出 gan_tsne.png（真实训练样本/生成样本联合 t-SNE）、gan_tsne.csv（二维坐标、来源行号、生成标记）和 gan_tsne.json（抽样、随机种子、尺度拟合范围及参数）。默认最多600点，两类各最多300，CPU最多500次迭代；可在 gan.tsne_max_points 调整，上限1000。StandardScaler 只拟合真实训练表，验证/测试不参与投影。质量被拒的生成样本仍可展示，但图例注明 rejected；常量或样本不足会明确说明不适用。点云重叠不作为质量通过依据。

缓存、来源快照和助手 JSON 会话仍在 data，便于审计和重用；不进入 Git。output 会覆盖，重要报告请使用者自行另存。配置和导出引用使用相对路径，运行时才解析本机绝对位置。
