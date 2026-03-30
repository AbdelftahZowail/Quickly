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
from collections import defaultdict
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


def _fetch_beacon_health_payload(base_url: str, setup_token: str | None) -> tuple[str, dict | None]:
    """GET Beacon ``/api/v1/health``. Returns (``'ok'``|``'error'``, parsed JSON or None)."""
    import httpx

    if not base_url or not (setup_token or "").strip():
        return "error", None
    base = base_url.strip().rstrip("/")
    url = f"{base}/api/v1/health"
    headers = {"Authorization": f"Bearer {setup_token.strip()}"}
    for verify_ssl in (True, False):
        try:
            with httpx.Client(timeout=15, follow_redirects=True, verify=verify_ssl) as client:
                resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if not data.get("ok"):
                        return "error", None
                    if not data.get("connected"):
                        return "error", None
                    return "ok", data
                except Exception:
                    return "error", None
            return "error", None
        except httpx.ConnectError:
            pass
        except httpx.TimeoutException:
            return "error", None
        except Exception:
            pass
    return "error", None


def _beacon_registration_actual(data: dict | None, inbox_id: int) -> int:
    if not data:
        return 0
    raw = data.get("registration_counts") or {}
    v = raw.get(str(inbox_id), raw.get(inbox_id))
    if v is None:
        return 0
    return int(v)


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
    beacon_reg_fields: dict[int, dict] = {}
    beacon_repaired_ids: list[int] = []

    beacon_by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    inbox_to_key: dict[int, tuple[str, str]] = {}
    for inbox in inbox_list:
        if not getattr(inbox, "beacon_connected", False) or not inbox.beacon_base_url:
            continue
        url = inbox.beacon_base_url.strip().rstrip("/")
        tok = (getattr(inbox, "beacon_setup_token", None) or "").strip()
        if not url or not tok:
            continue
        key = (url, tok)
        beacon_by_key[key].append(inbox.id)
        inbox_to_key[inbox.id] = key

    unique_beacon_keys = list(beacon_by_key.keys())
    key_to_data: dict[tuple[str, str], dict | None] = {}
    if unique_beacon_keys:
        _fetched = await asyncio.gather(
            *[
                asyncio.to_thread(_fetch_beacon_health_payload, u, t)
                for u, t in unique_beacon_keys
            ]
        )
        for _i, _key in enumerate(unique_beacon_keys):
            _st, _payload = _fetched[_i]
            key_to_data[_key] = _payload if _st == "ok" else None

    from app.beacon_sync import collect_inbox_beacon_items, sync_inbox_tracking_to_beacon

    drift_ids: list[int] = []
    for inbox in inbox_list:
        iid = inbox.id
        if iid not in inbox_to_key:
            continue
        key = inbox_to_key[iid]
        data = key_to_data.get(key)
        if data is None:
            beacon_probe_map[iid] = "error"
            beacon_reg_fields[iid] = {
                "beacon_registration_expected": None,
                "beacon_registration_actual": None,
                "beacon_registration_ok": None,
                "beacon_registration_note": None,
                "beacon_registration_repaired": False,
            }
            continue
        inbox_ids_b = set(data.get("inbox_ids") or [])
        if iid not in inbox_ids_b:
            beacon_probe_map[iid] = "error"
            beacon_reg_fields[iid] = {
                "beacon_registration_expected": None,
                "beacon_registration_actual": None,
                "beacon_registration_ok": None,
                "beacon_registration_note": "This inbox is not listed as connected on Beacon.",
                "beacon_registration_repaired": False,
            }
            continue
        beacon_probe_map[iid] = "ok"
        if "registration_counts" not in data:
            beacon_reg_fields[iid] = {
                "beacon_registration_expected": None,
                "beacon_registration_actual": None,
                "beacon_registration_ok": None,
                "beacon_registration_note": "Beacon does not report registration_counts (upgrade Beacon).",
                "beacon_registration_repaired": False,
            }
            continue
        items = await collect_inbox_beacon_items(db, iid)
        expected = len(items)
        actual = _beacon_registration_actual(data, iid)
        beacon_reg_fields[iid] = {
            "beacon_registration_expected": expected,
            "beacon_registration_actual": actual,
            "beacon_registration_ok": expected == actual,
            "beacon_registration_note": None,
            "beacon_registration_repaired": False,
        }
        if expected != actual:
            drift_ids.append(iid)

    keys_to_refetch: set[tuple[str, str]] = {inbox_to_key[i] for i in drift_ids}
    for iid in drift_ids:
        try:
            await sync_inbox_tracking_to_beacon(db, iid)
            beacon_reg_fields[iid]["beacon_registration_repaired"] = True
            beacon_repaired_ids.append(iid)
        except Exception:
            log.exception("beacon health reconcile: sync failed for inbox_id=%s", iid)

    if keys_to_refetch:
        _keys_list = list(keys_to_refetch)
        _refetched = await asyncio.gather(
            *[
                asyncio.to_thread(_fetch_beacon_health_payload, u, t)
                for u, t in _keys_list
            ]
        )
        for _key, _pair in zip(_keys_list, _refetched):
            _st, _payload = _pair
            if _st == "ok" and _payload:
                key_to_data[_key] = _payload

        for inbox in inbox_list:
            iid = inbox.id
            if iid not in inbox_to_key or beacon_probe_map.get(iid) != "ok":
                continue
            fld = beacon_reg_fields.get(iid)
            if not fld or fld.get("beacon_registration_expected") is None:
                continue
            if inbox_to_key[iid] not in keys_to_refetch:
                continue
            data2 = key_to_data.get(inbox_to_key[iid])
            expected = fld["beacon_registration_expected"]
            actual2 = _beacon_registration_actual(data2, iid)
            ok = expected == actual2
            note = None
            if not ok:
                if actual2 > expected:
                    note = (
                        "Beacon has more registration rows than Quickly "
                        "(possible orphan rows on Beacon)."
                    )
                else:
                    note = (
                        "Registration count still below Quickly after sync; "
                        "check Beacon logs and network."
                    )
            beacon_reg_fields[iid] = {
                **fld,
                "beacon_registration_actual": actual2,
                "beacon_registration_ok": ok,
                "beacon_registration_note": note,
            }

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
    _reg_default = {
        "beacon_registration_expected": None,
        "beacon_registration_actual": None,
        "beacon_registration_ok": None,
        "beacon_registration_note": None,
        "beacon_registration_repaired": False,
    }
    inboxes = []
    for inbox in inbox_list:
        reg = {**_reg_default, **beacon_reg_fields.get(inbox.id, {})}
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
            **reg,
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
        "beacon_reconciliation": {
            "repaired_inbox_ids": beacon_repaired_ids,
        },
    }
