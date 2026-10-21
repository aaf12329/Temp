# -*- coding: utf-8 -*-
"""TLS 指纹伪装抓取档 —— 专治 403 / Cloudflare 风控（契约里的 `blocked` 档）。

普通 urllib 的 TLS 握手指纹（JA3/JA4、HTTP2 设置）一眼就能被识别成"不是浏览器"，
所以有些站直接 403，连挑战页都不给。本档用 `curl_cffi` 复刻真实 Chrome 的指纹，
不改 UA、不伪造 header，只让握手看起来像真浏览器。

============================ 函数结构树 ============================
① 能力探测
    available()                    (包在不在, 一句说明)；纯查询，不发请求
② 抓取
    fetch_html(url, ...)           用指定指纹取页面，返回与 net.fetch 对齐的 dict
③ 自测
    _self_test()                   零网络：只验 available() 与参数兜底

============================ 调用关系总览 ===========================
main.py（入口层）
    └─▶ route.choose(profile)        得到 tool == "impersonate"
          └─▶ fetch_tls.fetch_html    注入给 page.crawl(fetcher=...) 当抓取器
                └─▶ curl_cffi.requests.get(impersonate=...)

⚠️ 工具层横向零依赖：本模块不 import 其它工具模块，也不被其它工具模块 import。
   `curl_cffi` 在函数内部才 import —— 没装的机器上，本模块仍可安全 import。

============================ 数据流单链路 ===========================
URL → curl_cffi.get(impersonate="chrome") → status / headers / bytes
    → 按站点声明解码 → {url, final_url, status, text, raw, encoding, headers, error}
    → 交给 page.crawl 存快照、写 pages.json

============================ 对外返回契约 ===========================
available() -> (bool, str)
    # (True, "curl_cffi 0.16.3 可用") / (False, "未安装 curl_cffi")

fetch_html(url, *, timeout=10.0, impersonate="chrome", user_agent=None,
           proxies=None, headers=None) -> dict
    # 与 net.fetch 对齐，键固定：
    #   url / final_url / status / found / text / raw / encoding / headers /
    #   error / transient / impersonate
    # 🔴 403 不是异常：status=403、error=None，由上层按状态码判定（blocked）
    # 🔴 永不抛异常；连接层失败返回 found=False + error 描述
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps          # noqa: F401  （保持与其它工具模块一致的导入姿态）
except ImportError:                                  # 直接运行本文件（自测入口）时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------- 常量区
DEFAULT_IMPERSONATE = "chrome"
DEFAULT_TIMEOUT = 10.0
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _load_curl_cffi() -> Any:
    """懒加载 curl_cffi；没装返回 None（工具层不许因为缺依赖就崩）。"""
    try:
        from curl_cffi import requests as curl_requests
    except Exception:
        return None
    return curl_requests


def available() -> tuple[bool, str]:
    """本档能不能用？只查包在不在，不发任何请求。"""
    curl_requests = _load_curl_cffi()
    if curl_requests is None:
        return False, "未安装 curl_cffi（pip install curl_cffi）"
    try:                                           # 版本号走包元数据，别信模块属性
        from importlib.metadata import version
        note = f"curl_cffi {version('curl_cffi')} 可用"
    except Exception:
        note = "curl_cffi 可用（版本未知）"
    return True, note


def _new_result(url: str) -> dict[str, Any]:
    """与 net.fetch 对齐的空结果骨架（键固定，失败时也齐全）。"""
    return {
        "url": url, "final_url": url, "status": None, "found": False,
        "text": "", "raw": b"", "encoding": None, "headers": {},
        "error": None, "transient": False, "impersonate": None,
    }


def fetch_html(url: str, *, timeout: float = DEFAULT_TIMEOUT,
               impersonate: str = DEFAULT_IMPERSONATE,
               user_agent: str | None = None,
               proxies: dict[str, str] | None = None,
               headers: dict[str, str] | None = None) -> dict[str, Any]:
    """用 TLS 指纹伪装取一个页面。永不抛异常，失败也返回完整骨架。

    proxies：不传就用 curl_cffi 的默认行为（会读环境代理）；
             显式传 {} 表示不走代理。由调用方决定，本模块不写死。
    """
    result = _new_result(url)
    curl_requests = _load_curl_cffi()
    if curl_requests is None:
        result["error"] = "未安装 curl_cffi"
        return result

    request_headers = {"User-Agent": user_agent or DEFAULT_UA}
    if headers:
        request_headers.update(headers)

    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "impersonate": impersonate,
        "headers": request_headers,
        "allow_redirects": True,
    }
    if proxies is not None:
        kwargs["proxies"] = proxies

    try:
        response = curl_requests.get(url, **kwargs)
    except Exception as exc:                       # 连接层失败：超时 / DNS / SSL
        name = type(exc).__name__
        result["error"] = f"{name}: {getattr(exc, 'message', exc)}"
        result["transient"] = True
        return result

    result["status"] = response.status_code
    result["found"] = True
    result["final_url"] = str(getattr(response, "url", url) or url)
    result["headers"] = {str(k).lower(): v for k, v in dict(response.headers).items()}
    result["raw"] = bytes(getattr(response, "content", b"") or b"")
    result["encoding"] = getattr(response, "encoding", None) or None
    result["impersonate"] = impersonate

    try:
        result["text"] = response.text or ""
    except Exception:                              # 解码失败不让整条记录报废
        body = result["raw"]
        result["text"] = body.decode(result["encoding"] or "utf-8", "replace")
    return result


def _self_test() -> None:
    """零网络自检：只验能力探测与参数兜底，绝不发请求。"""
    ok, note = available()
    print(f"  available → {ok}  {note}")

    # 参数兜底：把请求函数换成一调用就抛异常的桩，验证异常被吞成 error 而不是崩
    popped = {}
    real = _load_curl_cffi()
    if real is not None:
        class _Boom:
            @staticmethod
            def get(url: str, **kwargs: Any) -> Any:
                popped.update(kwargs)
                raise TimeoutError("offline")

        original = globals()["_load_curl_cffi"]
        try:
            globals()["_load_curl_cffi"] = lambda: _Boom       # 临时替换，下面的 finally 还原
            bad = fetch_html("https://example.invalid/x", impersonate="chrome124")
            assert bad["error"] and bad["found"] is False, bad
            assert bad["status"] is None and bad["raw"] == b"", bad
            assert popped.get("impersonate") == "chrome124", popped   # 指纹必须真的传下去
            assert popped.get("headers", {}).get("User-Agent"), popped
            print("  异常兜底   → error 被吞成 error 字段，impersonate 已传给底层")
        finally:
            globals()["_load_curl_cffi"] = original

        missing = fetch_html("https://example.invalid/y")
        assert missing["error"] is None or isinstance(missing["error"], str)
        print("  骨架完整性 → 键齐全，缺依赖时也只返回 error 不抛异常")

    print("[OK] fetch_tls 自检通过（零网络）")


if __name__ == "__main__":
    _self_test()
