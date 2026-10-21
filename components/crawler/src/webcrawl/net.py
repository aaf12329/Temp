# -*- coding: utf-8 -*-
"""HTTP 单一入口 —— 全项目只在这里发请求。

《工程架构规范》§04（I/O 单一入口）+ §06（错误处理哲学）：
只重试**暂时性错误**（429 / 5xx / 超时 / 传输中断），4xx 与逻辑错误立刻暴露。

============================== 函数结构树 ==============================
① 响应处理（纯函数，不联网，可离线单测）
    _decompress(raw, head)       按 Content-Encoding 解 gzip / deflate
    _decode_body(raw, head)      按 charset 解码成文本（含 GB2312/GBK 回退）
② 请求
    fetch(url, ...)              GET 一个 URL，统一返回 dict（状态 / 正文 / 原始字节 / 响应头）
    _opener(proxies)             按代理配置建 opener（None = 环境代理，{} = 不走代理）
    _sleep_backoff(attempt)      指数退避 + 抖动
③ 礼貌与节奏（给采集层用）
    sleep_between(seconds)       真正的 sleep，集中在一处，测试可整体替换
    polite_wait(own_delay, site_crawl_delay)
                                 算出「这次该等多久」：站点要求与自家间隔取较大者

============================== 调用关系总览 ==============================
fetch ──▶ _decompress ──▶ _decode_body      （先解压，再解码）
      └─▶ _sleep_backoff                    （仅暂时性错误时）
polite_wait ──▶ sleep_between               （采集层每条 URL 之间调用）

被谁用：web_check（取 robots.txt / 取页面）、crawl（抓正文）

================================ 对外返回契约 ===========================
fetch(url, ...) -> {
    "url": str, "final_url": str,          # 跟随重定向后的真实地址
    "status": int|None,                    # 无响应时为 None
    "found": bool,                         # False = 404/410，表示"资源不存在"
    "text": str,                           # 已按站点声明解码的正文（失败为空串）
    "raw": bytes,                          # 解压后的原始字节（存快照用这个）
    "encoding": str|None,                  # 实际使用的编码，如 "utf-8" / "gb18030"
    "headers": dict[str, str],             # 键统一小写
    "error": str|None,                     # 非 None 表示本次抓取失败
    "transient": bool,                     # 是否经历过可重试错误
}

字段一旦发布不改名；要扩展只加新字段。
"""

from __future__ import annotations

import gzip
import http.client
import random
import re
import time
import zlib
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

# ---------------------------------------------------------------- 常量区
DEFAULT_USER_AGENT = "Crawler-Research/0.1 (Kaili sour-soup-fish industry survey)"
DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 3
DEFAULT_DELAY = 1.5                     # 两条请求之间的默认间隔（秒）
RETRY_BACKOFF_BASE = 1.0
RETRY_BACKOFF_CAP = 8.0
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
MISSING_STATUS = frozenset({404, 410})
# 解码顺序：响应头 charset → <meta charset> → utf-8 → gb18030（中文站常见 GB2312/GBK）
FALLBACK_ENCODINGS = ("utf-8", "gb18030")
META_CHARSET_RE = re.compile(rb'charset=["\']?([\w-]+)', re.IGNORECASE)
HEADER_CHARSET_RE = re.compile(r"charset=([\w-]+)", re.IGNORECASE)


def _decompress(raw: bytes, headers: dict[str, str]) -> bytes:
    """按 Content-Encoding 解压正文；有站点无视请求头照样发 gzip（已踩：ymt.com）。"""
    encoding = (headers.get("content-encoding") or "").lower().strip()
    try:
        if "gzip" in encoding:
            return gzip.decompress(raw)
        if "deflate" in encoding:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error):
        return raw                       # 解压失败就原样返回，不掩盖问题
    return raw


def _decode_body(raw: bytes, headers: dict[str, str]) -> tuple[str, str]:
    """按响应头 / meta 声明的编码解码，返回 (文本, 实际使用的编码)。

    不硬解成 UTF-8 —— 中文站大量使用 GB2312/GBK，硬解会得到一堆乱码。
    """
    candidates: list[str] = []
    match = HEADER_CHARSET_RE.search(headers.get("content-type") or "")
    if match:
        candidates.append(match.group(1))
    meta = META_CHARSET_RE.search(raw[:2048])
    if meta:
        candidates.append(meta.group(1).decode("ascii", "ignore"))
    candidates.extend(FALLBACK_ENCODINGS)

    for encoding in candidates:
        name = encoding.strip().lower()
        if not name:
            continue
        try:
            return raw.decode(name), name
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _opener(proxies: dict[str, str] | None):
    """按调用方给的代理配置建一个 opener（None = 沿用环境代理，行为与旧版一致）。"""
    if proxies is None:
        return None
    return build_opener(ProxyHandler(dict(proxies)))


def fetch(url: str, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
          user_agent: str = DEFAULT_USER_AGENT,
          proxies: dict[str, str] | None = None) -> dict[str, Any]:
    """GET 一个 URL，返回统一 dict；网络失败不抛异常，写在 `error` 里。

    proxies：不传 = 沿用 urllib 默认行为（读环境变量里的代理，与旧版完全一致）；
             传 {} = 显式不走代理（本机系统代理常把国内站也劫持，这个开关必须有）；
             传 {"http": ..., "https": ...} = 走指定代理。
    """
    if "://" not in url:                 # 裸域名补 https，否则 urlopen 直接抛 ValueError
        url = "https://" + url
    result: dict[str, Any] = {"url": url, "final_url": url, "status": None,
                              "found": True, "text": "", "raw": b"", "encoding": None,
                              "headers": {}, "error": None, "transient": False}
    opener = _opener(proxies)
    attempt = 0
    while True:
        try:
            request = Request(url, headers={"User-Agent": user_agent})
            open_call = opener.open if opener is not None else urlopen
            with open_call(request, timeout=timeout) as response:
                raw = response.read()
                result["status"] = response.status
                result["final_url"] = response.geturl()
                result["headers"] = {key.lower(): value
                                     for key, value in response.headers.items()}
                body = _decompress(raw, result["headers"])   # gzip 先解压再解码
                result["raw"] = body
                result["text"], result["encoding"] = _decode_body(body, result["headers"])
                result["error"] = None      # 重试成功必须清掉上一次留下的错误
                return result
        except HTTPError as exc:
            result["status"] = exc.code
            if exc.code in MISSING_STATUS:
                result["found"] = False
                return result
            if exc.code in TRANSIENT_STATUS and attempt < retries:
                result["transient"] = True
                _sleep_backoff(attempt)
                attempt += 1
                continue
            result["error"] = f"HTTP {exc.code} {exc.reason}"
            return result
        except http.client.IncompleteRead as exc:
            # 分块传输中途断开（真踩过：抓 3.5 MB 的柳州日报 PDF，崩在 response.read()）
            # IncompleteRead 继承 HTTPException，**不是** OSError；不单独接就会穿透到上层
            result["error"] = f"IncompleteRead: 已读 {len(exc.partial)} 字节后连接中断"
            if attempt < retries:
                result["transient"] = True
                _sleep_backoff(attempt)
                attempt += 1
                continue
            return result
        except http.client.HTTPException as exc:
            # RemoteDisconnected / BadStatusLine 等协议级异常，同样按暂时性错误重试
            result["error"] = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                result["transient"] = True
                _sleep_backoff(attempt)
                attempt += 1
                continue
            return result
        except (URLError, TimeoutError, OSError) as exc:
            result["error"] = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            if attempt < retries:
                result["transient"] = True
                _sleep_backoff(attempt)
                attempt += 1
                continue
            return result


def _sleep_backoff(attempt: int) -> None:
    """指数退避 + 抖动，避免一堆请求同时打回去。"""
    delay = min(RETRY_BACKOFF_BASE * (2 ** attempt), RETRY_BACKOFF_CAP)
    time.sleep(delay * (0.5 + random.random() / 2))


# --------------------------------------------------------- ③ 礼貌与节奏
def sleep_between(seconds: float) -> None:
    """两条请求之间真正睡一会儿；集中在一个函数里，测试可整体替换掉。"""
    if seconds and seconds > 0:
        time.sleep(seconds)


def polite_wait(own_delay: float = DEFAULT_DELAY,
                site_crawl_delay: float | None = None) -> float:
    """算这次该等多久并睡过去：站点要求优先，站点没要求就用自家默认值。

    返回实际等待的秒数，方便调用方打日志。
    """
    wait = max(float(own_delay or 0), float(site_crawl_delay or 0))
    sleep_between(wait)
    return wait
