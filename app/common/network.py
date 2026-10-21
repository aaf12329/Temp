"""受限 HTTP 出口（共用基础设施，不依赖业务层）。

结构：URL/DNS 纯校验 → HostLimiter 跨进程限速 → GuardedTransport 请求。
调用：爬虫子进程安装 DNS 保护，再把 vendor net.fetch 接到 transport.fetch。
数据流：人工策略 + URL → 每跳验证/robots/限速 → 有大小上限的响应字节。
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from filelock import FileLock

from app.common.file_io import read_json, write_json
from app.common.paths import DataPaths

MAX_BODY_BYTES = 50 * 1024 * 1024


class NetworkPolicyError(ValueError):
    """表示必须停止且不可自动重试的出站边界错误。"""


def canonical_path(value: str) -> str:
    """反复解码路径并拒绝歧义；由 URL 与人工策略校验共用。"""
    for _ in range(8):
        decoded = unquote(value, errors="strict")
        if decoded == value:
            break
        value = decoded
    else:
        raise NetworkPolicyError("路径编码层数过多。")
    if (
        not value.startswith("/") or "\\" in value or "//" in value
        or any(part in {".", ".."} for part in value.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise NetworkPolicyError("路径含歧义分隔符、控制字符或目录穿越。")
    return value


def validate_target(url: str, policies: list[dict], *, robots: bool = False) -> dict:
    """验证协议、主机及规范化路径；每个请求和重定向均调用。"""
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"} or not parts.hostname
        or parts.username or parts.password or parts.fragment
        or any(c.isspace() or ord(c) < 32 for c in url)
        or parts.port not in {None, 80, 443}
    ):
        raise NetworkPolicyError("只接受无凭据的标准端口 HTTP(S) 地址。")
    host = parts.hostname.lower().rstrip(".")
    policy = next((p for p in policies if p["host"].lower().rstrip(".") == host), None)
    if policy is None or policy.get("requires_login"):
        raise NetworkPolicyError("目标主机未声明或需要登录。")
    path = canonical_path(parts.path or "/")
    if robots and path == "/robots.txt":
        return policy
    allowed = [canonical_path(p) for p in policy.get("allowed_paths", [])]
    denied = [canonical_path(p) for p in policy.get("disallowed_paths", [])]
    if any(path.startswith(p) for p in denied) or not any(path.startswith(p) for p in allowed):
        raise NetworkPolicyError("目标路径不在人工策略允许范围内。")
    return policy


def validate_addresses(records: list) -> list:
    """拒绝任何非公网 DNS 结果（包括 IPv4 映射 IPv6）；连接解析时调用。"""
    if not records:
        raise NetworkPolicyError("域名没有可用地址。")
    for entry in records:
        address = ipaddress.ip_address(entry[4][0].split("%", 1)[0])
        address = getattr(address, "ipv4_mapped", None) or address
        if not address.is_global:
            raise NetworkPolicyError("目标解析包含非公网地址。")
    return records


@contextmanager
def guarded_dns():
    """仅由隔离子进程安装实际连接的 DNS 闸门，退出后还原。"""
    original = socket.getaddrinfo

    def resolve(*args, **kwargs):
        """在 socket 建连实际解析点检查全部返回地址。"""
        return validate_addresses(original(*args, **kwargs))

    socket.getaddrinfo = resolve
    try:
        yield
    finally:
        socket.getaddrinfo = original


class HostLimiter:
    """把同主机所有探测、robots、正文和重试串入共享时钟。"""

    def __init__(self, data_root: Path):
        """绑定服务数据根目录；由 transport 构造。"""
        self.paths = DataPaths(data_root)

    def wait(self, host: str, interval: float) -> None:
        """持文件锁等待请求起点并保存时间；所有 HTTP 请求前调用。"""
        digest = hashlib.sha256(host.lower().rstrip(".").encode()).hexdigest()
        lock_path = self.paths.network_host(digest, ".lock")
        state_path = self.paths.network_host(digest, ".json")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path), timeout=300):
            try:
                previous = float(read_json(state_path)["at"])
            except (OSError, ValueError, KeyError, TypeError):
                previous = 0.0
            time.sleep(max(0.0, previous + interval - time.time()))
            write_json(state_path, {"at": time.time()})


class GuardedTransport:
    """提供与 vendor net.fetch 同形的受限、无环境代理 HTTP GET。"""

    def __init__(self, policies: list[dict], data_root: Path, *, delay: float = 0,
                 decoder=None, max_bytes: int = MAX_BODY_BYTES):
        """绑定策略及字节解码器；由桥接入口构造。"""
        self.policies = policies
        self.limiter = HostLimiter(data_root)
        self.delay = delay
        self.decoder = decoder
        self.max_bytes = max_bytes
        self.robots: dict[tuple[str, str], tuple[RobotFileParser, float]] = {}
        self.last_error: dict | None = None

    def _robots_for(self, url: str, timeout: float, user_agent: str) -> float:
        """获取/缓存 robots 并核验当前路径，返回 crawl-delay。"""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        key = (origin, user_agent)
        if key not in self.robots:
            response = self._request(origin + "/robots.txt", timeout, user_agent, robots=True)
            parser = RobotFileParser()
            if response["status"] in {404, 410}:
                parser.parse(["User-agent: *", "Allow: /"])
            elif response["status"] == 200:
                parser.parse(response["text"].splitlines())
            else:
                raise NetworkPolicyError("robots.txt 无法确定，停止请求正文。")
            self.robots[key] = (parser, float(parser.crawl_delay(user_agent) or 0))
        parser, delay = self.robots[key]
        if not parser.can_fetch(user_agent, url):
            raise NetworkPolicyError("robots.txt 禁止目标路径。")
        return delay

    def _read_body(self, response, headers: dict) -> bytes:
        """有界读取并有界解压，避免压缩正文突破内存预算。"""
        raw = response.read(self.max_bytes + 1)
        if len(raw) > self.max_bytes:
            raise NetworkPolicyError("响应超过 50 MB 字节预算。")
        encoding = headers.get("content-encoding", "").lower().strip()
        if encoding not in {"", "identity", "gzip", "deflate"}:
            raise NetworkPolicyError("未支持的压缩编码。")
        if encoding in {"gzip", "deflate"}:
            bits = zlib.MAX_WBITS + 16 if encoding == "gzip" else zlib.MAX_WBITS
            try:
                decoder = zlib.decompressobj(bits)
                body = decoder.decompress(raw, self.max_bytes + 1)
            except zlib.error:
                if encoding != "deflate":
                    raise
                decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                body = decoder.decompress(raw, self.max_bytes + 1)
            if len(body) > self.max_bytes or decoder.unconsumed_tail:
                raise NetworkPolicyError("解压正文超过 50 MB 字节预算。")
            if not decoder.eof or decoder.unused_data:
                raise NetworkPolicyError("压缩正文不完整或含多个压缩成员。")
            return body
        return raw

    def _request(self, url: str, timeout: float, user_agent: str, *, robots: bool = False,
                 crawl_delay: float = 0) -> dict:
        """执行一次 GET，每个重定向重新验证策略、robots 及限速。"""
        transport = self

        class RedirectGuard(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                """在 urllib 发出重定向请求前执行相同闸门。"""
                policy = validate_target(newurl, transport.policies, robots=robots)
                if robots and (urlsplit(newurl).path != "/robots.txt"
                               or urlsplit(newurl).hostname != urlsplit(url).hostname):
                    raise NetworkPolicyError("robots 重定向必须保留同主机标准路径。")
                delay = 0 if robots else transport._robots_for(newurl, timeout, user_agent)
                transport.limiter.wait(urlsplit(newurl).hostname or "",
                                       max(transport.delay, policy.get("min_interval_s", 0), delay))
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        policy = validate_target(url, self.policies, robots=robots)
        self.limiter.wait(urlsplit(url).hostname or "",
                          max(self.delay, policy.get("min_interval_s", 0), crawl_delay))
        opener = build_opener(ProxyHandler({}), RedirectGuard())
        request = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            raw = self._read_body(response, headers)
            text, encoding = self.decoder(raw, headers) if self.decoder else (
                raw.decode("utf-8", "replace"), "utf-8")
            status = response.status if response.status is not None else response.code
            return {"url": url, "final_url": response.geturl(), "status": status,
                    "found": status not in {404, 410}, "raw": raw, "text": text,
                    "encoding": encoding, "headers": headers, "error": None,
                    "transient": False}

    def fetch(self, url: str, timeout: float = 15, retries: int = 0,
              user_agent: str = "IndustryResearch/1.0", proxies=None) -> dict:
        """检查 robots 后抓取；暂时性失败由平台队列按契约统一重试。"""
        self.last_error = None
        try:
            validate_target(url, self.policies, robots=True)
            is_robots = urlsplit(url).path == "/robots.txt"
            delay = 0 if is_robots else self._robots_for(url, timeout, user_agent)
            result = self._request(url, timeout, user_agent, robots=is_robots, crawl_delay=delay)
            if result["status"] >= 400 and not is_robots:
                result["error"] = f"HTTP {result['status']}"
                self.last_error = {"code": "E_HTTP_" + str(result["status"]),
                                   "message": "来源站点返回 HTTP " + str(result["status"]),
                                   "retryable": result["status"] in {408, 429, 500, 502, 503, 504}}
            return result
        except (ValueError, OSError, URLError, zlib.error) as exc:
            code = "E_NETWORK_POLICY" if isinstance(exc, NetworkPolicyError) else "E_CONN"
            self.last_error = {"code": code, "message": str(exc), "retryable": code == "E_CONN"}
            return {"url": url, "final_url": url, "status": None, "found": True,
                    "raw": b"", "text": "", "encoding": None, "headers": {},
                    "error": str(exc), "transient": code == "E_CONN"}
