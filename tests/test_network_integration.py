"""服务网络闸门的无网络验收。

结构：路径/DNS 边界 → HTTP 重定向/robots → 解压上限。
调用：pytest → network 设施（假 opener/时钟）；数据流：合成响应 → 明确允许或拒绝。
"""

import io
import socket
import zlib

import pytest

from app.common.network import (
    GuardedTransport,
    NetworkPolicyError,
    canonical_path,
    guarded_dns,
    validate_target,
)

POLICIES = [{"host": "example.test", "allowed_paths": ["/public"],
             "disallowed_paths": ["/public/private"], "min_interval_s": 0}]


@pytest.mark.parametrize("url", ["file:///public", "http://127.0.0.1/public",
                                "https://example.test:9000/public", "https://u:p@example.test/public",
                                "https://example.test/public/%2e%2e/private",
                                "https://example.test/public/private/secret"])
def test_target_rejects_unapproved_destinations(url):
    """拒绝未声明主机、凭据、端口与编码穿越；由 pytest 调用。"""
    with pytest.raises((NetworkPolicyError, ValueError)):
        validate_target(url, POLICIES)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1",
                                    "::ffff:127.0.0.1"])
def test_dns_guard_checks_actual_connection(address, monkeypatch):
    """实际解析点拒绝私网/回环，退出后还原；由 pytest 调用。"""
    def resolve(*args, **kwargs):
        """返回合成 DNS 记录；由受测 socket 包装器调用。"""
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with guarded_dns(), pytest.raises(NetworkPolicyError):
        socket.getaddrinfo("example.test", 443)
    assert socket.getaddrinfo is resolve


def test_redirect_revalidates_target(tmp_path, monkeypatch):
    """重定向离开已声明主机时在发出请求前拒绝；由 pytest 调用。"""
    import app.common.network as network

    class Opener:
        """仅触发一次恶意重定向的假出口。"""

        def __init__(self, guard):
            """保存真实重定向守卫；由假 build_opener 调用。"""
            self.guard = guard

        def open(self, request, timeout):
            """模拟来源跳转内网；由受测 transport 调用。"""
            return self.guard.redirect_request(request, None, 302, "redirect", {},
                                               "http://127.0.0.1/public")

    monkeypatch.setattr(network, "build_opener", lambda proxy, guard: Opener(guard))
    transport = GuardedTransport(POLICIES, tmp_path)
    with pytest.raises(NetworkPolicyError):
        transport._request("https://example.test/public", 1, "test")


def test_robots_disallow_prevents_body(tmp_path, monkeypatch):
    """robots 禁止时仅取规则，不请求目标正文；由 pytest 调用。"""
    transport = GuardedTransport(POLICIES, tmp_path)
    calls = []

    def request(url, *args, **kwargs):
        """返回禁止规则并记录请求；由 transport.fetch 调用。"""
        calls.append(url)
        return {"status": 200, "text": "User-agent: *\nDisallow: /public"}

    monkeypatch.setattr(transport, "_request", request)
    response = transport.fetch("https://example.test/public")
    assert calls == ["https://example.test/robots.txt"]
    assert response["error"] and not response["transient"]


def test_bounded_decompression(tmp_path):
    """解压后超预算的正文不能进入内存/材料；由 pytest 调用。"""
    transport = GuardedTransport(POLICIES, tmp_path, max_bytes=1000)
    with pytest.raises(NetworkPolicyError):
        transport._read_body(io.BytesIO(zlib.compress(b"x" * 10000)), {"content-encoding": "deflate"})
    assert canonical_path("/public/%61") == "/public/a"


def test_http_failure_is_not_successful_material(tmp_path, monkeypatch):
    """404 不当成功正文，503 明确可重试；由 pytest 调用。"""
    transport = GuardedTransport(POLICIES, tmp_path)
    monkeypatch.setattr(transport, "_robots_for", lambda *args: 0)
    for status, retryable in [(404, False), (503, True)]:
        monkeypatch.setattr(transport, "_request", lambda *a, **kw: {"status": status})
        result = transport.fetch("https://example.test/public")
        assert result["error"]
        assert transport.last_error["retryable"] is retryable
