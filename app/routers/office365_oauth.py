"""Office 365 / Microsoft OAuth 2.0 routes for connecting accounts."""
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.settings_manager import settings
from app.database import get_db
from app.models import Inbox, Office365Account, OAuthState

log = logging.getLogger("quickly.office365_oauth")

router = APIRouter(tags=["office365-oauth"])
# Public router – no auth required. Microsoft redirects the browser here
# after the user consents; the httpOnly cookie may not be present on the
# callback request (cross-site redirect, session expiry, dev overrides).
callback_router = APIRouter(tags=["office365-oauth"])

# Microsoft identity platform endpoints
MICROSOFT_AUTHORITY_BASE = "https://login.microsoftonline.com"
MICROSOFT_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
OFFICE365_SCOPES = [
    "Mail.ReadWrite",
    "Mail.Send",
    "User.Read",
    "offline_access",
]


def _get_authority(tenant_id: str) -> str:
    """Return the Microsoft authority URL for the given tenant."""
    return f"{MICROSOFT_AUTHORITY_BASE}/{tenant_id or 'common'}"


@router.get("/api/office365/status")
async def office365_oauth_status(db: AsyncSession = Depends(get_db)):
    """Check if Office 365 OAuth credentials are configured."""
    from app.app_settings import get_office365_oauth_credentials
    client_id, client_secret, tenant_id = await get_office365_oauth_credentials(db)
    configured = bool(client_id and client_secret)
    return {
        "configured": configured,
        "redirect_uri": settings.office365_redirect_uri,
        "tenant_id": tenant_id,
    }


@router.get("/api/office365/accounts")
async def list_office365_accounts(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """List all connected Office 365 accounts."""
    result = await db.execute(
        select(Office365Account, Inbox)
        .join(Inbox, Office365Account.inbox_id == Inbox.id)
        .order_by(Office365Account.created_at.desc())
    )
    rows = result.all()
    return [
        {
            "id": oa.id,
            "inbox_id": oa.inbox_id,
            "microsoft_email": oa.microsoft_email,
            "inbox_email": inbox.email,
            "inbox_display_name": inbox.display_name,
            "max_emails_per_day": inbox.max_emails_per_day,
            "token_expiry": oa.token_expiry.isoformat() if oa.token_expiry else None,
            "connected_at": oa.created_at.isoformat() if oa.created_at else None,
        }
        for oa, inbox in rows
    ]


@router.get("/oauth/office365/authorize")
async def office365_authorize(
    display_name: str = "",
    max_per_day: int = 50,
    ramp_up_enabled: bool = False,
    ramp_up_start: int = 1,
    ramp_up_step_size: int = 1,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Redirect user to Microsoft consent screen."""
    from app.app_settings import get_office365_oauth_credentials
    client_id, client_secret, tenant_id = await get_office365_oauth_credentials(db)
    if not client_id or not client_secret:
        raise HTTPException(400, "Office 365 OAuth not configured. Set OFFICE365_CLIENT_ID and OFFICE365_CLIENT_SECRET environment variables.")

    # CSRF nonce
    from app.time import utcnow
    csrf_token = secrets.token_urlsafe(32)
    csrf_state = OAuthState(
        state_token=csrf_token,
        purpose="inbox_microsoft",
        metadata_json=json.dumps({"display_name": display_name, "max_per_day": max_per_day, "ramp_up_enabled": ramp_up_enabled, "ramp_up_start": ramp_up_start, "ramp_up_step_size": ramp_up_step_size}),
        expires_at=utcnow() + timedelta(minutes=10),
    )
    db.add(csrf_state)
    await db.flush()

    state_data = json.dumps({
        "display_name": display_name,
        "max_per_day": max_per_day,
        "ramp_up_enabled": ramp_up_enabled,
        "ramp_up_start": ramp_up_start,
        "ramp_up_step_size": ramp_up_step_size,
        "_csrf": csrf_token,
    })

    authority = _get_authority(tenant_id)
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


@callback_router.get("/oauth/office365/callback")
async def office365_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = "",
    error: str = "",
    error_description: str = "",
    state: str = "{}",
    db: AsyncSession = Depends(get_db),
):
    """Handle Microsoft OAuth callback — exchange code for tokens, create inbox + office365_account."""
    if error:
        raise HTTPException(400, f"Microsoft OAuth error: {error} — {error_description}")
    if not code:
        raise HTTPException(400, "No authorization code received")

    try:
        state_data = json.loads(state)
    except (json.JSONDecodeError, TypeError):
        state_data = {}
    display_name = state_data.get("display_name", "")
    max_per_day = state_data.get("max_per_day", 50)
    ramp_up_enabled = bool(state_data.get("ramp_up_enabled", False))
    ramp_up_start = int(state_data.get("ramp_up_start", 1))
    ramp_up_step_size = int(state_data.get("ramp_up_step_size", 1))

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
    if not hmac.compare_digest(csrf_record.purpose, "inbox_microsoft"):
        await db.delete(csrf_record)
        await db.flush()
        raise HTTPException(403, "Invalid OAuth state purpose")
    if csrf_record.expires_at < _utcnow_fn():
        await db.delete(csrf_record)
        await db.flush()
        raise HTTPException(403, "OAuth state expired")
    await db.delete(csrf_record)
    await db.flush()

    from app.app_settings import get_office365_oauth_credentials
    client_id, client_secret, tenant_id = await get_office365_oauth_credentials(db)

    # Exchange code for tokens using MSAL
    token_data = _exchange_code(code, client_id, client_secret, tenant_id)
    if not token_data:
        raise HTTPException(502, "Failed to exchange authorization code for tokens")

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)

    if not refresh_token:
        raise HTTPException(400, "No refresh token received. Ensure offline_access scope is requested.")

    # Get user email from Microsoft Graph
    user_email = _get_user_email(access_token)
    if not user_email:
        raise HTTPException(502, "Failed to get email address from Microsoft")

    # Check if inbox already exists for this email
    result = await db.execute(select(Inbox).where(Inbox.email == user_email))
    inbox = result.scalar_one_or_none()

    if inbox:
        inbox.provider = "office365"
        if display_name:
            inbox.display_name = display_name

        result2 = await db.execute(
            select(Office365Account).where(Office365Account.inbox_id == inbox.id)
        )
        oa = result2.scalar_one_or_none()
        if oa:
            oa.access_token = access_token
            oa.refresh_token = refresh_token
            oa.token_expiry = token_expiry
            oa.microsoft_email = user_email
            oa.updated_at = datetime.utcnow()
        else:
            oa = Office365Account(
                inbox_id=inbox.id,
                microsoft_email=user_email,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry,
            )
            db.add(oa)
    else:
        inbox = Inbox(
            email=user_email,
            display_name=display_name or user_email.split("@")[0],
            max_emails_per_day=max_per_day,
            provider="office365",
            ramp_up_enabled=ramp_up_enabled,
            ramp_up_start=ramp_up_start,
            ramp_up_step_size=ramp_up_step_size,
            ramp_up_started_at=datetime.utcnow() if ramp_up_enabled else None,
        )
        db.add(inbox)
        await db.flush()

        oa = Office365Account(
            inbox_id=inbox.id,
            microsoft_email=user_email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=token_expiry,
        )
        db.add(oa)

    # Commit now so background tasks that open a NEW session can see the
    # inbox + Office365Account rows just created/updated.  A second commit
    # triggered by the get_db cleanup is a safe no-op.
    await db.commit()
    log.info("Office 365 OAuth connected: %s (inbox_id=%s)", user_email, inbox.id)

    from app.unibox import queue_sync_for_inbox
    background_tasks.add_task(queue_sync_for_inbox, inbox.id, "oauth-connect")

    # automatically create a Graph webhook subscription for realtime mail
    # notifications; failures are logged but do not block the user.
    from app.routers.office365_webhook import ensure_subscription
    from app.database import AsyncSessionLocal

    async def _auto_subscribe(iid: int) -> None:
        async with AsyncSessionLocal() as db2:
            try:
                await ensure_subscription(db2, iid)
            except Exception:
                log.exception("auto Graph subscription failed for inbox_id=%s", iid)

    background_tasks.add_task(_auto_subscribe, inbox.id)

    target = settings.base_url.rstrip('/') + "/inboxes?connected=" + urllib.parse.quote(user_email)
    return RedirectResponse(target, status_code=303)


@router.delete("/api/office365/accounts/{account_id}")
async def disconnect_office365(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Disconnect an Office 365 account (removes tokens, reverts inbox provider)."""
    result = await db.execute(
        select(Office365Account).where(Office365Account.id == account_id)
    )
    oa = result.scalar_one_or_none()
    if not oa:
        raise HTTPException(404, "Office 365 account not found")

    result2 = await db.execute(select(Inbox).where(Inbox.id == oa.inbox_id))
    inbox = result2.scalar_one_or_none()

    email = oa.microsoft_email
    await db.delete(oa)
    if inbox and inbox.provider == "office365":
        inbox.provider = "gmail"
    await db.flush()
    log.info("Office 365 disconnected: %s", email)
    return {"ok": True, "email": email}


# ---- Helper functions ----

def _exchange_code(code: str, client_id: str, client_secret: str, tenant_id: str) -> dict | None:
    """Exchange authorization code for access/refresh tokens via Microsoft token endpoint."""
    authority = _get_authority(tenant_id)
    token_url = f"{authority}/oauth2/v2.0/token"

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": settings.office365_redirect_uri,
        "grant_type": "authorization_code",
        "scope": " ".join(OFFICE365_SCOPES),
    }).encode()

    req = urllib.request.Request(
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
        except Exception:
            pass
        log.error("Microsoft token exchange HTTP %s: %s", e.code, body)
        return None
    except Exception as e:
        log.error("Microsoft token exchange error: %s", e)
        return None


def _get_user_email(access_token: str) -> str | None:
    """Fetch the user's email from Microsoft Graph /me endpoint."""
    req = urllib.request.Request(
        f"{MICROSOFT_GRAPH_BASE}/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("mail") or data.get("userPrincipalName")
    except Exception as e:
        log.error("Microsoft Graph /me error: %s", e)
        return None


def refresh_access_token(
    office365_account: Office365Account,
    client_id: str = "",
    client_secret: str = "",
    tenant_id: str = "",
) -> str | None:
    """Refresh the access token using the refresh token. Updates the model in-place."""
    _cid = client_id or settings.office365_client_id
    _csec = client_secret or settings.office365_client_secret
    _tid = tenant_id or settings.office365_tenant_id

    authority = _get_authority(_tid)
    token_url = f"{authority}/oauth2/v2.0/token"

    data = urllib.parse.urlencode({
        "client_id": _cid,
        "client_secret": _csec,
        "refresh_token": office365_account.refresh_token,
        "grant_type": "refresh_token",
        "scope": " ".join(OFFICE365_SCOPES),
    }).encode()

    req = urllib.request.Request(
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode())
            office365_account.access_token = token_data["access_token"]
            if token_data.get("refresh_token"):
                office365_account.refresh_token = token_data["refresh_token"]
            office365_account.token_expiry = datetime.utcnow() + timedelta(
                seconds=token_data.get("expires_in", 3600)
            )
            office365_account.updated_at = datetime.utcnow()
            log.info("Refreshed Office 365 token for %s", office365_account.microsoft_email)
            return office365_account.access_token
    except Exception as e:
        log.error("Failed to refresh Office 365 token for %s: %s", office365_account.microsoft_email, e)
        return None
