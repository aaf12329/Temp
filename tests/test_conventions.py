"""规范回归验收：结构说明、功能依赖、导入副作用和敏感路径。

结构导航：pytest → 4 类静态/隔离行为断言。
调用关系：测试读取源码 AST；运行维护脚本时使用非主入口名称，不执行 main。
数据流：项目源码或临时文件 → 明确的规范断言；不读取密钥值，不修改真实数据。
"""

import ast
import runpy

import pytest

from app.common.paths import PROJECT_ROOT
from app.core.errors import ServiceError


def test_production_files_have_navigation_and_function_contracts():
    """验证生产 Python 的结构/调用/数据流和函数说明；由 pytest 调度。"""
    for area in ("app", "scripts", "migrations"):
        for path in (PROJECT_ROOT / area).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text("utf-8"))
            documentation = ast.get_docstring(tree) or ""
            assert all(word in documentation for word in ("结构", "调用", "数据流")), path
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    assert ast.get_docstring(node), f"{path.name}:{node.lineno}"


def test_feature_modules_have_no_sibling_imports():
    """验证业务功能只依赖共用设施而无横向服务引用；由 pytest 调度。"""
    for path in (PROJECT_ROOT / "app/services").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.services."), path


def test_maintenance_module_imports_do_not_write_files(tmp_path, monkeypatch):
    """验证导入初始化/导出脚本不改配置和接口文件；由 pytest 调度。"""
    from app.common.paths import DEPENDENCY_LOCK, ENV_FILE, OPENAPI_FILE

    targets = [ENV_FILE, DEPENDENCY_LOCK, OPENAPI_FILE]
    before = {path: path.stat().st_mtime_ns for path in targets}
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(PROJECT_ROOT / "scripts/init_config.py"), run_name="import_check")
    runpy.run_path(str(PROJECT_ROOT / "scripts/export_metadata.py"), run_name="import_check")
    assert before == {path: path.stat().st_mtime_ns for path in targets}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("filename", [".env", "secret.pem", "private.key"])
def test_sensitive_normalized_files_cannot_be_served(client, filename):
    """验证即使敏感文件位于 normalized 内也拒绝输出；由 pytest 调度。"""
    storage = client.app.state.runtime.storage
    path = storage.paths.path("text") / filename
    path.write_text("synthetic-sensitive-value", "utf-8")
    with pytest.raises(ServiceError) as caught:
        storage.resolve_normalized(str(path))
    assert caught.value.error.code == "E_STORAGE_PATH"
