"""实时消息的隔离回归用例；不访问网站、不调用付费API。

结构：事件状态机 → 任务令牌隔离 → 完成/失败保留消息。
调用：pytest显式执行；数据流：合成事件/临时数据库 → 真实查询契约断言。
"""

from dataclasses import replace

from app.common.progress import ResearchProgress


def test_crawl_messages_keep_exact_failure_and_counts():
    """成功、Robots和Fake-IP原因分别展示，完成数只在结果事件增加。"""
    snapshots = []
    reporter = ResearchProgress(lambda value: snapshots.append(value) or True, total=3)
    reporter.phase("collecting", "开始采集")
    for index, (status, reason) in enumerate([
        ("ok", "正文已保存"), ("skipped", "E_NETWORK_POLICY: robots.txt 禁止目标路径。"),
        ("skipped", "E_NETWORK_POLICY: 目标解析包含非公网地址。")], 1):
        reporter.crawl({"index": index, "total": 3, "url": "https://example.test/", "status": "fetching"})
        reporter.crawl({"index": index, "total": 3, "url": "https://example.test/", "status": status, "reason": reason})
    assert len(snapshots[0]["events"]) == 1
    assert snapshots[0]["completed"] == 0
    final = snapshots[-1]
    assert (final["completed"], final["succeeded"], final["failed"]) == (3, 1, 2)
    assert "(1/3) 爬取成功" in [event["message"] for event in final["events"]]
    assert any("Robots 协议不允许" in event["message"] for event in final["events"])
    assert any("代理 DNS" in event["message"] for event in final["events"])


def test_operation_progress_is_lease_checked_and_survives_completion(client):
    """运行中结果保持空，旧令牌不能覆盖消息，完成后真实结果和消息分别返回。"""
    operations = client.app.state.runtime.operations
    operations.submit("pipeline", {"operation_id": "progress-test", "project_id": "local"})
    claim = operations.claim("progress-test")
    snapshot = {"stage": "collecting", "events": [{"id": 1, "message": "(1/1) 爬取成功"}]}
    assert operations.update_progress(claim, snapshot)
    assert not operations.update_progress(replace(claim, token="expired-token"), {"events": []})
    running = operations.get(claim.operation_id, "local")
    assert running["result"] is None and running["progress"] == snapshot
    assert operations.complete(claim, {"stage": "completed", "files": {}})
    completed = operations.get(claim.operation_id, "local")
    assert completed["result"] == {"stage": "completed", "files": {}}
    assert completed["progress"] == snapshot
    assert not operations.update_progress(claim, {"events": []})


def test_failed_operation_retains_diagnostic_messages(client):
    """失败任务保留最后消息用于恢复查询，不冒充完成结果。"""
    operations = client.app.state.runtime.operations
    operations.submit("pipeline", {"operation_id": "progress-failed", "project_id": "local"})
    claim = operations.claim("progress-failed")
    snapshot = {"stage": "collecting", "events": [{"id": 1, "message": "连接失败"}]}
    operations.update_progress(claim, snapshot)
    operations.complete(claim, error={"code": "E_SYNTHETIC", "message": "合成失败"})
    failed = operations.get(claim.operation_id, "local")
    assert failed["state"] == "failed" and failed["result"] is None
    assert failed["progress"] == snapshot


def test_collect_reports_each_start_and_result(client, adapter):
    """用夹具采集器核对真实collect回调顺序，包含单个Robots失败后继续采集。"""
    from app.contracts.pipeline import CLIConfig
    from app.core.errors import ServiceError
    from app.pipeline_cli import collect

    urls = ["https://example.test/denied", "https://example.test/allowed"]
    adapter.behaviors[urls[0]] = ServiceError("E_NETWORK_POLICY", "robots.txt 禁止目标路径。", 403)
    events = []
    records, _, _, accesses = collect(urls, CLIConfig(llm="off"),
        client.app.state.runtime.settings, adapter=adapter, on_event=events.append)
    assert [item["status"] for item in events] == ["fetching", "skipped", "fetching", "ok"]
    assert [item["index"] for item in events] == [1, 1, 2, 2]
    assert len(records) == 1 and len(accesses) == 2
    assert "robots.txt" in events[1]["reason"]
