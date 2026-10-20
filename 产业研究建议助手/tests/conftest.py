"""隔离测试准备。

结构/调用：pytest → settings / model / bot；假模型记录消息，不访问网络。
数据流：临时目录 + 合成研究问题 → 独立测试对象；不读真实 .env 或真实记忆。

函数结构树（AST 核对）：
├─ FakeModel
│  ├─ __init__
│  ├─ complete
├─ settings
├─ model
├─ bot
项目内调用关系：
├─ complete → self.calls.append, ModelReply
├─ settings → BotSettings
├─ model → FakeModel
├─ bot → build_bot
"""

from copy import deepcopy

import pytest

from advisor_bot.common import BotSettings, ModelReply
from advisor_bot.service import build_bot


class FakeModel:
    """只供测试的确定性模型，不把输出当作实际研究结论。"""

    def __init__(self):
        """初始化调用记录；由 model fixture 调用。"""
        self.calls = []
        self.fail = None

    def complete(self, messages, *, summary=False):
        """记录角色消息并返回可断言结果；由独立建议模块调用。"""
        self.calls.append((deepcopy(messages), summary))
        if self.fail:
            raise self.fail
        content = (
            "用户明确研究地域，助手建议补充证据，尚未核实。"
            if summary
            else "建议先明确时间与纳入规则，证据待核实。"
        )
        return ModelReply(
            content=content,
            reasoning="test reasoning",
            usage={"completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 5}},
            raw_assistant={
                "role": "assistant",
                "content": content,
                "reasoning_content": "test reasoning",
            },
        )


@pytest.fixture
def settings(tmp_path):
    """把独立数据定向临时目录；由 pytest 注入测试。"""
    return BotSettings(_env_file=None, data_dir=tmp_path / "advisor", api_key="test-key")


@pytest.fixture
def model():
    """提供假模型；由 pytest 注入测试或 bot fixture。"""
    return FakeModel()


@pytest.fixture
def bot(settings, model):
    """装配隔离机器人；由 pytest 注入测试。"""
    return build_bot(settings, model)
