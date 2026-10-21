"""三模块离线集成验收：真实规范化/研究代码，假网络和假模型。

结构：mock_component_network → integrated_client → API/Worker 全链路断言。
调用：pytest → 测试服务 → 真实桥接子进程（仅 HTTP 出口替换）。
数据流：合成网页 → 不可变材料 → 候选事实 CSV → JSON 建议会话，无真实数据或付费请求。
"""

import json
import subprocess
import time

import pytest
from advisor_bot.common import ModelReply
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.common.file_io import read_json
from app.contracts.crawler import record_id_for
from app.core.errors import ServiceError
from app.db.models import OperationRow
from app.main import create_app
from app.operation_worker import run_operation
from app.worker import run_one

HTML = """<html><head><title>离线夹具：凯里酸汤产业资料</title></head><body><article>
<h1>合成测试，不能用于真实研究</h1>
<p>2024年凯里酸汤产业销售额为12亿元。这是为自动化测试编写的数字，不是真实统计。</p>
<p>研究需明确主体、地域、时间及指标口径。原文、发布日期和审核结论必须分别保留。</p>
<p>本段仅补足静态正文判定长度。材料中的机器候选不等于已确认事实，不得直接做产业预测。</p>
</article></body></html>"""

FAKE_NETWORK = '''
import io
import zlib
from app.common import network
class Response:
    def __init__(self, request):
        self.url = request.full_url
        self.status = 404 if self.url.endswith('/robots.txt') else 200
        kind = 'application/pdf' if self.url.endswith('.pdf') else 'text/html; charset=utf-8'
        self.headers = {'Content-Type': kind}
        raw = HTML.encode('utf-8')
        if self.url.endswith('.pdf'):
            raw = b'%PDF-1.4\\nstream\\n' + zlib.compress(b'BT (Synthetic PDF evidence) Tj ET') + b'\\nendstream\\n'
        if self.url.endswith('scan.pdf'):
            raw = b'%PDF-1.4 scanning fixture without text'
        self.body = io.BytesIO(b'' if self.status == 404 else raw)
    def read(self, size):
        return self.body.read(size)
    def geturl(self):
        return self.url
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.body.close()
class Opener:
    def open(self, request, timeout):
        return Response(request)
network.build_opener = lambda *args: Opener()
from app.crawler.bridge import main
main()
'''


@pytest.fixture
def mock_component_network(monkeypatch):
    """只在测试子进程替换 HTTP，无生产代码开关；由集成测试使用。"""
    original = subprocess.run

    def run(command, **kwargs):
        """维持实际子进程与环境，注入假 HTTP 出口；由 bridge runner 调用。"""
        if command[-2:] == ["-m", "app.crawler.bridge"]:
            message = json.loads(kwargs["input"])
            assert not any("KEY" in key or "TOKEN" in key for key in kwargs["env"])
            if message["action"] in {"crawl", "probe"}:
                command = [command[0], "-c", "HTML = " + repr(HTML) + "\n" + FAKE_NETWORK]
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


@pytest.fixture
def integrated_client(settings, mock_component_network):
    """使用真实集成适配器和临时数据库；由跨模块验收调用。"""
    from tests.conftest import TOKEN

    settings.crawler_adapter = "integrated"
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.runtime
        runtime.storage.write_json(runtime.policies.path, [{
            "host": "example.test", "allowed_paths": ["/"], "min_interval_s": 0,
            "note": "TEST FIXTURE ONLY"}])
        client.headers["Authorization"] = "Bearer " + TOKEN
        yield client


def crawl(client, job_id="crawl-001", project_id="kaili"):
    """通过实际 API 和 Worker 产生合成材料；供研究/建议验收调用。"""
    response = client.post("/v1/jobs", json={"job_id": job_id, "project_id": project_id,
                           "urls": ["https://example.test/article"], "per_host_delay_s": 0})
    assert response.status_code == 202, response.text
    assert run_one(client.app.state.runtime)
    state = client.get("/v1/jobs/" + job_id).json()
    assert state["state"] == "done", state
    return record_id_for(job_id, "https://example.test/article")


def research_request(record_id, operation_id="research-001"):
    """生成明确口径的无模型研究请求；供集成测试调用。"""
    return {"operation_id": operation_id, "project_id": "kaili", "material_ids": [record_id],
            "mode": "lexical", "config": {"keywords": ["酸汤"], "subjects": ["酸汤产业"],
            "regions": ["凯里"], "forecast_at": "2026-10-21T00:00:00+08:00"}}


def test_crawl_research_advice_roundtrip(integrated_client):
    """验证真实桥接、原文坐标、CSV、建议任务与 JSON 会话整条链路。"""
    client, runtime = integrated_client, integrated_client.app.state.runtime
    assert client.get("/health/ready").status_code == 200
    profile = client.post("/v1/probe", json={"urls": ["https://example.test/article"]})
    assert profile.status_code == 200, profile.text
    assert profile.json()["profiles"][0]["verdict"] in {"static", "thin_static"}
    record_id = crawl(client)
    text = client.get(f"/v1/materials/{record_id}/text").text
    assert "12亿元" in text
    listing = client.get("/v1/jobs/crawl-001/materials", params={"project_id": "kaili"}).json()
    assert listing["materials"][0]["record_id"] == record_id
    submitted = client.post("/v1/research/jobs", json=research_request(record_id))
    assert submitted.status_code == 202, submitted.text
    assert run_operation(runtime)
    result = client.get("/v1/operations/research-001", params={"project_id": "kaili"}).json()
    assert result["state"] == "done", result
    assert result["result"]["counts"]["documents"] == 1
    assert result["result"]["counts"]["candidate_facts"] >= 1
    assert result["result"]["counts"]["approved_facts"] == 0
    assert result["result"]["trend_status"] == "insufficient_data"
    exported = client.get("/v1/operations/research-001/files/analysis",
                          params={"project_id": "kaili"})
    assert exported.status_code == 200
    data = exported.json()
    assert data["documents"][0]["text_path"] == runtime.materials.get(record_id).storage["text_path"]
    assert runtime.settings.data_dir.as_posix() not in exported.text
    for fact in data["facts"]:
        assert fact["review_status"] == "pending"
        assert text[fact["char_start"]:fact["char_end"]] == fact["evidence_excerpt"]
    csv = client.get("/v1/operations/research-001/files/facts", params={"project_id": "kaili"})
    assert csv.status_code == 200 and csv.content.startswith(b"\xef\xbb\xbf")
    advice = {"operation_id": "advice-001", "project_id": "kaili", "question": "下一步核对哪些口径？",
              "material_ids": [record_id], "research_id": "research-001", "demo": True}
    assert client.post("/v1/advisor/jobs", json=advice).status_code == 202
    assert run_operation(runtime)
    result = client.get("/v1/operations/advice-001", params={"project_id": "kaili"}).json()
    assert result["state"] == "done", result
    assert result["result"]["mode"] == "demo"
    history = client.get("/v1/projects/kaili/advisor/sessions/default", params={"demo": True}).json()
    assert history["total_turns"] == 1
    assert client.get("/v1/projects/kaili/advisor/sessions/default").json()["total_turns"] == 0
    assert not run_operation(runtime)
    assert client.post("/v1/advisor/jobs", json=advice).status_code == 202
    assert not run_operation(runtime)
    assert not runtime.storage.paths.advisor_root("kaili").joinpath("memory.json").exists()


def test_project_hash_and_input_boundaries(integrated_client):
    """跨项目、任意路径、篡改证据和缺少模型密钥均在提交前被拒绝。"""
    client = integrated_client
    record_id = crawl(client)
    body = research_request(record_id)
    body["project_id"] = "other"
    assert client.post("/v1/research/jobs", json=body).status_code == 403
    body = research_request(record_id)
    body["config_path"] = "secret.env"
    assert client.post("/v1/research/jobs", json=body).status_code == 422
    advice = {"operation_id": "no-key", "project_id": "kaili", "question": "检查来源",
              "material_ids": [record_id]}
    assert client.post("/v1/advisor/jobs", json=advice).status_code == 503
    runtime = client.app.state.runtime
    runtime.materials.text(record_id).write_text("篡改的正文", encoding="utf-8")
    response = client.post("/v1/research/jobs", json=research_request(record_id))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "E_EVIDENCE_HASH"


def test_operation_concurrency_and_fencing(settings):
    """过期令牌不得覆盖新执行者结果，任务 ID 冲突明确拒绝。"""
    from app.runtime import create_runtime

    runtime = create_runtime(settings)
    try:
        request = {"operation_id": "op-lease", "project_id": "kaili"}
        runtime.operations.submit("research", request)
        first = runtime.operations.claim()
        assert first and runtime.operations.claim() is None
        with pytest.raises(ServiceError):
            runtime.operations.submit("advisor", request)
        with runtime.db.session() as session:
            session.execute(update(OperationRow).values(lease_until=time.time() - 1))
            session.commit()
        second = runtime.operations.claim()
        assert second.attempts == 2
        assert not runtime.operations.heartbeat(first)
        assert not runtime.operations.complete(first, {"old": True})
        assert runtime.operations.complete(second, {"new": True})
        assert runtime.operations.get("op-lease", "kaili")["result"] == {"new": True}
    finally:
        runtime.db.close()


def test_advisor_replay_and_unknown_outcome(settings):
    """重复调用只写一轮，开始后无结果的操作明确停止，不重试模型。"""
    from app.advisor.integrated import IntegratedAdvisor
    from app.common.file_io import write_json

    calls = []

    class Model:
        """带引用和私有 reasoning 的假模型。"""

        def complete(self, messages, **kwargs):
            """记录一次调用并返回固定结构；由独立助手调用。"""
            calls.append(messages)
            return ModelReply(content="先核对来源 [material-1]", reasoning="private")

    advisor = IntegratedAdvisor(settings, model_factory=lambda config: Model())
    request = {"operation_id": "op-replay", "project_id": "kaili", "question": "核对来源",
               "material_ids": ["material-1"], "demo": True}
    evidence = [{"record_id": "material-1", "text": "合成证据", "url": "https://example.test/"}]
    result = advisor.execute(request, evidence)
    assert result == advisor.execute(request, evidence)
    assert len(calls) == 1 and result["cited_materials"][0]["record_id"] == "material-1"
    assert "reasoning" not in result["reply"]
    cache = advisor.paths.advisor_operation("kaili", "op-replay", "result", True)
    value = read_json(cache)
    write_json(cache, {"fingerprint": value["fingerprint"], "state": "started"})
    with pytest.raises(ServiceError) as error:
        advisor.execute(request, evidence)
    assert error.value.error.code == "E_ADVISOR_OUTCOME_UNKNOWN" and len(calls) == 1


def test_pdf_text_and_scan_failures_are_distinct(integrated_client):
    """文本 PDF 可回读，扫描 PDF 保留无文本层错误而不伪装成功；由 pytest 调用。"""
    client, runtime = integrated_client, integrated_client.app.state.runtime
    urls = ["https://example.test/text.pdf", "https://example.test/scan.pdf"]
    response = client.post("/v1/jobs", json={"job_id": "pdf-test", "project_id": "kaili",
                                            "urls": urls, "per_host_delay_s": 0})
    assert response.status_code == 202
    assert run_one(runtime) and run_one(runtime)
    state = client.get("/v1/jobs/pdf-test").json()
    assert state["state"] == "partial", state
    text_id, scan_id = [record_id_for("pdf-test", url) for url in urls]
    result = client.get(f"/v1/materials/{text_id}/text")
    assert result.status_code == 200 and "Synthetic PDF evidence" in result.text
    scan = client.get(f"/v1/materials/{scan_id}").json()
    assert scan["error"]["code"] == "E_PDF_NO_TEXT"
    assert client.get(f"/v1/materials/{scan_id}/text").status_code == 404


def test_content_addressed_artifacts_preserve_previous_bytes(tmp_path):
    """同 URL 重抓不会覆盖此前正文；越界来源不能晋升；由 pytest 调用。"""
    from app.common.file_io import write_text
    from app.crawler.integrated import IntegratedCrawler

    adapter = IntegratedCrawler(tmp_path / "service")
    workspace = tmp_path / "workspace"
    source = workspace / "data/normalized/text/article.txt"
    first = {"record_id": "record-1", "verdict": "static",
             "storage": {"text_path": "data/normalized/text/article.txt"}}
    write_text(source, "第一版合成正文")
    adapter._promote(first, workspace)
    second = {"record_id": "record-1", "verdict": "static",
              "storage": {"text_path": "data/normalized/text/article.txt"}}
    write_text(source, "第二版合成正文")
    adapter._promote(second, workspace)
    assert first["storage"]["text_path"] != second["storage"]["text_path"]
    assert adapter.paths.normalized_reference(first["storage"]["text_path"]).read_text("utf-8") == "第一版合成正文"
    second["storage"]["text_path"] = "../outside.txt"
    with pytest.raises(ServiceError):
        adapter._promote(second, workspace)
