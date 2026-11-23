"""App-level OAuth login/signup routes (Google + Microsoft).

These endpoints handle authentication for the *application itself* — they
are completely separate from the inbox-connection OAuth flows in
``gmail_oauth.py`` / ``office365_oauth.py``.

The user's OAuth tokens (with mail-send scope) are stored on the ``User``
model so notification emails can be sent from the user's own account.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    create_refresh_token,
    is_setup_complete,
)
from app.database import get_db
from app.models import OAuthState, User
from app.setup_code import clear_setup_code, verify_setup_code
from app.settings_manager import settings
from app.time import utcnow

log = logging.getLogger("quickly.app_oauth")

router = APIRouter(tags=["app-oauth"])

# Google endpoints & scopes (app login — NOT inbox connection)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
APP_GOOGLE_SCOPES = (
    "openid email profile "
    "https://www.googleapis.com/auth/gmail.send "
    "https://www.googleapis.com/auth/userinfo.email"
)

# Microsoft endpoints & scopes (app login — NOT inbox connection)
MICROSOFT_AUTHORITY_BASE = "https://login.microsoftonline.com"
MICROSOFT_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
APP_MICROSOFT_SCOPES = "openid email profile User.Read Mail.Send offline_access"

# The "consumers" (personal Microsoft accounts) tenant.  Personal accounts are
# refused for app login: they are not tied to a work/school directory and the
# nOAuth class of attacks relies on mutable, tenant-controlled email claims.
MICROSOFT_CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"

# Authority values that mean "any tenant" rather than one specific directory.
# Anything else is treated as a concrete tenant GUID that must match the
# id_token's ``tid`` claim.
_MICROSOFT_MULTI_TENANT_AUTHORITIES = {"", "common", "organizations", "consumers"}

STATE_TTL_MINUTES = 10


# ---------------------------------------------------------------------------
# Helpers — CSRF state management
# ---------------------------------------------------------------------------

async def _create_state(db: AsyncSession, purpose: str, metadata: dict | None = None) -> str:
    """Generate a CSRF nonce, persist it in OAuthState, return the token."""
    token = secrets.token_urlsafe(32)
    state = OAuthState(
        state_token=token,
        purpose=purpose,
        metadata_json=json.dumps(metadata or {}),
        expires_at=utcnow() + timedelta(minutes=STATE_TTL_MINUTES),
    )
    db.add(state)
    await db.flush()
    return token


async def _validate_state(db: AsyncSession, token: str, expected_purpose: str) -> dict:
    """Validate and consume a CSRF nonce.  Raises 403 on failure."""
    result = await db.execute(
        select(OAuthState).where(OAuthState.state_token == token)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(403, "Invalid or expired OAuth state")
    # Constant-time comparison on purpose
    if not hmac.compare_digest(record.purpose, expected_purpose):
        await db.delete(record)
        await db.flush()
        raise HTTPException(403, "Invalid OAuth state purpose")
    if record.expires_at < utcnow():
        await db.delete(record)
        await db.flush()
        raise HTTPException(403, "OAuth state expired")
    metadata = json.loads(record.metadata_json or "{}")
    # Single-use: delete immediately
    await db.delete(record)
    await db.flush()
    return metadata


# ---------------------------------------------------------------------------
# Helpers — token exchange & userinfo
# ---------------------------------------------------------------------------

def _google_exchange_code(code: str, client_id: str, client_secret: str) -> dict | None:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": settings.app_google_redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("Google app-login token exchange error: %s", e)
        return None


def _google_userinfo(access_token: str) -> dict | None:
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("Google app-login userinfo error: %s", e)
        return None


def _microsoft_exchange_code(code: str, client_id: str, client_secret: str, tenant_id: str) -> dict | None:
    authority = f"{MICROSOFT_AUTHORITY_BASE}/{tenant_id or 'common'}"
    token_url = f"{authority}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": settings.app_microsoft_redirect_uri,
        "grant_type": "authorization_code",
        "scope": APP_MICROSOFT_SCOPES,
    }).encode()
    req = urllib.request.Request(
        token_url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("Microsoft app-login token exchange error: %s", e)
        return None


def _microsoft_userinfo(access_token: str) -> dict | None:
    req = urllib.request.Request(
        f"{MICROSOFT_GRAPH_BASE}/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("Microsoft app-login /me error: %s", e)
        return None


def _decode_jwt_payload(token: str) -> dict | None:
    """Decode the claims payload of a JWT without verifying its signature.

    The ``id_token`` is received directly from Microsoft's token endpoint over
    TLS during a confidential-client exchange (we send the client secret in the
    same request).  The transport therefore already authenticates the issuer and
    the signature check would only re-prove what TLS established; decoding the
    claims ourselves avoids adding a JWT dependency.  Never use this helper for
    tokens that arrive from an untrusted client.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2 or not parts[1]:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload + padding).decode())
        return claims if isinstance(claims, dict) else None
    except Exception as e:
        log.warning("Could not decode id_token claims: %s", e)
        return None


def _microsoft_identity_from_id_token(
    id_token: str, configured_tenant: str
) -> str:
    """Return the immutable app-login subject for a Microsoft id_token.

    Identity is ``<tid>:<oid>`` because Microsoft's per-tenant ``oid`` alone is
    not globally unique (two tenants can host the same object id) and Graph's
    ``id`` is tenant-scoped too.  ``tid`` + ``oid`` together are stable for a
    given directory user.  Raises HTTPException when the token is unsuitable.
    """
    claims = _decode_jwt_payload(id_token)
    if claims is None:
        raise HTTPException(502, "Microsoft returned an unreadable id_token")

    tid = str(claims.get("tid") or "").strip().lower()
    oid = str(claims.get("oid") or "").strip().lower()
    if not tid or not oid:
        raise HTTPException(
            502,
            "Microsoft id_token is missing the tenant (tid) or object (oid) claim",
        )

    if tid == MICROSOFT_CONSUMER_TENANT_ID:
        raise HTTPException(403, "Sign in with a work or school Microsoft account")

    configured = (configured_tenant or "").strip().lower()
    if configured not in _MICROSOFT_MULTI_TENANT_AUTHORITIES and configured != tid:
        raise HTTPException(
            403,
            "This Microsoft account belongs to a different tenant than the one "
            "configured for this deployment",
        )

    return f"{tid}:{oid}"


# ---------------------------------------------------------------------------
# Shared user resolution + session creation
# ---------------------------------------------------------------------------

async def _resolve_user_and_login(
    db: AsyncSession,
    response: Response,
    provider: str,            # "google" or "microsoft"
    sub: str,                 # provider's unique subject ID
    email: str,
    name: str,
    access_token: str,
    refresh_token: str,
    token_expiry: datetime,
    *,
    allow_email_link: bool,
    legacy_subs: tuple[str, ...] = (),
) -> str:
    """Find or create the User, set JWT cookies, redirect to /.

    ``allow_email_link`` must be passed explicitly by each caller.  Linking a
    provider identity to an existing local account by email is only safe when
    the provider has proven the address is verified and controlled by the
    signer.  For Microsoft that is not the case (a tenant admin can set
    ``mail``/``userPrincipalName`` arbitrarily — the nOAuth account-takeover
    class), so Microsoft always passes ``False``.

    ``legacy_subs`` lets a caller upgrade identities stored under an older
    subject format.  A match requires the same provider *and* the same email,
    and the stored subject is rewritten to the new one, so this is a one-time
    migration rather than a new trust path.
    """
    email = email.lower()

    # 1. Match by (oauth_provider, oauth_sub)
    result = await db.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_sub == sub)
    )
    user = result.scalar_one_or_none()

    if user is None and legacy_subs:
        # 1b. Upgrade pre-existing identities saved under the old subject
        # format (Microsoft used the tenant-scoped Graph id before).  Email is
        # only a corroborating signal here — the stored subject must match too.
        candidates = tuple(s for s in legacy_subs if s)
        if candidates:
            result_legacy = await db.execute(
                select(User).where(
                    User.oauth_provider == provider,
                    User.oauth_sub.in_(candidates),
                    func.lower(User.email) == email,
                )
            )
            user = result_legacy.scalar_one_or_none()
            if user is not None:
                user.oauth_sub = sub
                log.info(
                    "Upgraded legacy %s OAuth subject for user %s", provider, user.id
                )

    if user is None and allow_email_link:
        # 2. Fallback: match by email and link the identity to that account.
        result2 = await db.execute(select(User).where(User.email == email))
        user = result2.scalar_one_or_none()
        if user:
            # Link OAuth identity to existing account
            user.oauth_provider = provider
            user.oauth_sub = sub

    if user is None:
        # 3. Check if any users exist (first-user = admin setup)
        count_result = await db.execute(select(func.count(User.id)))
        user_count = count_result.scalar_one()
        if user_count > 0:
            if provider == "microsoft":
                raise HTTPException(
                    403,
                    "No account is linked to this Microsoft identity. Sign in "
                    "with your existing method to continue.",
                )
            raise HTTPException(
                403,
                "No account found for this email. Contact your admin to create your account.",
            )
        # First user — create admin
        username = email.split("@")[0].lower().replace(".", "_").replace("-", "_")
        # Ensure unique username
        exist = await db.execute(select(User).where(User.username == username))
        if exist.scalar_one_or_none():
            username = f"{username}_{secrets.token_hex(3)}"
        user = User(
            username=username,
            email=email,
            password_hash=None,
            oauth_provider=provider,
            oauth_sub=sub,
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        # Setup is complete now: the printed setup code must stop working.
        clear_setup_code()
        log.info("First user (admin) created via %s OAuth: %s", provider, email)

    if not user.is_active:
        raise HTTPException(403, "Account disabled")

    # Update notification tokens
    user.notif_access_token = access_token
    user.notif_refresh_token = refresh_token
    user.notif_token_expiry = token_expiry
    await db.flush()

    # Issue JWT tokens
    jwt_access = create_access_token(user.id, user.role)
    jwt_refresh = create_refresh_token(user.id, user.role)

    response.set_cookie(
        key="refresh_token", value=jwt_refresh,
        httponly=True, secure=True, samesite="lax",
        max_age=7 * 24 * 60 * 60, path="/api/auth",
    )
    response.set_cookie(
        key="access_token", value=jwt_access,
        httponly=True, secure=True, samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/",
    )

    return jwt_access


# ---------------------------------------------------------------------------
# Google OAuth — app login
# ---------------------------------------------------------------------------

@router.get("/oauth/app/google/authorize")
async def app_google_authorize(setup_code: str = "", db: AsyncSession = Depends(get_db)):
    """Redirect user to Google consent screen for app login."""
    from app.app_settings import get_google_oauth_credentials
    client_id, _ = await get_google_oauth_credentials(db)
    if not client_id:
        raise HTTPException(400, "Google OAuth not configured")

    # Before setup, the printed code proves ownership of the deployment.
    # After setup, the parameter is ignored (normal login).
    if not await is_setup_complete(db) and not verify_setup_code(setup_code):
        raise HTTPException(
            403, "Invalid or missing setup code. Find it in the server logs."
        )

    state_token = await _create_state(db, "app_login")

    params = {
        "client_id": client_id,
        "redirect_uri": settings.app_google_redirect_uri,
        "response_type": "code",
        "scope": APP_GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state_token,
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


@router.get("/oauth/app/google/callback")
async def app_google_callback(
    request: Request,
    code: str = "",
    error: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback for app login."""
    if error:
        raise HTTPException(400, f"Google OAuth error: {error}")
    if not code or not state:
        raise HTTPException(400, "Missing authorization code or state")

    # Validate CSRF state (single-use)
    await _validate_state(db, state, "app_login")

    from app.app_settings import get_google_oauth_credentials
    client_id, client_secret = await get_google_oauth_credentials(db)

    token_data = _google_exchange_code(code, client_id, client_secret)
    if not token_data:
        raise HTTPException(502, "Failed to exchange Google authorization code")

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)

    if not refresh_token:
        raise HTTPException(400, "No refresh token received. Revoke access at https://myaccount.google.com/permissions and try again.")

    userinfo = _google_userinfo(access_token)
    if not userinfo or not userinfo.get("email"):
        raise HTTPException(502, "Failed to get user info from Google")

    # Only a Google-verified address may be used to attach this identity to an
    # existing local account (prevents email-based account takeover).
    if userinfo.get("email_verified") is not True:
        raise HTTPException(
            403,
            "Your Google email address is not verified. Verify it with Google and try again.",
        )

    target = settings.base_url.rstrip("/") + "/"
    redirect = RedirectResponse(target, status_code=303)

    await _resolve_user_and_login(
        db, redirect,
        provider="google",
        sub=userinfo["sub"],
        email=userinfo["email"],
        name=userinfo.get("name", ""),
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=utcnow() + timedelta(seconds=expires_in),
        allow_email_link=True,
    )

    return redirect


# ---------------------------------------------------------------------------
# Microsoft OAuth — app login
# ---------------------------------------------------------------------------

@router.get("/oauth/app/microsoft/authorize")
async def app_microsoft_authorize(setup_code: str = "", db: AsyncSession = Depends(get_db)):
    """Redirect user to Microsoft consent screen for app login."""
    from app.app_settings import get_office365_oauth_credentials
    client_id, _, tenant_id = await get_office365_oauth_credentials(db)
    if not client_id:
        raise HTTPException(400, "Microsoft OAuth not configured")

    # Before setup, the printed code proves ownership of the deployment.
    # After setup, the parameter is ignored (normal login).
    if not await is_setup_complete(db) and not verify_setup_code(setup_code):
        raise HTTPException(
            403, "Invalid or missing setup code. Find it in the server logs."
        )

    state_token = await _create_state(db, "app_login")

    authority = f"{MICROSOFT_AUTHORITY_BASE}/{tenant_id or 'common'}"
    params = {
        "client_id": client_id,
        "redirect_uri": settings.app_microsoft_redirect_uri,
        "response_type": "code",
        "scope": APP_MICROSOFT_SCOPES,
        "response_mode": "query",
        "state": state_token,
        "prompt": "consent",
    }
    url = f"{authority}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


@router.get("/oauth/app/microsoft/callback")
async def app_microsoft_callback(
    request: Request,
    code: str = "",
    error: str = "",
    error_description: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Handle Microsoft OAuth callback for app login."""
    if error:
        raise HTTPException(400, f"Microsoft OAuth error: {error} — {error_description}")
    if not code or not state:
        raise HTTPException(400, "Missing authorization code or state")

    await _validate_state(db, state, "app_login")

    from app.app_settings import get_office365_oauth_credentials
    client_id, client_secret, tenant_id = await get_office365_oauth_credentials(db)

    token_data = _microsoft_exchange_code(code, client_id, client_secret, tenant_id)
    if not token_data:
        raise HTTPException(502, "Failed to exchange Microsoft authorization code")

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)

    if not refresh_token:
        raise HTTPException(400, "No refresh token received. Ensure offline_access scope is requested.")

    userinfo = _microsoft_userinfo(access_token)
    if not userinfo:
        raise HTTPException(502, "Failed to get user info from Microsoft")

    email = userinfo.get("mail") or userinfo.get("userPrincipalName")
    if not email:
        raise HTTPException(502, "Microsoft account has no email address")

    # Identity comes from the id_token's immutable (tid, oid) pair, never from
    # Graph's tenant-scoped ``id`` or the mutable email claims.  This closes the
    # nOAuth class where a tenant admin rewrites mail/UPN to a victim's address.
    sub = _microsoft_identity_from_id_token(
        str(token_data.get("id_token") or ""), tenant_id
    )

    target = settings.base_url.rstrip("/") + "/"
    redirect = RedirectResponse(target, status_code=303)

    await _resolve_user_and_login(
        db, redirect,
        provider="microsoft",
        sub=sub,
        email=email,
        name=userinfo.get("displayName", ""),
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=utcnow() + timedelta(seconds=expires_in),
        allow_email_link=False,
        # Releases before this one stored Graph's tenant-scoped ``id``.  Allow
        # the same account (same provider + same email) to upgrade in place so
        # existing Microsoft sign-ins keep working after this release.
        legacy_subs=(str(userinfo.get("id") or ""),),
    )

    return redirect
