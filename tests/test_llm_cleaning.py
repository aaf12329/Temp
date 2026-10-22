"""CSV后LLM清洗的隔离验证，不联网、不调用付费模型。

结构：来源边界/召回 → 原文校验 → 真子进程CSV重建 → 无记忆适配及失败降级。
调用：pytest → 正式纯逻辑和clean_csv；数据流：合成网页块 → 假LLM → 临时CSV/年度行。
"""

import copy
import hashlib
import json

import pytest
from advisor_bot.common import ModelReply

from app.advisor.integrated import IntegratedAdvisor
from app.common.file_io import read_csv_rows, write_csv_rows, write_json
from app.common.paths import PipelinePaths
from app.contracts.pipeline import CLIConfig
from app.core.config import Settings
from app.core.errors import ServiceError
from app.pipeline.cleaning import clean_candidates
from app.pipeline.llm_records import (
    merge_facts,
    parse_response,
    plan_batches,
    source_blocks,
    validate_rows,
)
from app.pipeline.selection import expand_numeric_neighbors
from app.pipeline_llm import clean_csv


def fixture_data():
    """构造带可核对位置的合成来源与LLM输出，不是产业事实。"""
    text = "全年餐饮收入12.5亿元。"
    block = {"block_id": "block-one", "passage_id": "block-one", "record_id": "synthetic-one",
             "url": "https://example.test/annual", "title": "2023年榕城市餐饮统计公报",
             "published_at": "2024-03-01", "header": text, "context": text, "text": text,
             "char_start": 0, "char_end": len(text), "context_start": 0, "context_end": len(text),
             "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
             "text_path": "data/normalized/text/synthetic.txt", "duplicate_group": "synthetic"}
    metadata = {"metrics": {"revenue": ["餐饮收入", "收入"]},
                "units": {"亿元": ["元", "100000000", "currency"]},
                "dimensions": {"revenue": "currency"},
                "subjects": {"餐饮业": ["餐饮业", "餐饮"]}, "regions": {}}
    row = {"block_id": "block-one", "quote": text, "raw_value": "12.5",
           "unit": {"value": "亿元", "quote": "亿元"}, "metric": "revenue", "metric_quote": "餐饮收入",
           "subject": {"value": "餐饮业", "quote": "餐饮"},
           "region": {"value": "榕城", "quote": "榕城市"},
           "year": {"value": "2023", "quote": "2023年"},
           "publication_date": {"value": None, "quote": None},
           "period_type": "year", "scope": "total", "scope_quote": None,
           "claim_type": "observation", "needs_review": False, "review_reason": ""}
    return block, metadata, row


def write_input(paths, block, metadata):
    """模拟已经完成匹配与CSV落盘，供正式清洗入口读取。"""
    write_csv_rows(paths.file("llm_input"), [block])
    write_json(paths.file("extraction_context"), metadata)
    analysis = {"question": "合成餐饮收入", "facts": [], "series": [], "issues": [],
        "warning": None, "truncated": {}, "counts": {"documents": 1, "passages": 1,
        "selected_passages": 1, "candidate_facts": 0, "cleaned_candidates": 0,
        "excluded_candidates": 0, "series_rows": 0}}
    write_json(paths.file("analysis"), analysis)
    write_csv_rows(paths.file("raw"), [], ["fact_id"])
    return analysis


def test_local_number_conversion_and_new_entity_proof():
    """新地域可由原文支持，不依赖默认名单；数值由本地精确换算。"""
    block, meta, row = fixture_data()
    facts, invalid = validate_rows([row], [block], meta)
    assert not invalid and len(facts) == 1
    assert facts[0]["value"] == 1250000000
    assert facts[0]["region"] == "榕城" and facts[0]["period"] == "2023"
    assert facts[0]["review_status"] == "pending" and facts[0]["uncertainty_flags"] == []
    assert len(clean_candidates(facts)[0]) == 1


@pytest.mark.parametrize("mutation", ["number", "quote", "source", "extra_field"])
def test_hallucinated_number_quote_source_and_approval_rejected(mutation):
    """编造数字、引句、来源或自行批准均不能进入可用数据。"""
    block, meta, row = fixture_data()
    if mutation == "number":
        row["raw_value"] = "99.5"
    elif mutation == "quote":
        row["quote"] = "不存在的观测"
    elif mutation == "source":
        row["block_id"] = "another-batch"
    else:
        row["review_status"] = "approved"
    facts, review = validate_rows([row], [block], meta)
    assert not facts and len(review) == 1


def test_missing_year_and_publication_cannot_be_filled_from_question():
    """用户问题不进入证据验证；缺年份/日期保留到待复核，不会默默补齐。"""
    block, meta, row = fixture_data()
    block["title"] = "榕城市餐饮报告"
    block["published_at"] = ""
    facts, invalid = validate_rows([row], [block], meta)
    assert not invalid
    assert facts[0]["period"] is None and facts[0]["known_at"] is None
    assert not clean_candidates(facts)[0]


def test_explicit_publication_label_can_be_recovered():
    """来源元数据未捕获时，可从同一材料已发送头部的发布标签核对完整日期。"""
    block, meta, row = fixture_data()
    block.update(published_at="", header="发布时间：2024年3月1日\n" + block["header"])
    row["publication_date"] = {"value": "2024-03-01", "quote": "发布时间：2024年3月1日"}
    facts, invalid = validate_rows([row], [block], meta)
    assert not invalid and facts[0]["known_at"] == "2024-03-01T00:00:00"
    row["year"] = {"value": "2024", "quote": "发布时间：2024年3月1日"}
    assert validate_rows([row], [block], meta)[0][0]["period"] is None


def test_qualified_and_nonannual_data_stays_in_review_even_if_llm_says_total():
    """LLM声称全行业全年也不能消除句内明确的限额与季度限制。"""
    block, meta, row = fixture_data()
    text = "第一季度限额以上餐饮收入12.5亿元。"
    block.update(text=text, context=text, header=text, char_end=len(text), context_end=len(text))
    row["quote"] = text
    facts, invalid = validate_rows([row], [block], meta)
    assert not invalid and not clean_candidates(facts)[0]
    assert {"qualified_scope_requires_review", "nonannual_period"} <= set(facts[0]["uncertainty_flags"])


def test_table_unit_and_metric_quotes_are_verified_and_scopes_do_not_merge():
    """单位或指标证据不符会留待复核；同年同口径不同值仍标记冲突。"""
    block, meta, row = fixture_data()
    facts, _ = validate_rows([row], [block], meta)
    another = {**facts[0], "fact_id": "other", "record_id": "other", "value": 999}
    merged = merge_facts(facts, [another])
    assert all("conflicting_scope_values" in f["uncertainty_flags"] for f in merged)
    row["unit"] = {"value": "亿元", "quote": "并不存在的单位证据"}
    row["metric_quote"] = "无关指标"
    invalid, _ = validate_rows([row], [block], meta)
    assert not clean_candidates(invalid)[0]


def test_numeric_neighbors_do_not_cascade_or_cross_documents():
    """只扩大一层邻段，不让整篇文章递归入选，不跨来源串上下文。"""
    rows = [{"record_id": "one", "char_start": i, "text": text, "selected": i == 0,
             "semantic_score": 0.3} for i, text in enumerate(["餐饮收入", "2023年 12.5", "2022年 11.5"])]
    rows.append({"record_id": "two", "text": "2023年 88", "selected": False})
    expanded = expand_numeric_neighbors(rows)
    assert [r["selected"] for r in expanded] == [True, True, False, False]
    assert not rows[1]["selected"] and expanded[1]["semantic_score"] == 0.3
    assert CLIConfig().semantic_threshold == 0.50


def test_unselected_rule_rows_are_preserved_for_review_not_silently_modeled():
    """成功清洗但未选中的规则候选不可继续冒充LLM确认；未覆盖来源仍明确本地回退。"""
    block, meta, row = fixture_data()
    facts, _ = validate_rows([row], [block], meta)
    original = copy.deepcopy(facts)
    merged = merge_facts(original, [], {block["block_id"]})
    assert "llm_not_selected" in merged[0]["uncertainty_flags"]
    assert not clean_candidates(merged)[0] and original[0]["uncertainty_flags"] == []
    assert clean_candidates(merge_facts(original, [], {"different"}))[0]


def test_batches_are_bounded_and_reject_truncated_json():
    """预算明确报告跳过，格式不完整时不截取一部分当成成功。"""
    block, _, row = fixture_data()
    batches, omitted = plan_batches([block] * 4, 1600, 1)
    assert len(batches) == 1 and omitted > 0
    assert sum(len(json.dumps(b, ensure_ascii=False)) + 2 for b in batches[0]) <= 1600
    assert parse_response(json.dumps({"rows": [row]}, ensure_ascii=False)) == [row]
    with pytest.raises(ValueError):
        parse_response('{"rows": [')


def test_csv_precedes_api_and_real_subprocess_exports_usable_and_review(tmp_path):
    """API读的是已落盘CSV，正式无密钥子进程生成可用与待复核表，重入不再调用API。"""
    block, meta, row = fixture_data()
    paths = PipelinePaths(tmp_path / "output", organized=True)
    analysis = write_input(paths, block, meta)
    before = paths.file("raw").read_bytes()
    calls = []

    class FakeAdvisor:
        """仅替换API，不替换CSV与本地序列构建。"""

        def clean_records(self, question, blocks, metadata, *, max_tokens):
            """断言清洗前CSV已存在，并返回一条有据记录与一条编造数字。"""
            assert read_csv_rows(paths.file("llm_input"))[0]["text"] == block["text"]
            calls.append(question)
            return {"content": json.dumps({"rows": [row, {**row, "raw_value": "666"}]}, ensure_ascii=False)}

    settings = Settings(_env_file=None, api_token="synthetic-cleaning-token-" + "x" * 32,
                        advisor_api_key="synthetic-only", data_dir=tmp_path / "state")
    config = CLIConfig(retrieval="keywords")
    result = clean_csv("合成研究", analysis, config, settings, paths, tmp_path / "workspace", advisor=FakeAdvisor())
    assert result["counts"]["cleaned_candidates"] == result["counts"]["series_rows"] == 1
    assert result["counts"]["review_records"] == 1
    assert result["data_quality"]["before"]["rows"] == 0
    assert result["data_quality"]["counts"]["usable_candidates"] == 1
    assert paths.file("data_quality_plot").is_file() and paths.file("data_quality_fields").is_file()
    assert paths.file("raw").read_bytes() == before
    assert read_csv_rows(paths.file("cleaned"))[0]["review_status"] == "pending"
    assert "666" in read_csv_rows(paths.file("review"))[0]["llm_raw"]
    clean_csv("合成研究", analysis, config, settings, paths, tmp_path / "workspace", advisor=FakeAdvisor())
    assert len(calls) == 1 and not list(tmp_path.rglob("*.db"))


def test_failed_batch_preserves_raw_and_does_not_retry(tmp_path):
    """网络/余额等错误不循环请求，同批材料留在CSV与review中。"""
    block, meta, _ = fixture_data()
    paths = PipelinePaths(tmp_path / "output", organized=True)
    analysis = write_input(paths, block, meta)
    calls = []

    class FailingAdvisor:
        """付费请求失败的替身，不访问网络。"""

        def clean_records(self, *args, **kwargs):
            """记录一次尝试并报告暂时错误，测试仍不允许自动重发。"""
            calls.append(1)
            raise ServiceError("E_SYNTHETIC", "合成网络失败", 502)

    settings = Settings(_env_file=None, api_token="synthetic-failure-token-" + "x" * 32,
                        advisor_api_key="synthetic-only", data_dir=tmp_path / "state")
    result = clean_csv("合成研究", analysis, CLIConfig(), settings, paths, tmp_path / "workspace", advisor=FailingAdvisor())
    assert result["llm_cleaning"]["status"] == "partial"
    assert read_csv_rows(paths.file("review")) and len(calls) == 1
    assert paths.file("llm_input").is_file()


def test_api_off_or_missing_key_never_calls_model(tmp_path):
    """关闭API或缺密钥保留本地结果并明确状态。"""
    block, meta, _ = fixture_data()
    settings = Settings(_env_file=None, api_token="synthetic-off-token-" + "x" * 32,
                        advisor_api_key="", data_dir=tmp_path / "state")
    for mode, expected in [("off", "disabled"), ("api", "missing_key")]:
        paths = PipelinePaths(tmp_path / mode)
        analysis = write_input(paths, block, meta)
        analysis.update(facts=[{"fact_id": "original"}], series=[{"period": 2023}])
        write_json(paths.file("analysis"), analysis)
        result = clean_csv("研究", {"counts": analysis["counts"]}, CLIConfig(llm=mode), settings, paths, tmp_path / "w")
        assert result["llm_cleaning"]["status"] == expected
        assert result["facts"] == [{"fact_id": "original"}] and result["series"] == [{"period": 2023}]


def test_cleaning_adapter_has_no_history_and_no_retry(tmp_path):
    """两次清洗各带本批证据，不读取聊天/记忆，接口关闭自动重试。"""
    calls, configurations = [], []

    class Model:
        """只记录消息的模型替身。"""

        def complete(self, messages):
            """返回空的有效清洗结果。"""
            calls.append(copy.deepcopy(messages))
            return ModelReply(content='{"rows": []}')

    def factory(config):
        """记录主进程适配配置。"""
        configurations.append(config)
        return Model()

    settings = Settings(_env_file=None, api_token="synthetic-adapter-token-" + "x" * 32,
                        advisor_api_key="synthetic-only", data_dir=tmp_path / "state")
    advisor = IntegratedAdvisor(settings, model_factory=factory)
    block, meta, _ = fixture_data()
    advisor.clean_records("第一轮", [block], meta)
    advisor.clean_records("第二轮", [{**block, "text": "SECOND_ONLY", "context": "SECOND_ONLY", "header": "SECOND_ONLY"}], meta)
    assert "12.5" not in calls[1][1]["content"]
    assert all(config.max_retries == 0 and config.data_dir is None for config in configurations)
    assert len(calls[0]) == 2 and "text_path" not in calls[0][1]["content"]
    assert not settings.data_dir.exists()


def test_source_blocks_are_fair_and_keep_exact_offsets():
    """清洗预算文档轮流分配；正文坐标不因扩大上下文改变。"""
    block, _, _ = fixture_data()
    text = block["text"]
    docs = [{**block, "text_sha256": key} for key in ("one", "two")]
    passages = [{**block, "text_sha256": "one", "passage_id": f"p{i}"} for i in range(3)]
    passages.append({**block, "text_sha256": "two", "passage_id": "later"})
    selected = source_blocks(passages, docs, 2)
    assert [p["block_id"] for p in selected] == ["p0", "later"]
    assert all(p["context"][p["char_start"]:p["char_end"]] == text for p in selected)


def test_scope_object_and_null_review_reason_are_normalized():
    """模型把 scope 返回成对象、review_reason 返回 null 时按契约归一，不放弃整条证据。"""
    block, meta, row = fixture_data()
    row["scope"] = {"value": "total", "quote": None}
    row["scope_quote"] = {"value": None, "quote": None}
    row["review_reason"] = None
    facts, invalid = validate_rows([row], [block], meta)
    assert not invalid and len(facts) == 1
    assert facts[0]["scope"] == "total"


def test_misattributed_block_reattaches_to_the_passage_that_contains_the_number():
    """同文档内模型挂错 block_id 时，只有引句逐字落在另一匹配段落才重挂。"""
    block, meta, row = fixture_data()
    tax_text = "全年税收收入100亿元。"
    tax = {**block, "block_id": "tax-block", "passage_id": "tax-block", "text": tax_text,
           "char_start": 0, "char_end": len(tax_text), "context": block["text"],
           "context_start": 0, "context_end": len(block["text"])}
    row["block_id"] = "tax-block"
    facts, invalid = validate_rows([row], [tax], meta, reattach_blocks=[tax, block])
    assert not invalid and len(facts) == 1
    assert facts[0]["record_id"] == block["record_id"]
    assert any(item.get("kind") == "llm_block_reattached"
               for item in facts[0]["context_evidence"])


def test_change_amount_does_not_hide_the_level_observation():
    """“比上年增加”是变动量，不能与当年总量互相判冲突。"""
    block, meta, row = fixture_data()
    text = "全年餐饮收入12.5亿元，比上年增加1.5亿元。"
    block.update(text=text, context=text, header=text, char_end=len(text), context_end=len(text))
    row["quote"] = "全年餐饮收入12.5亿元"
    totals, _ = validate_rows([row], [block], meta)
    change = copy.deepcopy(row)
    change.update(quote="比上年增加1.5亿元", raw_value="1.5")
    amounts, _ = validate_rows([change], [block], meta)
    assert "change_not_level" in amounts[0]["uncertainty_flags"]
    merged = merge_facts(totals, amounts)
    total = next(item for item in merged if item["value"] == 1250000000)
    assert "conflicting_scope_values" not in total["uncertainty_flags"]
    assert clean_candidates(merged)[0]
