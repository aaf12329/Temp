# 独立前端结构原型

Next.js + React + TypeScript，当前仅做到 GAN 训练之前。打开首页即可使用，不设计登录、注册或用户中心。样式只保留基础结构，供后续视觉设计替换。

**尚未接入后端。** 没有 API 请求、文件上传、任务轮询、API 密钥、模型调用或训练执行。页面上的成功、失败、建模条件、数字和文字结果均为标注的合成演示。浏览器刷新后清除当前输入和选择，不写 Python 的 data/output，也不使用浏览器存储保存会话。

Standalone Next.js/React/TypeScript prototype, with no login or backend connection. The four views cover input, progress, results and pre-training choices. All displayed results and downloads are clearly marked as synthetic demo content. Run npm ci and npm run dev with Node.js 24+.

## 本地预览

Node.js 24 或更高版本。在本目录运行：

```powershell
npm ci
npm run dev
```

打开 http://127.0.0.1:3000。只启动前端，不要求启动 FastAPI、Worker、数据库或配置模型密钥。根快速部署入口会识别本目录并安装 npm 依赖，但仍只负责启动原 Python 后端，不把两端接起来。

页面顶部展开“页面预览与演示分支”，可直接检查四个页面，也可先选择演示分支，再走真实的浏览器文件输入流程：

```text
TXT 文件（<= 1 MB，最多100个有效去重网址）+ 一句话需求
  → 浏览器本地读取、校验和去重
  → 手动推进演示进度与日志
  ├─ 数据不足演示 → 合成 CSV + 报告占位
  └─ 达到条件演示 → 是否 WGAN + PyTorch/TensorFlow → 页面内确认
                                                        └─ 到此为止，不训练
```

“下载示例 TXT”的网址是 example.com/example.org/example.net 示例，不能当成产业数据源。下载的 CSV/报告在文件名和内容中均标注演示，不来自用户选择的网站；文字占位不冒充 LLM 输出。真实数据是否满足建模条件由将来的后端判断。

## React 结构与调用

```text
src/
├─ app/
│  ├─ layout.tsx                 中文文档与基础样式
│  ├─ page.tsx                   页面入口
│  └─ globals.css                结构级布局
├─ features/research/
│  ├─ ResearchPrototype.tsx      显式聚合：页面状态、事件、下载和演示分支
│  ├─ InputPanel.tsx             TXT + 研究需求
│  ├─ ProgressPanel.tsx          阶段进度 + 失败/跳过原因占位
│  ├─ ResultPanel.tsx            数据不足退路、CSV/报告、LLM位置
│  ├─ ModelPanel.tsx             WGAN意向与框架选择，到训练之前
│  └─ demo.ts                    独立合成数据与下载示例
├─ types/research.ts             共用前端契约，不绑定现有后端协议
└─ lib/
   ├─ url-list.ts                纯 TXT 解析与有效网址检查
   └─ download.ts                浏览器 Blob 下载 I/O

Home → ResearchPrototype
       ├─ InputPanel → parseUrlList → ResearchInput
       ├─ ProgressPanel ← phases / phase
       ├─ ResultPanel ← makeDemoResult → downloadText
       └─ ModelPanel ← ModelChoice → 内存确认
```

功能组件只接收数据和回调；后续接接口时，由聚合入口或单独适配层替换 demo 数据，保持组件边界。没有实现登录、训练进度、XGBoost 结果或任何后端接线。

## 检查

```powershell
npm test
npm run typecheck
npm run build
```

Node 24 用于运行原生 TypeScript 解析测试。Next.js 自动生成 next-env.d.ts 与 .next 类型，无需手工维护；它们和 node_modules 不入库。npm 依赖版本由 package-lock.json 固定。
