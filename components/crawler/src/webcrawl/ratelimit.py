# -*- coding: utf-8 -*-
"""并发限速 —— 同一 host 串行、不同 host 并行，robots crawl-delay 绝不截断。

《爬虫标准API接口.md》v1.2 §6 要求"crawl-delay 要真正执行"；旧 `api.crawl` 里的
`time.sleep(min(wait, per_host_delay * 2))` 会把站点声明的大 crawl-delay 截成 3 秒，
等于把 robots 声明当摆设。本模块只做**限速与并发调度**这一件事：

- `effective_interval` 是纯函数：default / 人工 policy / robots crawl_delay **取最大**
  （保守优先），<=0 的候选视为未声明、回落到 default —— 与 `policy.min_interval`
  的 `max(default, declared)` 语义保持一致，不另造一套。
- `HostLimiter` 持有每个 host 的"上次请求时刻"，`acquire` 阻塞到可以再发请求为止。
  `clock` 与 `sleep` 都可注入：测试传假时钟，**秒级完成、不产生真实等待**。
- `run_parallel` 用线程池并发：**同 host 串行**（host 锁 + 限速），**不同 host 并行**；
  结果按输入顺序回填；单条 worker 异常收成 `{"_error": msg}`，不拖垮整批。

================================ 结构树 =================================
① 纯逻辑
    effective_interval(host, *, default_s, policy_s=None, crawl_delay=None) -> float
    _norm_host(host) -> str             host 归一化（去空白、小写、去 scheme）
    _to_float(value) -> float | None    数字清洗（非有限值视为未声明）
② 限速器
    HostLimiter.__init__(default_s=1.5, crawl_delays=None, clock=..., sleep=...)
    .interval(host) -> float            该 host 实际间隔（取最大、不截断）
    .wait_for(host) -> float            现在发请求还要等多久（秒）
    .acquire(host) -> float             阻塞等待并记时，返回本次等待秒数
    .note(host, ts=None)                记录该 host 已发生请求的时刻
    .set_interval(host, s) -> float     写入人工策略间隔（s<=0 清除）
③ 并发
    run_parallel(items, worker, *, max_workers=4, limiter=None, host_of=None,
                 progress=None) -> list
④ 自测
    _self_test()                        假时钟，零网络、秒级

================================ 调用关系 ================================
api.py ──▶ HostLimiter(.acquire) ──▶ 人工值来自 policy.min_interval(host)
       └─▶ run_parallel ──▶ worker(url) ──▶ api._crawl_one
job.py **不** import 本模块（job 只管状态机，调度归入口层编排）。

================================ 数据契约 ================================
- interval / wait_for / acquire 的返回值单位是**秒**（float）。
- acquire 在真正放行前调用 sleep；返回的是**本次睡了多久**，便于测试断言。
- `note(host, ts)` 的 ts 与注入 clock 必须是**同一时钟**（默认都是 time.monotonic）。
- run_parallel 返回长度 == len(items) 的 list，顺序与 items 一致；
  第 i 条 worker 抛异常 → 结果第 i 条是 `{"_error": "<异常类型>: <消息>"}`。
- progress 回调签名 `progress(done, total)`：每完成一条调用一次（done 从 1 到 total）；
  回调自身抛异常会被吞掉 —— 进度显示不许拖垮采集。
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

# ---------------------------------------------------------------- 常量区
DEFAULT_INTERVAL_S = 1.5            # 未注入默认值时的兜底间隔（与 api 默认一致）


# ===== ① 纯逻辑 =====
def _to_float(value: Any) -> float | None:
    """把候选间隔洗成有限 float；解析不了 / inf / nan 一律当"未声明"。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def effective_interval(host: str, *, default_s: float,
                       policy_s: float | None = None,
                       crawl_delay: float | None = None) -> float:
    """该 host 的最小请求间隔 = max(default, 人工 policy, robots crawl_delay)。

    语义与 `policy.min_interval(host, default)` 对齐（保守优先）：
    - 任何 <=0 的候选都表示"未声明"，回落到 default，不会把间隔压低；
    - **只取最大、绝不上限截断**：crawl_delay=10 就是 10，不是 min(10, 3)。
    `host` 只用于接口统一（本函数的结果不依赖 host 名）。
    """
    base = _to_float(default_s)
    if base is None:
        base = DEFAULT_INTERVAL_S
    best = base
    for candidate in (policy_s, crawl_delay):
        value = _to_float(candidate)
        if value is not None and value > 0 and value > best:
            best = value
    return float(best)


def _norm_host(host: Any) -> str:
    """host 归一化：去空白、小写；带 scheme 时取 hostname，否则原样（去尾部点）。"""
    text = str(host or "").strip()
    if not text:
        return ""
    if "://" in text:
        try:
            return (urlsplit(text).hostname or "").lower().rstrip(".")
        except ValueError:
            return text.lower()
    return text.lower().rstrip(".")


# ===== ② 限速器 =====
class HostLimiter:
    """按 host 限速：记录上次请求时刻，`acquire` 阻塞到间隔满足为止。

    线程安全：每个 host 一把锁，同一 host 的 `acquire` 串行；不同 host 互不阻塞。
    `clock` / `sleep` 可注入（测试用假时钟，验证 crawl-delay 时不需要真等 10 秒）。
    """

    def __init__(self, default_s: float = 1.5,
                 crawl_delays: dict[str, float] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.default_s = default_s
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self._crawl_delays: dict[str, float] = {}
        for host, value in dict(crawl_delays or {}).items():
            self._crawl_delays[_norm_host(host)] = value
        self._policy: dict[str, float] = {}          # set_interval 写入的人工策略
        self._last: dict[str, float] = {}            # host -> 上次请求时刻（同注入时钟）
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.RLock()

    # -------------------------------------------------- 查询
    def interval(self, host: str) -> float:
        """该 host 实际使用的间隔：default / 人工 policy / crawl_delay 取最大。"""
        key = _norm_host(host)
        with self._guard:
            policy_s = self._policy.get(key)
            crawl_delay = self._crawl_delays.get(key)
        return effective_interval(key, default_s=self.default_s,
                                  policy_s=policy_s, crawl_delay=crawl_delay)

    def wait_for(self, host: str) -> float:
        """现在给该 host 发请求还需要等多久（秒）；没请求过返回 0。"""
        key = _norm_host(host)
        interval = self.interval(key)
        now = float(self.clock())
        with self._guard:
            last = self._last.get(key)
        if last is None:
            return 0.0
        return max(0.0, interval - (now - last))

    # -------------------------------------------------- 记时与等待
    def note(self, host: str, ts: float | None = None) -> float:
        """记录该 host 已发生一次请求的时刻；ts 省略时取注入的 clock()。"""
        key = _norm_host(host)
        value = float(self.clock()) if ts is None else _to_float(ts)
        if value is None:
            raise ValueError("note 的 ts 必须是有限数字")
        with self._guard:
            self._last[key] = value
        return value

    def acquire(self, host: str) -> float:
        """阻塞到可以给该 host 发请求为止，记下本次时刻，返回等了多久（秒）。"""
        key = _norm_host(host)
        lock = self._lock_for(key)
        with lock:                                # 同 host 串行，等待期间不占用别的 host
            waited = self.wait_for(key)
            if waited > 0:
                self.sleep(waited)
            self.note(key, self.clock())
        return waited

    def set_interval(self, host: str, s: float) -> float:
        """写入人工策略间隔（保守优先：只可能抬高、不会压低到 default 以下）。

        `s <= 0` 表示清除该 host 的人工策略；返回写入后的实际间隔。
        """
        key = _norm_host(host)
        value = _to_float(s)
        if value is None:
            raise ValueError("set_interval 的间隔必须是有限数字")
        with self._guard:
            if value <= 0:
                self._policy.pop(key, None)
            else:
                self._policy[key] = value
        return self.interval(key)

    # -------------------------------------------------- 内部
    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock


# ===== ③ 并发 =====
def _default_host_of(item: Any) -> str:
    """默认 host 抽取：URL 字符串 / {"url": ...} / {"host": ...} 都认。"""
    target = item
    if isinstance(item, dict):
        target = item.get("url") or item.get("host") or ""
    text = str(target or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "//" + text
    try:
        return (urlsplit(text).hostname or "").lower()
    except ValueError:
        return ""


def _error_text(exc: BaseException) -> str:
    """异常 → 结果里的可读消息：保留类型名，排查时知道是哪种错。"""
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def run_parallel(items: Iterable[Any], worker: Callable[[Any], Any], *,
                 max_workers: int = 4,
                 limiter: HostLimiter | None = None,
                 host_of: Callable[[Any], str] | None = None,
                 progress: Callable[[int, int], None] | None = None) -> list[Any]:
    """线程池并发跑 `worker(item)`，结果**保持输入顺序**。

    语义（Lead 按此接线）：
    - 同一 host 串行：该 host 的任务持锁执行，`limiter` 存在时先 `acquire(host)` 再干活；
    - 不同 host 并行：锁按 host 划分，`max_workers` 控制总并发；
    - 单条异常不拖垮整批：该条结果是 `{"_error": "<类型>: <消息>"}`；
    - `progress(done, total)` 每完成一条回调一次；回调异常不影响采集。
    """
    batch = list(items)
    total = len(batch)
    try:
        workers = int(max_workers)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_workers 必须是整数") from exc
    if workers < 1:
        raise ValueError("max_workers 必须 >= 1")
    if total == 0:
        return []

    pick_host = host_of or _default_host_of
    hosts = [pick_host(item) for item in batch]          # 在提交前算好，host_of 的错直接暴露
    results: list[Any] = [None] * total
    host_locks: dict[str, threading.Lock] = {}
    for host in hosts:
        if host not in host_locks:
            host_locks[host] = threading.Lock()
    counter = {"done": 0}
    counter_lock = threading.Lock()

    def _task(index: int) -> None:
        try:
            with host_locks[hosts[index]]:               # 同 host 串行
                if limiter is not None:
                    limiter.acquire(hosts[index])
                results[index] = worker(batch[index])
        except Exception as exc:                         # noqa: BLE001 —— 单条失败不拖垮整批
            results[index] = {"_error": _error_text(exc)}
        if progress is not None:
            with counter_lock:
                counter["done"] += 1
                done = counter["done"]
            try:
                progress(done, total)
            except Exception:                            # noqa: BLE001 —— 进度不许影响采集
                pass

    with ThreadPoolExecutor(max_workers=min(workers, total)) as pool:
        futures = [pool.submit(_task, index) for index in range(total)]
        for index, future in enumerate(futures):
            try:
                future.result()
            except Exception as exc:                     # noqa: BLE001 —— 兜底，理论上到不了
                results[index] = {"_error": _error_text(exc)}
    return results


# ===== ④ 自测 =====
def _self_test() -> None:
    """零网络自检：crawl-delay 不截断、多 host 互不阻塞、保序、单条异常不拖垮整批。"""

    class _FakeClock:
        """假时钟：sleep 只推进数字，不真等 —— 验证 crawl-delay=10 也是毫秒级。"""

        def __init__(self) -> None:
            self.now = 0.0
            self._lock = threading.Lock()

        def clock(self) -> float:
            with self._lock:
                return self.now

        def sleep(self, seconds: float) -> None:
            with self._lock:
                self.now += max(0.0, float(seconds))

    # 1) 纯函数：取最大，crawl-delay 不截断
    assert effective_interval("a.cn", default_s=1.5, crawl_delay=10.0) == 10.0
    assert effective_interval("a.cn", default_s=1.5, policy_s=30.0, crawl_delay=10.0) == 30.0
    assert effective_interval("a.cn", default_s=1.5, policy_s=0, crawl_delay=-2) == 1.5
    assert effective_interval("a.cn", default_s=0) == 0.0

    # 2) crawl-delay=10：第二次请求至少等 10 秒（旧实现会被截成 3 秒）
    fake = _FakeClock()
    limiter = HostLimiter(default_s=1.5, crawl_delays={"a.cn": 10.0},
                          clock=fake.clock, sleep=fake.sleep)
    assert limiter.interval("a.cn") == 10.0, limiter.interval("a.cn")
    assert limiter.wait_for("a.cn") == 0.0, "还没请求过不该等"
    limiter.note("a.cn", fake.clock())
    waited = limiter.acquire("a.cn")
    assert waited == 10.0, waited
    assert fake.now == 10.0, fake.now
    assert limiter.wait_for("a.cn") == 10.0, limiter.wait_for("a.cn")

    # 3) 不同 host 互不阻塞
    assert limiter.acquire("b.cn") == 0.0
    assert limiter.set_interval("c.cn", 30.0) == 30.0

    # 4) run_parallel 保持输入顺序
    doubled = run_parallel([1, 2, 3], lambda n: n * 2, max_workers=2)
    assert doubled == [2, 4, 6], doubled

    # 5) 单条异常不拖垮整批，异常收在对应下标
    def _boom(value: int) -> int:
        if value == 2:
            raise RuntimeError("这一条坏掉")
        return value

    mixed = run_parallel([1, 2, 3], _boom, max_workers=2)
    assert mixed[0] == 1 and mixed[2] == 3, mixed
    assert mixed[1]["_error"].startswith("RuntimeError"), mixed[1]

    # 6) 批内同 host 也按 crawl-delay 串行：3 条 → 至少等 2 * 10 秒（假时钟）
    batch_fake = _FakeClock()
    batch_limiter = HostLimiter(default_s=1.5, crawl_delays={"x.cn": 10.0},
                                clock=batch_fake.clock, sleep=batch_fake.sleep)
    urls = ["https://x.cn/1", "https://x.cn/2", "https://x.cn/3"]
    ordered = run_parallel(urls, lambda url: url, max_workers=3, limiter=batch_limiter)
    assert ordered == urls, ordered
    assert batch_fake.now >= 20.0, batch_fake.now

    print("[OK] ratelimit 自检通过：crawl-delay 不截断 / 多 host 互不阻塞 / "
          "结果保序 / 单条异常不拖垮整批（假时钟，零网络）")


if __name__ == "__main__":
    _self_test()
