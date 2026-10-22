# 研究结果

此目录统一保存 CLI 和网页完整研究流程的结果。已有任务按各自执行时的代码和配置生成；旧结果不会随源码更新而重算，不能当作新版本已通过验收。

```text
output/
├─ README.md
├─ cli/                              CLI 最新一轮；只覆盖程序登记的旧产物
│  ├─ llm/                           LLM自动清洗路径（原路径）
│  │  ├─ report.md                   最终报告，直接打开此文件
│  │  ├─ data/                       匹配原文、原始候选、清洗/排除、年度序列 CSV
│  │  ├─ charts/                     整批缺失等图表
│  │  ├─ metadata/                   配置、访问原因、数据诊断、结果、状态、LLM回复 JSON
│  │  └─ series/<序列短哈希>/         每个统计口径分别保存
│  │     ├─ data/                    train/validation/test、生成数据、预测 CSV
│  │     ├─ charts/                  t-SNE、损失、误差、趋势、SHAP、性能对比 PNG
│  │     ├─ models/                  xgboost.json
│  │     ├─ metadata/                GAN质量、采用记录、投影参数 JSON
│  │     └─ gan/                     原 WGAN 组件的权重、归一化与训练记录
│  └─ deterministic/                 确定性规则路径（PDF式）；内部结构同上
└─ web/
   └─ <llm|deterministic>/<执行令牌>/  网页每次处理阶段的独立目录；内部结构同上
      ├─ report.md                  只有最终阶段生成
      ├─ metadata/manifest.json     任务ID、父任务、问题、阶段、抽取路径、实际指标
      └─ data/、charts/、series/…
```

网址 TXT + 一句需求 → 采集与本地处理 → `PipelinePaths` 分类文件 → CLI/网页报告；模型不足时仍导出已获得的信息。某类产物没有实际生成时，其目录或文件可以不存在。

两条抽取路径共用后续缺失诊断、年度序列、门槛和 XGBoost，仅入口不同：`deterministic` 按配置 `extraction.rules` 的正则逐份正文取数并本地核对年份、发布时间和单位，不调用清洗 LLM；`llm` 先写 CSV 再分批调用 LLM 清洗。确定性路径的规则数量、未采纳项与方式见 `metadata/analysis.json` 的 `extraction`；`data/llm_input.csv` 在该路径为空表头。2026-10-22 用26个粮食公报网址实测：确定性路径26个连续年度、LLM路径25个连续年度，均进入 XGBoost exploratory_model；两条路径测试均未优于末值基线。

LLM路径先写CSV再调用清洗：data/llm_input.csv保存分批输入，data/raw.csv保留清洗前规则候选；data/cleaned.csv为本地检查通过结果，data/review.csv保留疑问、无效返回或未处理块。metadata/llm_cleaning.json保存API批次审计，metadata/extraction_context.json保存本地指标/单位及别名定义。确定性路径跳过清洗，直接用规则候选生成本地检查结果，metadata/analysis.json的extraction记录规则与未采纳项。最终metadata/llm.json只存独立的发展趋势回复；两个阶段不共享聊天记忆。API未启用或失败会明确保留本地规则结果。

质量核对新增metadata/data_quality.json、data/data_quality_fields.csv、charts/data_quality.png。分别列清洗前、整理后、可用候选的字段缺失和格式异常；空集合比例留空，图表只展示实际计数。没有模型时仍可能存在这张处理质量图，不表示已经训练成功。HTML字段补查依据在metadata/materials.json各来源记录的meta.source_extraction。

网页的整理、GAN审查、最终建模分别使用独立执行令牌，父结果保留只读；因此一次完整研究可能对应多个目录。通过 metadata/manifest.json 的 operation_id、parent_id 和 question 找到所属研究。网页下载仍通过任务的登记文件列表和鉴权接口，不把 output 作为公开静态目录。

CLI 只覆盖 output/cli/<模式> 内地址簿登记的文件，不删除自建文件，也不影响网页任务或此说明。旧版根 output 平铺文件、data/pipeline 历史网页产物没有搬动或删除；旧网页任务仍可按原记录读取。旧文件请按其实际日期识别，不当作新一轮结果。

数据库、来源快照、向量权重缓存、助手会话和人工记忆继续保存在 data，运行日志保留在 work。独立旧研究接口也保留原 data/research 存储。自定义 INDUSTRY_DATA_DIR：名称为 data 时输出在其同级 output；其他名称时输出在该数据目录内部的 output，隔离演示与临时环境。

本目录结果默认不进入 Git，仅此说明入库。不要手动修改待审查 GAN 文件后继续采用，系统会核对内容哈希。CLI 重要结果请另存；网页结果暂无自动清理，需自行管理磁盘空间。
