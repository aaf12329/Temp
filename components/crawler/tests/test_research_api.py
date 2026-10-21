# -*- coding: utf-8 -*-
"""研究入口/审核/模型缓存回归：全部 synthetic 临时材料，禁止真实网络与数据目录。

结构树：TestResearchApi（临时 normalized→CSV→审核） / TestAssets（假下载与缓存）。
调用关系：unittest → research_api / review / assets；外部下载仅使用假 net.fetch。
数据流：合成 CrawlResult + 临时正文 → 可追溯报告 → 独立审核 → 趋势数据不足报告。
契约：验证 JSON 返回壳、CSV BOM 与偏移、原分析不变、复现清单及固定模型版本。
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import research_api
from research import assets, review
from webcrawl import io_utils


# ==================== ① 纯夹具：全量路径指向临时目录 ====================


class TestResearchApi(unittest.TestCase):
    """实际文件往返只发生在 TemporaryDirectory，模型和网络都被隔离。"""

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.out = self.root / "synthetic_run"
        self.config_path = self.root / "config.json"
        self.index_path = self.root / "pages.json"
        self.config = {"keywords": ["酸汤"], "queries": ["synthetic research only"],
                       "subjects": ["酸汤产业"], "regions": ["贵州"],
                       "semantic_threshold": 0.72, "rule_version": "synthetic-test-v1"}
        io_utils.write_json(self.config_path, self.config)
        self.text = "2023年贵州酸汤产业产值为50亿元。\n此段是无关的 synthetic 合成夹具。"
        self.text_path = self.root / "data" / "normalized" / "text" / "synthetic.txt"
        io_utils.write_text(self.text_path, self.text)
        para_path = self.root / "data" / "normalized" / "para" / "synthetic.jsonl"
        io_utils.write_text(para_path, json.dumps({"char_start": 0, "char_end": len(self.text),
                                                 "text": self.text}, ensure_ascii=False))
        self.records = [{"record_id": "synthetic-record", "job_id": "synthetic-job",
                         "status": "ok", "url": "https://synthetic.invalid/article",
                         "title": "synthetic fixture only", "fetched_at": "2024-06-01",
                         "source": {"publisher": "synthetic", "published_at": "2024-05-01"},
                         "storage": {"text_path": "data/normalized/text/synthetic.txt"},
                         "paragraph_file": "data/normalized/para/synthetic.jsonl",
                         "text_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest()}]
        io_utils.write_json(self.index_path, self.records)
        for name, value in {"PROJECT_ROOT": str(self.root), "RESEARCH_DIR": str(self.out),
                            "RESEARCH_CONFIG": str(self.config_path),
                            "PAGES_INDEX_JSON": str(self.index_path),
                            "EMBED_MODEL_DIR": str(self.root / "missing_model")}.items():
            self.stack.enter_context(patch.object(research_api.ps, name, value))
        self.network = self.stack.enter_context(patch.object(
            assets.net, "fetch", side_effect=AssertionError("real network forbidden")))
        self.stack.enter_context(patch.object(research_api, "_code_version", return_value="test"))
        self.stack.enter_context(patch.object(
            research_api, "_dependency_versions", return_value={}))

    def _analyze(self, **changes: object) -> dict:
        """默认明确 lexical，绝不意外构造真实编码器。"""
        return research_api.analyze({"mode": "lexical", **changes})

    def _analysis(self) -> dict:
        """读取本测试临时分析产物。"""
        return io_utils.read_json(research_api.ps.research_output(str(self.out), "analysis"))

    def _reviews(self, rows: list[dict]) -> str:
        """生成 Excel 可读的合成审核文件。"""
        path = self.root / "synthetic_reviews.csv"
        io_utils.write_csv(path, rows, ["fact_id", "review_status", "review_note", "value"])
        return str(path)

    def test_local_lexical_pipeline_preserves_unicode_offsets_and_bom(self) -> None:
        with patch.object(research_api.retrieval, "Encoder",
                          side_effect=AssertionError("lexical must not load model")):
            response = self._analyze()
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["counts"]["candidate_facts"], 1)
        self.assertEqual(response["data"]["counts"]["series_rows"], 0)
        data = self._analysis()
        self.assertEqual(data["manifest"]["embedding"], {"mode": "lexical"})
        self.assertEqual(data["manifest"]["code_version"], "test")
        self.assertEqual(data["manifest"]["config_sha256"], hashlib.sha256(
            self.config_path.read_bytes()).hexdigest())
        self.assertEqual(data["facts"][0]["review_status"], "pending")
        for passage in data["passages"]:
            self.assertEqual(self.text[passage["char_start"]:passage["char_end"]], passage["text"])
            self.assertIsNone(passage["semantic_score"])
        for fact in data["facts"]:
            excerpt = self.text[fact["char_start"]:fact["char_end"]]
            self.assertEqual(excerpt, fact["evidence_excerpt"])
        for name, fields in research_api.CSV_FIELDS.items():
            path = Path(response["data"]["paths"][name])
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, list(fields))
                if name == "passages":
                    row = next(reader)
                    self.assertEqual(self.text[int(row["char_start"]):int(row["char_end"])],
                                     row["text"])
        self.network.assert_not_called()

    def test_facade_filters_selected_candidates_and_passes_explicit_encoder(self) -> None:
        passage = {"passage_id": "synthetic-passage", "text": "synthetic", "selected": True}
        excluded = {**passage, "passage_id": "synthetic-unselected", "selected": False}
        with ExitStack() as stack:
            stack.enter_context(patch.object(research_api.evidence, "load_corpus", return_value={
                "documents": [], "passages": [passage, excluded], "issues": []}))
            score = stack.enter_context(patch.object(research_api.retrieval, "score_passages",
                                                     return_value=[passage, excluded]))
            extract = stack.enter_context(patch.object(research_api.evidence, "extract_facts",
                                                       return_value=[]))
            stack.enter_context(patch.object(
                research_api.evidence, "build_series", return_value=[]))
            model = stack.enter_context(patch.object(research_api.trend, "analyze_series",
                return_value={"status": "insufficient_data", "series_reports": []}))
            response = self._analyze(records=[])
        self.assertEqual(response["status"], "ok", response)
        self.assertIsNone(score.call_args.args[2])
        self.assertEqual(extract.call_args.args[0], [passage])
        self.assertEqual(model.call_args.args[0], [])

    def test_default_semantic_mode_missing_local_model_returns_error_without_download(self) -> None:
        response = research_api.analyze({})
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "E_ANALYSIS")
        self.assertFalse(response["error"]["retryable"])
        self.assertFalse(self.out.exists())
        self.network.assert_not_called()

    def test_malformed_modes_and_request_types_return_error_envelope(self) -> None:
        for request in [None, [], "lexical", {"mode": "gpu"}, {"mode": []}, {"mode": {}}]:
            with self.subTest(request=request):
                response = research_api.analyze(request)
                self.assertEqual(response["status"], "error")
                self.assertIsNone(response["data"])
                self.assertEqual(response["api_version"], research_api.API_VERSION)

    def test_malformed_configuration_returns_error_without_exports(self) -> None:
        configs = [None, [], {"semantic_threshold": None}, {"semantic_threshold": {}},
                   {"semantic_threshold": float("nan")}, {"semantic_threshold": 2},
                   {**self.config, "keywords": "not-a-list"}]
        for config in configs:
            with self.subTest(config=config):
                io_utils.write_json(self.config_path, config)
                response = self._analyze()
                self.assertEqual(response["status"], "error")
                self.assertEqual(response["error"]["code"], "E_ANALYSIS")
                self.assertFalse(self.out.exists())

    def test_nonarray_index_and_missing_file_have_error_envelopes(self) -> None:
        self.assertEqual(self._analyze(records={})["status"], "error")
        response = self._analyze(config_path=str(self.root / "absent.json"))
        self.assertEqual(response["error"]["code"], "E_ANALYSIS")

    def test_reviewed_train_preserves_candidates_and_saves_exact_decisions(self) -> None:
        self.assertEqual(self._analyze()["status"], "ok")
        analysis_path = Path(research_api.ps.research_output(str(self.out), "analysis"))
        original = analysis_path.read_bytes()
        fact = self._analysis()["facts"][0]
        reviews = self._reviews([{"fact_id": fact["fact_id"], "review_status": "approved",
                                  "review_note": "synthetic scope checked", "value": 999}])
        response = research_api.train({"reviews": reviews})
        self.assertEqual(response["status"], "ok", response)
        report = response["data"]["trend"]
        self.assertEqual(response["data"]["series_rows"], 1)
        self.assertEqual(report["status"], "insufficient_data")
        expected_hash = hashlib.sha256(Path(reviews).read_bytes()).hexdigest()
        self.assertEqual(report["reviews_sha256"], expected_hash)
        self.assertEqual(report["applied_reviews"][0]["review_status"], "approved")
        self.assertEqual(report["applied_reviews"][0]["review_note"], "synthetic scope checked")
        self.assertEqual(report["series_rows"][0]["value"], fact["value"])
        self.assertIsInstance(report["series_rows"][0]["period"], int)
        self.assertEqual(report["analysis_manifest"], self._analysis()["manifest"])
        self.assertEqual(analysis_path.read_bytes(), original)
        self.assertEqual(self._analysis()["facts"][0]["review_status"], "pending")
        saved = io_utils.read_json(research_api.ps.research_output(str(self.out), "trend"))
        self.assertEqual(saved["applied_reviews"], report["applied_reviews"])
        self.network.assert_not_called()

    def test_unknown_duplicate_invalid_or_unexplained_reviews_are_errors(self) -> None:
        self.assertEqual(self._analyze()["status"], "ok")
        fact_id = self._analysis()["facts"][0]["fact_id"]
        good = {"fact_id": fact_id, "review_status": "approved", "review_note": "synthetic"}
        bad_inputs = [[{**good, "fact_id": "unknown-synthetic"}], [good, good],
                      [{**good, "review_status": "auto-approved"}], [{**good, "review_note": ""}]]
        for rows in bad_inputs:
            with self.subTest(rows=rows):
                response = research_api.train({"reviews": self._reviews(rows)})
                self.assertEqual(response["status"], "error")
                self.assertEqual(response["error"]["code"], "E_TRAIN")

    def test_direct_review_cannot_modify_evidence_or_input(self) -> None:
        facts = [{"fact_id": "synthetic", "review_status": "pending", "value": 10,
                  "url": "https://synthetic.invalid/original"}]
        decisions = [{"fact_id": "synthetic", "review_status": "rejected", "review_note": "bad",
                      "value": 123, "url": "https://synthetic.invalid/replaced"}]
        result = review.apply_reviews(facts, decisions)
        self.assertEqual(result[0]["value"], 10)
        self.assertEqual(result[0]["url"], facts[0]["url"])
        self.assertEqual(facts[0]["review_status"], "pending")

    def test_train_invalid_request_stays_in_error_contract(self) -> None:
        for request in [None, [], {}, {"reviews": "missing.csv"}]:
            with self.subTest(request=request):
                self.assertEqual(research_api.train(request)["status"], "error")

    def test_facade_imports_only_declared_standalone_layers(self) -> None:
        allowed = {"__future__", "math", "json", "subprocess", "time", "collections",
                   "pathlib", "typing", "Path_Store", "webcrawl", "research", "importlib"}
        tree = ast.parse(Path(research_api.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name.split(".")[0] in allowed for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module.split(".")[0], allowed)
        self.assertEqual(research_api.capabilities()["data"]["integration_state"], "standalone")


# ==================== ② 网络边界：固定假响应，缓存全在临时目录 ====================


class TestAssets(unittest.TestCase):
    """缓存下载、校验与限额；net.fetch 在每个测试中均被替换。"""

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.manifest_path = self.root / "manifest.json"
        self.remote = {"model_quantized.onnx": ("onnx/model_quantized.onnx",
                                                str(self.root / "model_quantized.onnx")),
                       "tokenizer.json": ("tokenizer.json", str(self.root / "tokenizer.json"))}
        self.stack.enter_context(patch.object(assets, "REMOTE_FILES", self.remote))
        self.stack.enter_context(patch.object(assets.ps, "EMBED_MANIFEST_PATH",
                                              str(self.manifest_path)))
        self.fetch = self.stack.enter_context(patch.object(assets.net, "fetch", return_value={
            "raw": b"synthetic model bytes", "status": 200, "error": None}))

    def test_explicit_setup_pins_revision_and_reuses_verified_cache(self) -> None:
        first = assets.prepare_model()
        self.assertEqual(first["revision"], assets.REVISION)
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(first["provider"], "CPUExecutionProvider")
        for call in self.fetch.call_args_list:
            self.assertIn(f"/resolve/{assets.REVISION}/", call.args[0])
        self.fetch.reset_mock()
        second = assets.prepare_model()
        self.assertEqual(second, first)
        self.fetch.assert_not_called()
        self.assertTrue(self.manifest_path.is_file())

    def test_tampered_cached_file_triggers_fixed_revision_redownload(self) -> None:
        assets.prepare_model()
        Path(self.remote["model_quantized.onnx"][1]).write_bytes(b"synthetic tampered")
        self.fetch.reset_mock()
        result = assets.prepare_model()
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(result["files"]["model_quantized.onnx"]["sha256"], hashlib.sha256(
            b"synthetic model bytes").hexdigest())

    def test_download_failure_does_not_publish_manifest(self) -> None:
        for response in [{"raw": b"", "status": 200}, {"raw": b"fake", "status": 403},
                         {"raw": b"fake", "status": 200, "error": "synthetic failure"}]:
            with self.subTest(response=response):
                self.fetch.return_value = response
                with self.assertRaises(ValueError):
                    assets.prepare_model()
                self.assertFalse(self.manifest_path.exists())

    def test_size_budget_prevents_oversized_file_write(self) -> None:
        with patch.object(assets, "MAX_MODEL_BYTES", 4), self.assertRaises(ValueError):
            assets.prepare_model()
        self.assertFalse(Path(self.remote["model_quantized.onnx"][1]).exists())
        self.assertFalse(self.manifest_path.exists())

    def test_revision_and_required_hashes_are_mandatory(self) -> None:
        self.assertFalse(assets.validate_manifest({}))
        valid = {"revision": assets.REVISION,
                 "files": {name: {"sha256": "a" * 64} for name in self.remote}}
        self.assertTrue(assets.validate_manifest(valid))
        self.assertFalse(assets.validate_manifest({**valid, "revision": "main"}))
        self.assertFalse(assets.validate_manifest({**valid, "files": {}}))


# ==================== ③ 主流程：unittest，无真实网络或实际数据目录 ====================


if __name__ == "__main__":
    unittest.main()
