"""HTTP-level tests for the one-time setup-code gates.

Covers the public contract the frontend builds against:
  * GET /api/auth/setup-status shape (and that the code never leaks)
  * POST /api/auth/register requires the code for the first user
  * login with a username *or* an email
  * pre-setup restore metadata/preview/execute gates
  * OAuth authorize gate before setup, ignored after setup
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import setup_code
from app.settings_manager import settings

SETUP_CODE_RAW = "TEST-CODE-1234"
SETUP_CODE_NORMALIZED = "TESTCODE1234"
DETAIL = "Invalid or missing setup code. Find it in the server logs."


@pytest.fixture(autouse=True)
def _known_setup_code(monkeypatch):
    """Pin the code and reset module state for every test."""
    monkeypatch.setenv(setup_code.ENV_VAR, SETUP_CODE_RAW)
    setup_code.reset_for_tests()
    # Keep the rate limiters from leaking between tests/runs.
    from app import login_rate_limit, restore_preview_rate

    restore_preview_rate._by_ip.clear()
    login_rate_limit.reset_for_tests()
    yield
    setup_code.reset_for_tests()
    login_rate_limit.reset_for_tests()


@pytest.fixture
def client(engine, monkeypatch):
    """Client bound to this test's isolated DB.

    Deliberately not used as a context manager: the app lifespan's scheduler
    startup is orthogonal here and re-entering it per test fights the test
    engine.  The ``engine`` fixture already created the schema; we only
    re-point the sessionmaker captured at import time by the restore routes.
    """
    from app import database as adb
    from app.main import app
    from app.routers import auth as auth_routes

    monkeypatch.setattr(auth_routes, "AsyncSessionLocal", adb.AsyncSessionLocal)
    return TestClient(app)


REGISTER_BODY = {
    "username": "firstadmin",
    "email": "firstadmin@example.com",
    "password": "Password1",
}


def _register(client, **overrides):
    body = {**REGISTER_BODY, **overrides}
    return client.post("/api/auth/register", json=body)


# ---------------------------------------------------------------------------
# setup-status
# ---------------------------------------------------------------------------


def test_setup_status_shape_and_no_code_leak(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "google-id")
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(settings, "office365_client_id", "")
    monkeypatch.setattr(settings, "office365_client_secret", "")

    resp = client.get("/api/auth/setup-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "setup_complete": False,
        "setup_code_required": True,
        "auth_providers": {"google": True, "microsoft": False},
    }
    # The code itself (or any secret) is never returned.
    raw_text = resp.text
    assert SETUP_CODE_NORMALIZED not in raw_text
    assert "google-secret" not in raw_text
    assert "setup_code\"" not in raw_text


def test_setup_status_reflects_microsoft(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")
    monkeypatch.setattr(settings, "office365_client_id", "ms-id")
    monkeypatch.setattr(settings, "office365_client_secret", "ms-secret")

    data = client.get("/api/auth/setup-status").json()
    assert data["auth_providers"] == {"google": False, "microsoft": True}


# ---------------------------------------------------------------------------
# setup-code verify (what the login page calls before showing any form)
# ---------------------------------------------------------------------------


def test_verify_setup_code_succeeds_without_consuming_it(client):
    resp = client.post("/api/auth/setup-code/verify", json={"code": SETUP_CODE_RAW})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verifying is not consuming: the real register gate still accepts it.
    assert _register(client, setup_code=SETUP_CODE_RAW).status_code == 201


def test_verify_setup_code_rejects_wrong_code(client):
    resp = client.post("/api/auth/setup-code/verify", json={"code": "NOPE-NOPE"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == DETAIL
    assert client.get("/api/auth/setup-status").json()["setup_complete"] is False


def test_verify_setup_code_is_rate_limited(client, monkeypatch):
    monkeypatch.setenv("QUICKLY_LOGIN_MAX_FAILURES_PER_IP", "3")
    for _ in range(3):
        assert (
            client.post(
                "/api/auth/setup-code/verify", json={"code": "NOPE-NOPE"}
            ).status_code
            == 403
        )
    resp = client.post("/api/auth/setup-code/verify", json={"code": "NOPE-NOPE"})
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1


def test_verify_setup_code_closed_after_setup(client):
    assert _register(client, setup_code=SETUP_CODE_RAW).status_code == 201
    resp = client.post("/api/auth/setup-code/verify", json={"code": SETUP_CODE_RAW})
    assert resp.status_code == 403
    assert "already complete" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


def test_register_without_code_is_forbidden(client):
    resp = _register(client)
    assert resp.status_code == 403
    assert resp.json()["detail"] == DETAIL

    # Nothing was created.
    assert client.get("/api/auth/setup-status").json()["setup_complete"] is False


def test_register_with_wrong_code_is_forbidden(client):
    resp = _register(client, setup_code="WRON-CODE")
    assert resp.status_code == 403
    assert resp.json()["detail"] == DETAIL


def test_register_with_correct_code_creates_admin_and_consumes_code(client):
    resp = _register(client, setup_code=SETUP_CODE_RAW)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "firstadmin"
    assert body["role"] == "admin"

    # Code is cleared and setup is complete.
    assert setup_code.verify_setup_code(SETUP_CODE_RAW) is False
    assert setup_code.get_code() is None
    status = client.get("/api/auth/setup-status").json()
    assert status["setup_complete"] is True
    assert status["setup_code_required"] is False


def test_hyphen_case_insensitive_code_accepted(client):
    resp = _register(client, setup_code="  test code 1234 ")
    assert resp.status_code == 201, resp.text


def test_second_register_is_closed_even_with_valid_code(client):
    assert _register(client, setup_code=SETUP_CODE_RAW).status_code == 201
    again = _register(
        client,
        username="seconduser",
        email="second@example.com",
        setup_code=SETUP_CODE_RAW,
    )
    assert again.status_code == 403
    assert again.json()["detail"].startswith("Registration closed")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identifier", ["firstadmin", "FIRSTADMIN@EXAMPLE.COM"])
def test_login_by_username_or_email(client, identifier):
    assert _register(client, setup_code=SETUP_CODE_RAW).status_code == 201

    resp = client.post(
        "/api/auth/login",
        json={"username": identifier, "password": "Password1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]
    assert resp.json()["token_type"] == "bearer"
    assert "refresh_token" in resp.cookies


def test_login_wrong_password_is_generic_401(client):
    assert _register(client, setup_code=SETUP_CODE_RAW).status_code == 201
    resp = client.post(
        "/api/auth/login",
        json={"username": "firstadmin", "password": "WrongPass1"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


# ---------------------------------------------------------------------------
# restore-setup gates
# ---------------------------------------------------------------------------


def _dummy_backup() -> bytes:
    return b"not-a-real-backup-" + b"x" * 64


def test_restore_metadata_requires_code_before_reading_file(client):
    missing = client.post(
        "/api/auth/restore-setup/metadata",
        files={"file": ("backup.qbk", _dummy_backup(), "application/octet-stream")},
    )
    assert missing.status_code == 403
    assert missing.json()["detail"] == DETAIL

    wrong = client.post(
        "/api/auth/restore-setup/metadata",
        files={"file": ("backup.qbk", _dummy_backup(), "application/octet-stream")},
        data={"setup_code": "WRONG-CODE"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == DETAIL


def test_restore_preview_requires_code(client):
    resp = client.post(
        "/api/auth/restore-setup/preview",
        files={"file": ("backup.qbk", _dummy_backup(), "application/octet-stream")},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == DETAIL


def test_restore_execute_requires_code_before_consuming_token(client):
    resp = client.post(
        "/api/auth/restore-setup/execute",
        json={"restore_token": "a" * 24},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == DETAIL

    wrong = client.post(
        "/api/auth/restore-setup/execute",
        json={"restore_token": "a" * 24, "setup_code": "WRONG-CODE"},
    )
    assert wrong.status_code == 403


# ---------------------------------------------------------------------------
# OAuth authorize gate
# ---------------------------------------------------------------------------


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "google-id")
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(settings, "office365_client_id", "ms-id")
    monkeypatch.setattr(settings, "office365_client_secret", "ms-secret")


@pytest.mark.parametrize(
    "path",
    ["/oauth/app/google/authorize", "/oauth/app/microsoft/authorize"],
)
def test_oauth_authorize_requires_code_before_setup(client, oauth_configured, path):
    missing = client.get(path, follow_redirects=False)
    assert missing.status_code == 403
    assert missing.json()["detail"] == DETAIL

    wrong = client.get(f"{path}?setup_code=WRONG-CODE", follow_redirects=False)
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == DETAIL


@pytest.mark.parametrize(
    "path,host",
    [
        ("/oauth/app/google/authorize", "accounts.google.com"),
        ("/oauth/app/microsoft/authorize", "login.microsoftonline.com"),
    ],
)
def test_oauth_authorize_with_correct_code_redirects(
    client, oauth_configured, path, host
):
    resp = client.get(
        f"{path}?setup_code={SETUP_CODE_RAW}", follow_redirects=False
    )
    assert resp.status_code in (302, 307), resp.text
    assert host in resp.headers["location"]


def test_oauth_authorize_ignores_code_after_setup(client, oauth_configured):
    assert _register(client, setup_code=SETUP_CODE_RAW).status_code == 201

    # Setup complete: no code needed, parameter is ignored.
    resp = client.get("/oauth/app/google/authorize", follow_redirects=False)
    assert resp.status_code in (302, 307), resp.text
    assert "accounts.google.com" in resp.headers["location"]
