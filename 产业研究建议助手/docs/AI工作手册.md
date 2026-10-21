> 2026-10-21：已接入主平台，统一根 .venv / .env。正式与演示 JSON 按项目分别存放；本页旧环境数字保留作历史，当前接手先读根 AGENTS.md。

# AI 工作手册：产业研究建议助手

更新：2026年10月20日。本文记录项目事实，协作标准只给引用。

| 项目事实 | 当前值 |
| --- | --- |
| 本地目录 | `产业研究建议助手` |
| 类型 | 独立 Python 工程，终端入口，无 GUI 或 HTTP 服务 |
| 环境 | 根目录 `.venv`，Python 3.12.14 |
| 核心入口 | `advisor_bot.service.build_bot` → `Advisor.ask` |
| 命令入口 | `python -m advisor_bot` / `research-advisor` |
| 文件位置 | `advisor_bot/shared/paths.py` 是本工程唯一地址簿 |
| 文件读写 | `advisor_bot/shared/file_io.py`，UTF-8/LF 和原子替换 |
| 正式数据 | 当前工作目录 `data/`，或 ADVISOR_DATA_DIR 显式路径 |
| 演示数据 | 当前工作目录 `data_demo/`；假模型明确标记 |
| 真实调用验收 | 用户授权的一次调用成功，记录在 `work/api_smoke_result.json` |
| 自动化验收 | 临时目录 + 假模型/HTTP，详见 tests 与验收说明 |
| 参考仓库 | 桌面 `history_code_参考`，提交 4385a5f08a132286d1ff6c881be12d916b8598f4，只读参考 |
| 上游关系 | 未来服务产业信息研究平台；现在无主平台导入或数据连接 |

## 数据合同

`Advisor.ask(question, session_id)` 返回 `AdviceResponse`。归一化模型结果字段固定为 content/reasoning/usage/tool_calls；原始 assistant 消息另存于聊天流水。

memory.json 使用 `entries` 字典，序号从1起，next_id 单调增加；chat_history.json 使用 `turns` 字典；state.json 保存自动摘要及 through_turn。过程与事实物理分开。采用 JSON 是本次用户明确要求。

事实记忆在同一数据目录内共享，会话只隔离聊天历史。未来按项目提供独立数据根目录，避免跨研究对象共用事实。

## 已知坑

| 坑 | 对策 |
| --- | --- |
| Windows 管道中文编码与 Python -I | CLI 对重定向流显式设 UTF-8，不能只依赖 PYTHONUTF8 |
| 长对话被摘要覆盖 | 原始流水完整保留，摘要与覆盖轮号原子写入独立 state.json |
| 助手建议混入事实 | 事实只经显式人工记忆操作写入 |
| 测试偷偷消费真实密钥 | 测试显式 `_env_file=None` 并注入假模型/传输 |
| 多会话被当作多项目隔离 | 不同项目使用不同数据根目录 |
| 模型返回工具调用 | 本期不开放工具，收到调用直接返回明确错误 |
| 记忆损坏被自动重置 | 拒绝写入并提示恢复，保留原文件 |
| 复制虚拟环境到新位置 | 重建 .venv 并从依赖声明/锁定清单安装 |

## 规则引用

所有规范从 `<本地规范目录>/01_AI项目协作参考\README.md` 进入；工程架构、Python、LLM/Agent、协作授权的正文各以该索引指向的原文件为准。
