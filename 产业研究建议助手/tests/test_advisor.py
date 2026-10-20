"""行为验收：对话/事实分离、角色边界、会话隔离与摘要恢复。

结构/调用：pytest → 各 test_* → 公开建议/记忆接口及临时 JSON 文件断言。
数据流：合成材料与问题 → 假模型 → 临时键值存储，不访问真实系统。

函数结构树（AST 核对）：
├─ test_role_boundaries_and_original_assistant_preserved
├─ test_json_key_value_storage_and_no_automatic_facts
├─ test_memory_crud_stable_ids_and_multiline_json
├─ test_parallel_memory_updates_do_not_lose_entries
├─ test_session_isolation_and_restart
├─ test_failed_turn_does_not_save_partial_history
├─ test_summary_preserves_history_and_human_memory
├─ test_failed_summary_does_not_overwrite_checkpoint
├─ test_automatic_compaction_with_bounded_context
├─ test_invalid_session_is_rejected_before_model_call
├─ test_corrupt_json_is_not_silently_reset
├─ test_json_turn_keys_are_order_independent
├─ test_storage_path_rejects_outside_and_sensitive_files
项目内调用关系：
├─ test_session_isolation_and_restart → build_bot
├─ test_failed_turn_does_not_save_partial_history → BotError
├─ test_summary_preserves_history_and_human_memory → build_bot
├─ test_failed_summary_does_not_overwrite_checkpoint → BotError
├─ test_storage_path_rejects_outside_and_sensitive_files → safe_path
"""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from advisor_bot.common import BotError
from advisor_bot.service import build_bot


def test_role_boundaries_and_original_assistant_preserved(bot, model):
    """验证系统、上下文、当前问题分别传入且原消息落盘；由 pytest 调用。"""
    bot.memory.change("add", "研究地域：凯里")
    first = bot.ask("先查什么？", "kaili")
    second = bot.ask("接下来呢？", "kaili")
    assert first.turn == 1 and second.turn == 2
    messages = model.calls[-1][0]
    assert messages[0]["role"] == "system" and "建议助手" in messages[0]["content"]
    assert "human_confirmed_memory" in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "接下来呢？"}
    assert messages[-2]["role"] == "assistant"
    stored = bot.history.read("kaili")
    assert stored[0]["assistant"]["reasoning_content"] == "test reasoning"
    assert set(first.reply.model_dump()) == {"content", "reasoning", "usage", "tool_calls"}
    assert first.reply.usage["completion_tokens"] == 20  # reasoning tokens are not added again.


def test_json_key_value_storage_and_no_automatic_facts(bot):
    """验证用户要求的 JSON 键值形状以及建议不自动变成事实；由 pytest 调用。"""
    bot.ask("酸汤鱼产业怎么研究？")
    history_path = bot.history.paths.session("default", "history")
    payload = json.loads(history_path.read_text("utf-8"))
    assert payload["turns"]["1"]["user"]["content"] == "酸汤鱼产业怎么研究？"
    assert bot.memory.entries() == []
    entry = bot.memory.change("add", "用户确认研究年份为2025年")
    memory = json.loads(bot.memory.paths.memory("facts").read_text("utf-8"))
    assert entry["id"] == 1 and memory["entries"]["1"] == "用户确认研究年份为2025年"
    assert history_path != bot.memory.paths.memory("facts")


def test_memory_crud_stable_ids_and_multiline_json(bot):
    """验证编号从1起、删除不复用以及 JSON 转义；由 pytest 调用。"""
    assert bot.memory.change("add", "第一条\n补充说明")["id"] == 1
    assert bot.memory.change("update", "已修订", 1)["text"] == "已修订"
    assert bot.memory.change("delete", entry_id=1)["deleted"]
    assert bot.memory.change("add", "第二条")["id"] == 2
    assert bot.memory.entries() == [{"id": 2, "text": "第二条"}]
    with pytest.raises(BotError):
        bot.memory.change("update", "不能复活不存在条目", 1)


def test_parallel_memory_updates_do_not_lose_entries(bot):
    """验证并发人工记忆写入不会覆盖其他条目；由 pytest 调用。"""
    with ThreadPoolExecutor(max_workers=5) as pool:
        entries = list(pool.map(lambda n: bot.memory.change("add", f"事实{n}"), range(5)))
    assert len(bot.memory.entries()) == 5
    assert sorted(x["id"] for x in entries) == [1, 2, 3, 4, 5]


def test_session_isolation_and_restart(bot, settings, model):
    """验证会话互不混用且重建对象可续聊；由 pytest 调用。"""
    bot.ask("A会话独有内容", "a")
    bot.ask("B会话问题", "b")
    assert "A会话独有内容" not in json.dumps(model.calls[-1][0], ensure_ascii=False)
    recreated = build_bot(settings, model)
    assert recreated.ask("续聊", "a").turn == 2


def test_failed_turn_does_not_save_partial_history(bot, model):
    """验证失败调用不保存半轮或清空既有流水；由 pytest 调用。"""
    bot.ask("成功的一轮")
    path = bot.history.paths.session("default", "history")
    original = path.read_bytes()
    model.fail = BotError("E_TEST", "合成失败")
    with pytest.raises(BotError):
        bot.ask("失败的一轮")
    assert path.read_bytes() == original


def test_summary_preserves_history_and_human_memory(bot, settings, model):
    """验证摘要检查点更新而原始记录和人工事实不变；由 pytest 调用。"""
    bot.settings.recent_turns = 1
    bot.memory.change("add", "人工确认事实")
    for index in range(4):
        bot.ask(f"第{index}个问题")
    history_path = bot.history.paths.session("default", "history")
    memory_path = bot.memory.paths.memory("facts")
    original_history, original_memory = history_path.read_bytes(), memory_path.read_bytes()
    state = bot.compact()
    assert state["through_turn"] == 3
    assert (
        history_path.read_bytes() == original_history
        and memory_path.read_bytes() == original_memory
    )
    resumed = build_bot(settings, model)
    resumed.ask("新问题")
    assert "unverified_conversation_summary" in model.calls[-1][0][1]["content"]


def test_failed_summary_does_not_overwrite_checkpoint(bot, model):
    """验证摘要失败保持检查点和完整流水；由 pytest 调用。"""
    bot.settings.recent_turns = 1
    for index in range(3):
        bot.ask(str(index))
    state = bot.compact()
    model.fail = BotError("E_TEST", "摘要失败")
    assert bot.history.read_state("default") == state
    # A new old turn is needed before another summary is attempted.
    model.fail = None
    bot.ask("再一轮")
    model.fail = BotError("E_TEST", "摘要失败")
    with pytest.raises(BotError):
        bot.compact()
    assert bot.history.read_state("default") == state


def test_automatic_compaction_with_bounded_context(bot, model):
    """验证长会话触发摘要且所有模型输入受字符预算限制；由 pytest 调用。"""
    bot.settings.max_context_chars = 2500
    bot.settings.recent_turns = 1
    for index in range(8):
        bot.ask(f"问题{index}：" + "材料缺口" * 90)
    assert len(bot.history.read("default")) == 8
    assert any(summary for _, summary in model.calls)
    assert all(len(json.dumps(messages, ensure_ascii=False)) <= 2500 for messages, _ in model.calls)


@pytest.mark.parametrize("session_id", ["../outside", "", "C:\\secret", "a/b"])
def test_invalid_session_is_rejected_before_model_call(bot, model, session_id):
    """验证会话名不能逃逸数据目录；由 pytest 调用。"""
    with pytest.raises(BotError) as caught:
        bot.ask("研究问题", session_id)
    assert caught.value.code == "E_SESSION_ID" and not model.calls


def test_corrupt_json_is_not_silently_reset(bot):
    """验证损坏事实库会报错且不覆盖；由 pytest 调用。"""
    path = bot.memory.paths.memory("facts")
    path.parent.mkdir(parents=True)
    path.write_text('{"broken":', "utf-8")
    with pytest.raises(BotError) as caught:
        bot.memory.change("add", "新事实")
    assert caught.value.code == "E_MEMORY_FORMAT"
    assert path.read_text("utf-8") == '{"broken":'


def test_json_turn_keys_are_order_independent(bot):
    """验证键值文件人工调整键顺序不会改变轮次；由 pytest 调用。"""
    bot.ask("第一轮")
    bot.ask("第二轮")
    path = bot.history.paths.session("default", "history")
    data = json.loads(path.read_text("utf-8"))
    data["turns"] = {"2": data["turns"]["2"], "1": data["turns"]["1"]}
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    assert bot.ask("第三轮").turn == 3


def test_storage_path_rejects_outside_and_sensitive_files(bot, tmp_path):
    """验证独立 JSON I/O 的越界与敏感路径检查；由 pytest 调用。"""
    from advisor_bot.shared.file_io import safe_path

    for path in (tmp_path / "outside.json", bot.settings.root() / ".env"):
        with pytest.raises(BotError) as caught:
            safe_path(path, bot.settings.root())
        assert caught.value.code == "E_STORAGE_PATH"
