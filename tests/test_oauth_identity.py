"""Tests for app-level OAuth identity handling and notification-token storage.

Covers the nOAuth hardening:
  * Microsoft app login derives ``sub`` from the id_token's ``tid`` + ``oid``
    claims (immutable, tenant-qualified) instead of Graph's tenant-scoped id.
  * Email-based auto-linking only happens for Google when the address is
    verified, and never for Microsoft.
  * Consumer (personal) tenants and configured-tenant mismatches are rejected.

Also verifies that the OAuth notification tokens are encrypted at rest through
``EncryptedText`` while legacy plaintext values stay readable.
"""
from __future__ import annotations

import base64
import json
import secrets
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app import setup_code
from app.models import OAuthState, User
from app.routers import app_oauth
from app.settings_manager import settings
from app.time import utcnow

MS_TENANT = "a1b2c3d4-0000-1111-2222-333344445555"
MS_OID = "00000000-aaaa-bbbb-cccc-111122223333"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_id_token(claims: dict) -> str:
    """Build a JWT-shaped token; only the payload segment is meaningful here."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.signature-not-verified"


async def _add_state(session, purpose: str = "app_login") -> str:
    token = f"state-{secrets.token_hex(8)}"
    session.add(
        OAuthState(
            state_token=token,
            purpose=purpose,
            metadata_json="{}",
            expires_at=utcnow() + timedelta(minutes=5),
        )
    )
    await session.flush()
    return token


@pytest.fixture(autouse=True)
def _reset_setup_code():
    """First-user creation consumes the process-global setup code."""
    setup_code.reset_for_tests()
    yield
    setup_code.reset_for_tests()


@pytest.fixture
def microsoft_configured(monkeypatch):
    monkeypatch.setattr(settings, "office365_client_id", "ms-client")
    monkeypatch.setattr(settings, "office365_client_secret", "ms-secret")
    monkeypatch.setattr(settings, "office365_tenant_id", "common")
    monkeypatch.setattr(settings, "base_url", "https://quickly.test")


def _patch_microsoft_http(monkeypatch, claims: dict | None, email: str = "user@example.com"):
    token_data = {
        "access_token": "ms-access-token",
        "refresh_token": "ms-refresh-token",
        "expires_in": 3600,
    }
    if claims is not None:
        token_data["id_token"] = make_id_token(claims)
    monkeypatch.setattr(
        app_oauth, "_microsoft_exchange_code", lambda *a, **k: token_data
    )
    monkeypatch.setattr(
        app_oauth,
        "_microsoft_userinfo",
        lambda token: {
            # Graph fields are display-only now; ``id`` must not drive identity.
            "id": "graph-object-id",
            "mail": email,
            "userPrincipalName": email,
            "displayName": "Test User",
        },
    )


async def _microsoft_login(session, tenant: str = MS_TENANT, oid: str = MS_OID):
    state = await _add_state(session)
    return await app_oauth.app_microsoft_callback(
        request=None, code="code", state=state, db=session
    )


async def _make_existing_user(session, email: str = "victim@example.com") -> User:
    user = User(
        username="existing",
        email=email,
        password_hash="not-a-real-hash",
        role="admin",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Microsoft identity from id_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_microsoft_sub_is_tenant_and_object_id(
    session, monkeypatch, microsoft_configured
):
    _patch_microsoft_http(monkeypatch, {"tid": MS_TENANT, "oid": MS_OID})

    await _microsoft_login(session)

    result = await session.execute(
        User.__table__.select().where(User.email == "user@example.com")
    )
    row = result.mappings().one()
    assert row["oauth_provider"] == "microsoft"
    assert row["oauth_sub"] == f"{MS_TENANT}:{MS_OID}"
    # Fresh install: the first Microsoft user still becomes the admin.
    assert row["role"] == "admin"


@pytest.mark.asyncio
async def test_microsoft_does_not_email_link_existing_user(
    session, monkeypatch, microsoft_configured
):
    existing = await _make_existing_user(session, "victim@example.com")
    _patch_microsoft_http(
        monkeypatch, {"tid": MS_TENANT, "oid": MS_OID}, email="victim@example.com"
    )

    with pytest.raises(HTTPException) as exc:
        await _microsoft_login(session)

    assert exc.value.status_code == 403
    assert "No account is linked to this Microsoft identity" in exc.value.detail
    # The victim's account was not touched.
    await session.refresh(existing)
    assert existing.oauth_provider is None
    assert existing.oauth_sub is None


@pytest.mark.asyncio
async def test_microsoft_legacy_graph_id_subject_upgrades_in_place(
    session, monkeypatch, microsoft_configured
):
    """An account stored under the old Graph-id subject keeps working."""
    user = User(
        username="legacy",
        email="user@example.com",
        password_hash=None,
        oauth_provider="microsoft",
        oauth_sub="graph-object-id",  # what releases <= v2.6.2 saved
        role="admin",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    _patch_microsoft_http(monkeypatch, {"tid": MS_TENANT, "oid": MS_OID})
    await _microsoft_login(session)

    await session.refresh(user)
    assert user.oauth_sub == f"{MS_TENANT}:{MS_OID}"
    assert user.notif_access_token == "ms-access-token"
    # Still exactly one account: the upgrade never creates a duplicate.
    count = (await session.execute(User.__table__.select())).all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_microsoft_legacy_upgrade_requires_matching_email(
    session, monkeypatch, microsoft_configured
):
    other = User(
        username="someone-else",
        email="someone.else@example.com",
        password_hash=None,
        oauth_provider="microsoft",
        oauth_sub="graph-object-id",
        role="admin",
        is_active=True,
    )
    session.add(other)
    await session.flush()

    _patch_microsoft_http(monkeypatch, {"tid": MS_TENANT, "oid": MS_OID})
    with pytest.raises(HTTPException) as exc:
        await _microsoft_login(session)
    assert exc.value.status_code == 403
    await session.refresh(other)
    assert other.oauth_sub == "graph-object-id"


@pytest.mark.asyncio
async def test_microsoft_consumer_tenant_rejected(
    session, monkeypatch, microsoft_configured
):
    _patch_microsoft_http(
        monkeypatch,
        {"tid": app_oauth.MICROSOFT_CONSUMER_TENANT_ID, "oid": MS_OID},
    )

    with pytest.raises(HTTPException) as exc:
        await _microsoft_login(session, tenant=app_oauth.MICROSOFT_CONSUMER_TENANT_ID)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Sign in with a work or school Microsoft account"


@pytest.mark.asyncio
async def test_microsoft_configured_tenant_mismatch_rejected(
    session, monkeypatch, microsoft_configured
):
    other_tenant = "99999999-9999-9999-9999-999999999999"
    monkeypatch.setattr(settings, "office365_tenant_id", other_tenant)
    _patch_microsoft_http(monkeypatch, {"tid": MS_TENANT, "oid": MS_OID})

    with pytest.raises(HTTPException) as exc:
        await _microsoft_login(session)

    assert exc.value.status_code == 403
    assert "different tenant" in exc.value.detail


@pytest.mark.asyncio
async def test_microsoft_configured_tenant_match_accepted(
    session, monkeypatch, microsoft_configured
):
    monkeypatch.setattr(settings, "office365_tenant_id", MS_TENANT)
    _patch_microsoft_http(monkeypatch, {"tid": MS_TENANT, "oid": MS_OID})

    resp = await _microsoft_login(session)

    assert resp.status_code == 303


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"oid": MS_OID},
        {"tid": MS_TENANT},
        {},
    ],
)
async def test_microsoft_missing_tid_or_oid_rejected(
    session, monkeypatch, microsoft_configured, claims
):
    _patch_microsoft_http(monkeypatch, claims)

    with pytest.raises(HTTPException) as exc:
        await _microsoft_login(session)

    assert exc.value.status_code == 502
    assert "tid" in exc.value.detail and "oid" in exc.value.detail


@pytest.mark.asyncio
async def test_microsoft_missing_id_token_rejected(
    session, monkeypatch, microsoft_configured
):
    _patch_microsoft_http(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        await _microsoft_login(session)

    assert exc.value.status_code == 502
    assert "id_token" in exc.value.detail


# ---------------------------------------------------------------------------
# Google email linking requires a verified address
# ---------------------------------------------------------------------------


def _patch_google_http(monkeypatch, email: str, verified: bool):
    monkeypatch.setattr(settings, "google_client_id", "google-client")
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(settings, "base_url", "https://quickly.test")
    monkeypatch.setattr(
        app_oauth,
        "_google_exchange_code",
        lambda *a, **k: {
            "access_token": "google-access-token",
            "refresh_token": "google-refresh-token",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        app_oauth,
        "_google_userinfo",
        lambda token: {
            "sub": "google-sub-123",
            "email": email,
            "email_verified": verified,
            "name": "Google User",
        },
    )


async def _google_login(session):
    state = await _add_state(session)
    return await app_oauth.app_google_callback(
        request=None, code="code", state=state, db=session
    )


@pytest.mark.asyncio
async def test_google_unverified_email_does_not_link(session, monkeypatch):
    existing = await _make_existing_user(session, "victim@example.com")
    _patch_google_http(monkeypatch, "victim@example.com", verified=False)

    with pytest.raises(HTTPException) as exc:
        await _google_login(session)

    assert exc.value.status_code == 403
    await session.refresh(existing)
    assert existing.oauth_provider is None
    assert existing.oauth_sub is None


@pytest.mark.asyncio
async def test_google_verified_email_links_existing_account(session, monkeypatch):
    existing = await _make_existing_user(session, "victim@example.com")
    _patch_google_http(monkeypatch, "victim@example.com", verified=True)

    resp = await _google_login(session)

    assert resp.status_code == 303
    await session.refresh(existing)
    assert existing.oauth_provider == "google"
    assert existing.oauth_sub == "google-sub-123"


# ---------------------------------------------------------------------------
# Notification tokens encrypted at rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notif_tokens_round_trip_and_are_ciphertext(session, monkeypatch):
    from app import security

    original_fernet = security._fernet
    try:
        security.init_encryption("unit-test-encryption-key")
        user = User(
            username="enc-user",
            email="enc@example.com",
            password_hash=None,
            notif_access_token="ya29.plain-access-token",
            notif_refresh_token="1//plain-refresh-token",
        )
        session.add(user)
        await session.flush()

        raw = (
            await session.execute(
                text(
                    "SELECT notif_access_token, notif_refresh_token "
                    "FROM app_user WHERE username = 'enc-user'"
                )
            )
        ).one()
        stored_access, stored_refresh = raw[0], raw[1]

        # Raw DB values are Fernet ciphertext, not the plaintext tokens.
        assert stored_access != "ya29.plain-access-token"
        assert stored_refresh != "1//plain-refresh-token"
        assert stored_access.startswith("gAAAAA")
        assert stored_refresh.startswith("gAAAAA")
        assert "plain" not in stored_access
        assert "plain" not in stored_refresh

        # The ORM still returns the plaintext transparently.
        await session.refresh(user)
        assert user.notif_access_token == "ya29.plain-access-token"
        assert user.notif_refresh_token == "1//plain-refresh-token"
    finally:
        security._fernet = original_fernet


@pytest.mark.asyncio
async def test_legacy_plaintext_notif_token_still_readable(session, monkeypatch):
    from app import security

    original_fernet = security._fernet
    try:
        security.init_encryption("unit-test-encryption-key")
        user = User(username="legacy-user", email="legacy@example.com", password_hash=None)
        session.add(user)
        await session.flush()

        # Simulate a row written before encryption was enabled (or restored
        # from an older plaintext dump).
        await session.execute(
            text(
                "UPDATE app_user SET notif_access_token = :v WHERE id = :i"
            ),
            {"v": "legacy-plaintext-token", "i": user.id},
        )
        await session.refresh(user)
        assert user.notif_access_token == "legacy-plaintext-token"
    finally:
        security._fernet = original_fernet
