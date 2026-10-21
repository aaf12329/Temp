# 独立前端交互原型

项目目标很大，甚至可以说很宏大：把公开信息转化为有来源、有口径、可复核的产业判断。这里是给人类设计师使用的完整交互骨架，样式只保留基础布局，最终视觉设计交给人类。

Next.js 16.4.2 / React 19.3.1 / TypeScript 5.9.3。打开即用，不做登录、注册或用户中心。**未连接后端**；不采集、不训练、不调用 LLM，不需要 Python、CUDA、数据库或密钥。所有图与指标均为合成演示，刷新后清除状态。

Standalone frontend prototype: nine in-memory views, no backend, login, model execution or API credentials. All charts, metrics and downloads are synthetic demo fixtures. Human designers own the visual design; AI startup guidance is in Agent.md.

## 运行与切换

需要 Node.js 24+（含 npm）。在本目录打开终端：

```powershell
npm ci
npm run dev
```

打开终端打印的 Local 地址，通常是 http://127.0.0.1:3000。保持终端运行，Ctrl+C 停止。端口占用时以实际输出为准，不结束其他程序。独立交接包提供“启动预览.bat”，双击即可检查环境、首次安装依赖并启动。

顶部“页面预览与演示分支”默认展开，点击编号直接切换：

| 编号 | 页面 | 主要内容 |
| --- | --- | --- |
| 1 | 输入 | 每行一个网址的TXT + 一句话需求 |
| 2 | 处理过程 | 手动阶段、访问失败与跳过原因 |
| 3 | 整理结果 | CSV预览、建模门槛、数据不足退路 |
| 4 | 建模选择 | GAN可选、PyTorch/TensorFlow、生成倍数 |
| 5 | GAN生成 | 训练、生成和筛选阶段的演示 |
| 6 | t-SNE与确认 | 分布布局、质量失败示例、采用/不采用 |
| 7 | XGBoost建模 | 手动进度、失败和重试 |
| 8 | 模型评估 | 六种图、MAE/RMSE/R²、测试明细与下载 |
| 9 | 分析与建议 | LLM占位、结果与限制、完整报告下载 |

九个页面是同一个URL下的React视图，不是九个独立网站。直接预览会重置选项并使用预设演示状态；要看选项如何传递，请走页面下方的“继续”按钮。浏览器前进/后退不承担流程导航。

## 完整演示路径

先选择演示分支，再下载示例TXT、选取它并输入需求。TXT只在浏览器读取：最多1MB、100个去重有效HTTP(S)网址；支持BOM、注释、空行和错误行提示。

```text
ResearchInput { fileName, urls[N<=100], query }
  → 本地读取/去重 → 手动演示采集/匹配/整理
  ├─ 数据不足 → 整理CSV → 分析建议占位 → 报告
  └─ 达到条件（预设40条合成记录，非实际判定）
       → 建模选择
       ├─ 不尝试GAN ──────────────────────┐
       └─ WGAN框架 + 生成比 → 生成演示      │
             → t-SNE布局 + 质量检查         │
             ├─ 采用通过检查的数据 ─────────┤
             └─ 不采用，或检查失败 ─────────┤
                                           ↓
                         XGBoost演示（可演示失败/重试）
                           → 性能评估 → 分析建议 → 下载
```

生成比指“生成样本数 / 真实训练样本数”，只按训练集计算。可选0.5、1、2、5倍，预设5倍用于查看本轮讨论的方案；24条训练样本对应120条生成样本，不是总量扩大到5倍。验证/测试各8条不参与GAN。**这些是界面合同，不修改Python或实际训练JSON；也不表示24条数据足够训练可靠GAN。**

质量失败时禁用采用按钮，仍能仅用真实数据建模。返回建模选择会清除GAN确认和旧结果。切换演示分支或刷新会清空本轮状态。

## 图与下载的真实性边界

- t-SNE位置使用固定二维布局点，未执行真实降维；SVG下载也标明布局示意。
- 预测散点、时间对照、残差、方案比较来自同一组8条固定合成测试观测；指标在浏览器据此计算，CSV与JSON保持一致，不代表训练过模型。
- 回测条形与SHAP条形是另外的布局占位，未执行回测或SHAP计算，不是前述测试指标的拆分。
- 模型图类型参考 WGAN_Boost_Xgboost 的 S4/S11/S15 思路，未复制其材料领域数据、权重或随机划分流程： https://github.com/aaf12329/WGAN_Boost_Xgboost
- 目前“预测CSV”是留出测试期的对照示例，没有生成真实未来预测，也没有校准预测区间。
- 两行整理CSV是另一个最小导出示例，不冒充建模分支的40行记录。示例TXT使用example域名。
- 所有导出由浏览器下载；不写后端output、不配置SQL，也不调用LLM。下载文件名与内容保留演示标记。

## 结构与调用树

```text
Home → ResearchPrototype（显式聚合入口）
       ├─ ResearchFlow/reduceFlow：事件 → FlowState快照
       ├─ InputPanel → parseUrlList → ResearchInput
       ├─ ProgressPanel / ResultPanel / ModelPanel：数据与回调
       ├─ TrainingPanel：GAN和XGBoost共用进度结构
       ├─ GanReviewPanel：点布局、质量状态、采用确认
       ├─ EvaluationPanel → AnalysisChart / BarChart（共用SVG）
       ├─ ReportPanel：文本与下载动作
       └─ downloadText：统一浏览器Blob下载

共用层：
  src/types/research.ts        数据合同
  src/lib/research-flow.ts     流程类与reducer
  src/lib/demo-analysis.ts     合成观测、指标、生成计数、导出文本
  src/lib/url-list.ts          TXT解析
  src/lib/download.ts          下载I/O
  src/components/AnalysisChart.tsx  图形
演示输入与采集占位：src/features/research/demo.ts
样式入口：src/app/globals.css
```

组件只收props/回调，不横向操作其他页面。将来总装应增加适配层，替换演示数据并保持真实训练、验证、测试隔离；当前不预先绑定后端合同。

## 检查

```powershell
npm test
npm run typecheck
npm run build
```

8项合成合同检查覆盖TXT、门槛、GAN确认/失败、分支重置、训练失败恢复，以及CSV与指标一致性。类型检查和构建还需执行；实际本轮结果见交接包验收说明或主项目docs/前端后半流程验收记录.md。生成物node_modules、.next、next-env.d.ts和tsbuildinfo不需要分享。所有源码路径相对当前目录。
