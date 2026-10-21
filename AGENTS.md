# 本项目入口

先读 `README.md`、`docs/接口缺项与实现边界.md` 和 `docs/代码规范检查与修正.md`。

规范唯一入口：`<本地规范目录>/01_AI项目协作参考\README.md`。本文不复制协作规范。

环境为本目录 `.venv`。`app.common.paths` 是本工程路径地址簿，`app.common.file_io` 是文本/JSON 共用入口。`core`、`contracts`、`db` 与 `common` 为共享基础设施；`services` 是互不导入的功能层；`runtime`、`api/routes`、`worker`、`cli`、`main` 为入口/显式编排层。

原 HTTP 合同保持 v1.2。尚未提供真实爬虫，禁止用测试材料冒充实际研究数据。

产业研究建议助手已在本仓库子目录。所有者已明确授权将爬虫和建议助手接入本服务，并统一 Python 环境；早先的“不接线”约束已被该指令取代。当前按所有者要求暂停，恢复前先读 `docs/集成暂停交接.md`。已有适配层草稿尚未接入路由或 Worker，不得宣称集成完成。`.env` 仅供本地使用，不输出其中凭据。
