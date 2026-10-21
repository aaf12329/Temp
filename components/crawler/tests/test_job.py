# -*- coding: utf-8 -*-
"""并发限速（ratelimit）与任务状态机（job）测试 —— 零网络、零真实数据写入。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest tests.test_job -v

覆盖：
    ① ratelimit.effective_interval   取最大、crawl-delay 不截断、<=0 归 default
    ② HostLimiter                    假 clock/sleep：crawl-delay=10 时确实等 10 秒
    ③ run_parallel                   保序、同 host 串行、异 host 并行、单条异常不拖垮整批
    ④ job                            状态机校验 / save-load 往返 / 增量 plan / 进度 / 收口
全部落盘只在 tempfile 临时目录里发生；假时钟让等待类断言毫秒级完成。
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from Path_Store import Path_Store as ps              # noqa: E402
from webcrawl import job, ratelimit                  # noqa: E402


class FakeClock:
    """假时钟：sleep 只推进数字，不产生真实等待；线程安全（run_parallel 会用多线程）。"""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)
        self._lock = threading.Lock()

    def clock(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, float(seconds))

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += float(seconds)


# ============================================================== ① 纯函数
class TestEffectiveInterval(unittest.TestCase):
    """effective_interval：default / 人工 policy / crawl_delay 取最大。"""

    def test_max_of_three_sources(self):
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=1.5, policy_s=5.0, crawl_delay=10.0), 10.0)
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=1.5, policy_s=30.0, crawl_delay=10.0), 30.0)

    def test_crawl_delay_is_never_truncated(self):
        # 旧 api.crawl 的 min(wait, per_host_delay * 2) 会把它截成 3 秒
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=1.5, crawl_delay=10.0), 10.0)
        self.assertGreater(ratelimit.effective_interval(
            "a.cn", default_s=1.5, crawl_delay=10.0), 1.5 * 2)

    def test_non_positive_candidates_fall_back_to_default(self):
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=1.5, policy_s=0, crawl_delay=-1), 1.5)
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=2.0, policy_s=-5.0, crawl_delay=None), 2.0)

    def test_default_zero_means_no_minimum(self):
        self.assertEqual(ratelimit.effective_interval("a.cn", default_s=0), 0.0)

    def test_non_finite_and_garbage_ignored(self):
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=1.5, crawl_delay=float("inf")), 1.5)
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=1.5, crawl_delay=float("nan")), 1.5)
        self.assertEqual(ratelimit.effective_interval(
            "a.cn", default_s=1.5, policy_s="不是数字"), 1.5)

    def test_default_none_uses_fallback(self):
        self.assertEqual(ratelimit.effective_interval("a.cn", default_s=None), 1.5)


# ============================================================== ② HostLimiter
class TestHostLimiter(unittest.TestCase):
    """HostLimiter：注入假时钟，验证真实等待语义而不真等。"""

    def _limiter(self, fake, crawl_delays=None, default_s=1.5):
        return ratelimit.HostLimiter(default_s=default_s, crawl_delays=crawl_delays,
                                     clock=fake.clock, sleep=fake.sleep)

    def test_crawl_delay_10_waits_at_least_10_seconds(self):
        """验收硬指标：crawl_delay=10 时第二次请求至少等 10 秒（断言值 10.0）。"""
        fake = FakeClock()
        limiter = self._limiter(fake, {"a.cn": 10.0})
        self.assertEqual(limiter.interval("a.cn"), 10.0)
        self.assertEqual(limiter.wait_for("a.cn"), 0.0)     # 还没请求过，无需等
        limiter.note("a.cn", fake.clock())                  # 第一次请求发生 t=0
        waited = limiter.acquire("a.cn")                    # 第二次请求必须等
        self.assertEqual(waited, 10.0)                      # 断言值：等了 10.0 秒
        self.assertGreaterEqual(fake.now, 10.0)             # 假时钟确实推进了 10s
        self.assertAlmostEqual(limiter.wait_for("a.cn"), 10.0)
        self.assertNotEqual(waited, 3.0)                    # 旧实现截断值不得出现

    def test_different_hosts_do_not_block_each_other(self):
        fake = FakeClock()
        limiter = self._limiter(fake)
        limiter.note("a.cn", fake.clock())
        self.assertEqual(limiter.wait_for("a.cn"), 1.5)
        self.assertEqual(limiter.acquire("b.cn"), 0.0)      # 别的 host 不受影响
        self.assertEqual(fake.now, 0.0)
        self.assertEqual(limiter.acquire("c.cn"), 0.0)

    def test_wait_for_counts_elapsed_time(self):
        fake = FakeClock()
        limiter = self._limiter(fake, {"a.cn": 10.0})
        limiter.note("a.cn", fake.clock())
        fake.advance(4.0)
        self.assertEqual(limiter.wait_for("a.cn"), 6.0)

    def test_interval_takes_max_with_manual_policy(self):
        fake = FakeClock()
        limiter = self._limiter(fake, {"a.cn": 10.0})
        self.assertEqual(limiter.set_interval("a.cn", 30.0), 30.0)
        self.assertEqual(limiter.interval("a.cn"), 30.0)
        self.assertEqual(limiter.set_interval("b.cn", 0.2), 1.5)   # 保守优先，不压低

    def test_set_interval_non_positive_clears_policy(self):
        fake = FakeClock()
        limiter = self._limiter(fake, {"a.cn": 10.0})
        limiter.set_interval("a.cn", 30.0)
        self.assertEqual(limiter.set_interval("a.cn", 0), 10.0)

    def test_note_rejects_bad_timestamp(self):
        limiter = self._limiter(FakeClock())
        with self.assertRaises(ValueError):
            limiter.note("a.cn", "昨天")

    def test_unknown_host_uses_default(self):
        limiter = self._limiter(FakeClock())
        self.assertEqual(limiter.interval("nobody.cn"), 1.5)
        self.assertEqual(limiter.wait_for("nobody.cn"), 0.0)


# ============================================================== ③ run_parallel
class TestRunParallel(unittest.TestCase):
    """run_parallel：保序、同 host 串行、异 host 并行、单条异常兜底。"""

    def test_preserves_input_order(self):
        items = [5, 4, 3, 2, 1]
        result = ratelimit.run_parallel(items, lambda n: n * 10, max_workers=4)
        self.assertEqual(result, [50, 40, 30, 20, 10])

    def test_single_error_does_not_break_batch(self):
        def worker(value):
            if value == 2:
                raise RuntimeError("这一条坏掉")
            return value

        result = ratelimit.run_parallel([1, 2, 3], worker, max_workers=3)
        self.assertEqual(result[0], 1)
        self.assertEqual(result[2], 3)
        self.assertIn("_error", result[1])
        self.assertIn("RuntimeError", result[1]["_error"])
        self.assertIn("这一条坏掉", result[1]["_error"])

    def test_same_host_runs_serially(self):
        state = {"active": 0, "peak": 0}
        guard = threading.Lock()

        def worker(url):
            with guard:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.02)
            with guard:
                state["active"] -= 1
            return url

        urls = ["https://a.cn/1", "https://a.cn/2", "https://a.cn/3"]
        result = ratelimit.run_parallel(urls, worker, max_workers=4)
        self.assertEqual(result, urls)                 # 顺序不乱
        self.assertEqual(state["peak"], 1)             # 同一 host 任何时刻只有 1 条在跑

    def test_different_hosts_run_in_parallel(self):
        barrier = threading.Barrier(2, timeout=2)

        def worker(url):
            barrier.wait()                             # 若全局串行，这里会 BrokenBarrierError
            return url

        result = ratelimit.run_parallel(
            ["https://a.cn/1", "https://b.cn/1"], worker, max_workers=2)
        self.assertEqual(result, ["https://a.cn/1", "https://b.cn/1"])

    def test_same_host_respects_crawl_delay_inside_batch(self):
        fake = FakeClock()
        limiter = ratelimit.HostLimiter(default_s=1.5, crawl_delays={"x.cn": 10.0},
                                        clock=fake.clock, sleep=fake.sleep)
        urls = ["https://x.cn/1", "https://x.cn/2", "https://x.cn/3"]
        result = ratelimit.run_parallel(urls, lambda url: url,
                                        max_workers=3, limiter=limiter)
        self.assertEqual(result, urls)
        self.assertGreaterEqual(fake.now, 20.0)        # 3 条同 host → 至少 2 * 10 秒

    def test_progress_callback_counts_up(self):
        seen = []
        result = ratelimit.run_parallel(
            [1, 2, 3], lambda n: n, max_workers=2,
            progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(result, [1, 2, 3])
        self.assertEqual(sorted(done for done, _ in seen), [1, 2, 3])
        self.assertTrue(all(total == 3 for _, total in seen))

    def test_progress_exception_is_ignored(self):
        def broken_progress(done, total):
            raise RuntimeError("进度显示坏了")

        result = ratelimit.run_parallel([1, 2], lambda n: n, max_workers=2,
                                        progress=broken_progress)
        self.assertEqual(result, [1, 2])

    def test_empty_items(self):
        self.assertEqual(ratelimit.run_parallel([], lambda n: n), [])

    def test_bad_max_workers(self):
        with self.assertRaises(ValueError):
            ratelimit.run_parallel([1], lambda n: n, max_workers=0)

    def test_custom_host_of(self):
        items = [{"url": "https://a.cn/1", "host": "A.cn"},
                 {"url": "https://a.cn/2", "host": "A.cn"}]
        result = ratelimit.run_parallel(
            items, lambda item: item["url"], max_workers=2,
            host_of=lambda item: item["host"].lower())
        self.assertEqual(result, ["https://a.cn/1", "https://a.cn/2"])


# ============================================================== ④ job 基础
class TestJobCreate(unittest.TestCase):
    """new_job：骨架字段、去重保序、started_at 语义。"""

    def test_defaults(self):
        created = job.new_job("job-1", ["https://a.cn/1", "https://b.cn/2"], project_id="p1")
        self.assertEqual(created["status"], "queued")
        self.assertEqual(created["urls"], ["https://a.cn/1", "https://b.cn/2"])
        self.assertEqual(created["project_id"], "p1")
        self.assertEqual(created["items"]["https://a.cn/1"]["status"], "pending")
        self.assertEqual(created["items"]["https://a.cn/1"]["record_id"], None)
        self.assertEqual(created["counts"]["total"], 2)
        self.assertEqual(created["counts"]["done"], 0)
        self.assertEqual(created["progress"]["percent"], 0.0)

    def test_statuses_contract(self):
        self.assertEqual(job.STATUSES, ("queued", "running", "paused", "done",
                                        "partial", "failed", "cancelled"))

    def test_urls_deduplicated_and_ordered(self):
        created = job.new_job("job-2", ["u1", "u2", "u1", "u3", " "])
        self.assertEqual(created["urls"], ["u1", "u2", "u3"])

    def test_single_url_string_accepted(self):
        created = job.new_job("job-3", "https://a.cn/x")
        self.assertEqual(created["urls"], ["https://a.cn/x"])

    def test_started_at_marks_running(self):
        created = job.new_job("job-4", ["u1"],
                              started_at=datetime(2026, 10, 20, 8, 30,
                                                  tzinfo=timezone.utc))
        self.assertEqual(created["status"], "running")
        self.assertEqual(created["started_at"], "2026-10-20T08:30:00+00:00")

    def test_request_is_copied_not_aliased(self):
        request = {"urls": ["u1"], "nested": {"a": 1}}
        created = job.new_job("job-5", ["u1"], request=request)
        created["request"]["nested"]["a"] = 2
        self.assertEqual(request["nested"]["a"], 1)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            job.new_job("", ["u1"])
        with self.assertRaises(ValueError):
            job.new_job("job-6", ["u1"], request=["不是 dict"])


# ============================================================== ⑤ 状态机
class TestJobStateMachine(unittest.TestCase):
    """显式状态机：合法路径放行，非法流转抛 ValueError。"""

    def test_legal_path(self):
        created = job.new_job("j", ["u1"])
        job.transition(created, "running")
        self.assertEqual(created["status"], "running")
        self.assertTrue(created["started_at"])
        job.transition(created, "paused")
        self.assertEqual(created["status"], "paused")
        job.transition(created, "running")
        job.transition(created, "done")
        self.assertEqual(created["status"], "done")
        self.assertTrue(created["finished_at"])

    def test_same_status_is_idempotent(self):
        created = job.new_job("j", ["u1"])
        job.transition(created, "running")
        self.assertIs(job.transition(created, "running"), created)
        self.assertEqual(created["status"], "running")

    def test_illegal_transitions_raise(self):
        created = job.new_job("j", ["u1"])
        job.transition(created, "running")
        job.transition(created, "done")
        with self.assertRaises(ValueError):
            job.transition(created, "running")          # done 是终态
        cancelled = job.new_job("j2", ["u1"])
        job.transition(cancelled, "cancelled")
        with self.assertRaises(ValueError):
            job.transition(cancelled, "running")
        running = job.new_job("j3", ["u1"])
        job.transition(running, "running")
        with self.assertRaises(ValueError):
            job.transition(running, "queued")           # 不许倒退
        with self.assertRaises(ValueError):
            job.transition(running, "不存在的状态")

    def test_unknown_current_status_raises(self):
        created = job.new_job("j", ["u1"])
        created["status"] = "外星状态"
        with self.assertRaises(ValueError):
            job.transition(created, "running")

    def test_illegal_transition_message_names_both_states(self):
        created = job.new_job("j", ["u1"])
        job.transition(created, "cancelled")
        with self.assertRaises(ValueError) as caught:
            job.transition(created, "running")
        self.assertIn("cancelled", str(caught.exception))
        self.assertIn("running", str(caught.exception))


# ============================================================== ⑥ 落盘
class TestJobPersist(unittest.TestCase):
    """save/load：一 job 一 JSON，往返字段不丢，只写临时目录。"""

    def test_roundtrip_keeps_every_field(self):
        created = job.new_job("job-1", ["https://a.cn/1", "https://b.cn/2"],
                              project_id="p1", request={"urls": ["https://a.cn/1"]})
        job.transition(created, "running")
        job.mark(created, "https://a.cn/1",
                 {"status": "ok", "record_id": "r1", "error": None})
        with tempfile.TemporaryDirectory() as tmp:
            path = job.save_job(created, tmp)
            self.assertTrue(os.path.isfile(path))
            loaded = job.load_job("job-1", tmp)
            self.assertEqual(loaded, created)            # 深比较：字段一个不少
            self.assertEqual(loaded["request"], created["request"])
            self.assertEqual(loaded["items"], created["items"])

    def test_missing_job_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(job.load_job("不存在的 job", tmp))

    def test_save_creates_directory(self):
        created = job.new_job("job-2", ["u1"])
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "jobs", "deep")
            path = job.save_job(created, nested)
            self.assertTrue(os.path.isfile(path))

    def test_job_path_uses_jobs_dir_by_default(self):
        path = job.job_path("job-3")
        self.assertEqual(os.path.dirname(path), ps.JOBS_DIR)
        self.assertTrue(path.endswith(".json"))

    def test_job_path_does_not_escape_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = job.job_path("../逃逸/../job", tmp)
            self.assertEqual(os.path.dirname(path), tmp)
            self.assertNotIn(os.sep, os.path.basename(path))

    def test_corrupt_file_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = job.job_path("bad", tmp)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{ 这不是 JSON")
            with self.assertRaises(ValueError):
                job.load_job("bad", tmp)

    def test_empty_urls_job_roundtrip(self):
        created = job.new_job("empty", [])
        with tempfile.TemporaryDirectory() as tmp:
            job.save_job(created, tmp)
            self.assertEqual(job.load_job("empty", tmp), created)


# ============================================================== ⑦ 增量计划
class TestJobPlan(unittest.TestCase):
    """plan：known 里的 fresh 结果跳过，过期/失败/缺指纹的重新抓。"""

    NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def _make_job(self):
        return job.new_job("plan", ["https://a.cn/1", "https://a.cn/2",
                                    "https://b.cn/3", "https://b.cn/4"])

    def _known(self, url, *, status="ok", days_ago=1, sha="abc"):
        fetched = (self.NOW - timedelta(days=days_ago)).isoformat()
        return {"url": url, "status": status, "fetched_at": fetched,
                "content_sha256": sha}

    def test_incremental_skips_fresh_and_keeps_order(self):
        created = self._make_job()
        known = [self._known("https://a.cn/1"),
                 self._known("https://b.cn/3", status="ok_pdf")]
        planned = job.plan(created, known, mode="incremental",
                           recheck_after_days=30, now=self.NOW)
        self.assertEqual(planned["mode"], "incremental")
        self.assertEqual(planned["todo"], ["https://a.cn/2", "https://b.cn/4"])
        self.assertEqual(planned["skip"],
                         [{"url": "https://a.cn/1", "reason": "fresh"},
                          {"url": "https://b.cn/3", "reason": "fresh"}])

    def test_stale_failed_and_missing_sha_are_retried(self):
        created = self._make_job()
        known = [self._known("https://a.cn/1", days_ago=31),
                 self._known("https://a.cn/2", status="failed", days_ago=1),
                 self._known("https://b.cn/3", days_ago=1, sha=None),
                 {"url": "https://b.cn/4", "status": "ok", "fetched_at": "坏时间",
                  "content_sha256": "abc"}]
        planned = job.plan(created, known, mode="incremental", now=self.NOW)
        self.assertEqual(planned["todo"], created["urls"])
        self.assertEqual(planned["skip"], [])

    def test_freshness_boundary_is_inclusive(self):
        created = job.new_job("x", ["https://a.cn/1"])
        planned = job.plan(created, [self._known("https://a.cn/1", days_ago=30)],
                           mode="incremental", recheck_after_days=30, now=self.NOW)
        self.assertEqual(planned["skip"][0]["reason"], "fresh")
        planned = job.plan(created, [self._known("https://a.cn/1", days_ago=31)],
                           mode="incremental", recheck_after_days=30, now=self.NOW)
        self.assertEqual(planned["todo"], ["https://a.cn/1"])

    def test_full_mode_ignores_known(self):
        created = self._make_job()
        planned = job.plan(created, [self._known("https://a.cn/1")], mode="full",
                           now=self.NOW)
        self.assertEqual(planned["mode"], "full")
        self.assertEqual(planned["todo"], created["urls"])
        self.assertEqual(planned["skip"], [])

    def test_force_overrides_incremental(self):
        created = self._make_job()
        planned = job.plan(created, [self._known("https://a.cn/1")],
                           mode="incremental", force=True, now=self.NOW)
        self.assertEqual(planned["mode"], "full")
        self.assertEqual(planned["todo"], created["urls"])

    def test_known_can_be_dict_mapping(self):
        created = job.new_job("d", ["https://a.cn/1"])
        known = {"https://a.cn/1": {"status": "ok", "fetched_at": self.NOW.isoformat(),
                                    "content_sha256": "abc"}}
        planned = job.plan(created, known, mode="incremental", now=self.NOW)
        self.assertEqual(planned["skip"][0]["url"], "https://a.cn/1")

    def test_duplicate_known_url_newest_success_wins(self):
        # pages.json 按 (job_id, url) 存，同一 URL 可能有多条：必须看最新那条
        created = job.new_job("dup", ["https://a.cn/1"])
        known = [self._known("https://a.cn/1", days_ago=100),
                 self._known("https://a.cn/1", days_ago=1)]
        planned = job.plan(created, known, mode="incremental",
                           recheck_after_days=30, now=self.NOW)
        self.assertEqual(planned["todo"], [])
        self.assertEqual(planned["skip"],
                         [{"url": "https://a.cn/1", "reason": "fresh"}])

    def test_duplicate_known_url_newest_failure_wins(self):
        created = job.new_job("dup", ["https://a.cn/1"])
        known = [self._known("https://a.cn/1", days_ago=100),
                 self._known("https://a.cn/1", status="failed", days_ago=1)]
        planned = job.plan(created, known, mode="incremental",
                           recheck_after_days=30, now=self.NOW)
        self.assertEqual(planned["todo"], ["https://a.cn/1"])
        self.assertEqual(planned["skip"], [])

    def test_invalid_arguments(self):
        created = self._make_job()
        with self.assertRaises(ValueError):
            job.plan(created, [], mode="猜的")
        with self.assertRaises(ValueError):
            job.plan(created, [], recheck_after_days=-1, now=self.NOW)
        with self.assertRaises(ValueError):
            job.plan(created, [], now="不是时间")


# ============================================================== ⑧ 结果与进度
class TestJobMark(unittest.TestCase):
    """mark：写单条记录，重算 counts / progress。"""

    def setUp(self):
        self.created = job.new_job("m", ["https://a.cn/1", "https://b.cn/2"])

    def test_mark_ok_updates_counts_and_progress(self):
        job.mark(self.created, "https://a.cn/1",
                 {"status": "ok", "record_id": "r1", "error": None})
        item = self.created["items"]["https://a.cn/1"]
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["record_id"], "r1")
        self.assertEqual(item["error_code"], None)
        self.assertEqual(self.created["counts"]["ok"], 1)
        self.assertEqual(job.progress(self.created)["percent"], 50.0)

    def test_mark_failed_keeps_error_code(self):
        job.mark(self.created, "https://b.cn/2",
                 {"status": "failed", "record_id": "r2",
                  "error": {"code": "E_TIMEOUT", "message": "超时"}})
        item = self.created["items"]["https://b.cn/2"]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error_code"], "E_TIMEOUT")
        self.assertEqual(self.created["counts"]["failed"], 1)

    def test_mark_skipped_and_unknown_record_status(self):
        job.mark(self.created, "https://a.cn/1",
                 {"status": "skipped", "record_id": "r1",
                  "error": {"code": "E_ROBOTS_DISALLOW"}})
        self.assertEqual(self.created["items"]["https://a.cn/1"]["status"], "skipped")
        job.mark(self.created, "https://b.cn/2", {"status": "奇怪状态"})
        self.assertEqual(self.created["items"]["https://b.cn/2"]["status"], "failed")

    def test_mark_ok_pdf_counts_as_ok(self):
        job.mark(self.created, "https://a.cn/1",
                 {"status": "ok_pdf", "record_id": "r1"})
        self.assertEqual(self.created["items"]["https://a.cn/1"]["status"], "ok_pdf")
        progress = job.progress(self.created)
        self.assertEqual(progress["ok"], 1)
        self.assertEqual(progress["done"], 1)

    def test_mark_unknown_url_raises(self):
        with self.assertRaises(ValueError):
            job.mark(self.created, "https://不在-job.cn/", {"status": "ok"})
        with self.assertRaises(ValueError):
            job.mark(self.created, "https://a.cn/1", "不是 dict")

    def test_progress_shape(self):
        self.assertEqual(job.progress(self.created),
                         {"total": 2, "done": 0, "ok": 0,
                          "failed": 0, "skipped": 0, "percent": 0.0})


# ============================================================== ⑨ 收口
class TestJobSettle(unittest.TestCase):
    """settle：按完成情况收口 done / partial / failed / running。"""

    def _job(self, *urls):
        return job.new_job("s", list(urls) or ["u1"])

    def _mark_all(self, created, status):
        for url in created["urls"]:
            job.mark(created, url, {"status": status, "record_id": "r-" + url})

    def test_all_ok_is_done(self):
        created = self._job("u1", "u2")
        job.transition(created, "running")
        self._mark_all(created, "ok")
        job.settle(created)
        self.assertEqual(created["status"], "done")
        self.assertTrue(created["finished_at"])

    def test_mix_ok_and_failed_is_partial(self):
        created = self._job("u1", "u2")
        job.transition(created, "running")
        job.mark(created, "u1", {"status": "ok", "record_id": "r1"})
        job.mark(created, "u2", {"status": "failed", "record_id": "r2",
                                 "error": {"code": "E_NET"}})
        job.settle(created)
        self.assertEqual(created["status"], "partial")

    def test_all_failed_is_failed(self):
        created = self._job("u1", "u2")
        job.transition(created, "running")
        self._mark_all(created, "failed")
        job.settle(created)
        self.assertEqual(created["status"], "failed")

    def test_pending_items_keep_running(self):
        created = self._job("u1", "u2")
        job.transition(created, "running")
        job.mark(created, "u1", {"status": "ok", "record_id": "r1"})
        job.settle(created)
        self.assertEqual(created["status"], "running")
        self.assertIsNone(created["finished_at"])

    def test_all_skipped_is_done(self):
        created = self._job("u1")
        job.transition(created, "running")
        self._mark_all(created, "skipped")
        job.settle(created)
        self.assertEqual(created["status"], "done")

    def test_empty_job_is_done(self):
        created = job.new_job("empty", [])
        job.settle(created)
        self.assertEqual(created["status"], "done")

    def test_settle_from_queued_all_skipped_is_done(self):
        created = self._job("u1")
        self._mark_all(created, "skipped")             # 没有 transition，仍是 queued
        job.settle(created)
        self.assertEqual(created["status"], "done")

    def test_paused_job_stays_paused(self):
        created = self._job("u1", "u2")
        job.transition(created, "running")
        job.mark(created, "u1", {"status": "ok", "record_id": "r1"})
        job.transition(created, "paused")
        job.settle(created)
        self.assertEqual(created["status"], "paused")  # 暂停是用户状态，不被收口覆盖

    def test_cancelled_job_stays_cancelled(self):
        created = self._job("u1")
        job.transition(created, "cancelled")
        job.settle(created)
        self.assertEqual(created["status"], "cancelled")

    def test_done_job_is_idempotent(self):
        created = self._job("u1")
        job.transition(created, "running")
        self._mark_all(created, "ok")
        job.settle(created)
        status = created["status"]
        job.settle(created)
        self.assertEqual(created["status"], status)


# ============================================================== ⑩ 续跑
class TestJobResume(unittest.TestCase):
    """resume：只返回没做完（pending / queued）的 URL。"""

    def test_fresh_job_returns_all(self):
        created = job.new_job("r", ["u1", "u2", "u3"])
        self.assertEqual(job.resume(created), {"todo": ["u1", "u2", "u3"]})

    def test_completed_urls_are_excluded(self):
        created = job.new_job("r", ["u1", "u2", "u3"])
        job.transition(created, "running")
        job.mark(created, "u1", {"status": "ok", "record_id": "r1"})
        job.mark(created, "u2", {"status": "failed", "record_id": "r2"})
        self.assertEqual(job.resume(created), {"todo": ["u3"]})

    def test_queued_item_status_also_counts_as_pending(self):
        created = job.new_job("r", ["u1", "u2"])
        created["items"]["u1"]["status"] = "queued"
        self.assertEqual(job.resume(created), {"todo": ["u1", "u2"]})

    def test_resume_after_save_load(self):
        created = job.new_job("r", ["u1", "u2"])
        job.mark(created, "u1", {"status": "ok", "record_id": "r1"})
        with tempfile.TemporaryDirectory() as tmp:
            job.save_job(created, tmp)
            loaded = job.load_job("r", tmp)
        self.assertEqual(job.resume(loaded), {"todo": ["u2"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
