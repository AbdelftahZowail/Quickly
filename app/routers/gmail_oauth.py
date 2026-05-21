"""Gmail / G Suite OAuth 2.0 routes for connecting accounts."""
import hmac
import json
import logging
import re
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.settings_manager import settings
from app.database import get_db
from app.models import Inbox, GmailAccount, OAuthState, PendingOAuthConnect
from app.schemas import ConnectUrlRequest, ConnectUrlResponse

log = logging.getLogger("quickly.gmail_oauth")

router = APIRouter(tags=["gmail-oauth"])
# Public router – no auth required. Google redirects the browser here
# after the user consents; the httpOnly cookie may not be present on the
# callback request (cross-site redirect, session expiry, dev overrides).
callback_router = APIRouter(tags=["gmail-oauth"])

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_SCOPE = "https://mail.google.com/"
USERINFO_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"


@router.get("/api/gmail/status")
async def gmail_oauth_status(db: AsyncSession = Depends(get_db)):
    """Check if Google OAuth credentials are configured."""
    from app.app_settings import get_google_oauth_credentials
    client_id, client_secret = await get_google_oauth_credentials(db)
    configured = bool(client_id and client_secret)
    return {
        "configured": configured,
        "redirect_uri": settings.google_redirect_uri,
    }


@router.get("/api/gmail/accounts")
async def list_gmail_accounts(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """List all connected Gmail accounts."""
    result = await db.execute(
        select(GmailAccount, Inbox)
        .join(Inbox, GmailAccount.inbox_id == Inbox.id)
        .order_by(GmailAccount.created_at.desc())
    )
    rows = result.all()
    return [
        {
            "id": ga.id,
            "inbox_id": ga.inbox_id,
            "google_email": ga.google_email,
            "inbox_email": inbox.email,
            "inbox_display_name": inbox.display_name,
            "max_emails_per_day": inbox.max_emails_per_day,
            "token_expiry": ga.token_expiry.isoformat() if ga.token_expiry else None,
            "connected_at": ga.created_at.isoformat() if ga.created_at else None,
        }
        for ga, inbox in rows
    ]


@router.get("/api/gmail/permissions")
async def check_gmail_permissions(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Check permissions/scopes for all connected Gmail accounts."""
    result = await db.execute(
        select(GmailAccount, Inbox)
        .join(Inbox, GmailAccount.inbox_id == Inbox.id)
        .order_by(GmailAccount.created_at.desc())
    )
    rows = result.all()
    
    # Available Gmail API scopes
    available_scopes = {
        "https://www.googleapis.com/auth/gmail.send": {
            "name": "Send Email",
            "description": "Send email on your behalf",
            "category": "Send"
        },
        "https://www.googleapis.com/auth/gmail.readonly": {
            "name": "Read Email",
            "description": "Read all your email messages and settings",
            "category": "Read"
        },
        "https://www.googleapis.com/auth/gmail.modify": {
            "name": "Modify Email",
            "description": "Read, compose, send, and modify email",
            "category": "Full Access"
        },
        "https://www.googleapis.com/auth/gmail.compose": {
            "name": "Compose Email",
            "description": "Manage drafts and send emails",
            "category": "Compose"
        },
        "https://www.googleapis.com/auth/gmail.labels": {
            "name": "Manage Labels",
            "description": "Manage mailbox labels",
            "category": "Organization"
        },
        "https://www.googleapis.com/auth/gmail.settings.basic": {
            "name": "Basic Settings",
            "description": "Manage your basic mail settings",
            "category": "Settings"
        },
        "https://www.googleapis.com/auth/userinfo.email": {
            "name": "Email Address",
            "description": "See your primary email address",
            "category": "Profile"
        },
        "https://www.googleapis.com/auth/userinfo.profile": {
            "name": "Personal Info",
            "description": "See your personal info",
            "category": "Profile"
        }
    }

    def _parse_scopes(scopes_str: str | None) -> list[str]:
        if not scopes_str:
            return []
        raw = scopes_str.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return [str(s).strip() for s in data if str(s).strip()]
            except json.JSONDecodeError:
                pass
        return [s for s in re.split(r"[\s,]+", raw) if s]

    full_access_scopes = {
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/gmail.modify",
    }
    required_send_scope = "https://www.googleapis.com/auth/gmail.send"
    
    accounts_data = []
    for ga, inbox in rows:
        # Parse the scopes stored in the database (space-separated)
        granted_scopes = _parse_scopes(ga.scopes)
        
        # Check token validity
        token_valid = False
        token_status = "expired"
        if ga.token_expiry:
            time_until_expiry = ga.token_expiry - datetime.utcnow()
            token_valid = time_until_expiry.total_seconds() > 0
            token_status = "valid" if token_valid else "expired"
            if token_valid and time_until_expiry.total_seconds() < 600:  # Less than 10 minutes
                token_status = "expiring_soon"
        
        # Build granted scopes details
        granted_details = []
        for scope in granted_scopes:
            if scope in available_scopes:
                granted_details.append({
                    "scope": scope,
                    **available_scopes[scope]
                })
            else:
                granted_details.append({
                    "scope": scope,
                    "name": scope.split('/')[-1],
                    "description": "Custom scope",
                    "category": "Other"
                })
        
        # Find missing critical scopes (send capability only)
        missing_scopes = []
        if not (set(granted_scopes) & full_access_scopes) and required_send_scope not in granted_scopes:
            missing_scopes.append({
                "scope": required_send_scope,
                **available_scopes[required_send_scope],
            })
        
        accounts_data.append({
            "id": ga.id,
            "google_email": ga.google_email,
            "inbox_display_name": inbox.display_name,
            "token_status": token_status,
            "token_valid": token_valid,
            "token_expiry": ga.token_expiry.isoformat() if ga.token_expiry else None,
            "granted_scopes": granted_details,
            "missing_scopes": missing_scopes,
            "can_refresh": bool(ga.refresh_token),
            "connected_at": ga.created_at.isoformat() if ga.created_at else None,
        })
    
    return {
        "accounts": accounts_data,
        "available_scopes": [
            {"scope": scope, **details}
            for scope, details in available_scopes.items()
        ]
    }


@router.get("/oauth/google/authorize")
async def google_authorize(
    display_name: str = "",
    max_per_day: int = 50,
    ramp_up_enabled: bool = False,
    ramp_up_start: int = 1,
    ramp_up_step_size: int = 1,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Redirect user to Google consent screen."""
    from app.app_settings import get_google_oauth_credentials
    client_id, client_secret = await get_google_oauth_credentials(db)
    if not client_id or not client_secret:
        raise HTTPException(400, "Google OAuth not configured. Save your credentials in Settings first.")

    # CSRF nonce
    from app.time import utcnow
    csrf_token = secrets.token_urlsafe(32)
    csrf_state = OAuthState(
        state_token=csrf_token,
        purpose="inbox_google",
        metadata_json=json.dumps({"display_name": display_name, "max_per_day": max_per_day, "ramp_up_enabled": ramp_up_enabled, "ramp_up_start": ramp_up_start, "ramp_up_step_size": ramp_up_step_size}),
        expires_at=utcnow() + timedelta(minutes=10),
    )
    db.add(csrf_state)
    await db.flush()

    # Embed the nonce as the OAuth state parameter
    state_data = json.dumps({
        "display_name": display_name,
        "max_per_day": max_per_day,
        "ramp_up_enabled": ramp_up_enabled,
        "ramp_up_start": ramp_up_start,
        "ramp_up_step_size": ramp_up_step_size,
        "_csrf": csrf_token,
    })

    params = {
        "client_id": client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": f"{GMAIL_SCOPE} {USERINFO_EMAIL_SCOPE}",
        "access_type": "offline",
        "prompt": "consent",
        "state": state_data,
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


@callback_router.get("/oauth/google/callback")
async def google_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = "",
    error: str = "",
    state: str = "{}",
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback — exchange code for tokens, create inbox + gmail_account."""
    if error:
        raise HTTPException(400, f"Google OAuth error: {error}")
    if not code:
        raise HTTPException(400, "No authorization code received")

    # Parse state
    try:
        state_data = json.loads(state)
    except (json.JSONDecodeError, TypeError):
        state_data = {}
    display_name = state_data.get("display_name", "")
    max_per_day = state_data.get("max_per_day", 50)
    wait_minutes_between = state_data.get("wait_minutes_between", 5)
    max_jitter_seconds = state_data.get("max_jitter_seconds", 180)
    tracking_domain = state_data.get("tracking_domain", "") or None
    ramp_up_enabled = bool(state_data.get("ramp_up_enabled", False))
    ramp_up_start = int(state_data.get("ramp_up_start", 1))
    ramp_up_step_size = int(state_data.get("ramp_up_step_size", 1))
    source = state_data.get("source", "")

    # Validate CSRF nonce (single-use)
    csrf_token = state_data.get("_csrf", "")
    if not csrf_token:
        raise HTTPException(403, "Missing CSRF token in OAuth state")
    csrf_result = await db.execute(
        select(OAuthState).where(OAuthState.state_token == csrf_token)
    )
    csrf_record = csrf_result.scalar_one_or_none()
    if csrf_record is None:
        raise HTTPException(403, "Invalid or expired OAuth state")
    from app.time import utcnow as _utcnow_fn
    if not hmac.compare_digest(csrf_record.purpose, "inbox_google"):
        await db.delete(csrf_record)
        await db.flush()
        raise HTTPException(403, "Invalid OAuth state purpose")
    if csrf_record.expires_at < _utcnow_fn():
        await db.delete(csrf_record)
        await db.flush()
        raise HTTPException(403, "OAuth state expired")
    await db.delete(csrf_record)
    await db.flush()

    # Fetch OAuth credentials from DB
    from app.app_settings import get_google_oauth_credentials
    client_id, client_secret = await get_google_oauth_credentials(db)

    # Exchange code for tokens
    token_data = _exchange_code(code, client_id, client_secret, settings.google_redirect_uri)
    if not token_data:
        raise HTTPException(502, "Failed to exchange authorization code for tokens")

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    scopes_str = token_data.get("scope", "") or GMAIL_SCOPE
    token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)

    if not refresh_token:
        raise HTTPException(
            400,
            "No refresh token received. Revoke access at https://myaccount.google.com/permissions and try again.",
        )

    # Get user email from Google
    email = _get_user_email(access_token)
    if not email:
        raise HTTPException(502, "Failed to get email address from Google")

    # Check if inbox already exists for this email
    result = await db.execute(select(Inbox).where(Inbox.email == email))
    inbox = result.scalar_one_or_none()

    if inbox:
        # Update existing inbox to gmail provider
        inbox.provider = "gmail"
        if display_name:
            inbox.display_name = display_name
        # Update or create GmailAccount
        result2 = await db.execute(
            select(GmailAccount).where(GmailAccount.inbox_id == inbox.id)
        )
        ga = result2.scalar_one_or_none()
        if ga:
            ga.access_token = access_token
            ga.refresh_token = refresh_token
            ga.token_expiry = token_expiry
            if scopes_str:
                ga.scopes = scopes_str
            ga.google_email = email
            ga.updated_at = datetime.utcnow()
        else:
            ga = GmailAccount(
                inbox_id=inbox.id,
                google_email=email,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry,
                scopes=scopes_str or None,
            )
            db.add(ga)
    else:
        # Create new inbox + gmail account
        inbox = Inbox(
            email=email,
            display_name=display_name or email.split("@")[0],
            max_emails_per_day=max_per_day,
            wait_minutes_between=wait_minutes_between,
            max_jitter_seconds=max_jitter_seconds,
            provider="gmail",
            tracking_domain=tracking_domain,
            ramp_up_enabled=ramp_up_enabled,
            ramp_up_start=ramp_up_start,
            ramp_up_step_size=ramp_up_step_size,
            ramp_up_started_at=datetime.utcnow() if ramp_up_enabled else None,
        )
        db.add(inbox)
        await db.flush()

        ga = GmailAccount(
            inbox_id=inbox.id,
            google_email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=token_expiry,
            scopes=scopes_str or None,
        )
        db.add(ga)

    await db.flush()
    log.info("Gmail OAuth connected: %s (inbox_id=%s)", email, inbox.id)
    from app.unibox import queue_sync_for_inbox
    background_tasks.add_task(queue_sync_for_inbox, inbox.id, "oauth-connect")

    # schedule a background synchronization of the new inbox and ensure any
    # existing push watches are registered.  use a separate session so the
    # request commit isn't tied to the long‑running work.

    # Redirect to the front‑end inboxes page or a public success page for connect-URL flows
    base = settings.base_url.rstrip('/')
    if source == "connect_url":
        from app.auth import SECRET_KEY
        sig = hmac.new(SECRET_KEY.encode(), email.encode(), "sha256").hexdigest()[:12]
        target = f"{base}/oauth/connected?email=" + urllib.parse.quote(email) + "&sig=" + sig
    else:
        target = f"{base}/inboxes?connected=" + urllib.parse.quote(email)
    return RedirectResponse(target, status_code=303)


@router.delete("/api/gmail/accounts/{account_id}")
async def disconnect_gmail(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Disconnect a Gmail account (removes tokens, deletes inbox)."""
    result = await db.execute(
        select(GmailAccount).where(GmailAccount.id == account_id)
    )
    ga = result.scalar_one_or_none()
    if not ga:
        raise HTTPException(404, "Gmail account not found")

    # Get the inbox
    result2 = await db.execute(select(Inbox).where(Inbox.id == ga.inbox_id))
    inbox = result2.scalar_one_or_none()

    email = ga.google_email
    await db.delete(ga)
    # Optionally revert inbox provider or delete it
    if inbox and inbox.provider == "gmail":
        inbox.provider = "resend"  # revert so it's still usable if needed
    await db.flush()
    log.info("Gmail disconnected: %s", email)
    return {"ok": True, "email": email}


# ---- Helper functions ----

def _exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict | None:
    """Exchange authorization code for access/refresh tokens."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("Google token exchange error: %s", e)
        return None


def _get_user_email(access_token: str) -> str | None:
    """Fetch the user's email from Google userinfo endpoint."""
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("email")
    except Exception as e:
        log.error("Google userinfo error: %s", e)
        return None


def refresh_access_token(
    gmail_account: GmailAccount,
    client_id: str = "",
    client_secret: str = "",
) -> str | None:
    """Refresh the access token using the refresh token. Updates the model in-place.

    *client_id* / *client_secret* should be passed explicitly.  When
    omitted the function falls back to ``settings`` (.env) for backward
    compatibility.
    """
    _cid = client_id or settings.google_client_id
    _csec = client_secret or settings.google_client_secret
    data = urllib.parse.urlencode({
        "client_id": _cid,
        "client_secret": _csec,
        "refresh_token": gmail_account.refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode())
            gmail_account.access_token = token_data["access_token"]
            gmail_account.token_expiry = datetime.utcnow() + timedelta(
                seconds=token_data.get("expires_in", 3600)
            )
            gmail_account.updated_at = datetime.utcnow()
            log.info("Refreshed Gmail token for %s", gmail_account.google_email)
            return gmail_account.access_token
    except Exception as e:
        log.error("Failed to refresh Gmail token for %s: %s", gmail_account.google_email, e)
        return None


# ── One-time connect URL for OAuth flows ──────────────────────────────────────


@router.post("/api/oauth/connect-url", response_model=ConnectUrlResponse)
async def generate_connect_url_new_inbox(
    data: ConnectUrlRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Generate a one-time connect URL for a *new* inbox whose email isn't known yet."""
    if data.provider not in ("gmail", "office365"):
        raise HTTPException(400, f"Unsupported provider '{data.provider}'")
    if data.max_per_day < 1 or data.max_per_day > 1000:
        raise HTTPException(400, "max_per_day must be between 1 and 1000")

    from app.time import utcnow

    token = secrets.token_urlsafe(32)
    metadata = json.dumps({
        "provider": data.provider,
        "display_name": data.display_name,
        "max_per_day": data.max_per_day,
        "wait_minutes_between": data.wait_minutes_between,
        "max_jitter_seconds": data.max_jitter_seconds,
        "tracking_domain": data.tracking_domain or "",
        "ramp_up_enabled": data.ramp_up_enabled,
        "ramp_up_start": data.ramp_up_start,
        "ramp_up_step_size": data.ramp_up_step_size,
    })
    pending = PendingOAuthConnect(
        token=token,
        expires_at=utcnow() + timedelta(minutes=15),
        metadata_json=metadata,
    )
    db.add(pending)
    await db.flush()

    base = settings.base_url.rstrip("/")
    url = f"{base}/oauth/connect/{token}"
    log.info("generate_connect_url (new): token=%s… provider=%s", token[:12], data.provider)
    return ConnectUrlResponse(url=url)


@callback_router.get("/oauth/connect/{token}")
async def oauth_connect_redirect(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Validate a one-time connect token and redirect to the OAuth consent screen."""
    from app.time import utcnow
    now = utcnow()

    # 1. Try existing inbox (connect_token on the Inbox model)
    result = await db.execute(
        select(Inbox).where(
            Inbox.connect_token == token,
            Inbox.connect_token_expires_at > now,
        )
    )
    inbox = result.scalar_one_or_none()

    if inbox:
        inbox.connect_token = None
        inbox.connect_token_expires_at = None
        await db.flush()
        log.info("oauth_connect_redirect: existing inbox %s (provider=%s)", inbox.id, inbox.provider)
        provider = inbox.provider
        display_name = inbox.display_name
        max_per_day = inbox.max_emails_per_day
        wait_minutes_between = inbox.wait_minutes_between
        max_jitter_seconds = inbox.max_jitter_seconds
        tracking_domain = inbox.tracking_domain or ""
        ramp_up_enabled = inbox.ramp_up_enabled
        ramp_up_start = inbox.ramp_up_start
        ramp_up_step_size = inbox.ramp_up_step_size
    else:
        # 2. Try pending connect (new inbox flow)
        result = await db.execute(
            select(PendingOAuthConnect).where(
                PendingOAuthConnect.token == token,
                PendingOAuthConnect.expires_at > now,
                PendingOAuthConnect.used == False,
            )
        )
        pending = result.scalar_one_or_none()
        if not pending:
            raise HTTPException(400, "Invalid or expired connect token")

        pending.used = True
        await db.flush()
        log.info("oauth_connect_redirect: new inbox (token=%s…)", token[:12])

        metadata = json.loads(pending.metadata_json)
        provider = metadata["provider"]
        display_name = metadata.get("display_name", "")
        max_per_day = metadata.get("max_per_day", 50)
        wait_minutes_between = metadata.get("wait_minutes_between", 5)
        max_jitter_seconds = metadata.get("max_jitter_seconds", 180)
        tracking_domain = metadata.get("tracking_domain", "")
        ramp_up_enabled = metadata.get("ramp_up_enabled", False)
        ramp_up_start = metadata.get("ramp_up_start", 1)
        ramp_up_step_size = metadata.get("ramp_up_step_size", 1)

    # Build CSRF state and redirect
    csrf_token = secrets.token_urlsafe(32)

    if provider == "gmail":
        from app.app_settings import get_google_oauth_credentials
        client_id, client_secret = await get_google_oauth_credentials(db)
        if not client_id or not client_secret:
            raise HTTPException(400, "Google OAuth not configured.")

        state_data = json.dumps({
            "display_name": display_name,
            "max_per_day": max_per_day,
            "wait_minutes_between": wait_minutes_between,
            "max_jitter_seconds": max_jitter_seconds,
            "tracking_domain": tracking_domain,
            "ramp_up_enabled": ramp_up_enabled,
            "ramp_up_start": ramp_up_start,
            "ramp_up_step_size": ramp_up_step_size,
            "source": "connect_url",
            "_csrf": csrf_token,
        })

        csrf_state = OAuthState(
            state_token=csrf_token,
            purpose="inbox_google",
            metadata_json=state_data,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(csrf_state)
        await db.flush()

        params = {
            "client_id": client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": f"{GMAIL_SCOPE} {USERINFO_EMAIL_SCOPE}",
            "access_type": "offline",
            "prompt": "consent",
            "state": state_data,
        }
        url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
        return RedirectResponse(url)

    elif provider == "office365":
        from app.app_settings import get_office365_oauth_credentials
        client_id, client_secret, tenant_id = await get_office365_oauth_credentials(db)
        if not client_id or not client_secret:
            raise HTTPException(400, "Office 365 OAuth not configured.")

        state_data = json.dumps({
            "display_name": display_name,
            "max_per_day": max_per_day,
            "wait_minutes_between": wait_minutes_between,
            "max_jitter_seconds": max_jitter_seconds,
            "tracking_domain": tracking_domain,
            "ramp_up_enabled": ramp_up_enabled,
            "ramp_up_start": ramp_up_start,
            "ramp_up_step_size": ramp_up_step_size,
            "source": "connect_url",
            "_csrf": csrf_token,
        })

        csrf_state = OAuthState(
            state_token=csrf_token,
            purpose="inbox_microsoft",
            metadata_json=state_data,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(csrf_state)
        await db.flush()

        from app.routers.office365_oauth import OFFICE365_SCOPES, MICROSOFT_AUTHORITY_BASE

        authority = f"{MICROSOFT_AUTHORITY_BASE}/{tenant_id or 'common'}"
        params = {
            "client_id": client_id,
            "redirect_uri": settings.office365_redirect_uri,
            "response_type": "code",
            "scope": " ".join(OFFICE365_SCOPES),
            "response_mode": "query",
            "state": state_data,
            "prompt": "consent",
        }
        url = f"{authority}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"
        return RedirectResponse(url)

    else:
        raise HTTPException(400, f"Unknown provider '{provider}'")


@callback_router.get("/oauth/connected")
async def oauth_connected(email: str = "", sig: str = ""):
    """Public success page shown after connecting an inbox via a one-time connect URL.

    The ``sig`` parameter is an HMAC-SHA256 prefix of the email, signed with
    ``QUICKLY_SECRET_KEY``, to prevent unauthorized third parties from fabricating
    success confirmations.
    """
    if not email or not sig:
        return HTMLResponse("Missing parameters", status_code=400)
    from app.auth import SECRET_KEY
    expected = hmac.new(SECRET_KEY.encode(), email.encode(), "sha256").hexdigest()[:12]
    if not hmac.compare_digest(expected, sig):
        return HTMLResponse("Invalid link", status_code=400)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Connected!</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; background: #f9fafb; color: #1f2937; }}
    .card {{ background: white; border-radius: 12px; padding: 48px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1), 0 1px 2px rgba(0,0,0,0.06); max-width: 420px; }}
    .check {{ width: 64px; height: 64px; margin: 0 auto 24px; background: #d1fae5; border-radius: 50%; display: flex; align-items: center; justify-content: center; }}
    .check svg {{ width: 32px; height: 32px; stroke: #059669; }}
    h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 8px; }}
    p {{ font-size: 14px; color: #6b7280; line-height: 1.5; }}
    .email {{ font-weight: 600; color: #1f2937; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="check">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
    </div>
    <h1>Successfully Connected!</h1>
    <p>Your inbox <span class="email">{email}</span> has been connected. You can close this tab and return to Quickly.</p>
  </div>
</body>
</html>"""
    return HTMLResponse(html)

