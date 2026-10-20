"""人工事实记忆功能：按本次用户要求采用 JSON 键值对，序号从 1 起。

结构：Memory.entries → _read；add/update/delete → _save → 共用文件 I/O。
调用关系：入口/服务 → Memory；不导入模型、会话或其他功能模块。
数据流：人工确认事实 ↔ entries[id] = 文本；删除值为 null，next_id 防止复用旧编号。

函数结构树（AST 核对）：
├─ Memory
│  ├─ __init__
│  ├─ _read
│  ├─ _save
│  ├─ entries
│  ├─ change
项目内调用关系：
├─ _read → self.paths.memory, read_json, BotError
├─ _save → write_json, self.paths.memory
├─ entries → self._read()['entries'].items, self._read
├─ change → BotError, self.paths.root.mkdir, self.paths.memory, self._read, self._save
"""

from filelock import FileLock

from advisor_bot.common import BotError
from advisor_bot.shared.file_io import read_json, write_json
from advisor_bot.shared.paths import AdvisorPaths


class Memory:
    """只接受显式记忆操作；模型建议或摘要不会自动进入事实库。"""

    def __init__(self, paths: AdvisorPaths):
        """绑定地址簿；由服务入口构造，不读写磁盘。"""
        self.paths = paths

    def _read(self) -> dict:
        """解析人工可编辑 JSON 并保留删除编号；由记忆增删改查调用。"""
        path = self.paths.memory("facts")
        if not path.exists():
            return {"schema_version": "1.0", "next_id": 1, "entries": {}}
        try:
            state = read_json(path, root=self.paths.root)
            if (
                state.get("schema_version") != "1.0"
                or type(state["next_id"]) is not int
                or state["next_id"] < 1
                or not isinstance(state["entries"], dict)
            ):
                raise ValueError("invalid memory")
            for key, text in state["entries"].items():
                if (
                    not key.isdecimal()
                    or str(int(key)) != key
                    or int(key) < 1
                    or int(key) >= state["next_id"]
                    or (text is not None and (not isinstance(text, str) or not text.strip()))
                ):
                    raise ValueError("invalid entry")
            return state
        except (ValueError, TypeError, KeyError, AttributeError):
            raise BotError(
                "E_MEMORY_FORMAT",
                'memory.json 格式无效；应有 schema_version、next_id 和 entries（例：{"1":"研究地域为凯里"}）。',
            ) from None

    def _save(self, state: dict) -> None:
        """原子保存 JSON 事实库；由写操作在持锁状态下调用。"""
        write_json(self.paths.memory("facts"), state, root=self.paths.root)

    def entries(self) -> list[dict]:
        """返回有效事实的 id/text；由建议编排及 CLI 列表调用。"""
        return [
            {"id": int(key), "text": text}
            for key, text in self._read()["entries"].items()
            if text is not None
        ]

    def change(self, action: str, text: str = "", entry_id: int | None = None) -> dict:
        """执行人工增改删并返回记录；由 CLI 或外部显式调用，序号从 1 起。"""
        if action not in {"add", "update", "delete"}:
            raise BotError("E_MEMORY_ACTION", "操作应为 add/update/delete。")
        if action != "delete" and not text.strip():
            raise BotError("E_MEMORY_TEXT", "每条事实必须是非空文本。")
        self.paths.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.paths.memory("lock")), timeout=5):
            state = self._read()
            if action == "add":
                entry_id = state["next_id"]
                state["next_id"] += 1
                state["entries"][str(entry_id)] = text.strip()
            else:
                if state["entries"].get(str(entry_id)) is None:
                    raise BotError("E_MEMORY_ID", "未找到该有效编号，请先运行 memory list。", 404)
                state["entries"][str(entry_id)] = None if action == "delete" else text.strip()
            self._save(state)
            return {
                "id": entry_id,
                "text": state["entries"][str(entry_id)],
                "deleted": action == "delete",
            }
