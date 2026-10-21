"""独立命令行入口：横向编排壳，无网络或文件实现细节。

结构：parser → main → ask/chat/compact/记忆操作；_chat 单轮失败继续等待输入。
调用关系：__main__/research-advisor → main → build_bot → 窄业务接口。
数据流：命令参数或终端输入 → AdviceResponse/记忆条目/错误体 → 文本或 JSON。

函数结构树（AST 核对）：
├─ parser
├─ _chat
├─ main
项目内调用关系：
├─ main → parser, build_bot, BotSettings, _chat
"""

import argparse
import json
import sys

from filelock import Timeout

from advisor_bot.common import BotError, BotSettings
from advisor_bot.service import build_bot


def parser() -> argparse.ArgumentParser:
    """定义独立命令合同；由 main 调用。"""
    command = argparse.ArgumentParser(description="产业研究建议助手（可独立运行）")
    command.add_argument("--demo", action="store_true", help="零网络离线演示，使用 data_demo")
    command.add_argument("--session", default="default")
    command.add_argument("--json", action="store_true", help="输出稳定 JSON 结构")
    sub = command.add_subparsers(dest="action", required=True)
    sub.add_parser("chat", help="交互对话，/exit 退出")
    sub.add_parser("compact", help="显式压缩旧对话，保留原始流水")
    ask = sub.add_parser("ask", help="单次建议")
    ask.add_argument("question")
    memory = sub.add_parser("memory", help="仅人工显式维护的事实记忆")
    operations = memory.add_subparsers(dest="operation", required=True)
    operations.add_parser("list")
    add = operations.add_parser("add")
    add.add_argument("text")
    update = operations.add_parser("update")
    update.add_argument("id", type=int)
    update.add_argument("text")
    delete = operations.add_parser("delete")
    delete.add_argument("id", type=int)
    return command


def _chat(bot, session_id: str) -> None:
    """循环接收研究问题，单轮失败继续；仅由 main 的 chat 分支调用。"""
    print("产业研究建议助手；/exit 退出。当前模块只给建议，不执行外部操作。")
    while True:
        try:
            question = input("你：").strip()
            if question == "/exit":
                return
            if question:
                print("助手：" + bot.ask(question, session_id).reply.content)
        except BotError as exc:
            print(json.dumps(exc.as_dict(), ensure_ascii=False))
        except Timeout:
            print("会话正在处理，请稍后重试。")
        except OSError:
            print("本地 JSON 读写失败，请检查数据目录权限和磁盘空间后重试。")
        except (EOFError, KeyboardInterrupt):
            return


def main(settings: BotSettings | None = None) -> int:
    """编排一个明确命令并返回退出码；由命令入口调用。"""
    # 管道/子进程也明确使用 UTF-8；Python -I 会忽略 PYTHONUTF8 环境变量。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        bot = build_bot(settings or BotSettings(demo=args.demo))
        if args.action == "chat":
            _chat(bot, args.session)
            return 0
        if args.action == "ask":
            response = bot.ask(args.question, args.session)
            value = response.model_dump() if args.json else response.reply.content
        elif args.action == "compact":
            value = bot.compact(args.session)
        elif args.operation == "list":
            value = bot.memory.entries()
        else:
            value = bot.memory.change(
                args.operation, getattr(args, "text", ""), getattr(args, "id", None)
            )
        print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except BotError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False))
        return 1
    except Timeout:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "E_SESSION_BUSY",
                        "message": "数据正在处理，请稍后重试。",
                        "retryable": True,
                    }
                },
                ensure_ascii=False,
            )
        )
        return 1
    except OSError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "E_STORAGE_IO",
                        "message": "本地读写失败，请检查目录权限和磁盘空间。",
                        "retryable": False,
                    }
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
