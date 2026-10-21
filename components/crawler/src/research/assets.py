# -*- coding: utf-8 -*-
"""向量模型缓存准备，不参与网页采集。

结构树：① validate_manifest → 校验缓存；② prepare_model → 下载指定版本并校验。
调用：main.cmd_model → prepare_model → net.fetch / io_utils。
数据流：固定 Hugging Face revision → ONNX/tokenizer → SHA256 manifest。
契约：prepare_model() -> {model_id, revision, files, ...}；失败抛 ValueError。
不下载 PyTorch 权重，不执行远程代码。网页采集仍走原有 robots 与站点策略。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from Path_Store import Path_Store as ps
from webcrawl import io_utils, net

MODEL_ID = "Xenova/bge-small-zh-v1.5"
REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781"
REMOTE_FILES = {
    "model_quantized.onnx": ("onnx/model_quantized.onnx", ps.EMBED_MODEL_PATH),
    "tokenizer.json": ("tokenizer.json", ps.EMBED_TOKENIZER_PATH),
}
MAX_MODEL_BYTES = 150_000_000


# ===== ① 纯逻辑 =====
def validate_manifest(manifest: dict[str, Any]) -> bool:
    """只检查模型版本与 SHA256 结构；文件验证由准备流程执行。"""
    files = manifest.get("files", {})
    return (manifest.get("revision") == REVISION and all(
        len(files.get(name, {}).get("sha256", "")) == 64 for name in REMOTE_FILES))


# ===== ② 网络与落盘 =====
def prepare_model() -> dict[str, Any]:
    """固定版本下载；已验证的缓存复用，损坏时给出重新下载提示。"""
    if Path(ps.EMBED_MANIFEST_PATH).is_file():
        manifest = io_utils.read_json(ps.EMBED_MANIFEST_PATH)
        if validate_manifest(manifest) and all(
            Path(path).is_file() and hashlib.sha256(io_utils.read_bytes(path)).hexdigest()
            == manifest["files"][name]["sha256"]
            for name, (_, path) in REMOTE_FILES.items()
        ):
            return manifest
    files: dict[str, Any] = {}
    for name, (remote, path) in REMOTE_FILES.items():
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{remote}"
        result = net.fetch(url, timeout=60, retries=2, proxies={})
        if result.get("error"):
            # 本机直连 HF 部分 resolve 路径会 TLS EOF；环境代理可用时显式补试。
            result = net.fetch(url, timeout=60, retries=1)
        raw = result.get("raw", b"")
        if result.get("error") or result.get("status") != 200 or not raw:
            raise ValueError(f"模型下载失败：{name}；检查网络后重跑 model-setup")
        if len(raw) > MAX_MODEL_BYTES:
            raise ValueError("模型文件超出 150MB 预算，停止写入；请核对固定版本")
        io_utils.write_bytes(path, raw)
        files[name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                       "bytes": len(raw), "source_url": url}
    manifest = {"model_id": MODEL_ID, "revision": REVISION, "files": files,
                "upstream_model": "BAAI/bge-small-zh-v1.5", "provider": "CPUExecutionProvider"}
    io_utils.write_json(ps.EMBED_MANIFEST_PATH, manifest)
    return manifest


# ===== ③ 零网络自测 =====
def _self_test() -> None:
    """验证缓存契约，不触碰真实模型目录。"""
    assert not validate_manifest({})
    assert validate_manifest({"revision": REVISION, "files": {
        name: {"sha256": "a" * 64} for name in REMOTE_FILES}})


if __name__ == "__main__":
    _self_test()
