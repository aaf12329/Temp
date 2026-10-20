"""模型适配验收：假 HTTP 替换真实网络，不调用付费服务。

结构/调用：pytest → MockTransport → ChatModel.complete → 归一化或错误断言。
数据流：合成 HTTP 响应 → 四字段结果/可操作错误；不读取真实密钥。

函数结构树（AST 核对）：
├─ payload
├─ test_model_contract_and_distinct_system_user_roles
├─ test_only_temporary_failures_are_retried
├─ test_invalid_or_tool_responses_are_rejected
├─ test_missing_key_never_sends_request
项目内调用关系：
├─ test_model_contract_and_distinct_system_user_roles → ChatModel
├─ test_only_temporary_failures_are_retried → ChatModel
├─ test_invalid_or_tool_responses_are_rejected → ChatModel
├─ test_missing_key_never_sends_request → ChatModel
"""

import httpx
import pytest

from advisor_bot.common import BotError
from advisor_bot.model import ChatModel


def payload(content="建议补充证据", **extra):
    """构造合成完整响应；由模型测试调用。"""
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content,
                    **extra,
                },
            }
        ],
        "usage": {"completion_tokens": 20},
    }


def test_model_contract_and_distinct_system_user_roles(settings):
    """验证请求角色、思考开关和结果归一化；由 pytest 调用。"""

    def handle(request):
        """检查假请求后返回响应；由 MockTransport 回调。"""
        import json

        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][-1]["role"] == "user"
        assert body["thinking"] == {"type": "disabled"} and "tools" not in body
        return httpx.Response(200, json=payload(reasoning_content="测试思考"))

    model = ChatModel(settings, httpx.MockTransport(handle))
    result = model.complete(
        [{"role": "system", "content": "研究助手"}, {"role": "user", "content": "问题"}]
    )
    assert result.content == "建议补充证据" and result.reasoning == "测试思考"
    assert result.tool_calls == [] and result.usage["completion_tokens"] == 20


@pytest.mark.parametrize(
    "status,retries", [(429, 3), (500, 3), (503, 3), (401, 1), (402, 1), (400, 1)]
)
def test_only_temporary_failures_are_retried(settings, status, retries):
    """验证暂时错误最多两次重试且永久错误不重试；由 pytest 调用。"""
    calls, waits = [], []

    def handle(request):
        """记录次数；由假 HTTP 传输调用。"""
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "sensitive payload"}})

    model = ChatModel(settings, httpx.MockTransport(handle), sleep=waits.append)
    with pytest.raises(BotError) as caught:
        model.complete([{"role": "user", "content": "问题"}])
    assert len(calls) == retries and len(waits) == retries - 1
    assert "sensitive payload" not in str(caught.value)
    assert all(1 <= delay <= 5 for delay in waits)


@pytest.mark.parametrize(
    "response",
    [
        payload(content=""),
        payload(tool_calls=[{"id": "call1", "function": {"name": "delete"}}]),
        {"choices": []},
        {
            "choices": [
                {"finish_reason": "length", "message": {"role": "assistant", "content": "截断"}}
            ]
        },
    ],
)
def test_invalid_or_tool_responses_are_rejected(settings, response):
    """验证不把工具调用、空回复或截断响应当作成功建议；由 pytest 调用。"""
    model = ChatModel(settings, httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    with pytest.raises(BotError):
        model.complete([{"role": "user", "content": "问题"}])


def test_missing_key_never_sends_request(settings):
    """验证无密钥时给出配置动作且不联网；由 pytest 调用。"""
    from pydantic import SecretStr

    settings.api_key = SecretStr("")
    model = ChatModel(settings, httpx.MockTransport(lambda _: pytest.fail("must not send HTTP")))
    with pytest.raises(BotError) as caught:
        model.complete([{"role": "user", "content": "问题"}])
    assert caught.value.code == "E_ADVISOR_KEY"
