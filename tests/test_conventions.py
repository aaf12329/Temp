"""规范回归验收：结构说明、功能依赖、导入副作用和敏感路径。

结构导航：pytest → 4 类静态/隔离行为断言。
调用关系：测试读取源码 AST；运行维护脚本时使用非主入口名称，不执行 main。
数据流：项目源码或临时文件 → 明确的规范断言；不读取密钥值，不修改真实数据。
"""

import ast
import re
import runpy
import subprocess
import sys

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


def test_production_sources_have_no_machine_absolute_paths():
    """拒绝源码与默认配置中的开发机文件地址；运行时解析与 HTTP URL 不受影响。"""
    pattern = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|/(?:Users|home)/")
    paths = []
    for area in ("app", "scripts", "frontend/src", "快速部署"):
        paths.extend(path for path in (PROJECT_ROOT / area).rglob("*")
                     if path.suffix in {".py", ".ps1", ".ts", ".tsx"}
                     and "__pycache__" not in path.parts)
    paths.extend((PROJECT_ROOT / "config").glob("*.json"))
    paths.extend([PROJECT_ROOT / ".env.example", PROJECT_ROOT / "快速部署/config.example.json"])
    for path in paths:
        assert not pattern.search(path.read_text("utf-8-sig")), path.relative_to(PROJECT_ROOT)


def test_installed_packages_and_defaults_work_outside_project(tmp_path):
    """从项目外验证可编辑包与根配置定位，捕获目录改名后遗留的安装地址。"""
    code = """
from pathlib import Path
import sys
import app
import advisor_bot
from app.common.paths import ENV_FILE, PROJECT_ROOT
from app.core.config import Settings
root = Path(sys.argv[1]).resolve()
assert PROJECT_ROOT == root
assert Path(app.__file__).resolve().is_relative_to(root)
assert Path(advisor_bot.__file__).resolve().is_relative_to(root)
assert ENV_FILE == root / '.env'
settings = Settings(_env_file=None, api_token='relative-path-check-token-0000000000000000')
assert settings.data_dir == root / 'data'
assert settings.absolute_database_url() == 'sqlite:///' + (root / 'data/platform.db').as_posix()
"""
    result = subprocess.run([sys.executable, "-c", code, str(PROJECT_ROOT)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_maintenance_module_imports_do_not_write_files(tmp_path, monkeypatch):
    """验证导入初始化/导出脚本不改配置和接口文件；由 pytest 调度。"""
    from app.common.paths import DEPENDENCY_LOCK, ENV_FILE, OPENAPI_FILE

    targets = [ENV_FILE, DEPENDENCY_LOCK, OPENAPI_FILE]
    before = {path: path.stat().st_mtime_ns if path.exists() else None for path in targets}
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(PROJECT_ROOT / "scripts/init_config.py"), run_name="import_check")
    runpy.run_path(str(PROJECT_ROOT / "scripts/export_metadata.py"), run_name="import_check")
    assert before == {path: path.stat().st_mtime_ns if path.exists() else None for path in targets}
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
