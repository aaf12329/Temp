# CSV 后 LLM 数据清洗

2026-10-22，所有者确定顺序：先采集，再扩大向量和关键词匹配，先把数据写入CSV，之后调用LLM清洗；缺失处理与后续建模沿用现有体系。

同一入口另提供确定性规则路径（`extraction.mode=deterministic`）：按配置正则逐份正文取唯一数值，本地核对年份、发布时间和单位，跳过本文的LLM清洗；两条路径共用后续年度序列、门槛和XGBoost。本文描述LLM路径。字段形状归一、同文档 block_id 重挂、统计公报标题地域回填、变动量/分品种排除与发布日月份不计入季度口径，均在本地校验层完成。

## 输入和输出

```text
网址TXT U[N] + 研究需求 q
  → collect / stage_records → 校验正文、指纹与来源
  → workflow.analyze → BGE/关键词/指标数值/相邻数值行召回
       → data/information.csv：全部匹配原文
       → data/raw.csv：原规则初步数值候选，清洗后不覆盖
       → data/llm_input.csv：LLM预算内匹配段落、标题、发布元数据、正文头部与前后文
  → pipeline_llm.clean_csv：真实读取CSV，按字符预算组批
       → IntegratedAdvisor.clean_records：每批独立2条消息，无历史、无工具、无自动重试
       → {rows: [{block_id, quote, raw_value, unit, metric, ...}]} JSON
       → llm_records.validate_rows：原文引句、数字、字段证据、单位维度与统计范围检查
  → 无密钥子进程 workflow.finalize
       → data/candidates.csv：规则与有据LLM候选合并，仍为pending
       → data/cleaned.csv：通过本地检查的候选
       → data/review.csv：不确定、非法返回、未处理来源块及理由
       → data/rejected.csv：口径检查未通过候选及原因
       → metadata/missing.json、charts/missing.png：按清洗后候选重算原有缺失诊断
       → data/series.csv → 原有时间切分/建模门槛
  → 用户选择GAN、五倍生成、t-SNE审查与采用确认 → XGBoost
  → IntegratedAdvisor.analyze_trend：单独无记忆请求，只输出发展趋势
```

数据不满足建模条件时仍输出CSV与最终趋势或证据不足说明。LLM清洗不会填造真实年份、目标标签或观测，也不降低XGBoost/WGAN的真实样本门槛。当前缺失处理仍以原有诊断与训练处理为准，不新增通用插值，也不将GAN当作缺失历史证据。

## 配置

根config/cli.json中的semantic_threshold默认为0.50（此前0.64），这只是候选召回阈值，不是数据正确率。关键词、数值句与同材料相邻数值行补查均保留原分数；邻段扩展只有一层，不递归纳入整篇文章。

```json
"llm_cleaning": {
  "mode": "api",
  "batch_chars": 16000,
  "max_batches": 32,
  "max_blocks": 256,
  "max_output_tokens": 8192
}
```

mode=off关闭中间清洗API；根llm=off关闭中间与最终API，demo仅演示最终助手，中间使用本地规则。API设置沿用根.env的INDUSTRY_ADVISOR_MODEL、BASE_URL和API_KEY，不新增密钥。终端/网页打印实际清洗模型及批次状态，不输出凭据。无需新增依赖或Python环境。

批次输入默认最多16000个JSON字符（不含固定提示和词表），最多32批、256来源块，每批最多接收128条JSON记录。源文按文档轮流分配预算；超过预算的材料仍保留在information.csv，未处理块在review.csv中标记，analysis记录覆盖不足。来源块前后文和正文头部有界；标题较长或单块过预算会明确跳过。API截断、格式错误、网络、余额等失败停止后续批次，保留已完成结果，不自动重发。可按需求拆小来源清单或调整JSON预算。

## 本地校验边界

- 每条LLM记录只能引用本批来源块，数值句和字段引句必须出现在实际发送的同一来源中。
- 数字必须属于本块匹配段落，而非只出现在邻段；本地用Decimal和既有单位表换算，不接纳LLM自行计算的value。
- 年度、主体、地域要有原文依据；新实体可用明确原词，规范名需要配置别名支持。不能从问题或URL填事实。
- 发布时间优先用来源元数据；缺失时仅接受原文明示发布标签与完整日期。抓取时间不能替代发布时点。
- 限额、单企业、非全年、价格口径、目标/预测和约数保留复核限制。同年同口径不同值继续标记冲突，重复转载不增加真实样本数。
- JSON结构或引句无效的单条记录进入review，不使同批其他有效记录丢失；LLM不允许返回approved审核状态。
- 这些校验只验证引用与结构约束，不能数学证明语义归属正确。所有候选仍为machine_candidate_unreviewed，不能宣称人工核实。
- 当前只处理已采到规范化正文中的可读内容；没有新增OCR、浏览器渲染、任意多层表格解析或绕过网站访问限制。

## 状态与恢复

metadata/llm_cleaning.json保存输入指纹、逐批started/completed/failed、原始返回、用量、验证结果与未覆盖计数。状态可为disabled/missing_key/no_input/completed/partial/interrupted，不把失败显示成成功。相同输入重入复用已有结果，不再次付费；更改问题、CSV、模型或清洗预算后会生成不同指纹。

有可用原规则候选时，API关闭、缺密钥或失败会明确回退并保留本地结果；因此cleaned.csv中的extraction_method区分local_rules与llm_source_validated，不能将全部行称为LLM清洗成功。网页prepare或model阶段被重新领取时直接报告上次结果不明，不自动重做付费阶段。用户主动提交新任务是新的运行，可能再次产生API调用。

修改后需要正常退出原启动窗口，再重新启动API/Worker并提交新任务。恢复旧任务只读取旧产物。此次不改代理DNS、不重跑真实采集、不调用真实付费模型；隔离用例只使用合成来源和模型替身。验证结果在完成后记录到本页。

## 本轮验证

- 主项目完整Python回归：128通过、1跳过，107.31秒；跳过为Windows符号链接权限。
- 前端类型检查通过，10项现有交互检查通过；13个受影响Python文件Ruff通过。
- 新增清洗用例验证：CSV先于API存在、真隔离子进程生成CSV/年度行、原始CSV不覆盖、原文数字与跨批引用保护、缺失字段/发布时间限制、局部和季度口径保护、未选规则候选留待复核、失败不重试、相同输入复用及无记忆接口。
- 首次针对性检查发现关闭API时用子进程摘要覆盖磁盘完整分析的问题，已修复为先保留完整facts/series再加状态；完整回归已覆盖修复。
- 没有进行真实网站/付费LLM验收，没有新的生产构建或浏览器视觉验收，没有提交或推送。
