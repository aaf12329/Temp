"""显式聚合/编排层：本文件获准横向组织独立功能，不接主工程。

结构：build_bot → Advisor；ask → 事实/会话读取 → _fit_context → 模型 → 原子写入；compact → _summarize。
调用关系：CLI/外部调用者 → build_bot/Advisor.ask/compact；本入口依次调用 memory/history/prompts/model。
数据流：问题 + 人工事实 + 摘要 + 最近对话 → 建议 → 独立 JSON 键值流水；摘要成功才更新检查点。
对外契约：ask 返回 AdviceResponse；compact 返回 through_turn/summary，不修改人工事实。

函数结构树（AST 核对）：
├─ Advisor
│  ├─ __init__
│  ├─ ask
│  ├─ _fit_context
│  ├─ _summarize
│  ├─ compact
├─ build_bot
项目内调用关系：
├─ ask → BotError, self.memory.entries, self.history.lock, self.history.read, self.history.read_state, self._fit_context, self.model.complete, self.history.append, AdviceResponse
├─ _fit_context → build_messages, self._summarize, BotError
├─ _summarize → summary_messages, BotError, self.model.complete, self.history.save_state
├─ compact → self.history.lock, self.history.read, self.history.read_state, BotError, self._summarize
├─ build_bot → BotSettings, AdvisorPaths, Advisor, Memory, History, DemoModel, ChatModel, read_text
"""

import json
from datetime import UTC, datetime

from advisor_bot.common import AdviceResponse, BotError, BotSettings
from advisor_bot.history import History
from advisor_bot.memory import Memory
from advisor_bot.model import ChatModel, DemoModel
from advisor_bot.prompts import build_messages, summary_messages
from advisor_bot.shared.file_io import read_text
from advisor_bot.shared.paths import SUMMARY_PROMPT, SYSTEM_PROMPT, AdvisorPaths


class Advisor:
    """独立建议编排对象；可由命令行或未来装配层调用。"""

    def __init__(self, settings, memory, history, model, system: str, summary_system: str):
        """注入独立功能和固定提示词；由 build_bot 构造，无服务注册副作用。"""
        self.settings, self.memory, self.history, self.model = settings, memory, history, model
        self.system, self.summary_system = system, summary_system

    def ask(self, question: str, session_id: str = "default") -> AdviceResponse:
        """返回建议并保存完整成功轮次；由外部调用者或 CLI 调用。"""
        if not question.strip() or len(question) > self.settings.max_input_chars:
            raise BotError(
                "E_ADVISOR_INPUT", f"问题必须非空且不超过 {self.settings.max_input_chars} 字符。"
            )
        facts = self.memory.entries()
        if len(json.dumps(facts, ensure_ascii=False)) > self.settings.max_memory_chars:
            raise BotError(
                "E_MEMORY_LIMIT",
                "人工事实超出上下文预算，请精简事实或调大 ADVISOR_MAX_MEMORY_CHARS。",
            )
        with self.history.lock(session_id):
            turns = self.history.read(session_id)
            state = self.history.read_state(session_id)
            if state["through_turn"] > len(turns):
                raise BotError(
                    "E_HISTORY_STATE", "摘要覆盖轮号超过历史长度，请恢复一致的流水与检查点。"
                )
            messages = self._fit_context(session_id, turns, state, facts, question)
            reply = self.model.complete(messages)
            index = len(turns) + 1
            raw = reply.raw_assistant or {"role": "assistant", "content": reply.content}
            turn = {
                "index": index,
                "created_at": datetime.now(UTC).isoformat(),
                "user": {"role": "user", "content": question},
                "assistant": raw,
                "result": reply.model_dump(),
                "mode": "demo" if self.settings.demo else "model",
            }
            self.history.append(session_id, turns, turn)
            return AdviceResponse(session_id=session_id, turn=index, reply=reply, mode=turn["mode"])

    def _fit_context(self, session_id, turns, state, facts, question) -> list[dict]:
        """检查上下文，必要时滚动压缩旧轮次；只由 ask 调用。"""
        while True:
            remaining = [t for t in turns if t["index"] > state["through_turn"]]
            messages = build_messages(self.system, facts, state["summary"], remaining, question)
            if len(json.dumps(messages, ensure_ascii=False)) <= self.settings.max_context_chars:
                return messages
            new_state = self._summarize(session_id, turns, state)
            if new_state["through_turn"] == state["through_turn"]:
                raise BotError(
                    "E_CONTEXT_LIMIT",
                    "上下文仍过长，请缩短问题、精简事实，或降低 ADVISOR_RECENT_TURNS 后重试。",
                )
            state = new_state

    def _summarize(self, session_id: str, turns: list[dict], state: dict) -> dict:
        """压缩旧轮次并保留完整流水；由上下文编排或 compact 调用。"""
        candidates = [
            t for t in turns[: -self.settings.recent_turns] if t["index"] > state["through_turn"]
        ]
        if not candidates:
            return state
        batch = []
        for turn in candidates:
            proposal = [*batch, turn]
            messages = summary_messages(self.summary_system, state["summary"], proposal)
            if len(json.dumps(messages, ensure_ascii=False)) > self.settings.max_context_chars:
                if not batch:
                    raise BotError(
                        "E_CONTEXT_LIMIT", "单轮历史超出摘要预算，请提高上下文预算或新建会话。"
                    )
                break
            batch = proposal
        messages = summary_messages(self.summary_system, state["summary"], batch)
        reply = self.model.complete(messages, summary=True)
        if len(reply.content) > self.settings.summary_max_chars:
            raise BotError(
                "E_SUMMARY_LIMIT", "自动摘要过长，未覆盖旧检查点；请调整摘要提示词后重试。"
            )
        new_state = {"through_turn": batch[-1]["index"], "summary": reply.content}
        self.history.save_state(session_id, new_state)
        return new_state

    def compact(self, session_id: str = "default") -> dict:
        """显式推进一次摘要；由 CLI 或未来装配入口调用。"""
        with self.history.lock(session_id):
            turns, state = self.history.read(session_id), self.history.read_state(session_id)
            if state["through_turn"] > len(turns):
                raise BotError("E_HISTORY_STATE", "摘要检查点与历史不一致，请恢复备份。")
            return self._summarize(session_id, turns, state)


def build_bot(settings: BotSettings | None = None, model=None) -> Advisor:
    """装配独立建议模块；可注入假模型，构造时不联网、不创建数据。"""
    settings = settings or BotSettings()
    paths = AdvisorPaths(settings.root())
    return Advisor(
        settings,
        Memory(paths),
        History(paths),
        model or (DemoModel() if settings.demo else ChatModel(settings)),
        read_text(SYSTEM_PROMPT),
        read_text(SUMMARY_PROMPT),
    )
