"""框架/维护/隔离验收入口。
结构导航：
├─ create_adapter
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

from tests.conftest import FixtureAdapter


def create_adapter():
    """返回仅测试用采集器；由独立进程验收配置调用。"""
    return FixtureAdapter()
