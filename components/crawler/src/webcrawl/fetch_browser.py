# -*- coding: utf-8 -*-
"""浏览器渲染抓取档 —— 专治 JS 动态站（契约里的 `dynamic` 档）。

有些站静态 HTML 里只有空骨架，正文全靠 JS 拉接口后渲染。本档用 `patchright`
（Playwright 的反 CDP 检测分支）启动**真实 Chromium**，等页面渲染完再取 DOM，
拿到的就是人眼看到的内容。

============================ 函数结构树 ============================
① 能力探测
    available()                    (包在不在 + 浏览器二进制在不在, 一句说明)；结果缓存
② 抓取
    fetch_html(url, ...)           开真浏览器取渲染后的 HTML，返回与 net.fetch 对齐的 dict
③ 自测
    _self_test()                   零网络：只验 available() 与参数兜底，不启浏览器

============================ 调用关系总览 ===========================
main.py（入口层）
    └─▶ route.choose(profile)        得到 tool == "browser"
          └─▶ fetch_browser.fetch_html  注入给 page.crawl(fetcher=...) 当抓取器
                └─▶ patchright.sync_api.sync_playwright → chromium.launch(headless=True)

⚠️ 工具层横向零依赖：本模块不 import 其它工具模块，也不被其它工具模块 import。
   `patchright` 在函数内部才 import —— 没装的机器上，本模块仍可安全 import。

============================ 数据流单链路 ===========================
URL → Chromium 启动 → page.goto(等待 domcontentloaded) → page.content()
    → {url, final_url, status, text, raw, encoding, headers, error}
    → 交给 page.crawl 存快照、写 pages.json

============================ 对外返回契约 ===========================
available() -> (bool, str)
    # (True, "patchright 1.64.1，浏览器已就绪") / (False, "浏览器二进制未安装")

fetch_html(url, *, timeout=15.0, wait_until="domcontentloaded",
           user_agent=None, extra_wait_ms=0) -> dict
    # 与 net.fetch 对齐，键固定：
    #   url / final_url / status / found / text / raw / encoding / headers /
    #   error / transient / renderer
    # 🔴 永不抛异常；浏览器启动失败 / 超时都返回 found=False + error
    # 🔴 浏览器实例用完必须关：每次调用自建自毁，不跨调用复用（避免状态泄漏）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps          # noqa: F401  （与其它工具模块保持一致的导入姿态）
except ImportError:                                  # 直接运行本文件（自测入口）时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------- 常量区
DEFAULT_TIMEOUT = 15.0
DEFAULT_WAIT_UNTIL = "domcontentloaded"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# available() 每次都要探浏览器路径，探一次就够（进程内缓存）
_AVAILABLE_CACHE: dict[str, Any] = {}


def _load_playwright() -> Any:
    """懒加载 patchright 的 sync_playwright；没装返回 None。"""
    try:
        from patchright.sync_api import sync_playwright
    except Exception:
        return None
    return sync_playwright


def _browser_executable() -> tuple[str | None, str | None]:
    """探浏览器二进制路径：(路径, 错误)。只启动 Playwright 驱动，不启动浏览器本体。"""
    sync_playwright = _load_playwright()
    if sync_playwright is None:
        return None, "未安装 patchright（pip install patchright）"
    manager = None
    try:
        manager = sync_playwright().start()
        path = getattr(manager.chromium, "executable_path", None)
        if path and Path(path).exists():
            return path, None
        return None, "浏览器二进制未安装（python -m patchright install chromium）"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                pass


def available() -> tuple[bool, str]:
    """本档能不能用？包 + 浏览器二进制都在才算可用。结果进程内缓存。"""
    if "value" in _AVAILABLE_CACHE:
        return _AVAILABLE_CACHE["value"]
    path, error = _browser_executable()
    if path is None:
        value = (False, error or "浏览器不可用")
    else:
        value = (True, f"patchright 就绪：{path}")
    _AVAILABLE_CACHE["value"] = value
    return value


def _new_result(url: str) -> dict[str, Any]:
    """与 net.fetch 对齐的空结果骨架（键固定，失败时也齐全）。"""
    return {
        "url": url, "final_url": url, "status": None, "found": False,
        "text": "", "raw": b"", "encoding": None, "headers": {},
        "error": None, "transient": False, "renderer": "patchright-chromium",
    }


def fetch_html(url: str, *, timeout: float = DEFAULT_TIMEOUT,
               wait_until: str = DEFAULT_WAIT_UNTIL,
               user_agent: str | None = None,
               extra_wait_ms: int = 0) -> dict[str, Any]:
    """用真实浏览器取渲染后的页面。永不抛异常，失败也返回完整骨架。

    extra_wait_ms：等 domcontentloaded 之后再额外等这么多毫秒，给慢接口留余量。
    """
    result = _new_result(url)
    try:
        sync_playwright = _load_playwright()
    except Exception as exc:                       # 驱动本身起不来（缺库 / 环境坏）
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if sync_playwright is None:
        result["error"] = "未安装 patchright"
        return result

    ok, note = available()
    if not ok:
        result["error"] = note
        return result

    manager = browser = context = None
    try:
        manager = sync_playwright().start()
        browser = manager.chromium.launch(headless=True)
        context = browser.new_context(user_agent=user_agent or DEFAULT_UA)
        page = context.new_page()
        response = page.goto(url, timeout=timeout * 1000, wait_until=wait_until)
        if extra_wait_ms:
            page.wait_for_timeout(extra_wait_ms)
        html = page.content()

        result["found"] = True
        result["status"] = response.status if response is not None else None
        result["final_url"] = page.url or url
        result["headers"] = {str(k).lower(): v for k, v in
                             dict(response.headers if response is not None else {}).items()}
        result["text"] = html
        result["raw"] = html.encode("utf-8", "replace")
        result["encoding"] = "utf-8"
        return result
    except Exception as exc:
        name = type(exc).__name__
        message = getattr(exc, "message", None) or str(exc).splitlines()[0][:200]
        result["error"] = f"{name}: {message}"
        result["transient"] = True
        return result
    finally:
        # 自建自毁：任何一步失败都不许留僵尸进程
        for closer in (context, browser, manager):
            if closer is None:
                continue
            try:
                closer.close() if closer is not manager else closer.stop()
            except Exception:
                pass


def _self_test() -> None:
    """零网络自检：只验能力探测与异常兜底，绝不启动浏览器。"""
    ok, note = available()
    print(f"  available → {ok}  {note}")

    # 兜底：把 sync_playwright 换成"一调用就抛异常"的桩，验证异常被吞成 error
    original = globals()["_load_playwright"]

    def _boom() -> Any:
        raise RuntimeError("offline")

    try:
        globals()["_load_playwright"] = _boom
        bad = fetch_html("https://example.invalid/x")
        assert bad["found"] is False and bad["error"], bad
        assert bad["status"] is None and bad["raw"] == b"", bad
        print("  异常兜底   → 驱动起不来也返回 error 字段，不抛异常")
    finally:
        globals()["_load_playwright"] = original

    result = _new_result("https://example.invalid/y")
    expected = {"url", "final_url", "status", "found", "text", "raw", "encoding",
                "headers", "error", "transient", "renderer"}
    assert set(result) == expected, set(result) ^ expected
    print("  骨架完整性 → 键与 net.fetch 对齐，且多一个 renderer 便于事后核查")
    print("[OK] fetch_browser 自检通过（零网络，未启动浏览器）")


if __name__ == "__main__":
    _self_test()
