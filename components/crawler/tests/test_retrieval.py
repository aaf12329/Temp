# -*- coding: utf-8 -*-
"""语义召回的零网络测试：假向量测判定，假 ONNX 测 CPU 配置与批次映射。

结构树：FakeVectors → TestScoring；FakeTokenizer / FakeSession → TestEncoder。
调用关系：unittest → score_passages / Encoder（模型文件全部为临时假文件）。
数据流：合成段落 / token → 假模型 CLS → 归一化分数 → 选择与证据断言。
"""

from __future__ import annotations

import copy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from research import retrieval  # noqa: E402
from webcrawl.io_utils import sha256_of, write_bytes, write_json  # noqa: E402


# ------------------------------------------------------------ ① 纯判定测试
class FakeVectors:
    """按完整文本回填预设向量，记录编码输入以排除关键词预过滤。"""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[tuple[list[str], bool]] = []

    def encode(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        self.calls.append((list(texts), is_query))
        return [self.vectors[text] for text in texts]


class TestScoring(unittest.TestCase):
    """覆盖语义独立召回、词法显式模式、契约和错误输出隔离。"""

    def setUp(self) -> None:
        self.config = {"keywords": ["酸汤鱼"], "queries": ["行业趋势", "原料产量"]}
        self.passages = [
            {"id": "a", "text": "发酵调味品增长", "title": "酸汤鱼产业"},
            {"id": "b", "text": "酸汤鱼门店数量", "title": "调查"},
            {"id": "c", "text": "天气预报", "title": "酸汤鱼产业"},
        ]
        self.encoder = FakeVectors({
            "行业趋势": [1.0, 0.0], "原料产量": [0.0, 1.0],
            "发酵调味品增长": [1.0, 0.0], "酸汤鱼门店数量": [0.5, 0.5],
            "天气预报": [-1.0, -1.0],
        })

    def test_semantic_ranking_and_lexical_union(self) -> None:
        scored = retrieval.score_passages(self.passages, self.config, self.encoder)
        self.assertEqual([row["selected"] for row in scored], [True, True, False])
        self.assertTrue(scored[0]["semantic_only"])
        self.assertEqual(scored[0]["best_query"], "行业趋势")
        self.assertGreater(scored[0]["semantic_score"], scored[1]["semantic_score"])
        self.assertEqual(scored[1]["relevance_reason"], "keyword")
        self.assertEqual(scored[2]["relevance_reason"], "none")
        self.assertEqual(self.encoder.calls[1], ([row["text"] for row in self.passages], False))

    def test_title_alone_never_selects_unrelated_passage(self) -> None:
        scored = retrieval.score_passages(self.passages, self.config)
        self.assertFalse(scored[0]["selected"])
        self.assertFalse(scored[2]["selected"])
        self.assertEqual(scored[0]["keyword_hits"], [])

    def test_input_fields_order_and_objects_preserved(self) -> None:
        before = copy.deepcopy(self.passages)
        scored = retrieval.score_passages(self.passages, self.config, self.encoder)
        self.assertEqual(before, self.passages)
        self.assertEqual([row["id"] for row in scored], ["a", "b", "c"])
        self.assertIsNot(scored[0], self.passages[0])
        for original, result in zip(self.passages, scored):
            self.assertTrue(original.items() <= result.items())

    def test_lexical_mode_never_fakes_semantics(self) -> None:
        scored = retrieval.score_passages(self.passages, self.config)
        self.assertTrue(scored[1]["selected"])
        self.assertIsNone(scored[1]["semantic_score"])
        self.assertIsNone(scored[1]["best_query"])
        self.assertFalse(scored[1]["semantic_only"])

    def test_empty_inputs_do_not_call_model(self) -> None:
        self.assertEqual(retrieval.score_passages([], self.config, self.encoder), [])
        scored = retrieval.score_passages(self.passages, {}, self.encoder)
        self.assertTrue(all(row["semantic_score"] is None for row in scored))
        self.assertEqual(self.encoder.calls, [])

    def test_empty_text_is_never_semantically_selected(self) -> None:
        encoder = FakeVectors({"": [1.0, 0.0], "q": [1.0, 0.0]})
        scored = retrieval.score_passages([{"text": ""}], {"queries": ["q"]}, encoder)
        self.assertEqual(scored[0]["semantic_score"], 1.0)
        self.assertFalse(scored[0]["selected"])

    def test_casefold_terms_stable_dedup_and_body_alias(self) -> None:
        result = retrieval.score_passages(
            [{"passage_text": "XGBOOST 可用于预测"}],
            {"keywords": [" xgboost ", "xgboost", "预测", ""]},
        )
        self.assertEqual(result[0]["keyword_hits"], ["xgboost", "预测"])

    def test_threshold_boundary_and_combined_reason(self) -> None:
        config = dict(self.config, semantic_threshold=1.0, keywords=["发酵"])
        row = retrieval.score_passages(self.passages[:1], config, self.encoder)[0]
        self.assertTrue(row["selected"])
        self.assertEqual(row["relevance_reason"], "keyword+semantic")
        self.assertFalse(row["semantic_only"])

    def test_nonlexical_high_score_exclusion_retains_score_and_evidence(self) -> None:
        passage = {"id": "distractor", "text": "酸菜鱼门店产量增长"}
        encoder = FakeVectors({"行业趋势": [1.0, 0.0], passage["text"]: [1.0, 0.0]})
        config = {"keywords": ["酸汤鱼"], "queries": ["行业趋势"],
                  "semantic_threshold": 0.64, "semantic_exclude_keywords": ["酸菜鱼"]}
        row = retrieval.score_passages([passage], config, encoder)[0]
        self.assertFalse(row["selected"])
        self.assertFalse(row["semantic_only"])
        self.assertEqual(row["semantic_score"], 1.0)
        self.assertEqual(row["best_query"], "行业趋势")
        self.assertEqual(row["semantic_exclusion_hits"], ["酸菜鱼"])
        self.assertEqual(row["relevance_reason"], "semantic_excluded")
        self.assertEqual(row["text"], passage["text"])

    def test_lexical_comparison_survives_semantic_exclusion(self) -> None:
        passage = {"text": "酸汤鱼与酸菜鱼产业比较"}
        encoder = FakeVectors({"行业趋势": [1.0, 0.0], passage["text"]: [1.0, 0.0]})
        config = {"keywords": ["酸汤鱼"], "queries": ["行业趋势"],
                  "semantic_exclude_keywords": ["酸菜鱼"]}
        row = retrieval.score_passages([passage], config, encoder)[0]
        self.assertTrue(row["selected"])
        self.assertEqual(row["keyword_hits"], ["酸汤鱼"])
        self.assertEqual(row["semantic_exclusion_hits"], ["酸菜鱼"])
        self.assertEqual(row["relevance_reason"], "keyword+semantic")

    def test_no_exclusions_preserves_semantic_recall(self) -> None:
        passage = {"text": "酸菜鱼门店产量增长"}
        encoder = FakeVectors({"行业趋势": [1.0, 0.0], passage["text"]: [1.0, 0.0]})
        row = retrieval.score_passages([passage], {"queries": ["行业趋势"]}, encoder)[0]
        self.assertTrue(row["selected"])
        self.assertTrue(row["semantic_only"])
        self.assertEqual(row["semantic_exclusion_hits"], [])
        self.assertEqual(row["relevance_reason"], "semantic")

    def test_exclusion_terms_validate_like_keywords(self) -> None:
        with self.assertRaises(ValueError):
            retrieval.score_passages([], {"semantic_exclude_keywords": "酸菜鱼"})

    def test_invalid_threshold_or_terms_fail(self) -> None:
        for config in ({"semantic_threshold": float("nan")}, {"semantic_threshold": 1.1},
                       {"keywords": "酸汤鱼"}, {"queries": [1]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                retrieval.score_passages(self.passages, config)

    def test_dropped_vectors_fail_instead_of_silent_zip(self) -> None:
        class DroppingEncoder:
            def encode(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
                return [[1.0, 0.0]]

        with self.assertRaisesRegex(ValueError, "条数"):
            retrieval.score_passages(self.passages, self.config, DroppingEncoder())

    def test_bad_dimensions_nan_and_zero_vectors(self) -> None:
        for bad in ([1.0], [float("nan"), 0.0], []):
            encoder = FakeVectors({"q": [1.0, 0.0], "p": bad})
            with self.subTest(vector=bad), self.assertRaises(ValueError):
                retrieval.score_passages([{"text": "p"}], {"queries": ["q"]}, encoder)
        encoder = FakeVectors({"q": [1.0, 0.0], "p": [0.0, 0.0]})
        row = retrieval.score_passages([{"text": "p"}], {"queries": ["q"]}, encoder)[0]
        self.assertEqual(row["semantic_score"], 0.0)
        self.assertFalse(row["selected"])


# ------------------------------------------------------------ ② 模型适配测试
class FakeTokenizer:
    """保持每条文本与 token 一一对应，模拟真实 tokenizer 的截断与动态 padding。"""

    def __init__(self) -> None:
        self.max_length = 0
        self.calls: list[list[str]] = []

    def enable_truncation(self, *, max_length: int) -> None:
        self.max_length = max_length

    def enable_padding(self, **kwargs: Any) -> None:
        self.padding = kwargs

    def encode_batch(self, texts: list[str]) -> list[Any]:
        self.calls.append(list(texts))
        ids = [[101] + [ord(char) for char in text[:self.max_length - 2]] + [102]
               for text in texts]
        width = max(len(row) for row in ids)
        return [types.SimpleNamespace(
            ids=row + [0] * (width - len(row)),
            attention_mask=[1] * len(row) + [0] * (width - len(row)),
            type_ids=[0] * width,
        ) for row in ids]


class FakeSession:
    """输出第一个正文 token 作为 CLS 的一维，验证批次和输出行没有错配。"""

    def __init__(self, path: str, **kwargs: Any) -> None:
        self.path = path
        self.options = kwargs
        self.feeds: list[dict[str, Any]] = []

    def get_inputs(self) -> list[Any]:
        return [types.SimpleNamespace(name=name)
                for name in ("input_ids", "attention_mask", "token_type_ids")]

    def get_outputs(self) -> list[Any]:
        return [types.SimpleNamespace(name="last_hidden_state")]

    def run(self, outputs: list[str], feed: dict[str, Any]) -> list[Any]:
        self.feeds.append(feed)
        ids = feed["input_ids"]
        hidden = np.zeros((ids.shape[0], ids.shape[1], 2), dtype=np.float32)
        hidden[:, 0, 0] = ids[:, 1]
        hidden[:, 0, 1] = 1.0
        return [hidden]


class TestEncoder(unittest.TestCase):
    """不下载模型，验证 CPU、CLS、token 上限与归一化真实数组操作。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model_dir = Path(self.tmp.name)
        for filename in ("tokenizer.json", "model_quantized.onnx"):
            write_bytes(self.model_dir / filename, b"fake")
        write_json(self.model_dir / "manifest.json", {
            "model_id": retrieval.MODEL_ID, "revision": "pinned-commit",
            "files": {"model_quantized.onnx": {"sha256": sha256_of(b"fake")},
                      "tokenizer.json": {"sha256": sha256_of(b"fake")}},
        })
        self.tokenizer = FakeTokenizer()
        self.fake_ort = types.SimpleNamespace(
            SessionOptions=types.SimpleNamespace,
            ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL="sequential"),
            GraphOptimizationLevel=types.SimpleNamespace(ORT_ENABLE_ALL="all"),
            InferenceSession=FakeSession,
        )
        self.patch = patch.dict(sys.modules, {
            "onnxruntime": self.fake_ort,
            "tokenizers": types.SimpleNamespace(Tokenizer=types.SimpleNamespace(
                from_file=lambda path: self.tokenizer,
            )),
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_cpu_options_manifest_and_default_address_book(self) -> None:
        with patch.object(retrieval.ps, "EMBED_MODEL_DIR", str(self.model_dir), create=True):
            encoder = retrieval.Encoder()
        options = encoder._session.options
        self.assertEqual(options["providers"], ["CPUExecutionProvider"])
        self.assertEqual(options["sess_options"].intra_op_num_threads, 4)
        self.assertEqual(options["sess_options"].inter_op_num_threads, 1)
        report = encoder.report()
        self.assertEqual(report["model_sha256"], sha256_of(b"fake"))
        self.assertEqual(report["tokenizer_sha256"], sha256_of(b"fake"))
        self.assertEqual(report["revision"], "pinned-commit")
        self.assertEqual(report["batch_size"], 8)
        self.assertEqual(report["max_length"], 256)

    def test_batch_boundaries_truncation_and_cls_mapping(self) -> None:
        encoder = retrieval.Encoder(self.model_dir)
        texts = [chr(65 + index) + "正文" * 300 for index in range(19)]
        vectors = encoder.encode(texts)
        self.assertEqual(vectors.shape, (19, 2))
        self.assertEqual([len(batch) for batch in self.tokenizer.calls], [8, 8, 3])
        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), np.ones(19), atol=1e-6)
        for index, vector in enumerate(vectors):
            expected = np.array([ord(texts[index][0]), 1.0], dtype=np.float32)
            np.testing.assert_allclose(vector, expected / np.linalg.norm(expected))
        for feed in encoder._session.feeds:
            self.assertEqual(feed["input_ids"].shape[1], 256)
            self.assertEqual(feed["input_ids"].dtype, np.int64)
            self.assertEqual(feed["input_ids"].shape, feed["attention_mask"].shape)

    def test_query_prefix_only_applies_to_queries(self) -> None:
        encoder = retrieval.Encoder(self.model_dir)
        encoder.encode(["产业趋势"], is_query=True)
        encoder.encode(["正文"], is_query=False)
        self.assertEqual(self.tokenizer.calls, [[retrieval.QUERY_PREFIX + "产业趋势"], ["正文"]])

    def test_empty_batch_zero_session_calls(self) -> None:
        encoder = retrieval.Encoder(self.model_dir)
        self.assertEqual(encoder.encode([]).shape, (0, 512))
        self.assertEqual(encoder._session.feeds, [])
        encoder.encode([""])
        self.assertEqual(encoder.encode([]).shape, (0, 2))

    def test_missing_files_and_invalid_limits_fail_early(self) -> None:
        with self.assertRaises(FileNotFoundError):
            retrieval.Encoder(self.model_dir / "missing")
        for kwargs in ({"batch_size": 0}, {"threads": 0}, {"max_length": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                retrieval.Encoder(self.model_dir, **kwargs)

    def test_tampered_model_is_rejected_before_loading(self) -> None:
        write_bytes(self.model_dir / "model_quantized.onnx", b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            retrieval.Encoder(self.model_dir)

    def test_module_self_test(self) -> None:
        retrieval._self_test()


if __name__ == "__main__":
    unittest.main()
