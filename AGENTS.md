# 本项目入口

先读 `README.md`、`docs/接口缺项与实现边界.md` 和 `docs/代码规范检查与修正.md`。

规范唯一入口：`<本地规范目录>/01_AI项目协作参考\README.md`。本文不复制协作规范。

环境为本目录 `.venv`。`app.common.paths` 是本工程路径地址簿，`app.common.file_io` 是文本/JSON 共用入口。`core`、`contracts`、`db` 与 `common` 为共享基础设施；`services` 是互不导入的功能层；`runtime`、`api/routes`、`worker`、`cli`、`main` 为入口/显式编排层。

原 HTTP 合同保持 v1.2。尚未提供真实爬虫，禁止用测试材料冒充实际研究数据。

产业研究建议助手位于桌面另一个目录，当前未接线；不要擅自加入本项目路由或 Worker。`.env` 仅供本地使用，不输出其中凭据。
