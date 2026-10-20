"""独立子项目的唯一地址簿，无磁盘读写。

结构：包资源常量 → AdvisorPaths 会话/记忆地址。
调用关系：入口把工作目录传入 → 地址簿返回 Path；不引用主工程 app 包。
数据流：独立工作目录 + 配置根目录 → 提示词、流水、事实库位置。

函数结构树（AST 核对）：
├─ default_advisor_root
├─ AdvisorPaths
│  ├─ session
│  ├─ memory
项目内调用关系：
└─ 无项目内函数调用；入口由 Python/pytest 调度
"""

from dataclasses import dataclass
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets"
SYSTEM_PROMPT = ASSET_ROOT / "system.md"
SUMMARY_PROMPT = ASSET_ROOT / "summary.md"
ENV_FILENAME = ".env"


def default_advisor_root(demo: bool = False) -> Path:
    """以启动工作目录定位独立数据；由 BotSettings.root 调用。"""
    return Path.cwd() / ("data_demo" if demo else "data")


@dataclass(frozen=True)
class AdvisorPaths:
    """独立模块的运行数据地址簿；由服务入口构造。"""

    root: Path

    def session(self, session_id: str, name: str) -> Path:
        """返回会话已登记文件；由 History 调用，入口须验证 session_id。"""
        names = {"history": "chat_history.json", "state": "state.json", "lock": "session.lock"}
        return self.root / "sessions" / session_id / names[name]

    def memory(self, name: str) -> Path:
        """返回事实库或其锁；由 Memory 调用。"""
        return self.root / {"facts": "memory.json", "lock": "memory.lock"}[name]
