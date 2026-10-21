"""Celery 调度离线验收：内存消息代理，实际任务消费和数据库结果。

结构：持久提交 → dispatch → 内存 broker → solo 测试 Worker → JSON 会话。
调用：pytest → Celery 官方测试 Worker；数据流：重复消息 → CAS 单次执行。
此测试不证明 Windows 支持正式 Celery，也不替代 Redis/PostgreSQL 部署验收。
"""

import time

from celery.contrib.testing.worker import start_worker

from app.runtime import create_runtime


def test_celery_dispatch_consumes_persisted_advice_once(settings, monkeypatch):
    """真实消费两次相同消息，数据库最终完成且会话只有一轮；由 pytest 调用。"""
    import app.celery_app as tasks

    runtime = create_runtime(settings)
    monkeypatch.setattr(tasks, "create_runtime", lambda settings: runtime)
    celery = tasks.celery
    celery.conf.update(broker_url="memory://", task_always_eager=False,
                       broker_connection_retry_on_startup=False)
    request = {"operation_id": "celery-demo", "project_id": "test-project",
               "session_id": "default", "question": "合成测试：缺乏材料时如何规划研究？",
               "material_ids": [], "research_id": None, "demo": True}
    runtime.operations.submit("advisor", request)
    try:
        with start_worker(celery, pool="solo", concurrency=1, perform_ping_check=False,
                          queues=["control", "crawl", "operations"], shutdown_timeout=10):
            tasks.dispatch_pending.apply_async()
            tasks.execute_operation.apply_async(args=["celery-demo"])
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                state = runtime.operations.get("celery-demo", "test-project")
                if state["state"] == "done":
                    break
                time.sleep(0.05)
            assert state["state"] == "done", state
        assert runtime.advisor.history("test-project", "default", True)["total_turns"] == 1
        assert runtime.advisor.history("test-project", "default")["total_turns"] == 0
    finally:
        runtime.db.close()
