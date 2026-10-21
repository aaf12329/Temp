# 前端原型入口

先遵守 ../AGENTS.md，再读 README.md。此目录为独立 Next.js + React + TypeScript 结构原型，当前只做到 GAN 训练前。

所有交互均为浏览器内存状态，演示数据在 src/features/research/demo.ts。没有后端请求、上传、代理、API 密钥、数据库访问或训练执行。后续接线必须得到所有者明确授权。

ResearchPrototype 是显式聚合层，功能组件不横向调用；共用类型在 src/types，纯解析和浏览器下载在 src/lib。样式只保留结构级基础布局。

npm run typecheck / npm test / npm run build 验证；本地 Next.js 文档在 node_modules/next/dist/docs。构建、缓存、node_modules、next-env.d.ts 不入库。
