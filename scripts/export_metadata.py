"""Refresh reproducible local dependency pins and the frontend-facing API description.


框架/维护/隔离验收入口。
结构导航：
├─ main
调用关系：
└─ 由外部入口或框架调度；类型定义本身不执行任务。
数据流：隔离输入/显式参数 → 本文件定义的职责 → 固定返回或验收断言。
"""

from collections import deque
from importlib.metadata import distribution

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from app.common.file_io import write_json, write_text
from app.common.paths import DEPENDENCY_LOCK, OPENAPI_FILE


def dependency_pins(roots):
    """按实际依赖闭包锁定选中根包；不会把另一套可选 GAN 框架混进基础环境。"""
    pending = deque(roots)
    visited, pins = {}, {}
    while pending:
        name, extras = pending.popleft()
        name = canonicalize_name(name)
        previous = visited.get(name)
        if previous is not None and extras <= previous:
            continue
        active = extras | (previous or set())
        visited[name] = active
        package = distribution(name)
        pins[name] = package.version
        for raw in package.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or any(requirement.marker.evaluate({"extra": extra})
                                                  for extra in active | {""}):
                pending.append((requirement.name, set(requirement.extras)))
    return [f"{name}=={pins[name]}" for name in sorted(pins)
            if name not in {"industry-research-platform", "industry-research-advisor"}]


def main() -> None:
    """导出依赖和接口元数据；由显式维护命令调用，导入不会写文件。"""
    from app.main import create_app

    pins = dependency_pins([("industry-research-platform", {"dev"}),
                            ("industry-research-advisor", set())])
    write_text(
        DEPENDENCY_LOCK,
        "# Verified local environment: Windows x64, Python 3.12.14\n"
        "# Install pins, then install root and advisor packages with --no-deps -e.\n"
        "# Windows lock; Linux containers resolve dependencies from root pyproject.toml.\n"
        "# GAN backends are optional: requirements/gan-pytorch.txt / gan-tensorflow.txt.\n"
        + "\n".join(pins) + "\n",
    )
    write_json(OPENAPI_FILE, create_app().openapi())
    print("Updated dependency pins and OpenAPI; no credentials exported.")


if __name__ == "__main__":
    main()
