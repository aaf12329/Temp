"""框架/维护/隔离验收入口。
结构导航：
├─ submit
├─ runtime
├─ test_auth_version_and_uniform_errors
├─ test_unconfigured_adapter_is_explicit
├─ test_contract_validation
├─ test_idempotent_submit_and_conflict
├─ test_end_to_end_materials_and_unicode_anchors
├─ test_partial_and_retry_only_failed_retryable_items
├─ test_nonretryable_failure_is_not_retried
├─ test_new_runtime_can_resume_persisted_queue
├─ test_lease_recovery_rejects_stale_completion
├─ test_concurrent_workers_cannot_claim_same_item
├─ test_policy_manual_file_is_never_overwritten
├─ test_policy_login_and_path_denied
├─ test_openapi_matches_error_and_submission_contract
├─ test_normalized_path_boundary
├─ test_probe_persists_latest_only
├─ test_parallel_idempotent_submit
调用关系：
├─ test_unconfigured_adapter_is_explicit → submit
├─ test_contract_validation → submit
├─ test_idempotent_submit_and_conflict → runtime
├─ test_end_to_end_materials_and_unicode_anchors → submit, runtime
├─ test_partial_and_retry_only_failed_retryable_items → submit, runtime
├─ test_nonretryable_failure_is_not_retried → submit, runtime
├─ test_new_runtime_can_resume_persisted_queue → submit, runtime
├─ test_lease_recovery_rejects_stale_completion → submit, runtime
├─ test_concurrent_workers_cannot_claim_same_item → submit, runtime
├─ test_policy_manual_file_is_never_overwritten → runtime, submit
├─ test_policy_login_and_path_denied → runtime, submit
├─ test_normalized_path_boundary → runtime
├─ test_probe_persists_latest_only → runtime
├─ test_parallel_idempotent_submit → runtime。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.contracts.crawler import CrawlRequest, record_id_for
from app.core.errors import ServiceError
from app.db.models import ItemRow, JobRow, MaterialRow
from app.main import create_app
from app.runtime import create_runtime
from app.worker import run_one


def submit(client, job="test-job", urls=None, **extra):
    """在同一事务保存幂等任务和待执行项；由提交入口调用。"""
    return client.post(
        "/v1/jobs",
        json={
            "job_id": job,
            "urls": urls or ["https://example.test/a"],
            **extra,
        },
    )


def runtime(client):
    """读取测试客户端的运行资源；由服务行为验收调用。"""
    return client.app.state.runtime


def test_auth_version_and_uniform_errors(client):
    """验证 auth version and uniform errors；由 pytest 调度。"""
    result = client.get("/v1/jobs/missing", headers={"Authorization": "Bearer wrong"})
    assert result.status_code == 401
    assert result.json()["error"]["code"] == "E_UNAUTHORIZED"
    assert result.headers["x-crawler-api-version"] == "1.2"
    result = client.get("/v1/jobs/missing", headers={"X-Crawler-API-Version": "9"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "E_API_VERSION"
    assert client.get("/v1/jobs/missing").status_code == 404


def test_unconfigured_adapter_is_explicit(settings):
    """验证 unconfigured adapter is explicit；由 pytest 调度。"""
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        from tests.conftest import TOKEN

        client.headers["Authorization"] = f"Bearer {TOKEN}"
        result = submit(client)
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "E_CRAWLER_NOT_CONFIGURED"


@pytest.mark.parametrize(
    "changes",
    [
        {"respect_robots": False},
        {"urls": ["file:///C:/secret"]},
        {"urls": ["https://user:pass@example.test/"]},
        {"timeout_s": -1},
        {"urls": ["https://example.test/a", "https://example.test/a"]},
        {"unknown_flag": True},
    ],
)
def test_contract_validation(client, changes):
    """验证 contract validation；由 pytest 调度。"""
    result = submit(client, **changes)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "E_VALIDATION"


def test_idempotent_submit_and_conflict(client):
    """验证 idempotent submit and conflict；由 pytest 调度。"""
    payload = {"job_id": "idem", "urls": ["https://example.test/a"]}
    assert (
        client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "key"}).status_code == 202
    )
    assert (
        client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "key"}).status_code == 202
    )
    payload["urls"] = ["https://example.test/b"]
    assert (
        client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": "key"}).status_code == 409
    )
    with runtime(client).db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == 1
        assert session.scalar(select(func.count()).select_from(ItemRow)) == 1


def test_end_to_end_materials_and_unicode_anchors(client):
    """验证 end to end materials and unicode anchors；由 pytest 调度。"""
    assert submit(client, project_id="kaili", rule_version="r1").status_code == 202
    assert client.get("/v1/jobs/test-job").json()["state"] == "queued"
    assert run_one(runtime(client))
    job = client.get("/v1/jobs/test-job").json()
    assert job["state"] == "done" and job["finished"] == 1 and job["counts"]["ok"] == 1
    record_id = record_id_for("test-job", "https://example.test/a")
    record = client.get(f"/v1/materials/{record_id}").json()
    assert record["project_id"] == "kaili" and record["meta"]["rule_version"] == "r1"
    assert record["source"]["publisher"] is None
    text = client.get(f"/v1/materials/{record_id}/text").text
    response = client.get(f"/v1/materials/{record_id}/paragraphs")
    assert response.headers["content-type"].startswith("application/x-ndjson")
    for paragraph in map(json.loads, response.text.splitlines()):
        assert text[paragraph["char_start"] : paragraph["char_end"]] == paragraph["text"]
    assert client.get(f"/v1/materials/{record_id}/tables/0").json()["cells"][0]["text"] == "12"
    assert client.get(f"/v1/materials/{record_id}/tables/0?format=csv").text.strip() == "12"
    assert client.get(f"/v1/materials/{record_id}/tables/9").status_code == 404
    snapshot = json.loads((runtime(client).storage.root / "raw/pages.json").read_text("utf-8"))
    assert snapshot[0]["record_id"] == record_id
    assert not run_one(runtime(client))


def test_partial_and_retry_only_failed_retryable_items(client, adapter, transient_error):
    """验证 partial and retry only failed retryable items；由 pytest 调度。"""
    bad = "https://example.test/bad"
    adapter.behaviors[bad] = transient_error
    assert submit(client, urls=["https://example.test/a", bad]).status_code == 202
    rt = runtime(client)
    for _ in range(5):
        run_one(rt)
        with rt.db.session() as session:
            session.execute(update(ItemRow).values(next_run=0))
            session.commit()
    job = rt.jobs.get("test-job")
    assert job.state == "partial" and job.counts.ok == 1 and job.counts.failed == 1
    assert adapter.calls.count(bad) == 3
    assert adapter.calls.count("https://example.test/a") == 1
    del adapter.behaviors[bad]
    assert client.post("/v1/jobs/test-job/retry").status_code == 202
    assert client.get("/v1/jobs/test-job/retry").status_code == 202
    assert run_one(rt)
    assert rt.jobs.get("test-job").state == "done"
    with rt.db.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialRow)) == 2


def test_nonretryable_failure_is_not_retried(client, adapter):
    """验证 nonretryable failure is not retried；由 pytest 调度。"""
    adapter.behaviors["https://example.test/a"] = ServiceError(
        "E_FIXTURE_PERMANENT", "bad PDF", 422
    )
    submit(client)
    run_one(runtime(client))
    assert runtime(client).jobs.get("test-job").state == "failed"
    assert client.post("/v1/jobs/test-job/retry").status_code == 409
    assert len(adapter.calls) == 1


def test_new_runtime_can_resume_persisted_queue(client, adapter, settings):
    """验证 new runtime can resume persisted queue；由 pytest 调度。"""
    submit(client)
    other = create_runtime(settings, adapter)
    try:
        assert run_one(other)
        assert runtime(client).jobs.get("test-job").state == "done"
    finally:
        other.db.close()


def test_lease_recovery_rejects_stale_completion(client):
    """验证 lease recovery rejects stale completion；由 pytest 调度。"""
    submit(client)
    rt = runtime(client)
    old = rt.jobs.claim()
    assert old is not None
    with rt.db.session() as session:
        session.execute(update(ItemRow).values(lease_until=time.time() - 1))
        session.commit()
    new = rt.jobs.claim()
    assert new is not None and new.token != old.token
    assert not rt.jobs.heartbeat(old)
    from app.worker import failed_result

    result = failed_result(rt, old, {"code": "E_OLD", "message": "old", "retryable": False})
    assert not rt.jobs.complete(old, result, "failed")
    assert rt.jobs.heartbeat(new)


def test_concurrent_workers_cannot_claim_same_item(client):
    """验证 concurrent workers cannot claim same item；由 pytest 调度。"""
    submit(client)
    rt = runtime(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: rt.jobs.claim(), range(4)))
    assert len([x for x in claims if x is not None]) == 1


def test_policy_manual_file_is_never_overwritten(client):
    """验证 policy manual file is never overwritten；由 pytest 调度。"""
    rt = runtime(client)
    original = rt.policies.path.read_bytes()
    rt.storage.initialize()
    assert rt.policies.path.read_bytes() == original
    assert client.get("/v1/site-policy/example.test").status_code == 200
    assert client.get("/v1/site-policy/unknown.test").status_code == 404
    assert submit(client, urls=["https://unknown.test/"]).status_code == 404


def test_policy_login_and_path_denied(client, adapter):
    """验证 policy login and path denied；由 pytest 调度。"""
    rt = runtime(client)
    rt.storage.write_json(
        rt.policies.path,
        [
            {
                "host": "example.test",
                "allowed_paths": ["/"],
                "requires_login": True,
            }
        ],
    )
    submit(client)
    run_one(rt)
    assert rt.jobs.get("test-job").error.code == "E_POLICY_LOGIN"
    assert not adapter.calls
    response = client.post("/v1/probe", json={"urls": ["https://example.test/a"]})
    assert response.status_code == 403


def test_openapi_matches_error_and_submission_contract(client):
    """验证 openapi matches error and submission contract；由 pytest 调度。"""
    schema = client.get("/openapi.json").json()
    endpoint = schema["paths"]["/v1/jobs"]["post"]
    assert endpoint["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorEnvelope"
    }
    assert endpoint["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/JobSubmitted"
    }


@pytest.mark.parametrize("reference", ["../.env", "data/raw/pages.json", "C:/Windows/win.ini"])
def test_normalized_path_boundary(client, reference):
    """验证 normalized path boundary；由 pytest 调度。"""
    with pytest.raises(ServiceError) as exc:
        runtime(client).storage.resolve_normalized(reference)
    assert exc.value.error.code == "E_STORAGE_PATH"


def test_probe_persists_latest_only(client):
    """验证 probe persists latest only；由 pytest 调度。"""
    for _ in range(2):
        response = client.post("/v1/probe", json={"urls": ["https://example.test/a"]})
        assert response.status_code == 200
    profiles = json.loads(
        (runtime(client).storage.root / "profile/site_profile.json").read_text("utf-8")
    )
    assert len(profiles) == 1


def test_parallel_idempotent_submit(client):
    """验证 parallel idempotent submit；由 pytest 调度。"""
    rt = runtime(client)
    body = CrawlRequest(job_id="parallel", urls=["https://example.test/a"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: rt.jobs.submit(body, "parallel-key"), range(4)))
    assert values == ["parallel"] * 4
