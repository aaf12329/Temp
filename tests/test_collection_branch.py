"""采集后选择分支回归，全部使用临时合成材料。

结构：网页暂停/继续/顺序 → CLI询问 → 下载包边界；调用：pytest → 实际队列/CLI。
数据流：假网站材料 → 真实检查点与ZIP → 阶段断言，不访问真实网站或模型API。
"""

import hashlib
import json
from zipfile import ZipFile

import pytest

from app.common.collection import input_files, save_collection
from app.common.file_io import read_json
from app.common.paths import PipelinePaths
from app.common.storage import Storage
from app.contracts.pipeline import CLIConfig
from tests.conftest import FixtureAdapter
from tests.test_pipeline import cli_settings as cli_settings
from tests.test_pipeline_web import submit_and_run


def forbidden(*args, **kwargs):
    """任何提前分析、清洗或LLM调用都使本测试失败。"""
    raise AssertionError("选择教材分支时不应进入后续处理")


def test_web_collect_then_textbook_pauses_without_models(client, monkeypatch):
    """真实持久队列停在采集后，教材分支父材料只读、可下载且不能绕过阶段。"""
    from app import pipeline_service

    monkeypatch.setattr(pipeline_service, "run_component", forbidden)
    monkeypatch.setattr(pipeline_service, "clean_csv", forbidden)
    monkeypatch.setattr(pipeline_service, "summarize", forbidden)
    first = submit_and_run(client, {"operation_id": "collected", "stage": "collect",
        "urls": ["https://example.test/a"], "question": "合成采集", "llm": "api"})
    assert first["stage"] == "branch_choice"
    assert first["counts"]["documents"] == 1
    assert first["modeling"]["status"] == "not_started" and first["llm"] is None
    runtime = client.app.state.runtime
    assert len(runtime.adapter.calls) == 1
    snapshot = dict(first)
    paused = submit_and_run(client, {"operation_id": "textbook", "stage": "route",
        "parent_id": "collected", "extraction": "textbook"})
    assert paused["stage"] == "materials_ready" and paused["extraction"] == "textbook"
    assert len(runtime.adapter.calls) == 1 and paused["modeling"]["status"] == "not_started"
    assert not any(name.endswith(("analysis.json", "llm.json", "cleaned.csv")) for name in paused["files"])
    response = client.get("/v1/pipeline/textbook/files/data/sources.zip?project_id=local")
    assert response.status_code == 200
    from io import BytesIO

    with ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("collection.json"))
        assert not manifest["documents"][0]["raw_available"]
        assert any(name.endswith(".txt") for name in archive.namelist())
        assert not any(".env" in name for name in archive.namelist())
    assert runtime.operations.get("collected", "local")["result"] == snapshot
    for payload in [
        {"stage": "model", "parent_id": "textbook"},
        {"stage": "gan", "parent_id": "collected"},
        {"stage": "route", "parent_id": "textbook", "extraction": "llm"},
    ]:
        assert client.post("/v1/pipeline/jobs", json={"operation_id": "invalid", **payload}).status_code == 409
    assert client.post("/v1/pipeline/jobs", json={"operation_id": "cross-project", "project_id": "other",
        "stage": "route", "parent_id": "collected", "extraction": "textbook"}).status_code == 404


def test_web_continue_reuses_collected_materials(client, monkeypatch):
    """选择原流程后才调用实际离线桥接；继续处理不再访问网站。"""
    first = submit_and_run(client, {"operation_id": "collect-auto", "stage": "collect",
        "urls": ["https://example.test/a"], "question": "示例数量", "llm": "off", "retrieval": "keywords"})
    assert first["stage"] == "branch_choice"
    monkeypatch.setattr(client.app.state.runtime.adapter, "crawl_one", forbidden)
    result = submit_and_run(client, {"operation_id": "continue-auto", "stage": "route",
        "parent_id": "collect-auto", "extraction": "llm"})
    assert result["stage"] == "completed" and result["extraction"] == "llm"
    assert result["counts"]["documents"] == 1
    assert result["llm"]["status"] == "disabled"
    assert "metadata/analysis.json" in result["files"]
    assert "data/sources.zip" in result["files"]


def test_cli_asks_only_after_collection_and_textbook_stops(tmp_path, cli_settings, monkeypatch):
    """终端先采后问，选教材立即返回，默认BGE配置也不加载模型。"""
    from app import pipeline_cli

    adapter = FixtureAdapter()
    prompts = []

    def answer(prompt):
        """确认询问时已有来源文件，再选择教材。"""
        assert len(adapter.calls) == 1 and paths.file("sources").is_file()
        prompts.append(prompt)
        return "2"

    monkeypatch.setattr(pipeline_cli, "CLI_LOCK", tmp_path / "cli.lock")
    monkeypatch.setattr(pipeline_cli, "_offline", forbidden)
    monkeypatch.setattr(pipeline_cli, "clean_csv", forbidden)
    monkeypatch.setattr(pipeline_cli, "summarize", forbidden)
    paths = PipelinePaths(tmp_path / "output", organized=True)
    listing = tmp_path / "urls.txt"
    listing.write_text("https://example.test/a\n", "utf-8")
    result = pipeline_cli.run(str(listing), "合成需求", CLIConfig(), cli_settings,
        paths=paths, adapter=adapter, input_fn=answer, post_crawl="ask")
    assert result["status"] == "materials_ready" and len(prompts) == 1
    assert read_json(paths.file("status"))["status"] == "materials_ready"
    assert not paths.file("analysis").exists() and not paths.file("llm").exists()
    assert not list(tmp_path.rglob("*.db"))


def test_source_bundle_keeps_original_bytes_and_rejects_unsafe_refs(tmp_path, cli_settings):
    """HTML原字节保真、正文哈希二次核对，越界材料不能被打包。"""
    storage = Storage(cli_settings.data_dir)
    storage.initialize()
    html = b"<html><p>synthetic source</p></html>"
    text = b"synthetic source"
    (storage.paths.path("raw_html") / "source.html").write_bytes(html)
    source = storage.paths.path("text") / "source.txt"
    source.write_bytes(text)
    records = [{"record_id": "synthetic", "url": "https://example.test/a",
                "text_sha256": hashlib.sha256(text).hexdigest(),
                "storage": {"raw_path": "data/raw/html/source.html",
                            "text_path": "data/normalized/text/source.txt"}}]
    paths = PipelinePaths(tmp_path / "output", organized=True)
    save_collection(records, [], [], "synthetic", CLIConfig(), storage.root, paths)
    with ZipFile(paths.file("sources")) as archive:
        assert archive.read("data/raw/html/source.html") == html
        index = json.loads(archive.read("collection.json"))
        assert index["documents"][0]["raw_available"]
    assert len(input_files(records, storage.root)) == 1
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="发生变化"):
        input_files(records, storage.root)
    records[0]["storage"]["raw_path"] = "data/raw/../../.env"
    with pytest.raises(ValueError, match="相对引用"):
        save_collection(records, [], [], "synthetic", CLIConfig(), storage.root, paths)
