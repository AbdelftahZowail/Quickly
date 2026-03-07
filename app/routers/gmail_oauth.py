"""Gmail / G Suite OAuth 2.0 routes for connecting accounts."""
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings_manager import settings
from app.database import get_db
from app.models import Inbox, GmailAccount

log = logging.getLogger("quickly.gmail_oauth")

router = APIRouter(tags=["gmail-oauth"])

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
async def list_gmail_accounts(db: AsyncSession = Depends(get_db)):
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
async def check_gmail_permissions(db: AsyncSession = Depends(get_db)):
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
    
    accounts_data = []
    for ga, inbox in rows:
        # Parse the scopes stored in the database (space-separated)
        granted_scopes = ga.scopes.split() if ga.scopes else []
        
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
        
        # Find missing critical scopes
        missing_scopes = []
        for scope, details in available_scopes.items():
            if scope not in granted_scopes and details["category"] in ["Send", "Compose"]:
                missing_scopes.append({
                    "scope": scope,
                    **details
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
    db: AsyncSession = Depends(get_db),
):
    """Redirect user to Google consent screen."""
    from app.app_settings import get_google_oauth_credentials
    client_id, client_secret = await get_google_oauth_credentials(db)
    if not client_id or not client_secret:
        raise HTTPException(400, "Google OAuth not configured. Save your credentials in Settings first.")

    # Store display_name, max_per_day and ramp_up_enabled in state so we can use it in callback
    state_data = json.dumps({"display_name": display_name, "max_per_day": max_per_day, "ramp_up_enabled": ramp_up_enabled})

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


@router.get("/oauth/google/callback")
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
    ramp_up_enabled = bool(state_data.get("ramp_up_enabled", False))

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
            ga.google_email = email
            ga.updated_at = datetime.utcnow()
        else:
            ga = GmailAccount(
                inbox_id=inbox.id,
                google_email=email,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry,
            )
            db.add(ga)
    else:
        # Create new inbox + gmail account
        inbox = Inbox(
            email=email,
            display_name=display_name or email.split("@")[0],
            max_emails_per_day=max_per_day,
            provider="gmail",
            ramp_up_enabled=ramp_up_enabled,
        )
        db.add(inbox)
        await db.flush()

        ga = GmailAccount(
            inbox_id=inbox.id,
            google_email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=token_expiry,
        )
        db.add(ga)

    await db.flush()
    log.info("Gmail OAuth connected: %s (inbox_id=%s)", email, inbox.id)
    from app.unibox import queue_sync_for_inbox
    background_tasks.add_task(queue_sync_for_inbox, inbox.id, "oauth-connect")

    # schedule a background synchronization of the new inbox and ensure any
    # existing push watches are registered.  use a separate session so the
    # request commit isn't tied to the long‑running work.

    # Redirect to the front‑end inboxes page with success (use base_url if configured)
    target = settings.base_url.rstrip('/') + "/inboxes?connected=" + urllib.parse.quote(email)
    return RedirectResponse(target, status_code=303)


@router.delete("/api/gmail/accounts/{account_id}")
async def disconnect_gmail(account_id: int, db: AsyncSession = Depends(get_db)):
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

