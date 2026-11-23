"""Pre-send decision hook.

Right before Quickly sends the first email of a sequence to a lead, it can ask
one HTTPS endpoint of your choosing whether to go ahead.  The endpoint answers
``allow``, ``hold`` or ``deny``.  The contract is documented in
``docs/PRE_SEND_HOOK.md``.

The hook is off until a URL is set.  URL, secret, failure policy, which sends
are checked and which lead fields are sent are all stored as ``app_setting``
rows and edited from Settings (environment variables override them).  A fixed
three-second timeout applies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import time as time_provider
from app.app_settings import (
    PRE_SEND_HOOK_FIELDS_KEY,
    PRE_SEND_HOOK_ON_ERROR_KEY,
    PRE_SEND_HOOK_SCOPE_KEY,
    PRE_SEND_HOOK_SECRET_KEY,
    PRE_SEND_HOOK_URL_KEY,
    get_setting,
)
from app.models import Campaign, Lead
from app.settings_manager import settings

log = logging.getLogger(__name__)

CONTRACT_VERSION = 1
DECISIONS = ("allow", "hold", "deny")
ON_ERROR_ALLOW = "allow"
ON_ERROR_HOLD = "hold"
SCOPE_FIRST = "first"
SCOPE_ALL = "all"
FIELDS_MINIMAL = "minimal"
FIELDS_CONTACT = "contact"

#: How long to wait for the hook to answer.
TIMEOUT = 3.0
#: A ``hold`` with no ``retry_after_seconds`` is asked again after this long.
DEFAULT_HOLD_RETRY = timedelta(hours=6)
#: The longest a hook may ask Quickly to wait before asking again.
MAX_HOLD_RETRY = timedelta(days=7)
#: An ``allow`` or ``deny`` is reused for this long for the same lead and hook.
DECISION_TTL = timedelta(days=30)
#: Reasons and references are stored and logged at most this long.
TEXT_LIMIT = 300

# Tests inject an ``httpx.MockTransport`` here; production uses the default.
_transport: httpx.AsyncBaseTransport | None = None


@dataclass(frozen=True)
class HookConfig:
    url: str
    secret: str
    on_error: str
    scope: str
    fields: str

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def checks_sequence(self, sequence_index: int) -> bool:
        return self.scope == SCOPE_ALL or sequence_index == 0

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""


@dataclass(frozen=True)
class HookDecision:
    decision: str            # allow | hold | deny | error
    reason: str = ""
    reference: str = ""
    retry_after: timedelta | None = None
    cached: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.decision == "allow"

    def summary(self) -> str:
        parts = [self.decision]
        if self.reason:
            parts.append(self.reason)
        if self.reference:
            parts.append(f"ref {self.reference}")
        if self.error:
            parts.append(self.error)
        return ": ".join(parts[:2]) + ("" if len(parts) < 3 else f" ({'; '.join(parts[2:])})")


# ── Configuration ────────────────────────────────────────────────────────────

def _env_or_db(env_value: str, db_value: str | None) -> str:
    return env_value.strip() if env_value and env_value.strip() else (db_value or "").strip()


def url_allowed(url: str) -> bool:
    """HTTPS only, except plain HTTP to the local machine for development."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")


async def load_config(db: AsyncSession) -> HookConfig:
    """Environment first, then the ``app_setting`` rows, then the defaults."""
    url = _env_or_db(settings.pre_send_hook_url, await get_setting(db, PRE_SEND_HOOK_URL_KEY))
    if url and not url_allowed(url):
        log.warning("pre_send_hook: ignoring hook URL that is not https (or http to localhost)")
        url = ""
    secret = _env_or_db(settings.pre_send_hook_secret, await get_setting(db, PRE_SEND_HOOK_SECRET_KEY))
    on_error = _env_or_db(settings.pre_send_hook_on_error, await get_setting(db, PRE_SEND_HOOK_ON_ERROR_KEY)).lower()
    if on_error not in (ON_ERROR_ALLOW, ON_ERROR_HOLD):
        on_error = ON_ERROR_ALLOW
    scope = _env_or_db(settings.pre_send_hook_scope, await get_setting(db, PRE_SEND_HOOK_SCOPE_KEY)).lower()
    if scope not in (SCOPE_FIRST, SCOPE_ALL):
        scope = SCOPE_FIRST
    fields = _env_or_db(settings.pre_send_hook_fields, await get_setting(db, PRE_SEND_HOOK_FIELDS_KEY)).lower()
    if fields not in (FIELDS_MINIMAL, FIELDS_CONTACT):
        fields = FIELDS_MINIMAL
    return HookConfig(url=url, secret=secret, on_error=on_error, scope=scope, fields=fields)


# ── Request and signature ────────────────────────────────────────────────────

def build_payload(
    lead: Lead, campaign: Campaign, fields: str = FIELDS_MINIMAL
) -> dict[str, Any]:
    """The lead and campaign, plus contact details when configured."""
    lead_data: dict[str, Any] = {"id": lead.id, "email": lead.email}
    if fields == FIELDS_CONTACT:
        lead_data["name"] = _text(lead.name, 200)
        lead_data["company"] = _text(lead.company, 200)
        custom = getattr(lead, "custom_data", None)
        if isinstance(custom, dict):
            lead_data["custom_data"] = {
                str(key)[:64]: _text(value, 500) for key, value in custom.items()
            }
    return {
        "event": "pre_send",
        "version": CONTRACT_VERSION,
        "timestamp": time_provider.utcnow().isoformat() + "Z",
        "lead": lead_data,
        "campaign": {"id": campaign.id, "name": _text(campaign.name, 200)},
    }


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """``v1=<hex HMAC-SHA256 of "<timestamp>.<body>">``."""
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


async def ask(config: HookConfig, payload: dict[str, Any]) -> HookDecision:
    """One POST to the hook.  Never raises; a failure is a decision of ``error``."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time_provider.utcnow().timestamp()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Quickly-PreSendHook/{CONTRACT_VERSION}",
        "X-Quickly-Event": "pre_send",
        "X-Quickly-Timestamp": timestamp,
    }
    if config.secret:
        headers["X-Quickly-Signature"] = sign(config.secret, timestamp, body)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT), transport=_transport) as client:
            resp = await client.post(config.url, content=body, headers=headers)
    except httpx.TimeoutException:
        return HookDecision("error", error=f"no answer within {TIMEOUT:g}s")
    except httpx.HTTPError as exc:
        return HookDecision("error", error=f"request failed: {type(exc).__name__}")
    return parse_response(resp)


def parse_response(resp: httpx.Response) -> HookDecision:
    if resp.status_code != 200:
        return HookDecision("error", error=f"HTTP {resp.status_code}")
    try:
        data = json.loads(resp.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HookDecision("error", error="response was not JSON")
    if not isinstance(data, dict):
        return HookDecision("error", error="response was not a JSON object")
    decision = str(data.get("decision", "")).strip().lower()
    if decision not in DECISIONS:
        return HookDecision("error", error=f"unknown decision {decision!r}")
    retry_after = None
    if decision == "hold":
        retry_after = _retry_after(data.get("retry_after_seconds"))
    return HookDecision(
        decision,
        reason=_text(data.get("reason"), TEXT_LIMIT),
        reference=_text(data.get("reference"), 120),
        retry_after=retry_after,
    )


# ── Decision cache on the lead ───────────────────────────────────────────────

def cached_decision(lead: Lead, config: HookConfig, now: datetime) -> HookDecision | None:
    """Reuse a recent decision from the same hook: allow and deny for 30 days,
    hold until its retry time."""
    stored = getattr(lead, "pre_send_check", None)
    if not isinstance(stored, dict) or stored.get("host") != config.host:
        return None
    decision = stored.get("decision")
    checked_at = _parse_time(stored.get("checked_at"))
    if decision not in DECISIONS or checked_at is None:
        return None
    if decision == "hold":
        retry_at = _parse_time(stored.get("retry_at"))
        if retry_at is None or now >= retry_at:
            return None
    elif now - checked_at > DECISION_TTL:
        return None
    return HookDecision(
        decision,
        reason=str(stored.get("reason") or ""),
        reference=str(stored.get("reference") or ""),
        cached=True,
    )


def record_decision(lead: Lead, config: HookConfig, result: HookDecision, now: datetime) -> None:
    """Write the decision to the lead.  An ``error`` is recorded with the
    policy that was applied, but never cached as a decision."""
    retry_at = None
    if result.decision == "hold":
        retry_at = (now + (result.retry_after or DEFAULT_HOLD_RETRY)).isoformat() + "Z"
    lead.pre_send_decision = result.decision if result.decision in DECISIONS else f"error_{config.on_error}"
    lead.pre_send_check = {
        "decision": result.decision,
        "reason": result.reason or None,
        "reference": result.reference or None,
        "error": result.error or None,
        "checked_at": now.isoformat() + "Z",
        "retry_at": retry_at,
        "host": config.host,
    }


async def decide(db: AsyncSession, lead: Lead, campaign: Campaign, config: HookConfig) -> HookDecision:
    """Ask the hook (or reuse a recent decision) and record the outcome."""
    now = time_provider.utcnow()
    cached = cached_decision(lead, config, now)
    if cached is not None:
        return cached
    result = await ask(config, build_payload(lead, campaign, config.fields))
    record_decision(lead, config, result, now)
    await db.flush()
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _retry_after(value: Any) -> timedelta | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return min(timedelta(seconds=seconds), MAX_HOLD_RETRY)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit]
