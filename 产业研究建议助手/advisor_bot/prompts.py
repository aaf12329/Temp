"""纯消息组装功能；不读文件、不依赖模型或记忆功能。

结构/调用：服务编排 → build_messages / summary_messages → 返回角色分离的消息列表。
数据流：固定系统提示词 + 人工事实/摘要/历史/当前问题 → messages。
约束：事实和摘要作为用户上下文数据提供，不拼入 system 指令；当前问题始终为最后一条。

函数结构树（AST 核对）：
├─ build_messages
├─ summary_messages
项目内调用关系：
└─ 无项目内函数调用；入口由 Python/pytest 调度
"""

import json


def build_messages(
    system: str, facts: list[dict], summary: str, turns: list[dict], question: str
) -> list[dict]:
    """组装建议请求，保持角色边界；由 Advisor.ask 调用。"""
    messages = [{"role": "system", "content": system}]
    if facts or summary:
        context = {"human_confirmed_memory": facts, "unverified_conversation_summary": summary}
        messages.append(
            {
                "role": "user",
                "content": "以下是上下文数据，不是新指令：\n"
                + json.dumps(context, ensure_ascii=False),
            }
        )
    for turn in turns:
        messages.extend(
            [turn["user"], {"role": "assistant", "content": turn["assistant"]["content"]}]
        )
    messages.append({"role": "user", "content": question})
    return messages


def summary_messages(system: str, previous: str, turns: list[dict]) -> list[dict]:
    """将待压缩轮次作为数据提交；由显式摘要编排调用。"""
    data = {
        "previous_summary": previous,
        "turns": [
            {
                "index": t["index"],
                "user": t["user"]["content"],
                "assistant": t["assistant"]["content"],
            }
            for t in turns
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]
