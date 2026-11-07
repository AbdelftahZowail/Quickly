"""Provider-agnostic pre-send decision hook.

Right before Quickly sends the first email of a sequence to a lead, it can ask
one HTTPS endpoint of your choosing whether to go ahead.  The endpoint answers
``allow``, ``hold`` or ``deny``.  Quickly does not know or care who runs the
endpoint: a compliance checker, your CRM, a do-not-contact list, a script of
your own.  The contract is documented in ``docs/PRE_SEND_HOOK.md``.

Design rules, in order of importance:

* Off by default.  Nothing leaves Quickly until ``PRE_SEND_HOOK_URL`` is set.
* Fail open by default.  A hook that is down, slow or broken never stops
  sending unless the operator chooses ``PRE_SEND_HOOK_ON_ERROR=hold``.
* Minimal data by default.  The request carries the lead's address and the
  campaign, nothing else, unless ``PRE_SEND_HOOK_FIELDS=contact`` adds the
  name and custom data.
* Short timeout.  Three seconds by default, ten at most.
* Signed.  With ``PRE_SEND_HOOK_SECRET`` set, every request carries an HMAC
  signature the endpoint can verify, and a signed response is verified too.
* Decisions are recorded on the lead (``pre_send_decision`` and
  ``pre_send_check``) so the UI and the API can show why a lead was held.
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
    PRE_SEND_HOOK_TIMEOUT_KEY,
    PRE_SEND_HOOK_URL_KEY,
    get_setting,
)
from app.models import Campaign, Lead
from app.settings_manager import settings

log = logging.getLogger(__name__)

CONTRACT_VERSION = 1
DECISIONS = ("allow", "hold", "deny")
FIELDS_MINIMAL = "minimal"
FIELDS_CONTACT = "contact"
SCOPE_FIRST = "first"
SCOPE_ALL = "all"
ON_ERROR_ALLOW = "allow"
ON_ERROR_HOLD = "hold"

DEFAULT_TIMEOUT = 3.0
MAX_TIMEOUT = 10.0
MIN_TIMEOUT = 1.0
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
    timeout: float
    on_error: str
    fields: str
    scope: str

    @property
    def enabled(self) -> bool:
        return bool(self.url)

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


def _clamp_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, value))


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
    timeout = _clamp_timeout(
        _env_or_db(settings.pre_send_hook_timeout, await get_setting(db, PRE_SEND_HOOK_TIMEOUT_KEY))
    )
    on_error = _env_or_db(settings.pre_send_hook_on_error, await get_setting(db, PRE_SEND_HOOK_ON_ERROR_KEY)).lower()
    if on_error not in (ON_ERROR_ALLOW, ON_ERROR_HOLD):
        on_error = ON_ERROR_ALLOW
    fields = _env_or_db(settings.pre_send_hook_fields, await get_setting(db, PRE_SEND_HOOK_FIELDS_KEY)).lower()
    if fields not in (FIELDS_MINIMAL, FIELDS_CONTACT):
        fields = FIELDS_MINIMAL
    scope = _env_or_db(settings.pre_send_hook_scope, await get_setting(db, PRE_SEND_HOOK_SCOPE_KEY)).lower()
    if scope not in (SCOPE_FIRST, SCOPE_ALL):
        scope = SCOPE_FIRST
    return HookConfig(url=url, secret=secret, timeout=timeout, on_error=on_error, fields=fields, scope=scope)


# ── Request and signature ────────────────────────────────────────────────────

def build_payload(lead: Lead, campaign: Campaign, sequence_index: int, config: HookConfig) -> dict[str, Any]:
    """What the hook receives.  ``minimal`` is the address and the campaign only."""
    lead_data: dict[str, Any] = {"id": lead.id, "email": lead.email}
    if config.fields == FIELDS_CONTACT:
        lead_data["name"] = _text(getattr(lead, "name", ""), 160)
        custom = getattr(lead, "custom_data", None) or {}
        if isinstance(custom, dict):
            lead_data["custom_data"] = {
                str(k)[:64]: (_text(v, 200) if isinstance(v, str) else v)
                for k, v in list(custom.items())[:40]
                if isinstance(v, (str, int, float, bool))
            }
    return {
        "event": "pre_send",
        "version": CONTRACT_VERSION,
        "timestamp": time_provider.utcnow().isoformat() + "Z",
        "lead": lead_data,
        "campaign": {
            "id": campaign.id,
            "public_id": getattr(campaign, "public_id", None),
            "name": _text(campaign.name, 200),
        },
        "sequence_index": sequence_index,
    }


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """``v1=<hex HMAC-SHA256 of "<timestamp>.<body>">``."""
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def verify_response_signature(secret: str, header: str | None, body: bytes) -> bool:
    """A response may carry ``X-Hook-Signature: v1=<hex HMAC-SHA256 of the body>``.

    Missing header: accepted (signing responses is optional).  Present but
    wrong: rejected."""
    if not header:
        return True
    expected = "v1=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


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
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout), transport=_transport) as client:
            resp = await client.post(config.url, content=body, headers=headers)
    except httpx.TimeoutException:
        return HookDecision("error", error=f"no answer within {config.timeout:g}s")
    except httpx.HTTPError as exc:
        return HookDecision("error", error=f"request failed: {type(exc).__name__}")
    return parse_response(config, resp)


def parse_response(config: HookConfig, resp: httpx.Response) -> HookDecision:
    if resp.status_code != 200:
        return HookDecision("error", error=f"HTTP {resp.status_code}")
    raw = resp.content
    if config.secret and not verify_response_signature(config.secret, resp.headers.get("X-Hook-Signature"), raw):
        return HookDecision("error", error="response signature did not verify")
    try:
        data = json.loads(raw.decode("utf-8"))
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
    if not isinstance(stored, dict) or stored.get("host") != config.host or stored.get("fields") != config.fields:
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


def record_decision(lead: Lead, config: HookConfig, result: HookDecision, sequence_index: int, now: datetime) -> None:
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
        "fields": config.fields,
        "sequence_index": sequence_index,
    }


async def decide(
    db: AsyncSession, lead: Lead, campaign: Campaign, sequence_index: int, config: HookConfig
) -> HookDecision:
    """Ask the hook (or reuse a recent decision) and record the outcome."""
    now = time_provider.utcnow()
    cached = cached_decision(lead, config, now)
    if cached is not None:
        return cached
    result = await ask(config, build_payload(lead, campaign, sequence_index, config))
    record_decision(lead, config, result, sequence_index, now)
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
