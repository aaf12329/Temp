"""模型适配功能：DeepSeek/OpenAI 兼容的非流式 Chat Completions。

结构：ChatModel.complete → 请求/暂时性错误退避 → _decode；DemoModel 为显式离线替代。
调用关系：服务编排 → complete → _decode；不导入记忆、会话或提示词功能。
数据流：messages → HTTP JSON → ModelReply 四字段契约。
边界：本期不发送工具定义，收到 tool_calls 明确拒绝，不执行任何工具；不自动联网自测。

函数结构树（AST 核对）：
├─ ChatModel
│  ├─ __init__
│  ├─ complete
│  ├─ _decode
├─ DemoModel
│  ├─ complete
项目内调用关系：
├─ complete → self.settings.api_key.get_secret_value, BotError, self.sleep, self._decode
├─ _decode → BotError, ModelReply
├─ complete → ModelReply
"""

import random
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from advisor_bot.common import BotError, BotSettings, ModelReply


class ChatModel:
    """真实模型适配器；由 build_bot 创建，直到 complete 才访问网络。"""

    def __init__(
        self, settings: BotSettings, transport=None, sleep: Callable[[float], None] = time.sleep
    ):
        """注入配置、测试传输和等待函数；由服务入口或隔离测试调用。"""
        self.settings, self.transport, self.sleep = settings, transport, sleep

    def complete(self, messages: list[dict[str, str]], *, summary: bool = False) -> ModelReply:
        """请求一次完整回复；由建议或摘要编排调用，只重试超时/429/5xx。"""
        key = self.settings.api_key.get_secret_value()
        if not key:
            raise BotError(
                "E_ADVISOR_KEY", "请在根目录 .env 设置 ADVISOR_API_KEY，或使用 --demo。", 503
            )
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "max_tokens": min(self.settings.max_tokens, 2048)
            if summary
            else self.settings.max_tokens,
        }
        if urlsplit(self.settings.base_url).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": "disabled"}
        with httpx.Client(
            timeout=self.settings.timeout_s,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(self.settings.max_retries + 1):
                try:
                    response = client.post(
                        self.settings.base_url + "/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {key}"},
                    )
                except httpx.TransportError:
                    if attempt < self.settings.max_retries:
                        self.sleep(2**attempt + random.uniform(0, 1))
                        continue
                    raise BotError(
                        "E_ADVISOR_NETWORK",
                        "模型连接失败，请检查网络与 ADVISOR_BASE_URL。",
                        502,
                        True,
                    ) from None
                if (
                    response.status_code == 408
                    or response.status_code == 429
                    or response.status_code >= 500
                ):
                    if attempt < self.settings.max_retries:
                        self.sleep(2**attempt + random.uniform(0, 1))
                        continue
                    raise BotError(
                        "E_ADVISOR_TRANSIENT",
                        f"模型服务暂时不可用（HTTP {response.status_code}），稍后重试。",
                        502,
                        True,
                    )
                if response.status_code != 200:
                    raise BotError(
                        "E_ADVISOR_HTTP",
                        f"模型返回 HTTP {response.status_code}；检查密钥、余额、模型名和地址。",
                        502,
                    )
                if len(response.content) > 2_000_000:
                    raise BotError("E_ADVISOR_RESPONSE", "模型响应过大，请降低输出长度。", 502)
                try:
                    return self._decode(response.json())
                except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                    raise BotError(
                        "E_ADVISOR_RESPONSE", "模型响应结构无效，请核对兼容接口格式。", 502
                    ) from None
        raise AssertionError("unreachable")

    @staticmethod
    def _decode(payload: dict) -> ModelReply:
        """校验完整响应并统一字段；只由 complete 调用，保留原 assistant 消息。"""
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "length":
            raise BotError(
                "E_ADVISOR_TRUNCATED", "回复被截断，请增加 ADVISOR_MAX_TOKENS 或缩小问题。", 502
            )
        raw = choice["message"]
        if raw.get("role") != "assistant":
            raise ValueError("invalid assistant role")
        if raw.get("tool_calls"):
            raise BotError(
                "E_ADVISOR_TOOLS_DISABLED", "独立建议模块未开放工具，请只返回文字建议。", 502
            )
        content = raw.get("content")
        if not isinstance(content, str) or not content.strip():
            raise BotError("E_ADVISOR_EMPTY", "模型没有返回文字建议，请检查模型配置。", 502)
        reasoning = raw.get("reasoning_content") or ""
        if not isinstance(reasoning, str):
            raise ValueError("invalid reasoning")
        return ModelReply(
            content=content,
            reasoning=reasoning,
            usage=payload.get("usage"),
            tool_calls=[],
            raw_assistant=raw,
        )


class DemoModel:
    """显式离线演示模型；仅验证模块运行，不代表真实模型生成质量。"""

    def complete(self, messages: list[dict[str, str]], *, summary: bool = False) -> ModelReply:
        """返回带演示标记的固定建议；由 --demo 模式或隔离自测调用。"""
        content = (
            "[离线演示摘要] 历史讨论涉及研究范围、证据缺口和下一步核实，具体内容请回看历史。"
            if summary
            else "[离线演示，非真实模型建议]\n"
            "建议先明确研究地域、时间与主体纳入规则，再列出已有证据及缺失字段。\n"
            "下一步：为一小批经营主体逐项关联来源，人工核对企业、品牌与门店是否重复。\n"
            "待确认：你目前已收集哪些材料，最希望回答哪个具体问题？"
        )
        return ModelReply(content=content, raw_assistant={"role": "assistant", "content": content})
