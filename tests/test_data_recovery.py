"""数据漏检与口径保护回归；只用合成文本、假分数，无网络和模型训练。

结构：词表/召回/预算 → 标题上下文及限制 → 诊断/GAN门槛。
调用：pytest → 纯规则 + 旧证据抽取器（临时import路径）；不读写真实output。
用户已恢复测试；与真实来源执行的不同范围见docs/验证记录_20261022.md。
"""

import hashlib

from app.contracts.pipeline import CLIConfig
from app.pipeline.cleaning import clean_candidates
from app.pipeline.context import complete_context
from app.pipeline.diagnostics import diagnose, gan_gate
from app.pipeline.selection import balanced_passages, retrieval_settings, supplement_numeric
from tests.test_pipeline import legacy_imports as legacy_imports


def settings_for(evidence, question="贵阳餐饮行业发展趋势", **overrides):
    """从生产配置构造合成研究的有效词表。"""
    return retrieval_settings(question, CLIConfig(**overrides), evidence.DEFAULT_SUBJECTS,
                              evidence.DEFAULT_REGIONS, evidence.METRICS)


def candidates(evidence, settings, text, title="2020年贵阳市国民经济和社会发展统计公报"):
    """带真实切片关系的合成文档，仅数字为测试夹具。"""
    digest = hashlib.sha256(text.encode()).hexdigest()
    passages, offset = [], 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        passages.append({"passage_id": f"p{index}", "record_id": "fixture", "text": line,
                         "char_start": offset, "char_end": offset + len(line), "title": title,
                         "published_at": "2021-05-01", "text_sha256": digest,
                         "duplicate_group": digest, "text_path": "data/normalized/text/fixture.txt"})
        offset += len(line)
    document = {"text": text, "text_sha256": digest, "title": title}
    raw = evidence.extract_facts(passages, settings)
    return raw, complete_context(raw, passages, [document], settings)


def test_empty_configuration_and_low_score_numeric_recall(legacy_imports):
    """空口径不禁用默认匹配；低分数值句可由产业关键词召回且分数不伪造。"""
    from research import evidence, retrieval

    settings = settings_for(evidence)
    assert "餐饮" in settings["keywords"] and "贵阳" in settings["regions"]
    source = [{"text": "餐饮收入12.5亿元。", "title": "合成统计公报"}]
    scored = retrieval.score_passages(source, settings)
    assert scored[0]["selected"] and scored[0]["semantic_score"] is None
    missed = {**source[0], "selected": False, "semantic_score": 0.54,
              "semantic_only": False, "relevance_reason": "none"}
    recovered = supplement_numeric([missed], settings)
    assert recovered[0]["selected"] and recovered[0]["semantic_score"] == 0.54
    assert not missed["selected"] and "selected" not in source[0]


def test_balanced_budget_retains_later_document_data(legacy_imports):
    """早期长文不能占满预算，后面文档中的指标行优先保留，顺序和偏移不变。"""
    from research import evidence

    settings = settings_for(evidence)
    passages = [{"text": "普通导航", "record_id": "early", "char_start": i} for i in range(50)]
    passages += [{"text": "餐饮收入12.5亿元。", "record_id": "late", "char_start": 99}]
    chosen = balanced_passages(passages, settings, 2)
    assert chosen == [passages[0], passages[-1]]


def test_explicit_statistical_title_recovers_scope_without_changing_number(legacy_imports):
    """标题明确年度/地域、指标明确产业时可恢复候选，同时保留原文与证据。"""
    from research import evidence

    settings = settings_for(evidence)
    text = "餐饮收入12.5亿元。"
    raw, result = candidates(evidence, settings, text)
    assert raw[0]["period"] is None and raw[0]["region"] is None
    row = result[0]
    assert (row["period"], row["region"], row["subject"]) == ("2020", "贵阳", "餐饮业")
    assert row["value"] == raw[0]["value"] and row["review_status"] == "pending"
    assert text[row["number_char_start"]:row["number_char_end"]] == "12.5亿元"
    assert text[row["char_start"]:row["char_end"]] == row["evidence_excerpt"]
    assert {item["field"] for item in row["context_evidence"]} == {"period", "region", "subject"}
    cleaned, _ = clean_candidates(result)
    assert len(evidence.build_series(cleaned, require_approval=False)) == 1
    assert not evidence.build_series(cleaned)  # 旧默认人工批准闸门未改变。


def test_qualified_scope_is_not_promoted_to_total_industry(legacy_imports):
    """上文的限额口径不能被丢掉；数字导出，但不得自动进入总行业年度训练。"""
    from research import evidence

    _, result = candidates(evidence, settings_for(evidence),
                           "按消费类型分，限额以上商品零售额100亿元；\n餐饮收入12.5亿元。")
    revenue = next(row for row in result if row["metric"] == "revenue")
    assert "qualified_scope_requires_review" in revenue["uncertainty_flags"]
    assert any(item["kind"] == "scope_restriction" for item in revenue["context_evidence"])
    cleaned, rejected = clean_candidates([revenue])
    assert not cleaned and rejected


def test_event_period_and_explicit_foreign_region_are_preserved(legacy_imports):
    """活动、季度、其他城市和明确年份不能被统计标题改写。"""
    from research import evidence

    _, result = candidates(evidence, settings_for(evidence),
                           "2019年凯里餐饮业营业收入2亿元。\n第一季度餐饮收入3亿元。")
    assert result[0]["period"] == "2019" and result[0]["region"] == "凯里"
    assert "nonannual_period" in result[1]["uncertainty_flags"]
    assert result[1]["period"] is None
    _, event = candidates(evidence, settings_for(evidence), "餐饮业营业收入2亿元。", "2020年数博会闭幕")
    assert "nonannual_period" in event[0]["uncertainty_flags"]
    assert event[0]["period"] is None


def test_publication_year_and_query_do_not_fill_fact_year(legacy_imports):
    """问题、发布日期和年鉴出版年均不充当指标年度。"""
    from research import evidence

    settings = settings_for(evidence, "2020年贵阳餐饮收入")
    _, result = candidates(evidence, settings, "餐饮收入12.5亿元。", "贵阳统计年鉴2020年版")
    assert result[0]["period"] is None
    assert "missing_measurement_year" in result[0]["uncertainty_flags"]
    _, relative = candidates(evidence, settings, "去年餐饮收入12.5亿元。")
    assert relative[0]["period"] is None
    assert "relative_period_requires_review" in relative[0]["uncertainty_flags"]


def test_recovered_conflicting_values_remain_blocked(legacy_imports):
    """补全之后同年同口径的不同值必须再次标冲突，不能任取一个。"""
    from research import evidence

    _, result = candidates(evidence, settings_for(evidence), "餐饮收入12.5亿元。\n餐饮收入13亿元。")
    assert all("conflicting_scope_values" in row["uncertainty_flags"] for row in result)
    assert not clean_candidates(result)[0]


def test_diagnostic_and_gan_gates_distinguish_not_started_from_failed():
    """没有数据或不足64行不显示训练失败；实际错误才显示失败。"""
    diagnosis = diagnose({"documents": 1, "selected_passages": 1, "candidate_facts": 0}, [], {})
    assert diagnosis["code"] == "no_numeric_candidates"
    assert gan_gate([], 64, "auto")["status"] == "not_started"
    small = [{"split": {"train": 63}, "gan": {"status": "skipped"}, "needs_gan": False}]
    assert "63" in gan_gate(small, 64, "auto")["reason"]
    assert gan_gate(small, 64, "auto")["status"] == "not_started"
    ready = [{"split": {"train": 64}, "gan": {"status": "skipped"}, "needs_gan": True}]
    assert gan_gate(ready, 64, "auto")["status"] == "available"
    assert gan_gate(ready, 64, "auto", planning=False)["status"] == "not_used"
    assert gan_gate([{"gan": {"status": "failed"}}], 64, "auto")["status"] == "failed"


def test_context_limits_reach_final_stateless_evidence(tmp_path):
    """最终助手不能只看到脱离限额上下文的数值，历史CSV仍可正常读取。"""
    from app.common.file_io import write_csv_rows
    from app.common.paths import PipelinePaths
    from app.pipeline.workflow import _information_rows
    from app.pipeline_cli import selected_evidence

    paths = PipelinePaths(tmp_path, organized=True)
    selected = [{"passage_id": "p", "record_id": "fixture", "text": "餐饮收入12.5亿元。"}]
    facts = [{"passage_id": "p", "uncertainty_flags": ["qualified_scope_requires_review"],
              "context_evidence": [{"kind": "scope_restriction", "quote": "限额以上"}]}]
    write_csv_rows(paths.file("information"), _information_rows(selected, facts))
    result = selected_evidence(paths, [{"record_id": "fixture", "text": "网页开头"}])
    assert "限额以上" in result[0]["text"]
    assert "qualified_scope_requires_review" in result[0]["text"]
    assert "网页开头" not in result[0]["text"]
