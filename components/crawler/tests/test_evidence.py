# -*- coding: utf-8 -*-
"""证据链回归：只用临时目录/内置中文，零网络、零真实 data 写入。

函数结构树：CorpusTests 测快照边界/指纹/偏移/去重；FactTests 测口径/数字关联；
SeriesTests 测人工批准/已知日期/冲突排除。
调用关系：unittest → 各测试 → research.evidence 对外 API。
数据流：临时 CrawlResult → corpus → pending facts → approved annual series。
返回契约：标准 unittest 断言；不修改项目数据或入口配置。
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from research import evidence  # noqa: E402


# ===== ① 纯逻辑测试夹具 =====
def passage(text: str, identifier: str = "p1", base: int = 0,
            published: str | None = "2025-03-01") -> dict[str, Any]:
    """构造只含必要元数据的离线段落。"""
    return {"text": text, "passage_id": identifier, "record_id": "r1", "char_start": base,
            "url": "https://example.invalid/a", "published_at": published,
            "fetched_at": "2026-10-20T20:00:00+08:00", "duplicate_group": "d1"}


def approved(value: float = 100.0, identifier: str = "f1", **updates: Any) -> dict[str, Any]:
    """显式人工批准夹具；不把自动候选默认为真值。"""
    fact = {"fact_id": identifier, "subject": "酸汤鱼", "metric": "output_value",
            "region": "凯里", "period_type": "year", "period": "2024", "unit": "元",
            "value": value, "published_at": "2025-03-01", "known_at": "2025-03-01",
            "review_status": "approved", "claim_type": "observation",
            "uncertainty_flags": [], "duplicate_group": "d1"}
    fact.update(updates)
    return fact


# ===== ② 文件边界测试：只写 tempfile =====
class CorpusTests(unittest.TestCase):
    """在临时根目录验证真实文件读取、哈希与段落锚点。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patch_root = mock.patch.object(evidence.ps, "PROJECT_ROOT", str(self.root))
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.addCleanup(self.temporary.cleanup)

    def record(self, text: str, name: str = "a", url: str = "https://a.invalid/",
               fetched: str = "2025-02-01T12:00:00+08:00") -> dict[str, Any]:
        """写临时 normalized 正文及精确锚点，返回真实契约形状。"""
        text_path = self.root / "data" / "normalized" / "text" / (name + ".txt")
        para_path = self.root / "data" / "normalized" / "para" / (name + ".jsonl")
        text_path.parent.mkdir(parents=True, exist_ok=True)
        para_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8", newline="\n")
        anchor = {"index": 0, "char_start": 0, "char_end": len(text), "text": text}
        para_path.write_text(json.dumps(anchor, ensure_ascii=False), encoding="utf-8")
        return {"record_id": name, "job_id": "j1", "url": url, "status": "ok",
                "fetched_at": fetched, "title": "产业", "source": {
                    "publisher": "来源", "published_at": "2025-01-02"},
                "storage": {"text_path": text_path.relative_to(self.root).as_posix()},
                "paragraph_file": para_path.relative_to(self.root).as_posix(),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}

    def test_unicode_offsets_and_stable_chunks(self) -> None:
        text = "🐟凯里酸汤鱼2024年收入10亿元。" * 35
        record = self.record(text)
        corpus = evidence.load_corpus([record])
        self.assertFalse(corpus["issues"])
        self.assertGreater(len(corpus["passages"]), 1)
        for chunk in corpus["passages"]:
            self.assertLessEqual(len(chunk["text"]), 240)
            self.assertEqual(text[chunk["char_start"]:chunk["char_end"]], chunk["text"])
            self.assertEqual(chunk["record_id"], "a")
        self.assertEqual(corpus, evidence.load_corpus([record]))

    def test_latest_success_does_not_use_overwritten_history(self) -> None:
        current = self.record("2024年凯里酸汤鱼收入10亿元。")
        old = copy.deepcopy(current)
        old.update(record_id="old", fetched_at="2023-01-01", text_sha256="0" * 64)
        failed = copy.deepcopy(current)
        failed.update(status="failed", fetched_at="2026-01-01")
        corpus = evidence.load_corpus([current, old, failed])
        self.assertEqual([d["record_id"] for d in corpus["documents"]], ["a"])
        self.assertFalse(corpus["issues"])

    def test_latest_hash_mismatch_fails_closed_no_old_fallback(self) -> None:
        current = self.record("2024年凯里酸汤鱼收入10亿元。")
        old = copy.deepcopy(current)
        old.update(record_id="old", fetched_at="2023-01-01")
        current["text_sha256"] = "f" * 64
        corpus = evidence.load_corpus([old, current])
        self.assertEqual(corpus["documents"], [])
        self.assertEqual(corpus["issues"][0]["issue"], "text_hash_mismatch")

    def test_missing_file_and_missing_hash_are_reported(self) -> None:
        record = self.record("中文")
        record["text_sha256"] = None
        self.assertEqual(evidence.load_corpus([record])["issues"][0]["issue"],
                         "missing_text_sha256")
        record["storage"]["text_path"] = "data/normalized/text/absent.txt"
        self.assertIn("text_unavailable", evidence.load_corpus([record])["issues"][0]["issue"])

    def test_path_escape_absolute_raw_and_sensitive_files_rejected(self) -> None:
        record = self.record("中文")
        for bad_path in ("../outside.txt", str(self.root / "outside.txt"),
                         "data/raw/html/a.html", "data/normalized/text/../../../a.txt",
                         "data/normalized/text/.env", "data/normalized/para/a.txt"):
            with self.subTest(path=bad_path):
                record["storage"]["text_path"] = bad_path
                self.assertFalse(evidence.load_corpus([record])["documents"])

    def test_cross_url_duplicates_preserve_variants(self) -> None:
        a = self.record("凯里 酸汤鱼\n产值１０亿元。", "a", "https://a.invalid/")
        b = self.record("凯里 酸汤鱼 产值10亿元。", "b", "https://b.invalid/")
        corpus = evidence.load_corpus([b, a])
        self.assertEqual(len(corpus["documents"]), 1)
        document = corpus["documents"][0]
        self.assertEqual(document["record_ids"], ["a", "b"])
        self.assertEqual(len(document["source_variants"]), 2)
        self.assertEqual(len(corpus["passages"]), 1)
        self.assertEqual(corpus["passages"][0]["text"], document["text"])

    def test_broken_anchor_falls_back_without_wrong_offsets(self) -> None:
        text = "第一句。\n第二句🐟。"
        record = self.record(text)
        path = self.root / record["paragraph_file"]
        path.write_text('{"char_start":1,"char_end":3,"text":"错"}', encoding="utf-8")
        corpus = evidence.load_corpus([record])
        self.assertIn("paragraph_fallback", corpus["issues"][0]["issue"])
        self.assertEqual(corpus["passages"][0]["text"], text)

    def test_partial_anchor_coverage_falls_back(self) -> None:
        record = self.record("第一句。第二句。")
        path = self.root / record["paragraph_file"]
        path.write_text('{"char_start":0,"char_end":4,"text":"第一句。"}', encoding="utf-8")
        self.assertIn("coverage_mismatch", evidence.load_corpus([record])["issues"][0]["issue"])

    def test_windows_newlines_hash_matches_logical_text(self) -> None:
        record = self.record("第一句\n第二句")
        path = self.root / record["storage"]["text_path"]
        path.write_bytes("第一句\r\n第二句".encode("utf-8"))
        self.assertEqual(len(evidence.load_corpus([record])["documents"]), 1)

    def pdf_record(self, text: str = "2024年凯里酸汤鱼产值12亿元。") -> dict[str, Any]:
        """模拟 CrawlResult 的独立 PDF 正文路径，不接触原件或真实数据。"""
        record = self.record(text)
        old_path = self.root / record["storage"]["text_path"]
        pdf_path = self.root / "data" / "normalized" / "pdf" / "a.txt"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(pdf_path)
        record.update(status="ok_pdf", paragraph_file=None,
                      pdf={"text_path": pdf_path.relative_to(self.root).as_posix(),
                           "page_map": [{"page": 1, "char_start": 0, "char_end": len(text)}]})
        record["storage"]["text_path"] = None
        return record

    def test_pdf_text_path_fallback_verified_with_page_context(self) -> None:
        record = self.pdf_record()
        corpus = evidence.load_corpus([record])
        self.assertEqual(len(corpus["documents"]), 1)
        self.assertFalse(corpus["issues"])
        chunk = corpus["passages"][0]
        self.assertEqual(chunk["pages"], [1])
        self.assertEqual(chunk["text_path"], "data/normalized/pdf/a.txt")
        self.assertEqual(evidence.extract_facts([chunk], {})[0]["pages"], [1])

    def test_pdf_storage_text_path_takes_precedence(self) -> None:
        record = self.pdf_record()
        record["storage"]["text_path"] = "data/normalized/pdf/a.txt"
        record["pdf"]["text_path"] = "data/normalized/pdf/nonexistent.txt"
        self.assertEqual(len(evidence.load_corpus([record])["documents"]), 1)

    def test_pdf_hash_mismatch_rejected(self) -> None:
        record = self.pdf_record()
        record["text_sha256"] = "0" * 64
        corpus = evidence.load_corpus([record])
        self.assertFalse(corpus["documents"])
        self.assertEqual(corpus["issues"][0]["issue"], "text_hash_mismatch")

    def test_pdf_missing_text_and_scanned_are_explicit_issues(self) -> None:
        record = self.pdf_record()
        record["pdf"]["text_path"] = None
        corpus = evidence.load_corpus([record])
        self.assertFalse(corpus["documents"])
        self.assertIn("text_unavailable", corpus["issues"][0]["issue"])
        record["error"] = {"code": "E_PDF_NO_TEXT"}
        corpus = evidence.load_corpus([record])
        self.assertFalse(corpus["documents"])
        self.assertIn("E_PDF_NO_TEXT", corpus["issues"][0]["issue"])
        record["status"] = "failed"
        self.assertIn("E_PDF_NO_TEXT", evidence.load_corpus([record])["issues"][0]["issue"])

    def test_pdf_no_page_map_chunks_exact_text_without_inventing_page(self) -> None:
        record = self.pdf_record()
        record["pdf"]["page_map"] = []
        corpus = evidence.load_corpus([record])
        self.assertFalse(corpus["issues"])
        self.assertEqual(corpus["passages"][0]["pages"], [])
        self.assertEqual(corpus["passages"][0]["char_start"], 0)

    def test_pdf_bad_page_map_falls_back_without_false_page_numbers(self) -> None:
        record = self.pdf_record()
        record["pdf"]["page_map"][0]["char_end"] = 10000
        corpus = evidence.load_corpus([record])
        self.assertEqual(corpus["passages"][0]["pages"], [])
        self.assertIn("pdf_page_map_fallback", corpus["issues"][0]["issue"])

    def test_pdf_text_path_cannot_escape_normalized_boundary(self) -> None:
        record = self.pdf_record()
        record["pdf"]["text_path"] = "data/raw/files/original.pdf"
        self.assertFalse(evidence.load_corpus([record])["documents"])


# ===== ③ 主流程与防误归因回归 =====
class FactTests(unittest.TestCase):
    """不同指标、年份、单位、口径和政策语气不得误并。"""

    def facts(self, text: str, **changes: Any) -> list[dict[str, Any]]:
        return evidence.extract_facts([passage(text, **changes)], {})

    def test_metrics_and_numbers_are_independently_aligned(self) -> None:
        facts = self.facts("2024年凯里酸汤鱼收入10亿元，产值12亿元，销售额8亿元，产量3万吨。")
        self.assertEqual([(f["metric"], f["value"], f["unit"]) for f in facts], [
            ("revenue", 1e9, "元"), ("output_value", 1.2e9, "元"),
            ("sales_value", 8e8, "元"), ("production", 30000.0, "吨")])
        self.assertTrue(all(f["review_status"] == "pending" for f in facts))

    def test_sales_income_is_not_revenue(self) -> None:
        facts = self.facts("2024年凯里酸汤鱼销售收入1,234.5万元。")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["metric"], "sales_value")
        self.assertEqual(facts[0]["value"], 12345000)
        self.assertEqual(facts[0]["uncertainty_flags"], [])

    def test_explicit_year_per_number_and_no_publication_year_inference(self) -> None:
        facts = self.facts("2023年凯里酸汤鱼产值10亿元，2024年凯里酸汤鱼产值12亿元。")
        self.assertEqual([f["period"] for f in facts], ["2023", "2024"])
        unknown = self.facts("凯里酸汤鱼产值12亿元。")[0]
        self.assertIsNone(unknown["period"])
        self.assertIn("missing_measurement_year", unknown["uncertainty_flags"])

    def test_targets_and_forecasts_are_not_observations(self) -> None:
        for qualifier, expected in (("力争", "target"), ("目标为", "target"),
                                    ("预计", "forecast"), ("有望达到", "forecast")):
            with self.subTest(qualifier=qualifier):
                fact = self.facts("2024年凯里酸汤鱼产值" + qualifier + "10亿元。")[0]
                self.assertEqual(fact["claim_type"], expected)
                fact["review_status"] = "approved"
                self.assertEqual(evidence.build_series([fact]), [])

    def test_mixed_observation_and_target_clauses(self) -> None:
        facts = self.facts("2023年凯里酸汤鱼产值10亿元，目标2024年凯里酸汤鱼产值12亿元。")
        self.assertEqual([f["claim_type"] for f in facts], ["observation", "target"])

    def test_sentence_evidence_offsets_are_exact_unicode(self) -> None:
        text = "🐟说明。2024年凯里酸汤鱼产值12亿元。"
        fact = self.facts(text, base=13)[0]
        self.assertEqual(text[fact["char_start"] - 13:fact["char_end"] - 13],
                         fact["evidence_excerpt"])
        self.assertEqual(text[fact["number_char_start"] - 13:fact["number_char_end"] - 13],
                         "12亿元")

    def test_scope_requires_explicit_subject_and_region(self) -> None:
        unknown = self.facts("2024年产值12亿元。")[0]
        self.assertIsNone(unknown["subject"])
        self.assertIsNone(unknown["region"])
        self.assertIn("missing_subject", unknown["uncertainty_flags"])
        ambiguous = self.facts("2024年凯里与贵阳酸汤鱼产值12亿元。")[0]
        self.assertIn("ambiguous_region", ambiguous["uncertainty_flags"])

    def test_destination_mentions_do_not_become_observation_scope(self) -> None:
        fact = self.facts("2024年全州酸汤产业产值12亿元，产品销往全国各地。")[0]
        self.assertIsNone(fact["region"])
        self.assertIn("missing_region", fact["uncertainty_flags"])

    def test_single_company_values_do_not_become_sector_totals(self) -> None:
        fact = self.facts("2024年凯里某酸汤鱼食品公司产值12亿元。")[0]
        self.assertIn("enterprise_scope_requires_review", fact["uncertainty_flags"])
        fact["review_status"] = "approved"
        self.assertEqual(evidence.build_series([fact]), [])

    def test_qualified_scope_and_distinct_output_metrics(self) -> None:
        facts = self.facts("2024年凯里酸汤产业综合产值12亿元，工业总产值8亿元。")
        self.assertEqual([f["metric"] for f in facts],
                         ["comprehensive_output_value", "industrial_output_value"])
        fact = self.facts("2024年凯里规模以上酸汤鱼产值12亿元。")[0]
        self.assertIn("qualified_scope_requires_review", fact["uncertainty_flags"])

    def test_increments_are_not_levels(self) -> None:
        fact = self.facts("2024年凯里酸汤鱼收入增加2亿元。")[0]
        self.assertIn("change_not_level", fact["uncertainty_flags"])

    def test_coordinated_years_require_review(self) -> None:
        fact = self.facts("2023年和2024年凯里酸汤鱼产值分别为10亿元和12亿元。")[0]
        self.assertIn("ambiguous_measurement_year", fact["uncertainty_flags"])

    def test_specific_subject_wins_substring_and_aliases_work(self) -> None:
        fact = self.facts("2024年凯里酸汤鱼产值12亿元。")[0]
        self.assertEqual(fact["subject"], "酸汤鱼")
        custom = evidence.extract_facts([passage("2024年凯里市红汤产业产值12亿元。")], {
            "subjects": {"酸汤": ["红汤产业"]}, "regions": {"凯里": ["凯里市"]}})
        self.assertEqual((custom[0]["subject"], custom[0]["region"]), ("酸汤", "凯里"))

    def test_conflicts_same_scope_marked_not_different_metrics(self) -> None:
        facts = self.facts("2024年凯里酸汤鱼收入10亿元。2024年凯里酸汤鱼收入12亿元。"
                           "2024年凯里酸汤鱼产值8亿元。")
        self.assertIn("conflicting_scope_values", facts[0]["uncertainty_flags"])
        self.assertIn("conflicting_scope_values", facts[1]["uncertainty_flags"])
        self.assertNotIn("conflicting_scope_values", facts[2]["uncertainty_flags"])

    def test_percent_growth_separate_from_level_and_decline_signed(self) -> None:
        facts = self.facts("2024年凯里酸汤鱼收入10亿元，同比下降8%。")
        self.assertEqual([(f["metric"], f["value"]) for f in facts],
                         [("revenue", 1e9), ("revenue_growth_rate", -8.0)])

    def test_ranges_approximations_cumulative_and_missing_date_flagged(self) -> None:
        samples = [("2024年凯里酸汤鱼产值约12亿元。", "bounded_or_approximate"),
                   ("2024年凯里酸汤鱼产值10至12亿元。", "numeric_range"),
                   ("2024年上半年凯里酸汤鱼产值12亿元。", "nonannual_period")]
        for text, flag in samples:
            with self.subTest(text=text):
                self.assertIn(flag, self.facts(text)[0]["uncertainty_flags"])
        fact = self.facts("2024年凯里酸汤鱼产值12亿元。", published=None)[0]
        self.assertIn("missing_publication_date", fact["uncertainty_flags"])

    def test_extraction_is_deterministic_and_does_not_mutate_input(self) -> None:
        source = [passage("2024年凯里酸汤鱼产值12亿元。")]
        original = copy.deepcopy(source)
        facts = evidence.extract_facts(source, {})
        self.assertEqual(facts, evidence.extract_facts(source, {}))
        self.assertEqual(source, original)

    def test_fact_id_binds_canonical_scope_so_old_reviews_cannot_transfer(self) -> None:
        source = [passage("2024年凯里红汤产业产值12亿元。")]
        original_config = {"subjects": {"酸汤产业": ["红汤产业"]}}
        revised_config = {"subjects": {"其他产业": ["红汤产业"]}}
        original = evidence.extract_facts(source, original_config)[0]
        revised = evidence.extract_facts(source, revised_config)[0]
        self.assertEqual(original["evidence_excerpt"], revised["evidence_excerpt"])
        self.assertNotEqual(original["subject"], revised["subject"])
        self.assertNotEqual(original["fact_id"], revised["fact_id"])
        self.assertEqual(original["fact_id"],
                         evidence.extract_facts(source, original_config)[0]["fact_id"])
        region_config = {**original_config, "regions": {"另一口径": ["凯里"]}}
        self.assertNotEqual(original["fact_id"],
                            evidence.extract_facts(source, region_config)[0]["fact_id"])


class SeriesTests(unittest.TestCase):
    """批准门槛、时间口径、防泄漏与冲突必须 fail closed。"""

    def test_pending_or_rejected_never_enters_series(self) -> None:
        for status in ("pending", "rejected", "", None):
            self.assertEqual(evidence.build_series([approved(review_status=status)]), [])

    def test_normalization_deduplicates_evidence_without_averaging(self) -> None:
        first, second = approved(identifier="f1"), approved(identifier="f2")
        second["published_at"] = "2025-05-01"
        series = evidence.build_series([second, first])
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["evidence_ids"], ["f1", "f2"])
        self.assertTrue(series[0]["known_at"].startswith("2025-03-01"))
        self.assertEqual(series[0]["value"], 100.0)
        self.assertIsInstance(series[0]["period"], int)

    def test_conflicts_excluded_even_if_human_approved_both(self) -> None:
        self.assertEqual(evidence.build_series([approved(100), approved(101, "f2")]), [])

    def test_rejected_conflicting_source_allows_explicit_human_resolution(self) -> None:
        first = approved(100, uncertainty_flags=["conflicting_scope_values"])
        second = approved(101, "f2", uncertainty_flags=["conflicting_scope_values"])
        self.assertEqual(evidence.build_series([first, second]), [])
        second["review_status"] = "rejected"
        self.assertEqual(len(evidence.build_series([first, second])), 1)

    def test_scope_separates_subject_metric_region_frequency_and_unit(self) -> None:
        facts = [approved(), approved(identifier="f2", subject="辣椒"),
                 approved(identifier="f3", metric="revenue"),
                 approved(identifier="f4", region="贵州"),
                 approved(identifier="f5", unit="吨")]
        rows = evidence.build_series(facts)
        self.assertEqual(len(rows), 5)
        self.assertEqual(len({row["series_id"] for row in rows}), 5)
        self.assertEqual(evidence.build_series([approved(period_type="quarter")]), [])

    def test_missing_or_future_annual_publication_fails_closed(self) -> None:
        for updates in ({"published_at": None, "known_at": None},
                        {"published_at": "invalid"}, {"published_at": "2023-01-01"},
                        {"published_at": "2024-06-30"}, {"period": None}):
            self.assertEqual(evidence.build_series([approved(**updates)]), [])

    def test_uncertainty_and_nonfinite_values_fail_closed(self) -> None:
        for updates in ({"uncertainty_flags": ["missing_region"]}, {"value": float("nan")},
                        {"value": float("inf")}, {"value": "bad"}, {"subject": None}):
            self.assertEqual(evidence.build_series([approved(**updates)]), [])

    def test_inputs_unchanged(self) -> None:
        facts = [approved()]
        original = copy.deepcopy(facts)
        evidence.build_series(facts)
        self.assertEqual(facts, original)

    def test_series_contract_is_accepted_by_actual_trend_api(self) -> None:
        from research import trend
        rows = evidence.build_series([approved()])
        report = trend.analyze_series(rows)
        self.assertEqual(report["status"], "insufficient_data")
        self.assertEqual(report["series_reports"][0]["input_count"], 1)
        self.assertEqual(report["series_reports"][0]["scope"], rows[0]["scope"])


if __name__ == "__main__":
    unittest.main()
