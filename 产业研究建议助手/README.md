> 2026-10-21：本模块已通过平台适配层接线，启动按钮使用平台根 .venv 和 .env。JSON 存储与独立 Python 包保留。当前使用方式见根 README.md；下文独立安装方式仅适用于将本包单独导出使用。

# 产业研究建议助手

这是一个独立的终端模块，先完成本模块的运行与验收，再与产业信息研究平台总装。没有 GUI、HTTP 服务、爬虫调用或主平台数据库依赖。

## 现在能做什么

- 根据用户给出的研究目标、当前进展和资料缺口，提出下一步行动与待确认问题。
- 重点检查地域/时间/主体口径、证据缺失、企业/品牌/门店混算和转载同源。
- 多轮对话、按会话续聊、人工事实记忆增删改查。
- 超出字符上下文预算时压缩旧轮次；完整聊天记录另存，人工事实不自动压缩或改写。
- 使用 DeepSeek 兼容接口；归一化返回 content、reasoning、usage、tool_calls。
- 显式离线演示，不需要密钥。真实模式已做一次授权连接验证，实际研究质量仍需用真实任务评估。

当前回复采用完整返回后显示，尚未实现参考仓库的流式逐字输出。没有文件上传、搜索、工具执行、SQL 记忆或自动修改研究数据的能力。

## 开始使用

项目根目录已有 `.venv`，依赖安装在此环境。密钥只放在根目录 `.env`，不会随源代码或报告导出。

- 双击平台 `启动脚本/02_命令行/启动建议助手.bat`：真实模型多轮对话，输入 `/exit` 退出。
- 双击平台 `启动脚本/02_命令行/建议助手离线演示.bat`：固定示例回复，输出会标明“离线演示”，数据进入平台 data/advisor_demo/terminal。
- 恢复验收后可手动双击平台 `启动脚本/03_开发维护/仅检查建议助手.bat`：运行本模块隔离测试。当前仍暂停测试，未执行此入口。

原子目录按钮已统一归类。集成模式的环境和密钥位于平台根，JSON会话/记忆保留在平台data；网页/CLI的最终报告写入平台output。下方手动安装命令及独立环境描述仅适用于将本包单独导出使用。

手动命令（在本文件所在目录执行）：

```powershell
.\.venv\Scripts\python.exe -m advisor_bot chat
.\.venv\Scripts\python.exe -m advisor_bot --session kaili ask "目前只有零散报道，下一步先查什么？"
.\.venv\Scripts\python.exe -m advisor_bot --json ask "帮我检查研究口径还缺哪些信息"
.\.venv\Scripts\python.exe -m advisor_bot --demo ask "凯里酸汤鱼先查什么？"
```

根目录 `.env.example` 是无密钥模板。`ADVISOR_BASE_URL` 和 `ADVISOR_MODEL` 可修改；默认参考当前 DeepSeek 官方 Chat Completions 接口，思考模式关闭。只自动重试连接故障、408、429 与 5xx，最多两次；密钥、余额和其他参数问题直接提示修正。

## JSON 键值存储

按本次用户明确要求，记忆与聊天流水均使用 JSON 键值对；这覆盖参考规范对人工库采用逐行文本的默认约定。

```text
data/                          # 真实使用；可由 ADVISOR_DATA_DIR 改位置
├── memory.json                # 人工确认事实，目录级共享
└── sessions/
    └── kaili/
        ├── chat_history.json  # 本会话完整流水，轮号键从 "1" 起
        └── state.json         # 自动摘要及 through_turn 检查点
```

事实记忆例子：

```json
{
  "schema_version": "1.0",
  "next_id": 2,
  "entries": {
    "1": "研究地域暂定凯里市，时间范围等待用户确认。"
  }
}
```

会话流水的外层形状是 `{"schema_version":"1.0","turns":{"1":{...},"2":{...}}}`。每轮保存 user、原始 assistant、归一化结果和时间。只有成功的完整轮次会写入。自动摘要是未核实上下文，不能当作证据或人工确认事实。

记忆管理：

```powershell
.\.venv\Scripts\python.exe -m advisor_bot memory add "研究地域暂定凯里市"
.\.venv\Scripts\python.exe -m advisor_bot memory list
.\.venv\Scripts\python.exe -m advisor_bot memory update 1 "研究地域由用户确认后再固定"
.\.venv\Scripts\python.exe -m advisor_bot memory delete 1
.\.venv\Scripts\python.exe -m advisor_bot --session kaili compact
```

删除条目保留 null 值，`next_id` 保证旧编号不复用。可以手改 JSON，但须保持 schema_version、编号和 next_id 一致；损坏文件会报错，程序不会静默清空。

**历史按会话隔离，事实记忆按数据目录共享。** 多个产业项目应使用不同 `ADVISOR_DATA_DIR`；总装时由调用方显式分配各项目的数据目录。`max_context_chars` 是字符预算，尚未实现精确 tokenizer 预算。

## 分层与输入输出

```text
用户问题: str
人工事实: [{id:int, text:str}, ...]     自动摘要: str（未核实）
会话历史: {"1": {user, assistant, ...}, ...}
          │
          ▼
cli.main / 外部调用方
├─ service.build_bot（装配独立对象）
└─ Advisor.ask（在已装配对象上调用）
      ├─ Memory.entries
      ├─ History.lock / read / read_state
      ├─ _fit_context
      │  ├─ prompts.build_messages
      │  └─ 必要时 _summarize
      │     ├─ prompts.summary_messages
      │     ├─ model.complete
      │     └─ History.save_state
      ├─ model.complete
      └─ History.append
          │
          ▼
AdviceResponse = {session_id, turn, mode,
                 reply:{content, reasoning, usage, tool_calls}}
```

`service.py` 是明确标注的横向聚合层。模型、记忆、会话、提示词功能互不导入，只依赖共用契约与 shared 层。所有位置集中在 `shared/paths.py`，同类文件读写集中在 `shared/file_io.py`。

## 未来总装合同（当前没有接线）

```python
from pathlib import Path
from advisor_bot.common import BotSettings
from advisor_bot.service import build_bot

settings = BotSettings(data_dir=Path("由总装层明确分配的独立项目目录"))
advisor = build_bot(settings)
result = advisor.ask("当前研究目标与材料缺口……", session_id="kaili")
payload = result.model_dump()
```

输入由调用方显式提供。本模块不会自行扫描桌面、读取爬虫库、执行命令或抓取网址。建议返回后由用户或总装层决定下一步。

## 独立搬运与验证

本文件夹可以作为单独 Python 项目安装。搬到其他位置后重新创建 `.venv`，不要直接复制旧虚拟环境：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q
```

锁定清单对应本机 Windows / Python 3.12 验证环境；其他平台可以从 `pyproject.toml` 安装并重新验证。

测试均在临时目录使用假模型/假 HTTP，覆盖 JSON 持久化、角色分离、原始消息保存、错误重试、摘要保护及独立进程运行。用户另行授权的一次真实调用记录放在 `work/api_smoke_result.json`，没有写入正式事实库。

## 参考与规范

- 行为参考：[history_code 仓库](https://github.com/aaf12329/history_code)，参考提交 `4385a5f08a132286d1ff6c881be12d916b8598f4`。仅借鉴对话、独立记忆和摘要思路，未导入旧记忆、个人提示词或旧凭据。
- 模型接口：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。
- 编程与协作规范入口：`<本地规范目录>/01_AI项目协作参考\README.md`。

参考仓库副本位于桌面 `history_code_参考`，只作阅读参考，未运行其中原始程序。规范审计与项目交接见 `docs/`。
