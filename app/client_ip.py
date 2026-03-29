"""Resolve the original client IP behind reverse proxies and CDNs."""
from __future__ import annotations

# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import re
from starlette.requests import Request

# RFC 7239 Forwarded: for=192.0.2.60, for="[2001:db8::1]", for=_hidden
_FORWARDED_HEADER_FOR = re.compile(
    r'(?i)(?:^|[;,])\s*for=\s*(?:"([^"]+)"|([^;,\s]+))',
)


def _first_in_csv(value: str | None) -> str | None:
    if not value:
        return None
    for part in value.split(","):
        ip = part.strip()
        if ip:
            return ip
    return None


def _from_forwarded(value: str | None) -> str | None:
    if not value:
        return None
    m = _FORWARDED_HEADER_FOR.search(value)
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").strip().strip('"')
    if raw.startswith("[") and "]" in raw:
        return raw[1 : raw.index("]")]
    if "." in raw and raw.count(":") == 1:
        return raw.rsplit(":", 1)[0]
    return raw or None


def client_ip_from_request(request: Request) -> str | None:
    """Best-effort client IP: common proxy/CDN headers, then the socket peer."""
    h = request.headers
    for candidate in (
        h.get("cf-connecting-ip"),
        h.get("true-client-ip"),
        _first_in_csv(h.get("x-forwarded-for")),
        h.get("x-real-ip"),
        _from_forwarded(h.get("forwarded")),
    ):
        if candidate:
            return candidate.strip()
    if request.client:
        return request.client.host
    return None
