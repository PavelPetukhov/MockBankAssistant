"""Fail-closed egress guard: block outbound connections to hosts not in the allowlist.

Every hostname-based connection in Python (httpx, requests, asyncio, urllib) resolves the
host through socket.getaddrinfo, so wrapping it is enough to stop calls to anything outside
config.EGRESS_ALLOWLIST. A blocked call raises EgressBlocked instead of connecting.

Call install() once at process start, before any network client is created.
"""

import socket

import config

_original_getaddrinfo = socket.getaddrinfo


class EgressBlocked(ConnectionError):
    pass


def _guarded_getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if name is not None and name not in config.EGRESS_ALLOWLIST:
        raise EgressBlocked(f"egress to {name!r} blocked: not in EGRESS_ALLOWLIST")
    return _original_getaddrinfo(host, *args, **kwargs)


def install() -> None:
    socket.getaddrinfo = _guarded_getaddrinfo


def uninstall() -> None:
    socket.getaddrinfo = _original_getaddrinfo
