# 前端原型入口

先遵守 ../AGENTS.md，再读 README.md 和 Agent.md。此目录为独立 Next.js + React + TypeScript 交互原型，当前覆盖输入到最终报告的九个视图。

所有交互均为浏览器内存状态，演示数据在 src/features/research/demo.ts。没有后端请求、上传、代理、API 密钥、数据库访问或训练执行。后续接线必须得到所有者明确授权。

ResearchPrototype 是显式聚合层，功能组件不横向调用；共用类型在 src/types，纯解析和浏览器下载在 src/lib。样式只保留结构级基础布局。

ResearchFlow封装演示状态。数据不足不进入建模；GAN只按真实训练数量计算倍数，生成后另行确认采用；质量失败不能采用，放弃仍可用真实数据建模。返回选择会清除旧GAN确认与结果。固定示例不证明真实样本足够训练GAN；不要把前端选择当成实际JSON修改。

性能数字与CSV来自同一组合成观测；t-SNE为固定布局点，回测/SHAP为额外布局占位，均未执行模型。不连接服务、不调用训练或LLM。Agent.md用于独立交接运行，人类负责最终设计。

npm run typecheck / npm test / npm run build 验证；本地 Next.js 文档在 node_modules/next/dist/docs。构建、缓存、node_modules、next-env.d.ts 不入库。
