"""会话流水功能：与事实记忆物理分开，整轮原子保存。

结构：History.lock → read/read_state → append/save_state。
调用关系：服务编排持有会话锁后调用本功能；本功能不调用模型或记忆模块。
数据流：用户消息 + 原始 assistant → chat_history.json 键值流水；摘要及覆盖轮号 → state.json。

函数结构树（AST 核对）：
├─ History
│  ├─ __init__
│  ├─ lock
│  ├─ read
│  ├─ read_state
│  ├─ append
│  ├─ save_state
项目内调用关系：
├─ lock → BotError, self.paths.session, self.paths.root.resolve
├─ read → self.paths.session, read_json, BotError
├─ read_state → self.paths.session, read_json, BotError
├─ append → write_json, self.paths.session
├─ save_state → write_json, self.paths.session
"""

import re

from filelock import FileLock

from advisor_bot.common import BotError
from advisor_bot.shared.file_io import read_json, write_json
from advisor_bot.shared.paths import AdvisorPaths


class History:
    """提供会话隔离与完整流水；由服务入口注入。"""

    def __init__(self, paths: AdvisorPaths):
        """绑定地址簿；由 build_bot 构造。"""
        self.paths = paths

    def lock(self, session_id: str) -> FileLock:
        """校验会话名并创建其专用锁；由建议和压缩入口调用。"""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
            raise BotError("E_SESSION_ID", "会话名仅允许 1–64 位英文字母、数字、下划线和短横线。")
        path = self.paths.session(session_id, "lock")
        if not path.resolve().is_relative_to(self.paths.root.resolve()):
            raise BotError("E_SESSION_PATH", "会话目录越界，请检查符号链接。")
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(path), timeout=5)

    def read(self, session_id: str) -> list[dict]:
        """读取并检查连续轮号；由持锁的建议/压缩编排调用。"""
        path = self.paths.session(session_id, "history")
        if not path.exists():
            return []
        try:
            payload = read_json(path, root=self.paths.root)
            if payload.get("schema_version") != "1.0" or not isinstance(payload["turns"], dict):
                raise ValueError("invalid history")
            keys = sorted(payload["turns"], key=int)
            if keys != [str(index) for index in range(1, len(keys) + 1)]:
                raise ValueError("invalid turn keys")
            turns = [payload["turns"][key] for key in keys]
            for index, turn in enumerate(turns, 1):
                if (
                    str(index) not in payload["turns"]
                    or payload["turns"][str(index)] != turn
                    or turn["index"] != index
                    or turn["user"]["role"] != "user"
                    or turn["assistant"]["role"] != "assistant"
                    or not isinstance(turn["user"]["content"], str)
                    or not isinstance(turn["assistant"]["content"], str)
                ):
                    raise ValueError("invalid turn")
            return turns
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BotError(
                "E_HISTORY_FORMAT", "会话流水损坏，请恢复备份；不会清空或覆盖原记录。"
            ) from None

    def read_state(self, session_id: str) -> dict:
        """读取自动摘要检查点；由持锁的服务入口调用。"""
        path = self.paths.session(session_id, "state")
        if not path.exists():
            return {"through_turn": 0, "summary": ""}
        try:
            state = read_json(path, root=self.paths.root)
            if (
                type(state["through_turn"]) is not int
                or state["through_turn"] < 0
                or not isinstance(state["summary"], str)
            ):
                raise ValueError("invalid state")
            return state
        except (ValueError, KeyError, TypeError):
            raise BotError(
                "E_HISTORY_STATE", "摘要检查点损坏，请恢复 state.json 后重试。"
            ) from None

    def append(self, session_id: str, turns: list[dict], turn: dict) -> None:
        """按完整轮次原子保存流水；由成功回复后的建议入口调用。"""
        payload = {
            "schema_version": "1.0",
            "turns": {str(item["index"]): item for item in [*turns, turn]},
        }
        write_json(self.paths.session(session_id, "history"), payload, root=self.paths.root)

    def save_state(self, session_id: str, state: dict) -> None:
        """原子保存摘要和覆盖轮号；仅在摘要成功后由编排调用。"""
        write_json(self.paths.session(session_id, "state"), state, root=self.paths.root)
