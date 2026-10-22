# 网页前端与设计预览

主项目已在代码层接通 React/Next.js → FastAPI → 持久Worker → Python研究管线。首页默认“实际研究”，顶部可切换“设计预览（合成演示）”。不做登录、注册或用户中心；两种模式的数据与状态分开。最终视觉设计由人类负责。

React/Next.js frontend with a local FastAPI/worker connection. The actual workflow and synthetic design preview are separate. Credentials stay server-side. After collection, users choose the original automatic workflow or the textbook branch, which currently saves source materials and stops for inspection.

## 启动

完整平台：双击“启动脚本/01_平台/一键启动.bat”，默认网页 http://127.0.0.1:3000 ，API http://127.0.0.1:8000/docs 。脚本准备根Python环境及npm依赖，再监管API、Worker和Next。已有端口占用时不会接管其他服务。

已有依赖可在根目录运行：

```powershell
./.venv/Scripts/python.exe -m app.local_service --with-frontend
```

单独改前端：需要Node.js 24+，在本目录执行npm ci、npm run dev。没有API/Worker时仍可切到设计预览；实际研究会明确报连接失败，不回退到假结果。停止用Ctrl+C。不为设计人员配置Python、CUDA或API key。

## 实际流程

```text
TXT本地读取（最多1MB/100个去重HTTP(S)网址）+ 一句话需求
 → Next同源接口 → FastAPI提交collect操作 → Worker仅采集并保存材料
 → branch_choice：采集完成后让用户选择
      ├─ 教材分支 → route(textbook) → materials_ready，保存材料并停止
      └─ 原自动流程 → route(llm)，复用已采正文继续
      → 扩大向量/关键词匹配 → 先写CSV
      → 分批无记忆LLM清洗 → 本地原文/数字校验
      → 缺失诊断/年度序列/查门槛
 ├─ 不足 → CSV + 原因 + 最终分析/状态报告
 └─ 达到条件 → 用户选择
      ├─ 不训练GAN → model操作 → XGBoost
      └─ 选PyTorch/TensorFlow → gan操作
           → 真实训练行数×5生成 → 实际t-SNE与数值检查
           → 用户确认采用，或拒绝后仅用真实数据
           → model操作 → XGBoost
 → 真实测试指标/实际PNG/CSV → 最终LLM（可关闭）→ 报告
```

分支选择出现在采集完成后；此前不会启动研究检索、清洗或建模。教材分支展示来源、失败原因及sources.zip下载，后续定制解析待确定。网页任务目录按`output/web/<路径>/<令牌>`分层，GAN/建模阶段沿用父任务路径。旧prepare/确定性正则接口兼容保留，当前网页不将其当作教材分支。详见[采集后分支](../docs/采集后分支.md)。

GAN还有独立的真实训练样本门槛，达到XGBoost门槛不意味着一定可以用GAN。生成数量只按真实训练集计算，验证/测试完全隔离。确认后读取同一批带哈希的生成文件，不重训GAN。采用后真实验证未改善时回退真实数据模型，页面会说明。

实际任务由Worker执行，页面只轮询queued/running/done/failed，不显示虚假的训练百分比。切换设计预览保留本页状态；刷新后可用任务编号恢复查询。浏览器关闭不会取消后台任务。提交响应不明确时重试相同幂等ID，不重复提交另一项计算。

实际图包括预测对照/残差/误差分布、时间趋势、原生重要性、模型对比、原生TreeSHAP及缺失图；有GAN才显示t-SNE和训练图。没有真实回测产物时不显示回测占位。R²不是可信度百分比，SHAP不证明因果。最终助手缺密钥或失败时仍有CSV与明确状态报告。

## 服务端边界

浏览器只调用 /api/platform 白名单接口。Node从根.env或自身环境读取服务token，转发到本机FastAPI；不使用NEXT_PUBLIC凭据，不把token交给浏览器。POST检查同源，转发目标仅本机。新网页产物保存在每次领取的独立output/web目录，经鉴权下载；历史任务继续读取data/pipeline。CLI覆盖output/cli登记产物，不用SQL。结果分类见根output/README.md。

实际运行消息由ResearchMessages展示：每个网址的真实开始和结果带“当前序号/总网址数”，失败保留具体原因。LiveResearch每1.5秒查询任务进度；滚动查看历史时不强制跳到底部，完成或失败后仍保留。消息来源为Worker受令牌保护的持久快照，恢复任务编号可继续查看；没有伪造训练进度。后端更新需正常重启API/Worker后再提交新任务。

最终“发展趋势”区域只显示本轮单次LLM趋势结论，不渲染整份技术状态报告。最终调用不读取或写入聊天、人工记忆、对话摘要，不输出追问或行动建议；数据不足则说明无法判断。完整数据、模型图表与report.md仍可下载。独立终端聊天及旧建议接口的记忆功能不属于这个最终研究调用。

实际结果另有“清洗前后数据核对”：列出规则候选、整理候选、可用候选、待复核记录和年度行，并可展开查看七个字段的缺失/格式异常。质量PNG和CSV可下载；这些是数据处理统计，不是模型可信度评分。没有候选时明确显示无候选，不显示伪造的完整率。

## 设计组独立目录

桌面“产业研究平台_前端设计交接”与同名ZIP是完整独立前端交互包，保持未接后端的设计快照。含源码、锁文件、启动预览.bat、Agent.md和预览图，不包含Python、环境、密钥或Node构建缓存。它直接打开九个演示视图，不依赖父目录。

九个设计视图：1输入、2处理、3整理结果、4建模选择、5GAN生成、6t-SNE确认、7XGBoost、8模型评估、9分析建议。顶部编号直接预览会重置演示选项；要看选择传递用各页继续按钮。生成比当前固定5倍。演示t-SNE是固定点布局、性能来自8条合成观测、回测/SHAP是位置占位，均未运行模型或LLM。

## 结构

```text
Home → ResearchWorkspace（模式聚合）
       ├─ LiveResearch（实际业务聚合）
       │   ├─ InputPanel → parseUrlList → ResearchInput
       │   ├─ submitStage/queryOperation/artifactUrl → 同源Next接口
       │   └─ LiveResults ← PipelineResult（实际产物）
       └─ ResearchPrototype（独立设计聚合）
           ├─ ResearchFlow/reduceFlow → FlowState
           ├─ Input/Progress/Result/Model/Training/GanReview/Evaluation/Report
           └─ 共用demo-analysis / AnalysisChart / downloadText

Next Route Handler → server/platform-proxy → FastAPI
src/types：前端与实际服务合同
src/lib：纯逻辑、浏览器I/O与合成fixture
src/server/paths.ts：服务端配置地址簿
src/app/globals.css：结构样式
```

npm test、npm run typecheck、npm run build是检查命令。所有者2026-10-22已恢复本轮测试：10项交互测试和类型检查通过；本轮未做生产构建或浏览器视觉验收，详见主项目docs/验证记录_20261022.md。node_modules、.next、next-env.d.ts与tsbuildinfo是生成物，不入库。
