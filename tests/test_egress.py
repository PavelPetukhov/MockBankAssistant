"""The egress guard fails closed: unknown hosts are refused before any connection."""

import socket

import pytest

import egress


@pytest.fixture
def guard():
    egress.install()
    yield
    egress.uninstall()


def test_blocks_unlisted_host(guard):
    with pytest.raises(egress.EgressBlocked, match="example.org"):
        socket.getaddrinfo("example.org", 443)


def test_blocks_via_high_level_client(guard):
    import httpx
    with pytest.raises(httpx.ConnectError, match="blocked"):
        httpx.get("https://example.org", timeout=2)


def test_allows_localhost(guard):
    assert socket.getaddrinfo("localhost", 8001)
