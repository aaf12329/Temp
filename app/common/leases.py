"""租约心跳共用设施，不依赖任何业务模块。

结构：maintain_lease → 后台续租 → lost 标记 → 收尾；调用：各 Worker 注入续租回调。
数据流：续租布尔结果/异常 → 线程安全失效标记，调用方据此禁止提交。
"""

import logging
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def maintain_lease(renew, seconds: float):
    """在执行区间维护租约并返回失效事件；由采集/操作执行入口调用。"""
    stopped, lost = threading.Event(), threading.Event()

    def heartbeat():
        """按租约间隔调用注入回调，丢失或异常立即失效；由心跳线程调用。"""
        while not stopped.wait(min(seconds / 3, 10)):
            try:
                if not renew():
                    lost.set()
                    return
            except Exception:
                logger.exception("Lease heartbeat failed")
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stopped.set()
        thread.join(timeout=5)
