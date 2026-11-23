"""Security utilities — SSRF protection, security headers, Fernet encryption, input validation.

Combines the best of both security approaches:
- Comprehensive SSRF blocked-network list (19 ranges, RFC-complete)
- Named hostname blocklist for cloud metadata endpoints
- SecurityHeadersMiddleware with proper CSP, correct X-XSS-Protection, and reverse-proxy HSTS
- Fernet encryption helpers for sensitive data at rest
- EncryptedText SQLAlchemy TypeDecorator for transparent column encryption
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import os
import re
import secrets
import socket
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("quickly.security")

# ---------------------------------------------------------------------------
# Fernet encryption for sensitive data at rest (OAuth tokens, etc.)
# ---------------------------------------------------------------------------

_ENCRYPTION_KEY_ENV: str = os.getenv("QUICKLY_ENCRYPTION_KEY", "")
_fernet: Fernet | None = None


def _derive_key(raw: str) -> bytes:
    """Derive a 32-byte Fernet key from an arbitrary passphrase."""
    digest = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def init_encryption(key: str | None = None) -> None:
    """Initialise the module-level Fernet instance.

    *key* may be a raw passphrase (any length) or a valid Fernet key.
    If *key* is ``None``, ``QUICKLY_ENCRYPTION_KEY`` env var is used.
    If neither is set, encryption is disabled and sensitive columns are
    stored as plaintext (development/migration fallback).
    """
    global _fernet
    raw = key or _ENCRYPTION_KEY_ENV
    if not raw:
        log.warning(
            "QUICKLY_ENCRYPTION_KEY not set – sensitive columns (including SMTP/IMAP "
            "mailbox and relay passwords) will be stored as PLAINTEXT. Set the key "
            "before going to production. Generate one with: python -c \"from "
            "cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        _fernet = None
        return
    try:
        fernet_key = _derive_key(raw)
        _fernet = Fernet(fernet_key)
        log.info("Encryption initialised")
    except Exception:
        log.exception("Failed to initialise Fernet encryption")
        _fernet = None


# Initialise at import time using the environment variable (if set).
init_encryption()


def generate_encryption_key() -> str:
    """Generate a random URL-safe key suitable for ``QUICKLY_ENCRYPTION_KEY``."""
    return secrets.token_urlsafe(32)


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* and return a base64-encoded ciphertext string.

    Returns *plaintext* unchanged when encryption is not initialised.
    """
    if _fernet is None or not plaintext:
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt *ciphertext* and return the original plaintext.

    If decryption fails (e.g. data stored before encryption was enabled),
    the original value is returned as-is so migration is non-destructive.
    """
    if _fernet is None or not ciphertext:
        return ciphertext
    # Fast path for legacy plaintext rows and pre-restore dumps: a Fernet
    # token always starts with ``gAAAAA``, so anything else is not ciphertext
    # and must be returned unchanged (never lose an older stored value).
    if not is_encrypted(ciphertext):
        return ciphertext
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return ciphertext


def is_encrypted(value: str) -> bool:
    """Heuristic: Fernet tokens always start with ``gAAAAA``."""
    return bool(value) and value.startswith("gAAAAA")


# ---------------------------------------------------------------------------
# SQLAlchemy TypeDecorator for transparent column encryption
# ---------------------------------------------------------------------------
from sqlalchemy import Text, TypeDecorator  # noqa: E402 – import after stdlib section


class EncryptedText(TypeDecorator):
    """Column type that transparently encrypts on write and decrypts on read.

    Falls through as plain text when encryption is not configured, making
    development and migration seamless.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return decrypt(value)


# ---------------------------------------------------------------------------
# SSRF protection — comprehensive private / reserved IP ranges
#
# Covers RFC 1918 (private), RFC 5737 (documentation/test), RFC 6598
# (shared address space), loopback, link-local, cloud metadata, multicast,
# broadcast, and the IPv6 equivalents.
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),        # "This" network (RFC 1122)
    ipaddress.ip_network("10.0.0.0/8"),        # Private (RFC 1918)
    ipaddress.ip_network("100.64.0.0/10"),     # Shared address space (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),     # Private (RFC 1918)
    ipaddress.ip_network("192.0.0.0/24"),      # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("192.168.0.0/16"),    # Private (RFC 1918)
    ipaddress.ip_network("198.18.0.0/15"),     # Benchmarking (RFC 2544)
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2 (RFC 5737)
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3 (RFC 5737)
    ipaddress.ip_network("224.0.0.0/4"),       # Multicast (RFC 5771)
    ipaddress.ip_network("240.0.0.0/4"),       # Reserved (RFC 1112)
    ipaddress.ip_network("255.255.255.255/32"),# Broadcast
    # IPv6
    ipaddress.ip_network("::1/128"),           # Loopback
    ipaddress.ip_network("fc00::/7"),          # Unique-local
    ipaddress.ip_network("fe80::/10"),         # Link-local
    ipaddress.ip_network("ff00::/8"),          # Multicast
]

# IPv6 transition / embedding prefixes that can smuggle an IPv4 target past a
# naive check.  All of them are blocked wholesale — they are either deprecated
# or only ever used to reach an embedded IPv4 address.
_IPV4_COMPAT_NETWORK = ipaddress.ip_network("::/96")           # ::a.b.c.d (deprecated)
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")          # NAT64 (RFC 6052)
_NAT64_LOCAL_NETWORK = ipaddress.ip_network("64:ff9b:1::/48")  # NAT64 local-use (RFC 8215)
_SIX_TO_FOUR_NETWORK = ipaddress.ip_network("2002::/16")       # 6to4 (RFC 3056)

# Hostnames that should be blocked before DNS resolution (cloud metadata endpoints).
_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.internal",
    "169.254.169.254",
}

# Loopback hostnames.  Plain-HTTP webhooks are tolerated for these during
# development only when QUICKLY_WEBHOOK_ALLOW_LOCALHOST is explicitly enabled.
_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def _allow_localhost_webhook_targets() -> bool:
    """Dev escape hatch: allow loopback webhook targets when explicitly enabled."""
    return os.getenv("QUICKLY_WEBHOOK_ALLOW_LOCALHOST", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True when *addr* is private/reserved or hides one behind a prefix."""
    if isinstance(addr, ipaddress.IPv6Address):
        # IPv4-mapped (::ffff:a.b.c.d) — inspect the embedded IPv4 address.
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return _is_blocked_ip(mapped)
        # NAT64 — embeds an IPv4 address in the low 32 bits.
        if addr in _NAT64_NETWORK or addr in _NAT64_LOCAL_NETWORK:
            return True
        # 6to4 — embeds an IPv4 address in bits 16..48; deprecated and
        # unreachable in practice, so block the whole prefix.
        if addr in _SIX_TO_FOUR_NETWORK:
            return True
        # Deprecated IPv4-compatible form ::a.b.c.d — unwrap and inspect.
        if addr in _IPV4_COMPAT_NETWORK and int(addr) != 0:
            return _is_blocked_ip(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))

    if any(addr in net for net in _BLOCKED_NETWORKS):
        return True
    # Belt-and-braces: the stdlib flags ranges that the explicit list may miss.
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def is_private_ip(ip_str: str) -> bool:
    """Return True if *ip_str* is a private, reserved or otherwise unsafe address.

    Handles IPv4-mapped IPv6 (``::ffff:127.0.0.1``), NAT64
    (``64:ff9b::/96``), 6to4 (``2002::/16``) and IPv4-compatible (``::a.b.c.d``)
    forms as well as the classic IPv4/IPv6 private and reserved ranges.
    """
    try:
        addr = ipaddress.ip_address(str(ip_str).strip())
    except ValueError:
        return True  # unparseable → block
    return _is_blocked_ip(addr)


def resolve_and_check(hostname: str, *, strict_dns: bool = True) -> tuple[bool, str]:
    """Resolve *hostname* and return ``(safe, reason_or_ip)``.

    Returns ``(False, reason)`` when the hostname points to a blocked address.
    When *strict_dns* is true (the default) a DNS failure is also a rejection —
    use it immediately before an outbound request.  When it is false (e.g.
    validating a configured URL at save time) an unresolvable hostname is
    allowed through: it cannot be a private target right now, and the
    dispatch-time check will resolve it again.
    """
    hostname = (hostname or "").strip().lower().strip("[]").rstrip(".")
    if not hostname:
        return False, "Empty hostname"
    if hostname in _BLOCKED_HOSTNAMES:
        return False, f"Blocked hostname: {hostname}"

    # Fast path: raw IP address
    try:
        addr = ipaddress.ip_address(hostname)
        if is_private_ip(str(addr)):
            return False, f"Private IP not allowed: {hostname}"
        return True, str(addr)
    except ValueError:
        pass  # not a raw IP — fall through to DNS

    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        if strict_dns:
            return False, f"DNS resolution failed for {hostname}"
        return True, hostname

    for _family, _type, _proto, _canon, sockaddr in results:
        ip_str = sockaddr[0]
        if is_private_ip(ip_str):
            return False, f"Hostname {hostname} resolves to private IP {ip_str}"

    if not results:
        if strict_dns:
            return False, f"No DNS results for {hostname}"
        return True, hostname
    return True, results[0][4][0]


def validate_url_not_ssrf(url: str) -> tuple[bool, str]:
    """Validate that *url* does not target a private/internal address.

    Returns ``(ok, error_message)``.  ``ok`` is True when the URL is safe.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"
    return resolve_and_check(hostname)


# ---------------------------------------------------------------------------
# Webhook URL validator
# ---------------------------------------------------------------------------

def validate_webhook_url(
    url: str,
    allow_http_localhost: bool = True,
    *,
    strict_dns: bool = True,
) -> str | None:
    """Return an error string if *url* is not an acceptable webhook target.

    Returns ``None`` when the URL is safe.

    *strict_dns* controls whether a DNS lookup failure rejects the URL: pass
    ``False`` for save-time validation (the host may not resolve yet) and keep
    the default ``True`` immediately before an outbound request.
    """
    parsed = urlparse(url if isinstance(url, str) else "")
    if parsed.scheme not in ("http", "https"):
        return "URL scheme must be http or https"
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return "URL must include a hostname"
    is_loopback_host = hostname in _LOOPBACK_HOSTNAMES
    if parsed.scheme == "http":
        if allow_http_localhost and is_loopback_host:
            pass  # existing dev exception: plain HTTP is tolerated for localhost
        else:
            return "Production webhooks must use https"
    if is_loopback_host:
        # Loopback is an SSRF target even over http.  Development can opt back
        # in with QUICKLY_WEBHOOK_ALLOW_LOCALHOST=true, in which case the
        # scheme was already validated above and no DNS lookup is needed.
        if not _allow_localhost_webhook_targets():
            return "Localhost webhook targets are not allowed"
        return None
    safe, reason = resolve_and_check(hostname, strict_dns=strict_dns)
    if not safe:
        return reason
    return None


# ---------------------------------------------------------------------------
# Hostname validation for tracking domains
# ---------------------------------------------------------------------------

_HOSTNAME_RE = re.compile(r"^(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.[a-zA-Z0-9-]{1,63})*\.[a-zA-Z]{2,}$")


def is_valid_hostname(hostname: str) -> bool:
    """Return True if *hostname* looks like a valid public domain name."""
    return bool(_HOSTNAME_RE.match(hostname)) and len(hostname) <= 253


# ---------------------------------------------------------------------------
# Security headers middleware
#
# - CSP that prevents inline script injection
# - X-XSS-Protection: 0 (correct — the old "1; mode=block" triggers legacy browser bugs)
# - HSTS detects reverse-proxy HTTPS via x-forwarded-proto
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com https://static.cloudflareinsights.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self' https://cloudflareinsights.com; "
            "font-src 'self'; "
            "frame-ancestors 'none'"
        )
        # Detect HTTPS whether the server terminates TLS directly or behind a proxy.
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
