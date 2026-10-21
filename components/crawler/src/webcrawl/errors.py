# -*- coding: utf-8 -*-
"""错误码与可重试判定 —— 契约里所有失败原因的**唯一出处**。

《爬虫标准API接口.md》v1.2 §4.7：错误码是**封闭枚举**，只许新增不许改名。
本模块同时负责把「HTTP 状态 / 异常 / 内容类型」映射成码——**映射逻辑只写在这里**，
别处只准引用常量，不许自己拼字符串。

================================ 结构树 =================================
① 常量
    E_*                20 个错误码常量
    RETRYABLE          code → 能不能自动重试
② 构造
    make(code, message)      统一构造 {code, message, retryable}
    from_status(status)      HTTP 状态码 → Error
    from_exception(exc)      异常 → Error（区分 超时 / DNS / TLS / 连接）
③ 内容类型
    from_content_type(ct)    非 html/pdf → E_UNSUPPORTED_TYPE

================================ 调用关系 ================================
fetch_tls / fetch_browser / net / page / api 都只 import 本模块，不各自拼码。
"""

from __future__ import annotations

import socket
import ssl
from typing import Any

# ---------------------------------------------------------------- 错误码
E_BAD_REQUEST = "E_BAD_REQUEST"
E_ROBOTS_DISALLOW = "E_ROBOTS_DISALLOW"
E_ROBOTS_UNKNOWN = "E_ROBOTS_UNKNOWN"
E_ROBOTS_PARSE = "E_ROBOTS_PARSE"
E_HTTP_403 = "E_HTTP_403"
E_HTTP_429 = "E_HTTP_429"
E_HTTP_4XX = "E_HTTP_4XX"
E_HTTP_5XX = "E_HTTP_5XX"
E_BLOCKED = "E_BLOCKED"
E_TIMEOUT = "E_TIMEOUT"
E_CONN = "E_CONN"
E_DNS = "E_DNS"
E_TLS = "E_TLS"
E_LOGIN_WALL = "E_LOGIN_WALL"
E_TOOL_UNAVAILABLE = "E_TOOL_UNAVAILABLE"
E_TOOL_FAILED = "E_TOOL_FAILED"
E_STORAGE = "E_STORAGE"
E_UNSUPPORTED_TYPE = "E_UNSUPPORTED_TYPE"
E_PDF_PARSE = "E_PDF_PARSE"
E_PDF_NO_TEXT = "E_PDF_NO_TEXT"

ALL_CODES: tuple[str, ...] = (
    E_BAD_REQUEST, E_ROBOTS_DISALLOW, E_ROBOTS_UNKNOWN, E_ROBOTS_PARSE,
    E_HTTP_403, E_HTTP_429, E_HTTP_4XX, E_HTTP_5XX, E_BLOCKED,
    E_TIMEOUT, E_CONN, E_DNS, E_TLS, E_LOGIN_WALL,
    E_TOOL_UNAVAILABLE, E_TOOL_FAILED, E_STORAGE, E_UNSUPPORTED_TYPE,
    E_PDF_PARSE, E_PDF_NO_TEXT,
)

# 契约 §4.7：只有这些允许自动重试
RETRYABLE: dict[str, bool] = {
    E_BAD_REQUEST: False,
    E_ROBOTS_DISALLOW: False,
    E_ROBOTS_UNKNOWN: True,
    E_ROBOTS_PARSE: False,
    E_HTTP_403: True,
    E_HTTP_429: True,
    E_HTTP_4XX: False,
    E_HTTP_5XX: True,
    E_BLOCKED: True,
    E_TIMEOUT: True,
    E_CONN: True,
    E_DNS: False,
    E_TLS: False,
    E_LOGIN_WALL: False,
    E_TOOL_UNAVAILABLE: False,
    E_TOOL_FAILED: True,
    E_STORAGE: False,
    E_UNSUPPORTED_TYPE: False,
    E_PDF_PARSE: False,
    E_PDF_NO_TEXT: False,
}


def make(code: str, message: str) -> dict[str, Any]:
    """统一错误对象。未知码按不可重试处理（宁可不重试，也不许乱重试）。"""
    return {"code": code, "message": message, "retryable": bool(RETRYABLE.get(code, False))}


def from_status(status: int | None, *, blocked: bool = False) -> dict[str, Any] | None:
    """HTTP 状态码 → Error；2xx/3xx 返回 None（不是错误）。

    blocked=True（命中 CF / 风控特征）时，403 与 503 归 E_BLOCKED 而不是普通 HTTP 码。
    """
    if status is None:
        return make(E_CONN, "没有拿到 HTTP 响应")
    if 200 <= status < 400:
        return None
    if blocked and status in (403, 503):
        return make(E_BLOCKED, f"HTTP {status}，命中风控 / Cloudflare 特征")
    if status == 403:
        return make(E_HTTP_403, "HTTP 403，被站点拒绝")
    if status == 429:
        return make(E_HTTP_429, "HTTP 429，被限流")
    if 400 <= status < 500:
        return make(E_HTTP_4XX, f"HTTP {status}，客户端错误")
    if status >= 500:
        return make(E_HTTP_5XX, f"HTTP {status}，服务端错误")
    return make(E_HTTP_4XX, f"HTTP {status}，非常规状态码")


def from_exception(exc: BaseException) -> dict[str, Any]:
    """异常 → Error。⚠️ 契约要求区分 超时 / DNS / TLS / 连接，别一律 E_CONN。"""
    name = type(exc).__name__
    text = str(exc) or name

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return make(E_TIMEOUT, f"{name}: {text}")
    if isinstance(exc, ssl.SSLError):
        return make(E_TLS, f"{name}: {text}")
    if isinstance(exc, socket.gaierror):
        return make(E_DNS, f"{name}: {text}")

    lowered = f"{name} {text}".lower()
    if "timed out" in lowered or "timeout" in lowered:
        return make(E_TIMEOUT, f"{name}: {text}")
    if "ssl" in lowered or "certificate" in lowered or "handshake" in lowered:
        return make(E_TLS, f"{name}: {text}")
    if "name or service not known" in lowered or "getaddrinfo" in lowered \
            or "nodename nor servname" in lowered or "no address" in lowered:
        return make(E_DNS, f"{name}: {text}")
    return make(E_CONN, f"{name}: {text}")


def from_content_type(content_type: str | None) -> dict[str, Any] | None:
    """内容类型不在 html / pdf 内 → E_UNSUPPORTED_TYPE。"""
    if not content_type:
        return None
    lowered = content_type.lower()
    if "html" in lowered or "xml" in lowered or "text/plain" in lowered:
        return None
    if "pdf" in lowered:
        return None
    return make(E_UNSUPPORTED_TYPE, f"内容类型 {content_type} 本期不支持")


def _self_test() -> None:
    """零网络自检：码表完整、可重试表齐全、映射符合契约。"""
    assert len(ALL_CODES) == len(set(ALL_CODES)) == 20, len(ALL_CODES)
    assert set(RETRYABLE) == set(ALL_CODES), set(RETRYABLE) ^ set(ALL_CODES)

    assert make(E_TIMEOUT, "x")["retryable"] is True
    assert make(E_BAD_REQUEST, "x")["retryable"] is False
    assert make("E_NOT_EXIST", "x")["retryable"] is False   # 未知码保守处理

    assert from_status(200) is None and from_status(301) is None
    assert from_status(403)["code"] == E_HTTP_403
    assert from_status(403, blocked=True)["code"] == E_BLOCKED
    assert from_status(503, blocked=True)["code"] == E_BLOCKED
    assert from_status(503)["code"] == E_HTTP_5XX
    assert from_status(429)["code"] == E_HTTP_429
    assert from_status(404)["code"] == E_HTTP_4XX

    assert from_exception(TimeoutError("t"))["code"] == E_TIMEOUT
    assert from_exception(ssl.SSLError("cert"))["code"] == E_TLS
    assert from_exception(socket.gaierror("nope"))["code"] == E_DNS
    assert from_exception(ConnectionResetError("r"))["code"] == E_CONN

    assert from_content_type("text/html; charset=utf-8") is None
    assert from_content_type("application/pdf") is None
    assert from_content_type("application/zip")["code"] == E_UNSUPPORTED_TYPE

    print("[OK] errors 自检通过：20 码 / 可重试表 / 状态与异常映射（零网络）")


if __name__ == "__main__":
    _self_test()
