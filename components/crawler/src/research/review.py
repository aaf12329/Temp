# -*- coding: utf-8 -*-
"""人工审核应用，保留机器候选及独立审核记录。

结构：① apply_reviews（纯逻辑）；② load_reviews（CSV I/O）；③ 自测。
调用：research_api → load_reviews/apply_reviews → io_utils。
数据流：facts + review CSV → facts 副本；不改原文、数值或原始候选文件。
契约：apply_reviews -> list[dict]；重复、未知 ID、空理由、非法状态直接报错。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from webcrawl import io_utils


# ===== ① 纯逻辑 =====
def apply_reviews(facts: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> list[dict]:
    """仅应用明确的人工决定，不能以更改 CSV 的证据字段覆写原始事实。"""
    known = {f["fact_id"] for f in facts}
    decisions: dict[str, dict] = {}
    for review in reviews:
        rid = review.get("fact_id")
        state = (review.get("review_status") or "pending").strip()
        if rid not in known or rid in decisions:
            raise ValueError("审核 CSV 有未知或重复 fact_id；请使用本次 review_template.csv")
        if state not in {"pending", "approved", "rejected"}:
            raise ValueError("review_status 仅接受 pending / approved / rejected")
        if state != "pending" and not (review.get("review_note") or "").strip():
            raise ValueError("批准或拒绝必须填写 review_note，说明口径核对依据")
        decisions[rid] = {"review_status": state,
                          "review_note": review.get("review_note", "")}
    return [dict(fact, **decisions.get(fact["fact_id"], {})) for fact in facts]


# ===== ② I/O =====
def load_reviews(path: str) -> list[dict[str, str]]:
    """CSV 读取统一通过文本 I/O，容忍 Excel BOM。"""
    return list(csv.DictReader(io.StringIO(io_utils.read_text(path).lstrip("\ufeff"))))


# ===== ③ 自测 =====
def _self_test() -> None:
    """审核不改变原始候选，不接受未知 ID。"""
    facts = [{"fact_id": "f", "review_status": "pending", "value": 1}]
    out = apply_reviews(facts, [{"fact_id": "f", "review_status": "approved",
                                 "review_note": "checked", "value": 100}])
    assert out[0]["value"] == 1 and facts[0]["review_status"] == "pending"


if __name__ == "__main__":
    _self_test()
