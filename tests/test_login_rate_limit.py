"""HTTP-level tests for login / registration brute-force rate limiting.

The limits are exercised through the real FastAPI app (TestClient) with real
database rows, and each TestClient carries a distinct socket peer so per-IP
and per-account buckets can be told apart.  Headers such as X-Forwarded-For
must *not* let a public peer pick its own bucket.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import login_rate_limit as lrl
from app import setup_code
from app.login_rate_limit import (
    LOGIN_RATE_LIMIT_DETAIL,
    REGISTER_RATE_LIMIT_DETAIL,
)

SETUP_CODE_RAW = "TEST-CODE-1234"

ADMIN = {
    "username": "firstadmin",
    "email": "firstadmin@example.com",
    "password": "Password1",
}


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Pin the setup code and wipe limiter state before every test."""
    monkeypatch.setenv(setup_code.ENV_VAR, SETUP_CODE_RAW)
    setup_code.reset_for_tests()
    lrl.reset_for_tests()
    yield
    setup_code.reset_for_tests()
    lrl.reset_for_tests()


@pytest.fixture
def client_factory(engine, monkeypatch):
    """Build TestClients with distinct socket peers on this test's database."""
    from app import database as adb
    from app.main import app
    from app.routers import auth as auth_routes

    monkeypatch.setattr(auth_routes, "AsyncSessionLocal", adb.AsyncSessionLocal)

    def factory(ip: str = "203.0.113.10") -> TestClient:
        return TestClient(app, client=(ip, 50000))

    return factory


def _register(client: TestClient, setup: str = SETUP_CODE_RAW):
    return client.post("/api/auth/register", json={**ADMIN, "setup_code": setup})


def _login(client: TestClient, identifier: str, password: str):
    return client.post(
        "/api/auth/login",
        json={"username": identifier, "password": password},
    )


def _limits(monkeypatch, *, per_ip: str, per_account: str = "100"):
    monkeypatch.setenv(lrl.MAX_FAILURES_PER_IP_ENV, per_ip)
    monkeypatch.setenv(lrl.MAX_FAILURES_PER_ACCOUNT_ENV, per_account)
    monkeypatch.setenv(lrl.WINDOW_ENV, "300")
    monkeypatch.setenv(lrl.ACCOUNT_WINDOW_ENV, "900")
    monkeypatch.setenv(lrl.LOCK_ENV, "300")


def _assert_429(resp, detail: str):
    assert resp.status_code == 429, resp.text
    retry_after = resp.headers.get("retry-after")
    assert retry_after is not None
    assert int(retry_after) >= 1
    assert resp.json()["detail"] == detail


# ---------------------------------------------------------------------------
# Login: per-IP window
# ---------------------------------------------------------------------------


def test_ip_limit_blocks_after_max_failures(monkeypatch, client_factory):
    _limits(monkeypatch, per_ip="3")
    client = client_factory("203.0.113.1")
    assert _register(client).status_code == 201

    for _ in range(3):
        resp = _login(client, "firstadmin", "WrongPass1")
        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"] == "Invalid credentials"

    # The 4th attempt is rejected before credentials are checked: even the
    # right password is refused while the bucket is locked.
    locked = _login(client, "firstadmin", "Password1")
    _assert_429(locked, LOGIN_RATE_LIMIT_DETAIL)
    assert "firstadmin" not in locked.text
    assert "example.com" not in locked.text


def test_successful_login_clears_ip_and_account_buckets(monkeypatch, client_factory):
    _limits(monkeypatch, per_ip="3")
    client = client_factory("203.0.113.2")
    assert _register(client).status_code == 201

    for _ in range(2):
        assert _login(client, "firstadmin", "WrongPass1").status_code == 401

    assert _login(client, "firstadmin", "Password1").status_code == 200

    # Budget is fresh again: three more failures still answer 401.
    for _ in range(3):
        assert _login(client, "firstadmin", "WrongPass1").status_code == 401
    _assert_429(
        _login(client, "firstadmin", "WrongPass1"), LOGIN_RATE_LIMIT_DETAIL
    )


def test_spoofed_x_forwarded_for_cannot_pick_a_new_bucket(monkeypatch, client_factory):
    _limits(monkeypatch, per_ip="2")
    client = client_factory("203.0.113.50")
    assert _register(client).status_code == 201

    for i in range(2):
        resp = client.post(
            "/api/auth/login",
            json={"username": "firstadmin", "password": "WrongPass1"},
            headers={"X-Forwarded-For": f"10.9.9.{i}", "CF-Connecting-IP": f"10.8.8.{i}"},
        )
        assert resp.status_code == 401, resp.text

    spoofed = client.post(
        "/api/auth/login",
        json={"username": "firstadmin", "password": "WrongPass1"},
        headers={"X-Forwarded-For": "10.7.7.7"},
    )
    _assert_429(spoofed, LOGIN_RATE_LIMIT_DETAIL)


# ---------------------------------------------------------------------------
# Login: per-account window
# ---------------------------------------------------------------------------


def test_account_lock_applies_across_ips_and_identifier_case(monkeypatch, client_factory):
    _limits(monkeypatch, per_ip="100", per_account="2")
    registration = client_factory("203.0.113.3")
    assert _register(registration).status_code == 201

    attacker = client_factory("198.51.100.1")
    for _ in range(2):
        resp = _login(attacker, "FIRSTADMIN@EXAMPLE.COM", "WrongPass1")
        assert resp.status_code == 401, resp.text

    # A different IP (and a different casing of the same identifier) cannot
    # bypass the account lock; a valid password is still refused.
    other = client_factory("198.51.100.2")
    _assert_429(
        _login(other, "FirstAdmin@Example.com", "Password1"),
        LOGIN_RATE_LIMIT_DETAIL,
    )


def test_login_lock_expires(monkeypatch, client_factory):
    _limits(monkeypatch, per_ip="2")
    clock = [1_000_000.0]
    monkeypatch.setattr(lrl, "_now", lambda: clock[0])

    client = client_factory("203.0.113.4")
    # Registration is done through the API, so it happens on the clock too.
    assert _register(client).status_code == 201

    for _ in range(2):
        assert _login(client, "firstadmin", "WrongPass1").status_code == 401
    _assert_429(
        _login(client, "firstadmin", "Password1"), LOGIN_RATE_LIMIT_DETAIL
    )

    # Past the 300 s lock: the bucket is fresh and a valid login succeeds.
    clock[0] += 301
    assert _login(client, "firstadmin", "Password1").status_code == 200


# ---------------------------------------------------------------------------
# No account enumeration
# ---------------------------------------------------------------------------


def test_401_and_429_bodies_do_not_reveal_account_existence(
    monkeypatch, client_factory
):
    _limits(monkeypatch, per_ip="100", per_account="1")
    client = client_factory("203.0.113.5")
    assert _register(client).status_code == 201

    unknown_401 = _login(client, "nobody-here", "WrongPass1")
    existing_401 = _login(client, "firstadmin", "WrongPass1")
    assert unknown_401.status_code == existing_401.status_code == 401
    assert unknown_401.json() == existing_401.json()
    assert unknown_401.text == existing_401.text

    unknown_429 = _login(client, "nobody-here", "WrongPass1")
    existing_429 = _login(client, "firstadmin", "WrongPass1")
    _assert_429(unknown_429, LOGIN_RATE_LIMIT_DETAIL)
    _assert_429(existing_429, LOGIN_RATE_LIMIT_DETAIL)
    assert unknown_429.json() == existing_429.json()
    assert unknown_429.text == existing_429.text


# ---------------------------------------------------------------------------
# Registration: per-IP window
# ---------------------------------------------------------------------------


def test_register_ip_limit_blocks_setup_code_guessing(monkeypatch, client_factory):
    _limits(monkeypatch, per_ip="2")
    attacker = client_factory("203.0.113.6")

    for _ in range(2):
        resp = _register(attacker, setup="WRONG-CODE")
        assert resp.status_code == 403, resp.text

    # Even the real code is refused while the IP bucket is locked.
    blocked = _register(attacker, setup=SETUP_CODE_RAW)
    _assert_429(blocked, REGISTER_RATE_LIMIT_DETAIL)

    # Another IP is unaffected and can complete the first-admin setup.
    other = client_factory("198.51.100.20")
    assert _register(other).status_code == 201


def test_register_success_clears_ip_bucket(monkeypatch):
    monkeypatch.setenv(lrl.MAX_FAILURES_PER_IP_ENV, "1")
    lrl.reset_for_tests()

    lrl.record_register_failure("203.0.113.7")
    assert lrl.check_register("203.0.113.7") is not None

    lrl.clear_register("203.0.113.7")
    assert lrl.check_register("203.0.113.7") is None


# ---------------------------------------------------------------------------
# Module-level building blocks
# ---------------------------------------------------------------------------


def test_identifier_normalization_is_case_and_space_insensitive():
    assert lrl.normalize_identifier("  FirstAdmin@Example.COM ") == (
        "firstadmin@example.com"
    )
    assert lrl.normalize_identifier(None) == ""
    assert lrl.normalize_identifier("") == ""


def test_pruning_drops_stale_buckets(monkeypatch):
    monkeypatch.setenv(lrl.MAX_FAILURES_PER_IP_ENV, "3")
    clock = [5_000_000.0]
    monkeypatch.setattr(lrl, "_now", lambda: clock[0])

    lrl.record_login_failure("203.0.113.8", "firstadmin")
    assert lrl._login_by_ip  # noqa: SLF001 - intentional white-box assertion

    clock[0] += 3600
    assert lrl.check_login("203.0.113.8", "firstadmin") is None
    assert not lrl._login_by_ip  # noqa: SLF001
    assert not lrl._login_by_identifier  # noqa: SLF001
