# CLI 阶段验收

日期：2026-10-21。来源网站的查找清单另行整理，不属于本次爬虫验收。

## 已完成

- 输入网址 TXT 地址和终端一句需求；支持带空格的绝对文件地址、UTF-8 BOM、注释、逐行去重和错误原因。
- 不构造 SQL runtime，直接受限采集；本地 CPU 向量、规则抽取/清洗、缺失诊断、自动年度探索建模与 API 助手串联。
- output 覆盖程序登记的旧产物；新轮不能建模时旧预测、旧图表清除，保留用户自建文件。输入清单不改写。
- WGAN 只有真实样本与验证表现符合尝试条件时询问 PyTorch/TensorFlow/跳过；其他参数保留在 JSON，实际配置写入 output/config.json。
- 训练异常模型、GAN 与归一化只拟合训练集；生成行单独标记；真实验证选择方案后才评分真实测试集。
- 小型 XGBoost 输出真实回测、探索预测、模型数值 JSON 与性能图。无证据/不足/异常时输出 CSV、原因与 API 总结；API 未配置时如实标记。
- 框架只装根 .venv；保留 HTTP 服务原行为和默认人工审核要求，CLI 自动候选始终未审核。

## 实际结果

| 检查 | 结果 |
| --- | --- |
| 平台+CLI测试（最终CUDA环境回归） | 71通过，1跳过 |
| 原爬虫/研究回归 | 522通过，80子测试通过 |
| 独立助手回归 | 30通过 |
| 合计 | 623通过，80子测试，1跳过 |
| 两后端真实CPU梯度惩罚训练 | PyTorch 2.9.1+cpu / TensorFlow CPU 2.21.0均通过 |
| Windows PowerShell 5.1 Setup -GANBackend pytorch | 实际安装/保留配置/迁移成功；pip check无冲突 |
| Ruff及生产源码结构/函数说明检查 | 通过 |
| PNG视觉核对 | WGAN曲线、真实留出散点/残差布局可读 |

跳过项是 Windows 当前用户无建立符号链接的权限；普通覆盖与文件边界用例已实际执行，不声称链接创建用例通过。测试全部使用临时目录、合成网页、合成数值或演示助手；没有请求真实付费模型，没有用真实产业数字评估预测质量。

默认小网络真实冒烟（接入t-SNE前，分别独立进程，CPU、2线程、64x3合成表、100 epochs）：

| 后端 | 训练及诊断耗时 | 峰值进程工作集 |
| --- | ---: | ---: |
| PyTorch 2.9.1+cpu | 3.285秒 | 390.19MB |
| TensorFlow CPU 2.21.0 | 4.137秒 | 409.16MB |

这些是当前执行机器的一轮观测，不是用户 i5-12450H 轻薄本的实测，不能外推任意训练数据的耗时。真实数据与大参数仍受逐阶段超时、输入规模和单任务限制。两后端均安装用于兼容验证，但实际运行只导入所选框架；不同时训练两套。

本机证据在忽略目录 work/：cli-final-regression.log、cli-crawler-regression.log、cli-advisor-regression.log、cli-setup-check.log、cli-pytorch-check.log、cli-tensorflow-check.log、cli-check/*/runtime.json。源代码修改前备份为 work/before-analysis-pipeline-43d7f4a.zip。

随后补入 GAN t-SNE：真实训练数据拟合尺度，两类联合降维，默认最多600点、500次迭代，拒绝的生成样本图例明确标记。接入后相关用例22通过、1链接权限跳过；新增5000+5000行预算抽样用例另1项通过。PyTorch默认100轮含t-SNE的一轮观测为4.124秒、397.14MB峰值工作集，已实际查看PNG。相关记录为work/cli-tsne-tests.log、cli-tsne-budget-check.log、cli-tsne-visual-check.log。该阶段累计覆盖621个测试和80个子测试；后续原JSON与质量门槛用例加入后，最终总数以上表623项为准，未重复计算重叠运行。

## 当前限制

支持静态 HTML/文本 PDF，未支持网页反爬绕过、浏览器渲染、扫描 PDF OCR。向量相似度门槛和 WGAN 质量阈值未经真实贵阳数据校准。自动年度建模要求来源、发布时间、主体/地域/单位明确且年度连续，其他信息仍能导出和总结。模型不优于末值基线时不能据此宣称有可靠预测能力。

没有新增前端、登录、多用户权限或 Linux Redis/PostgreSQL部署验收。真实模型 API key 仍由根 .env 提供，未从历史聊天复制凭据。本轮使用0个子代理，未向远端 push。


## 后续所有者调整与完整原配置验收

GAN取消性能兼容降档，config/wgan.json逐字节复制原仓库config.json。training和charts直接生效：5000轮、G=[128,256,512]、D=[256,128,64]、batch64、latent100、n_critic5、device=auto、生成2000行、t-SNE每组最多1000点/1000次迭代。GAN正式阶段不默认超时或强制CPU，检索及XGBoost预算独立保留。

早期100轮测试发现生成数据均值和相关性偏移却通过旧初筛，已增加均值标准差偏移、跨列相关性和最少生成数量检查，图例改为initial checks passed/failed。新门槛仍是工程启发式，不代替真实验证。原100轮夹具现在会被拒绝，并有回归用例。

原JSON的完整PyTorch CPU验收实际完成5000轮：64x3合成输入、生成2000行、联合t-SNE完成、通过收紧后的初筛；耗时189.847秒、峰值工作集409.74MB。记录work/cli-wgan-reference-5000.log。只证明完整算法接入可执行，不代表贵阳数据建模有效。随后原配置相关测试25通过、1项链接权限跳过。测试阶段按所有者要求优先使用现有机器GPU能力，GPU验收另记实际结果。


最终统一环境为根.venv：PyTorch 2.9.1+cu128、CUDA可用、RTX 5060 Ti 16GB。原JSON GPU完整5000轮实际结束，生成2000行并输出t-SNE，数值初筛通过。实际记录251.789秒（含生成、图表等）、峰值工作集1457.08MB、PyTorch峰值已分配显存21.44MB，见work/cli-wgan-cuda-5000.log。测试使用64x3合成矩阵，并与部分回归并行，不能作为严格GPU提速比；本轮没有比此前CPU记录更快。正式配置仍忠实于原JSON，不因为展示图好看而放宽初筛。

CUDA环境最终平台/CLI回归71通过、1链接权限跳过；与此前未改算法的爬虫522项、助手30项合计623项，另有80子测试。pip check、Ruff均通过。GPU可选安装文件requirements/gan-pytorch-cuda.txt（Setup -GANBackend pytorch-cuda）；CPU与CUDA版本互斥安装在同一个根环境，不创建第二个环境。没有网站采集测试、真实付费API调用或push。
