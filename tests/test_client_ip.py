"""Tests for trust-aware client IP resolution (header spoofing defence)."""
from __future__ import annotations

import ipaddress

import pytest
from starlette.requests import Request

from app.client_ip import (
    DEFAULT_TRUSTED_PROXIES,
    TRUSTED_PROXIES_ENV,
    client_ip_from_request,
    is_trusted_proxy,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _fresh_ip_cache():
    reset_for_tests()
    yield
    reset_for_tests()


def make_request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": raw_headers,
        "client": (peer, 51234) if peer is not None else None,
        "server": ("testserver", 80),
    }
    return Request(scope)


def canon(value: str) -> str:
    return str(ipaddress.ip_address(value))


# ---------------------------------------------------------------------------
# Untrusted peers cannot influence the result
# ---------------------------------------------------------------------------


SPOOF_HEADERS = {
    "cf-connecting-ip": "1.1.1.1",
    "true-client-ip": "2.2.2.2",
    "x-forwarded-for": "3.3.3.3, 4.4.4.4",
    "x-real-ip": "5.5.5.5",
    "forwarded": "for=6.6.6.6",
}


def test_untrusted_peer_ignores_all_proxy_headers():
    resp = make_request("203.0.113.9", SPOOF_HEADERS)
    assert client_ip_from_request(resp) == "203.0.113.9"


def test_untrusted_peer_without_headers_uses_socket_peer():
    assert client_ip_from_request(make_request("198.51.100.20")) == "198.51.100.20"


def test_missing_socket_peer_falls_back_to_headers():
    # No peer to vet (tests / Unix-socket setups): header behaviour is kept.
    req = make_request(None, SPOOF_HEADERS)
    assert client_ip_from_request(req) == "1.1.1.1"
    assert client_ip_from_request(make_request(None)) is None


def test_untrusted_peer_is_canonicalised():
    # Equivalent IPv6 spellings must share a rate-limit bucket.
    req = make_request("2001:0DB8:0000:0000:0000:0000:0000:0001", SPOOF_HEADERS)
    assert client_ip_from_request(req) == "2001:db8::1"
    # Non-IP peers (Starlette TestClient) are passed through untouched.
    assert client_ip_from_request(make_request("testclient")) == "testclient"


# ---------------------------------------------------------------------------
# Trusted peers keep the documented header precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "peer",
    ["127.0.0.1", "::1", "10.4.5.6", "172.18.0.2", "192.168.1.10"],
)
def test_default_trusted_ranges(peer):
    assert is_trusted_proxy(peer)


@pytest.mark.parametrize(
    "peer,headers,expected",
    [
        ("127.0.0.1", SPOOF_HEADERS, "1.1.1.1"),
        (
            "127.0.0.1",
            {k: v for k, v in SPOOF_HEADERS.items() if k != "cf-connecting-ip"},
            "2.2.2.2",
        ),
        (
            "127.0.0.1",
            {"x-forwarded-for": "3.3.3.3, 4.4.4.4", "x-real-ip": "5.5.5.5"},
            "3.3.3.3",
        ),
        ("127.0.0.1", {"x-real-ip": "5.5.5.5"}, "5.5.5.5"),
        ("127.0.0.1", {"forwarded": "for=6.6.6.6;proto=https"}, "6.6.6.6"),
        # Trusted peer, no usable headers → socket peer.
        ("10.0.0.7", {}, "10.0.0.7"),
        # IPv6 peers are matched too.
        ("::1", {"x-forwarded-for": "8.8.8.8"}, "8.8.8.8"),
    ],
)
def test_trusted_peer_header_precedence(peer, headers, expected):
    assert client_ip_from_request(make_request(peer, headers)) == canon(expected)


def test_forwarded_ipv6_literal_and_port_are_parsed():
    req = make_request("127.0.0.1", {"forwarded": 'for="[2001:db8::1]:8080"'})
    assert client_ip_from_request(req) == canon("2001:db8::1")

    req = make_request("127.0.0.1", {"x-real-ip": "203.0.113.7:443"})
    assert client_ip_from_request(req) == "203.0.113.7"


# ---------------------------------------------------------------------------
# Invalid values are skipped, not trusted
# ---------------------------------------------------------------------------


def test_invalid_forwarded_values_are_ignored():
    req = make_request(
        "127.0.0.1",
        {
            "cf-connecting-ip": "not-an-ip",
            "true-client-ip": "_hidden",
            "x-forwarded-for": "unknown, 198.51.100.7, also-bad",
            "x-real-ip": "definitely-invalid",
        },
    )
    assert client_ip_from_request(req) == "198.51.100.7"


def test_all_invalid_headers_fall_back_to_peer():
    req = make_request(
        "127.0.0.1",
        {
            "cf-connecting-ip": "garbage",
            "x-forwarded-for": "garbage, ,",
            "x-real-ip": "",
            "forwarded": "for=_hidden",
        },
    )
    assert client_ip_from_request(req) == "127.0.0.1"


# ---------------------------------------------------------------------------
# QUICKLY_TRUSTED_PROXIES override
# ---------------------------------------------------------------------------


def test_env_override_replaces_defaults(monkeypatch):
    monkeypatch.setenv(TRUSTED_PROXIES_ENV, "203.0.113.0/24")
    reset_for_tests()

    assert is_trusted_proxy("203.0.113.42")
    # Loopback is no longer trusted once the operator pins their own proxy.
    assert not is_trusted_proxy("127.0.0.1")

    trusted = make_request("203.0.113.42", {"x-forwarded-for": "9.9.9.9"})
    assert client_ip_from_request(trusted) == "9.9.9.9"

    untrusted = make_request("127.0.0.1", {"x-forwarded-for": "9.9.9.9"})
    assert client_ip_from_request(untrusted) == "127.0.0.1"


def test_env_override_accepts_multiple_entries_and_plain_ips(monkeypatch):
    monkeypatch.setenv(
        TRUSTED_PROXIES_ENV, " 203.0.113.10 , 2001:db8::/32 ,, "
    )
    reset_for_tests()

    assert is_trusted_proxy("203.0.113.10")
    assert is_trusted_proxy("2001:db8::99")
    assert not is_trusted_proxy("203.0.113.11")


def test_malformed_env_entries_are_skipped(monkeypatch):
    monkeypatch.setenv(TRUSTED_PROXIES_ENV, "not-a-cidr, 10.1.2.3/32, also-bad")
    reset_for_tests()

    assert is_trusted_proxy("10.1.2.3")
    assert not is_trusted_proxy("10.9.9.9")


def test_unparsable_env_fails_closed(monkeypatch):
    monkeypatch.setenv(TRUSTED_PROXIES_ENV, "completely-bogus")
    reset_for_tests()

    assert not is_trusted_proxy("127.0.0.1")
    req = make_request("127.0.0.1", {"x-forwarded-for": "9.9.9.9"})
    assert client_ip_from_request(req) == "127.0.0.1"


def test_blank_env_uses_defaults(monkeypatch):
    monkeypatch.setenv(TRUSTED_PROXIES_ENV, "   ")
    reset_for_tests()

    # Defaults are restored when the variable is blank.
    assert DEFAULT_TRUSTED_PROXIES
    assert is_trusted_proxy("127.0.0.1")
    req = make_request("192.168.0.5", {"cf-connecting-ip": "9.9.9.9"})
    assert client_ip_from_request(req) == "9.9.9.9"
