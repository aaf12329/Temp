# -*- coding: utf-8 -*-
"""CPU 语义召回与关键词并集；只读本地模型，不下载、不写业务数据。

函数结构树 / 分区导航：
    ① 纯判定：_terms / _passage_text / _cosine / score_passages
    ② CPU 模型适配：Encoder.__init__ / encode / report
    ③ 离线自测：_self_test
调用关系：
    入口层 → score_passages → _terms / _passage_text / _cosine
                            → Encoder.encode（查询、全部段落各一趟）
    入口层 → Encoder → io_utils.read_json / tokenizers / onnxruntime
    _self_test → score_passages（假向量编码器，无网络）
数据流：段落 dict[] + 配置 → 关键词命中 ∪ 归一化 CLS 余弦召回 → 带证据的 dict[]。

契约：score_passages 保留输入顺序、数量和原字段，不改原对象，新增 keyword_hits:list[str]、
semantic_score:float|None、best_query:str|None、selected:bool、relevance_reason:str、
semantic_only:bool。相关原因取 keyword / semantic / keyword+semantic / none /
semantic_excluded。
config 使用 keywords:list[str]、queries:list[str]、semantic_threshold:float（默认 0.72），
以及 semantic_exclude_keywords:list[str]（默认空）；新增 semantic_exclusion_hits:list[str]。
排除词只阻止无关键词段落的语义入选，保留原始分数及段落，原因取 semantic_excluded。
正文取 text（兼容 passage_text）；关键词和语义均只检查正文，所有段落均参与。
标题只保留为来源元数据，避免同一标题使全文每个无关段落全部入选。
encoder=None 是显式关键词模式，semantic_score/best_query 为 None，绝不伪造相似度。
Encoder.encode(texts, is_query=False) 返回 (条数, 维度) float32 数组；查询使用 BGE 前缀。
模型目录来自 Path_Store.EMBED_MODEL_DIR，含 tokenizer.json、model_quantized.onnx、
manifest.json；report 返回模型清单 SHA 和实际推理参数，供入口层写入复现报告。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Path_Store import Path_Store as ps
from webcrawl.io_utils import read_bytes, read_json, sha256_of

MODEL_ID = "Xenova/bge-small-zh-v1.5"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


# ------------------------------------------------------------ ① 纯判定
def _terms(value: Any, name: str) -> list[str]:
    """校验配置词表并稳定去重，被 score_passages 调用。"""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} 必须是字符串列表，例如 [\"酸汤鱼\"]")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} 只能包含字符串")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _passage_text(passage: dict[str, Any]) -> str:
    """取段落正文，被 score_passages 调用；未知正文类型立即报错。"""
    text = passage.get("text", passage.get("passage_text", ""))
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("段落 text 必须是字符串")
    return text


def _cosine(left: Any, right: Any) -> float:
    """校验向量并计算有界余弦；假编码器也遵守同一契约。"""
    if len(left) != len(right) or not len(left):
        raise ValueError("编码向量维度为空或不一致，请检查编码器输出")
    left_values = [float(value) for value in left]
    right_values = [float(value) for value in right]
    if not all(math.isfinite(value) for value in left_values + right_values):
        raise ValueError("编码向量含 NaN 或无穷大，请检查模型输出")
    dot = sum(a * b for a, b in zip(left_values, right_values))
    norm = math.sqrt(sum(a * a for a in left_values) * sum(b * b for b in right_values))
    return max(-1.0, min(1.0, dot / norm)) if norm else 0.0


def score_passages(
    passages: list[dict[str, Any]],
    config: dict[str, Any],
    encoder: Any | None = None,
) -> list[dict[str, Any]]:
    """入口层调用：对全部段落做关键词或语义召回，返回带分数的副本。"""
    keywords = _terms(config.get("keywords", []), "keywords")
    queries = _terms(config.get("queries", []), "queries")
    exclusions = _terms(config.get("semantic_exclude_keywords", []),
                        "semantic_exclude_keywords")
    threshold = float(config.get("semantic_threshold", 0.72))
    if not math.isfinite(threshold) or not -1.0 <= threshold <= 1.0:
        raise ValueError("semantic_threshold 必须是 [-1, 1] 内的有限数值")
    texts = [_passage_text(passage) for passage in passages]
    matches: list[tuple[float, str] | None] = [None] * len(passages)
    if encoder is not None and queries and passages:
        query_vectors = encoder.encode(queries, is_query=True)
        passage_vectors = encoder.encode(texts, is_query=False)
        if len(query_vectors) != len(queries) or len(passage_vectors) != len(passages):
            raise ValueError("编码器返回条数与输入不一致；禁止截断、丢段或错配证据")
        for index, vector in enumerate(passage_vectors):
            scores = [_cosine(vector, query_vector) for query_vector in query_vectors]
            best = max(range(len(scores)), key=scores.__getitem__)
            matches[index] = (scores[best], queries[best])
    results: list[dict[str, Any]] = []
    for index, passage in enumerate(passages):
        search_text = texts[index].casefold()
        hits = [word for word in keywords if word.casefold() in search_text]
        exclusion_hits = [word for word in exclusions if word.casefold() in search_text]
        match = matches[index]
        score = match[0] if match is not None else None
        semantic = score is not None and bool(texts[index].strip()) and score >= threshold
        excluded = semantic and bool(exclusion_hits) and not hits
        if excluded:
            semantic = False
        reason = "keyword+semantic" if hits and semantic else "keyword" if hits else "none"
        if semantic and not hits:
            reason = "semantic"
        if excluded:
            reason = "semantic_excluded"
        row = dict(passage)
        row.update({
            "keyword_hits": hits,
            "semantic_exclusion_hits": exclusion_hits,
            "semantic_score": score,
            "best_query": match[1] if match is not None else None,
            "selected": bool(hits or semantic),
            "relevance_reason": reason,
            "semantic_only": bool(semantic and not hits),
        })
        results.append(row)
    return results


# ------------------------------------------------------------ ② CPU 模型适配
class Encoder:
    """入口层构造一次、批量复用的量化 BGE 编码器，仅启用 CPUExecutionProvider。"""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        batch_size: int = 8,
        max_length: int = 256,
        threads: int = 4,
    ) -> None:
        """加载已下载模型；缺依赖或文件时给出修复提示，不隐式降级。"""
        if batch_size < 1 or max_length < 2 or threads < 1:
            raise ValueError("batch_size/threads 必须为正数，max_length 必须至少为 2")
        self.model_dir = Path(model_dir if model_dir is not None else ps.EMBED_MODEL_DIR)
        self.batch_size = batch_size
        self.max_length = max_length
        self.threads = threads
        for filename in ("tokenizer.json", "model_quantized.onnx", "manifest.json"):
            if not (self.model_dir / filename).is_file():
                raise FileNotFoundError(f"缺少本地模型文件：{self.model_dir / filename}")
        self.manifest = read_json(self.model_dir / "manifest.json")
        if not isinstance(self.manifest, dict):
            raise ValueError("manifest.json 必须是 JSON 对象，请重新生成模型清单")
        files = self.manifest.get("files", {})
        for filename in ("tokenizer.json", "model_quantized.onnx"):
            entry = files.get(filename, {}) if isinstance(files, dict) else {}
            expected = entry.get("sha256") if isinstance(entry, dict) else entry
            actual = sha256_of(read_bytes(self.model_dir / filename))
            if not isinstance(expected, str) or actual != expected.lower():
                raise ValueError(f"模型文件 SHA256 与 manifest 不符：{filename}；请重新下载")
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            message = "语义模式需要 numpy、onnxruntime、tokenizers；请按依赖清单安装"
            raise RuntimeError(message) from exc
        self._np = np
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(self.model_dir / "model_quantized.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._inputs = {item.name for item in self._session.get_inputs()}
        if not {"input_ids", "attention_mask"}.issubset(self._inputs):
            raise ValueError("ONNX 模型缺少 input_ids/attention_mask，确认使用 BGE 模型")
        output_names = [item.name for item in self._session.get_outputs()]
        self._output = (
            "last_hidden_state" if "last_hidden_state" in output_names else output_names[0]
        )
        self._dimension = 512

    def encode(self, texts: list[str], *, is_query: bool = False) -> Any:
        """分批截断 token、提取 CLS 并 L2 归一化，每个输入严格对应一行。"""
        np = self._np
        if not texts:
            return np.empty((0, self._dimension), dtype=np.float32)
        if any(not isinstance(text, str) for text in texts):
            raise ValueError("encode 输入只能包含字符串")
        batches = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset:offset + self.batch_size]
            if is_query:
                batch = [QUERY_PREFIX + text for text in batch]
            tokens = self._tokenizer.encode_batch(batch)
            arrays = {
                "input_ids": np.asarray([token.ids for token in tokens], dtype=np.int64),
                "attention_mask": np.asarray(
                    [token.attention_mask for token in tokens], dtype=np.int64,
                ),
                "token_type_ids": np.asarray(
                    [token.type_ids for token in tokens], dtype=np.int64,
                ),
            }
            feed = {name: arrays[name] for name in self._inputs if name in arrays}
            hidden = self._session.run([self._output], feed)[0]
            if hidden.ndim != 3 or hidden.shape[0] != len(batch):
                raise ValueError("BGE 输出必须是 (批次, token, 维度)，请检查 ONNX 模型")
            cls = np.asarray(hidden[:, 0, :], dtype=np.float32)
            if not np.isfinite(cls).all():
                raise ValueError("BGE 输出含 NaN 或无穷大")
            norms = np.linalg.norm(cls, axis=1, keepdims=True)
            batches.append(cls / np.maximum(norms, np.finfo(np.float32).eps))
        result = np.concatenate(batches, axis=0)
        self._dimension = result.shape[1]
        return result

    def report(self) -> dict[str, Any]:
        """返回清单中的模型指纹及 CPU 配置，供入口层写复现报告。"""
        files = self.manifest.get("files", {})
        hashes: dict[str, Any] = {}
        for name in ("model_quantized.onnx", "tokenizer.json"):
            entry = files.get(name, {}) if isinstance(files, dict) else {}
            hashes[name] = entry.get("sha256") if isinstance(entry, dict) else entry
        return {
            "model_id": self.manifest.get("model_id", MODEL_ID),
            "revision": self.manifest.get("revision"),
            "model_sha256": hashes["model_quantized.onnx"],
            "tokenizer_sha256": hashes["tokenizer.json"],
            "model_dir": ps.EMBED_MODEL_REFERENCE,
            "provider": "CPUExecutionProvider",
            "threads": self.threads,
            "batch_size": self.batch_size,
            "max_length": self.max_length,
            "pooling": "CLS",
            "normalized": True,
            "query_prefix": QUERY_PREFIX,
        }


# ------------------------------------------------------------ ③ 离线自测
def _self_test() -> None:
    """仅用假编码器验证语义独立召回和关键词模式，不联网、不碰实际数据。"""
    class FakeEncoder:
        def encode(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
            return [[1.0, 0.0] if is_query or "发酵" in text else [0.0, 1.0]
                    for text in texts]

    passages = [{"text": "发酵调味品产量增长"}, {"text": "酸汤鱼门店"}, {"text": "天气晴"}]
    config = {"keywords": ["酸汤鱼"], "queries": ["产业趋势"]}
    scored = score_passages(passages, config, FakeEncoder())
    assert [row["selected"] for row in scored] == [True, True, False]
    assert scored[0]["semantic_only"] and "semantic_score" not in passages[0]
    assert score_passages(passages, config)[1]["semantic_score"] is None
    assert score_passages([], config, FakeEncoder()) == []


if __name__ == "__main__":
    _self_test()
    print("retrieval self-test: OK")
