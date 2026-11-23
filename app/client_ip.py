"""Resolve the original client IP behind reverse proxies and CDNs.

Proxy/CDN headers (``CF-Connecting-IP``, ``True-Client-IP``,
``X-Forwarded-For``, ``X-Real-IP``, ``Forwarded``) are entirely client
controlled unless the request actually arrived from a proxy we operate.  This
module therefore only honours them when the *socket peer* is inside a trusted
proxy range; every other request is keyed on the peer address, so a spoofed
``X-Forwarded-For`` cannot change rate-limit buckets, tracking rows or logs.

Trust is configured with ``QUICKLY_TRUSTED_PROXIES`` (comma-separated IPs or
CIDRs, read on each call).  When unset, loopback plus RFC1918 ranges are
trusted — that covers the stock Docker/Caddy Compose files, where Caddy reaches
the app over a private bridge.  **Operators should pin their proxy** (for
example ``QUICKLY_TRUSTED_PROXIES=203.0.113.10``) so an arbitrary host on the
same private network cannot forge headers.  Setting the variable replaces the
defaults entirely; unparsable entries are ignored (fail closed — a value that
parses to nothing trusts no peer).
"""
from __future__ import annotations

# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import ipaddress
import os
import re
from typing import Union

from starlette.requests import Request

TRUSTED_PROXIES_ENV = "QUICKLY_TRUSTED_PROXIES"

# Stock Docker + Caddy deployments talk to the app over loopback or a private
# bridge; these defaults keep header resolution working out of the box.
DEFAULT_TRUSTED_PROXIES: tuple[str, ...] = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)

IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# RFC 7239 Forwarded: for=192.0.2.60, for="[2001:db8::1]", for=_hidden
_FORWARDED_HEADER_FOR = re.compile(
    r'(?i)(?:^|[;,])\s*for=\s*(?:"([^"]+)"|([^;,\s]+))',
)

# (raw env value, parsed networks) so we parse at most once per env change.
_networks_cache: tuple[str | None, tuple[IPNetwork, ...]] | None = None


def _parse_networks(raw: str) -> tuple[IPNetwork, ...]:
    """Parse a comma-separated list, silently dropping invalid entries."""
    networks: list[IPNetwork] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            # Hostnames, typos, bracketed literals, etc. — ignore rather than
            # crashing the request path.
            continue
    return tuple(networks)


def _trusted_networks() -> tuple[IPNetwork, ...]:
    """Trusted proxy ranges for the current environment (cached per value)."""
    global _networks_cache
    raw = os.getenv(TRUSTED_PROXIES_ENV)
    if _networks_cache is not None and _networks_cache[0] == raw:
        return _networks_cache[1]
    if raw is None or not raw.strip():
        networks = _parse_networks(",".join(DEFAULT_TRUSTED_PROXIES))
    else:
        networks = _parse_networks(raw)
    _networks_cache = (raw, networks)
    return networks


def reset_for_tests() -> None:
    """Drop cached trusted ranges (tests change the env between cases)."""
    global _networks_cache
    _networks_cache = None


def parse_ip(value: str | None) -> IPAddress | None:
    """Parse one address, tolerating quotes, brackets and ``ip:port`` forms."""
    if not value:
        return None
    raw = value.strip().strip('"')
    if not raw:
        return None
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        pass
    # ``192.0.2.10:443`` (Forwarded / some proxies).  IPv6 literals already
    # failed above, so a single colon plus a dot is a v4 address with a port.
    if raw.count(":") == 1 and "." in raw:
        try:
            return ipaddress.ip_address(raw.rsplit(":", 1)[0])
        except ValueError:
            return None
    return None


def is_trusted_proxy(host: str | None) -> bool:
    """True when *host* (the socket peer) is inside a trusted proxy range."""
    address = parse_ip(host)
    if address is None:
        return False
    for network in _trusted_networks():
        if address.version == network.version and address in network:
            return True
    return False


def _normalized(value: str | None) -> str | None:
    """Return a canonical IP string, or ``None`` when *value* is not an IP."""
    address = parse_ip(value)
    return str(address) if address is not None else None


def _first_valid_in_csv(value: str | None) -> str | None:
    if not value:
        return None
    for part in value.split(","):
        candidate = _normalized(part)
        if candidate:
            return candidate
    return None


def _from_forwarded(value: str | None) -> str | None:
    if not value:
        return None
    for match in _FORWARDED_HEADER_FOR.finditer(value):
        candidate = _normalized(match.group(1) or match.group(2))
        if candidate:
            return candidate
    return None


def client_ip_from_request(request: Request) -> str | None:
    """Best-effort client IP: proxy/CDN headers from trusted peers only.

    Header precedence for trusted peers is unchanged: ``CF-Connecting-IP``,
    ``True-Client-IP``, the first valid ``X-Forwarded-For`` entry,
    ``X-Real-IP``, then RFC 7239 ``Forwarded``.  Invalid header values are
    skipped.  Requests from anywhere else are keyed on the socket peer, so
    client-supplied headers are ignored.
    """
    peer = request.client.host if request.client else None
    # Real ASGI servers always expose the socket peer, so ``peer is None`` is
    # only reachable from tests or Unix-socket setups; with nothing to check we
    # keep the historical header behaviour.
    if peer is None or is_trusted_proxy(peer):
        h = request.headers
        for candidate in (
            _normalized(h.get("cf-connecting-ip")),
            _normalized(h.get("true-client-ip")),
            _first_valid_in_csv(h.get("x-forwarded-for")),
            _normalized(h.get("x-real-ip")),
            _from_forwarded(h.get("forwarded")),
        ):
            if candidate:
                return candidate
    if peer:
        # Canonicalise the peer too, so equivalent IPv6 spellings share a
        # rate-limit bucket; non-IP peers (TestClient's "testclient") pass
        # through unchanged.
        return _normalized(peer) or peer
    return None
