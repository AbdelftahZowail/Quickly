"""System Health API — aggregates status for all monitored services into a
single response so the frontend only needs one round-trip."""
# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Inbox, GmailAccount, Office365Account, AppSetting

log = logging.getLogger("quickly.system_health")

router = APIRouter(prefix="/api/system-health", tags=["system-health"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gmail_token_status(token_expiry: datetime | None) -> str:
    if not token_expiry:
        return "expired"
    delta = (token_expiry - datetime.utcnow()).total_seconds()
    if delta <= 0:
        return "expired"
    return "valid"


def _o365_token_status(token_expiry: datetime | None) -> str:
    if not token_expiry:
        return "expired"
    delta = (token_expiry - datetime.utcnow()).total_seconds()
    if delta <= 0:
        return "expired"
    return "valid"


_GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_GMAIL_FULL_SCOPES = {
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
}


def _parse_scopes(scopes_str: str | None) -> set[str]:
    if not scopes_str:
        return set()
    raw = scopes_str.strip()
    if not raw:
        return set()
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return {str(s).strip() for s in data if str(s).strip()}
        except json.JSONDecodeError:
            pass
    return {s for s in re.split(r"[\s,]+", raw) if s}


def _gmail_missing_scopes(scopes_str: str | None) -> list[str]:
    granted = _parse_scopes(scopes_str)
    # Any full-access scope covers send capability.
    if granted.intersection(_GMAIL_FULL_SCOPES):
        return []
    if _GMAIL_SEND_SCOPE in granted:
        return []
    return [_GMAIL_SEND_SCOPE]


def _probe_token(url: str, access_token: str) -> str:
    if not access_token:
        return "invalid"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.getcode() < 300:
                return "valid"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "invalid"
        return "unknown"
    except Exception:
        return "unknown"
    return "unknown"


def _probe_gmail(access_token: str) -> str:
    return _probe_token(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        access_token,
    )


def _probe_o365(access_token: str) -> str:
    return _probe_token(
        "https://graph.microsoft.com/v1.0/me?$select=id",
        access_token,
    )


def _probe_tracking_domain(domain: str) -> str:
    """Return 'ok' if *domain* correctly serves the tracking-probe endpoint.

    Makes a GET to ``https://<domain>/api/tracking-probe`` and checks that the
    response is HTTP 200 with ``{"ok": true}``.  Uses the same httpx library
    (sync client) and the same two-pass SSL strategy as the interactive
    ``verify-tracking-domain`` endpoint so the health status is consistent
    with manual verification.
    """
    import httpx

    if not domain:
        return "error"
    url = f"https://{domain}/api/tracking-probe"
    for verify_ssl in (True, False):
        try:
            with httpx.Client(timeout=15, follow_redirects=True, verify=verify_ssl) as client:
                resp = client.get(url)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return "ok" if data.get("ok") else "error"
                except Exception:
                    return "error"
            # Non-200 from server — domain reachable but not our server
            return "error"
        except httpx.ConnectError:
            pass  # DNS / connection error: retry without SSL verification
        except httpx.TimeoutException:
            return "error"
        except Exception:
            pass  # SSL or other error: retry without SSL verification
    return "error"


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@router.get("")
async def get_system_health(db: AsyncSession = Depends(get_db)):
    """Return a single aggregated health snapshot covering:
    - Google OAuth (credentials configured + per-account token status)
    - Microsoft OAuth (credentials configured + per-account token status)
    - Inboxes (list with paused flag)
    - Unibox sync (push vs. polling, in-progress flag)
    - AI features (enabled + whether key is set)
    - Active flags (test mode)
    """
    from app.app_settings import (
        get_google_oauth_credentials,
        get_office365_oauth_credentials,
        get_gmail_sync_config,
        get_test_mode,
    )
    from app.settings_manager import settings as app_settings
    from app.unibox import get_unibox_sync_status
    from app.ai_classifier import FEATURES as AI_FEATURES
    from app.queue_logic import compute_effective_daily_limit

    # ------------------------------------------------------------------
    # Gather non-DB calls concurrently; run DB queries sequentially.
    # AsyncSession does not support concurrent operations on the same session.
    # ------------------------------------------------------------------
    google_creds, o365_creds, unibox_status = await asyncio.gather(
        get_google_oauth_credentials(db),
        get_office365_oauth_credentials(db),
        get_unibox_sync_status(),
    )

    gmail_sync_cfg = await get_gmail_sync_config(db)
    test_mode_val = await get_test_mode(db)
    gmail_rows = await db.execute(
        select(GmailAccount, Inbox).join(Inbox, GmailAccount.inbox_id == Inbox.id).order_by(GmailAccount.created_at.desc())
    )
    o365_rows = await db.execute(
        select(Office365Account, Inbox).join(Inbox, Office365Account.inbox_id == Inbox.id).order_by(Office365Account.created_at.desc())
    )
    inbox_rows = await db.execute(select(Inbox).order_by(Inbox.id))
    ai_settings_rows = await db.execute(select(AppSetting).where(AppSetting.key.like("ai_%")))

    google_client_id, google_client_secret = google_creds
    o365_client_id, o365_client_secret, o365_tenant_id = o365_creds

    gmail_rows_list = gmail_rows.all()
    o365_rows_list = o365_rows.all()
    inbox_list = list(inbox_rows.scalars().all())

    gmail_probe_results = []
    o365_probe_results = []
    domain_probe_map: dict[int, str] = {}
    if gmail_rows_list:
        gmail_probe_results = await asyncio.gather(
            *[asyncio.to_thread(_probe_gmail, ga.access_token or "") for ga, _ in gmail_rows_list]
        )
    if o365_rows_list:
        o365_probe_results = await asyncio.gather(
            *[asyncio.to_thread(_probe_o365, oa.access_token or "") for oa, _ in o365_rows_list]
        )
    inboxes_with_domains = [(inbox.id, inbox.tracking_domain) for inbox in inbox_list if inbox.tracking_domain]
    if inboxes_with_domains:
        raw_domain_probes = await asyncio.gather(
            *[asyncio.to_thread(_probe_tracking_domain, domain) for _, domain in inboxes_with_domains]
        )
        for (inbox_id, _), result in zip(inboxes_with_domains, raw_domain_probes):
            domain_probe_map[inbox_id] = result

    beacon_probe_map: dict[int, str] = {}

    def _host_from_beacon_base(base_url: str) -> str:
        from urllib.parse import urlparse

        h = (urlparse(base_url).hostname or "").strip().lower()
        return h

    inboxes_with_beacon = [
        (inbox.id, inbox.beacon_base_url)
        for inbox in inbox_list
        if getattr(inbox, "beacon_connected", False) and inbox.beacon_base_url
    ]
    if inboxes_with_beacon:
        hosts = [_host_from_beacon_base(url) for _, url in inboxes_with_beacon]
        raw_beacon = await asyncio.gather(
            *[
                asyncio.to_thread(_probe_tracking_domain, host if host else "invalid")
                for host in hosts
            ]
        )
        for (inbox_id, _), host, result in zip(inboxes_with_beacon, hosts, raw_beacon):
            beacon_probe_map[inbox_id] = "error" if not host else result

    # ------------------------------------------------------------------
    # Google OAuth
    # ------------------------------------------------------------------
    google_configured = bool(google_client_id and google_client_secret)
    google_accounts = []
    for idx, (ga, inbox) in enumerate(gmail_rows_list):
        probe = gmail_probe_results[idx] if idx < len(gmail_probe_results) else "unknown"
        token_st = _gmail_token_status(ga.token_expiry)
        missing = _gmail_missing_scopes(ga.scopes)
        google_accounts.append({
            "id": ga.id,
            "inbox_id": ga.inbox_id,
            "google_email": ga.google_email,
            "inbox_display_name": inbox.display_name,
            "token_status": token_st,
            "token_valid": token_st == "valid",
            "token_expiry": ga.token_expiry.isoformat() if ga.token_expiry else None,
            "can_refresh": bool(ga.refresh_token),
            "login_status": probe or "unknown",
            "login_valid": probe == "valid",
            "missing_scopes": [{"scope": s, "name": s.split("/")[-1]} for s in missing],
        })

    # ------------------------------------------------------------------
    # Microsoft OAuth
    # ------------------------------------------------------------------
    o365_configured = bool(o365_client_id and o365_client_secret)
    o365_accounts = []
    for idx, (oa, inbox) in enumerate(o365_rows_list):
        probe = o365_probe_results[idx] if idx < len(o365_probe_results) else "unknown"
        token_st = _o365_token_status(oa.token_expiry)
        o365_accounts.append({
            "id": oa.id,
            "inbox_id": oa.inbox_id,
            "microsoft_email": oa.microsoft_email,
            "inbox_display_name": inbox.display_name,
            "token_status": token_st,
            "token_valid": token_st == "valid",
            "token_expiry": oa.token_expiry.isoformat() if oa.token_expiry else None,
            "login_status": probe or "unknown",
            "login_valid": probe == "valid",
        })

    # ------------------------------------------------------------------
    # Inboxes
    # ------------------------------------------------------------------
    inboxes = []
    for inbox in inbox_list:
        inboxes.append({
            "id": inbox.id,
            "email": inbox.email,
            "display_name": inbox.display_name,
            "provider": inbox.provider,
            "paused": inbox.paused,
            "effective_max_per_day": compute_effective_daily_limit(inbox),
            "tracking_domain": inbox.tracking_domain,
            "tracking_domain_status": domain_probe_map.get(inbox.id),
            "beacon_connected": getattr(inbox, "beacon_connected", False),
            "beacon_base_url": getattr(inbox, "beacon_base_url", None),
            "beacon_status": beacon_probe_map.get(inbox.id),
        })

    # ------------------------------------------------------------------
    # AI features
    # ------------------------------------------------------------------
    ai_rows_dict = {r.key: r.value for r in ai_settings_rows.scalars().all()}

    def _build_ai(fid: str) -> dict:
        meta = AI_FEATURES.get(fid, {})
        prefix = f"ai_{fid}_"
        raw_key = ai_rows_dict.get(f"{prefix}api_key", "")
        return {
            "id": fid,
            "label": meta.get("label", fid),
            "enabled": ai_rows_dict.get(f"{prefix}enabled", "false").lower() in ("true", "1", "yes"),
            "api_key_set": bool(raw_key),
            "connection_tested": ai_rows_dict.get(f"{prefix}connection_tested", "false").lower() in ("true", "1", "yes"),
            "last_error": ai_rows_dict.get(f"{prefix}last_error", ""),
            "last_error_at": ai_rows_dict.get(f"{prefix}last_error_at", ""),
        }

    ai_features = [_build_ai(fid) for fid in AI_FEATURES]

    # ------------------------------------------------------------------
    # Email verification
    # ------------------------------------------------------------------
    from app.app_settings import (
        get_setting as _get_setting,
        EMAIL_VERIFICATION_ENABLED,
        EMAIL_VERIFICATION_PROVIDER,
        EMAIL_VERIFICATION_API_KEY,
        EMAIL_VERIFICATION_CONNECTION_TESTED,
        EMAIL_VERIFICATION_LAST_ERROR,
        EMAIL_VERIFICATION_LAST_ERROR_AT,
    )
    ev_enabled = (await _get_setting(db, EMAIL_VERIFICATION_ENABLED) or "false").lower() in ("true", "1", "yes")
    ev_provider = await _get_setting(db, EMAIL_VERIFICATION_PROVIDER) or "mailtester_ninja"
    ev_api_key_set = bool(await _get_setting(db, EMAIL_VERIFICATION_API_KEY) or "")
    ev_connection_tested = (await _get_setting(db, EMAIL_VERIFICATION_CONNECTION_TESTED) or "false").lower() in ("true", "1", "yes")
    ev_last_error = await _get_setting(db, EMAIL_VERIFICATION_LAST_ERROR) or ""
    ev_last_error_at = await _get_setting(db, EMAIL_VERIFICATION_LAST_ERROR_AT) or ""

    # ------------------------------------------------------------------
    # Unibox / Gmail push sync
    # ------------------------------------------------------------------
    push_topic = (gmail_sync_cfg or {}).get("push_topic", "")
    sync_interval = int((gmail_sync_cfg or {}).get("sync_interval_minutes", 5))

    # ------------------------------------------------------------------
    # Active flags
    # ------------------------------------------------------------------
    test_mode = bool(test_mode_val)

    return {
        "google_oauth": {
            "configured": google_configured,
            "accounts": google_accounts,
        },
        "microsoft_oauth": {
            "configured": o365_configured,
            "accounts": o365_accounts,
        },
        "inboxes": inboxes,
        "unibox_sync": {
            "push_enabled": bool(push_topic),
            "push_topic": push_topic,
            "sync_interval_minutes": sync_interval,
            "initial_list_sync_in_progress": unibox_status.get("initial_list_sync_in_progress", False),
            "inflight_inbox_ids": unibox_status.get("inflight_inbox_ids", []),
        },
        "ai_features": ai_features,
        "email_verification": {
            "enabled": ev_enabled,
            "provider": ev_provider,
            "api_key_set": ev_api_key_set,
            "connection_tested": ev_connection_tested,
            "last_error": ev_last_error,
            "last_error_at": ev_last_error_at,
        },
        "flags": {
            "test_mode": test_mode,
        },
    }
