"""Precleared cold-email check (https://precleared.dev).

Before the first email of a campaign goes to a lead, the send job asks
Precleared whether a cold email to that address is allowed where the lead
is: UK PECR (a named contact at a limited company may be emailed, a sole
trader may not without consent), US CAN-SPAM, Canada's CASL and so on.
Precleared looks the company up live (Companies House, the domain, the
company's website) and answers with a verdict, the rule that decided it,
what to do next and a signed receipt id.  Follow-ups, replies and test-mode
sends are never checked.

Settings, environment first and then the ``app_setting`` table:

* ``PRECLEARED_MODE``: ``hold`` (default) sends only on ``ok_to_email`` and
  pauses the lead for anything else; ``warn`` records the verdict and sends
  anyway; ``off`` makes no call.
* ``PRECLEARED_API_KEY``: optional.  Without a key Precleared allows 10 checks
  a day per network.  When that runs out the sender asks Precleared for a free
  key once, with the sending inbox's address, and stores it as
  ``precleared_api_key``.
* ``PRECLEARED_SENDER_COUNTRY``: ``GB`` (default), ``US`` or ``AE``, where the
  sending business is based.

One check per address: a definite verdict (``ok_to_email``, ``consent_needed``
or ``blocked``) is kept on the lead for CACHE_DAYS together with a fingerprint
of the facts it was decided on, so re-enrolments and resumed leads cost
nothing.  ``unknown`` is never reused.  A check that cannot run (no
connection, a 5xx, a rejected key, the daily allowance used up) trips a short
cooldown so other due slots skip the call, and is reported once through the
``feature.error`` event so it shows in Notifications.

Decision support, not legal advice.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import time as time_provider
from app.app_settings import (
    PRECLEARED_API_KEY_KEY,
    PRECLEARED_LAST_ERROR,
    PRECLEARED_LAST_ERROR_AT,
    PRECLEARED_MODE_KEY,
    PRECLEARED_SENDER_COUNTRY_KEY,
    put_setting,
)
from app.models import AppSetting, Lead
from app.settings_manager import settings

log = logging.getLogger("quickly.precleared")

API_URL = "https://precleared.dev/api/v1/check?src=quickly"
USER_AGENT = "quickly (+https://github.com/AbdelftahZowail/Quickly)"
GET_KEY_URL = "https://precleared.dev/get-key"
# Precleared answers within 25 seconds (with verdict "unknown" when a register
# lookup is slow), so the read timeout sits just above that.
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
CACHE_DAYS = 30
# Longest wait for a 429 rate_limited ``retry_after`` before the single retry.
MAX_RETRY_AFTER = 30.0
# After a check could not run, no fresh calls for this long; stored verdicts
# are still used, so leads already cleared keep sending.
UNAVAILABLE_COOLDOWN = timedelta(minutes=15)

MODE_HOLD = "hold"
MODE_WARN = "warn"
MODE_OFF = "off"
MODES = (MODE_HOLD, MODE_WARN, MODE_OFF)
SENDER_COUNTRIES = ("GB", "US", "AE")
DEFAULT_SENDER_COUNTRY = "GB"
# Quickly does not know how a CSV address was obtained, so it never claims
# the address was published.
ADDRESS_SOURCE = "unknown"

VERDICTS = ("ok_to_email", "consent_needed", "blocked", "unknown")
DEFINITE_VERDICTS = ("ok_to_email", "consent_needed", "blocked")
# Precleared's limits for the names sent with a check.
MAX_COMPANY_NAME = 160
MAX_RECIPIENT_NAME = 120
# Only Precleared itself is ever asked for a key.
KEY_HOSTS = ("precleared.dev",)

# Tests inject an ``httpx.MockTransport`` here; production uses the default.
_transport: httpx.AsyncBaseTransport | None = None
# The wait before the one retry on a 429 rate_limited answer (tests patch it).
_sleep = asyncio.sleep


# ── Settings ─────────────────────────────────────────────────────────────────

@dataclass
class PreclearedConfig:
    mode: str = MODE_HOLD
    api_key: str = ""
    sender_country: str = DEFAULT_SENDER_COUNTRY


def _env_or_db(env_value: str, db_value: str | None) -> str:
    return (env_value or "").strip() or (db_value or "").strip()


async def load_config(db: AsyncSession) -> PreclearedConfig:
    """Return the Precleared settings: environment first, then ``app_setting``,
    then the defaults (``hold``, no key, ``GB``)."""
    rows = await db.execute(
        select(AppSetting.key, AppSetting.value).where(
            AppSetting.key.in_(
                (PRECLEARED_MODE_KEY, PRECLEARED_API_KEY_KEY, PRECLEARED_SENDER_COUNTRY_KEY)
            )
        )
    )
    stored = {key: value for key, value in rows.all()}

    mode = _env_or_db(settings.precleared_mode, stored.get(PRECLEARED_MODE_KEY)).lower() or MODE_HOLD
    if mode not in MODES:
        log.warning("Precleared: PRECLEARED_MODE %r is not hold, warn or off; using hold", mode)
        mode = MODE_HOLD

    country = _env_or_db(
        settings.precleared_sender_country, stored.get(PRECLEARED_SENDER_COUNTRY_KEY)
    ).upper() or DEFAULT_SENDER_COUNTRY
    if country not in SENDER_COUNTRIES:
        log.warning(
            "Precleared: PRECLEARED_SENDER_COUNTRY %r is not GB, US or AE; using GB", country
        )
        country = DEFAULT_SENDER_COUNTRY

    api_key = _env_or_db(settings.precleared_api_key, stored.get(PRECLEARED_API_KEY_KEY))
    return PreclearedConfig(mode=mode, api_key=api_key, sender_country=country)


# ── Result ───────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Precleared's answer for one lead.

    ``verdict`` is one of VERDICTS, or ``""`` when the check could not run;
    ``error`` then says why, in words meant for the operator.  ``reason`` is
    the rule that decided a hold (with its source) and ``next_step`` what to
    do about it.  ``cached`` is True when the answer came from the lead's
    stored verdict rather than a call.
    """

    verdict: str = ""
    reason: str = ""
    next_step: str = ""
    receipt_id: str = ""
    error: str = ""
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.verdict == "ok_to_email"

    @property
    def unavailable(self) -> bool:
        """True when the check could not run (network, allowance, key, server)."""
        return not self.verdict

    def summary(self) -> str:
        """One line for logs and notifications."""
        if self.unavailable:
            return self.error
        text = self.verdict
        if self.reason:
            text += f": {self.reason}"
        if self.next_step:
            text += ("" if text.endswith((".", "!", "?")) else ".") + f" Next step: {self.next_step}"
        return text


class _Unavailable(Exception):
    """The check could not run; ``str(exc)`` is the operator-facing reason."""


# ── Cooldown after a failed call ─────────────────────────────────────────────
# Process-wide, like the inbox auth-failure cooldown in app.jobs: one failure
# per cooldown window, not one per due slot.
_unavailable_until: datetime | None = None
_unavailable_error: str = ""
# A free key is requested at most once per process.
_key_requested: bool = False


def reset_state() -> None:
    """Forget the cooldown and the key request (tests)."""
    global _unavailable_until, _unavailable_error, _key_requested
    _unavailable_until = None
    _unavailable_error = ""
    _key_requested = False


def _cooldown_error(now: datetime) -> str:
    """The error that tripped the cooldown while it is still active, else ""."""
    global _unavailable_until, _unavailable_error
    if _unavailable_until is None:
        return ""
    if _unavailable_until <= now:
        _unavailable_until = None
        return ""
    return _unavailable_error


async def _trip_cooldown(db: AsyncSession, error: str, now: datetime) -> None:
    """Hold fresh calls for UNAVAILABLE_COOLDOWN and tell the operator once."""
    global _unavailable_until, _unavailable_error
    fresh = _unavailable_until is None or _unavailable_until <= now
    _unavailable_until = now + UNAVAILABLE_COOLDOWN
    _unavailable_error = error
    if not fresh:
        return
    log.warning("Precleared: check unavailable, first emails wait: %s", error)
    # Same shape as the AI classifier and email verification: a recorded
    # last error plus a feature.error event for Notifications.
    try:
        await put_setting(db, PRECLEARED_LAST_ERROR, error[:500])
        await put_setting(db, PRECLEARED_LAST_ERROR_AT, now.isoformat() + "Z")
        from app.webhooks import fire_webhook_event

        await fire_webhook_event(db, "feature.error", {
            "feature": "precleared",
            "label": "Precleared cold-email check",
            "error": error,
        })
    except Exception:
        log.debug("Precleared: could not record the failed check", exc_info=True)


async def _clear_last_error(db: AsyncSession) -> None:
    """Back after a failure: forget the recorded last error."""
    global _unavailable_error
    if not _unavailable_error:
        return
    _unavailable_error = ""
    try:
        await put_setting(db, PRECLEARED_LAST_ERROR, "")
    except Exception:
        log.debug("Precleared: could not clear the last error", exc_info=True)


# ── The check ────────────────────────────────────────────────────────────────

async def check_lead(
    db: AsyncSession,
    lead: Lead,
    config: PreclearedConfig,
    operator_email: str = "",
) -> CheckResult:
    """The verdict for *lead*: the stored one when it is still good, else a
    fresh call whose answer is written to ``lead.precleared_verdict`` and
    ``lead.precleared_check`` (the caller commits).

    *operator_email* (the sending inbox's address) is only used to ask
    Precleared for a free key when the keyless daily allowance runs out; the
    new key is stored in ``app_setting`` and committed at once.
    """
    payload = _payload(lead, config)
    inputs = _fingerprint(payload)
    now = time_provider.utcnow()

    cached = _cached(lead, inputs, now)
    if cached is not None:
        return cached

    error = _cooldown_error(now)
    if error:
        return CheckResult(error=error)

    result = await _ask(db, payload, config, operator_email)
    if result.unavailable:
        await _trip_cooldown(db, result.error, now)
        return result
    await _clear_last_error(db)

    lead.precleared_verdict = result.verdict
    lead.precleared_check = {
        "verdict": result.verdict,
        "reason": result.reason,
        "next_step": result.next_step,
        "receipt_id": result.receipt_id,
        "checked_at": now.isoformat(),
        "inputs": inputs,
    }
    return result


def _payload(lead: Lead, config: PreclearedConfig) -> dict[str, Any]:
    custom = lead.custom_data if isinstance(lead.custom_data, dict) else {}
    payload: dict[str, Any] = {
        "recipient_email": (lead.email or "").strip().lower(),
        "sender_country": config.sender_country,
        "address_source": ADDRESS_SOURCE,
        "existing_customer": False,
    }
    # The names help Precleared find a UK company on the Companies House
    # register when the domain alone does not; it does not store them.
    company = _name(custom.get("company"), MAX_COMPANY_NAME)
    if company:
        payload["company_name"] = company
    name = _name(lead.name, MAX_RECIPIENT_NAME)
    if name:
        payload["recipient_name"] = name
    # Only a two-letter code from a ``country`` CSV column counts as certain;
    # "United Kingdom" or a guess would be worse than nothing.
    country = _country(custom.get("country"))
    if country:
        payload["recipient_country"] = country
    return payload


def _fingerprint(payload: dict[str, Any]) -> str:
    """Hash of the facts a verdict is decided on, so a stored verdict is only
    reused for the same question (a changed sender country or company name
    forces a fresh check)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _cached(lead: Lead, inputs: str, now: datetime) -> CheckResult | None:
    """The lead's stored verdict when it is definite, fresh and for these
    facts, else None.  ``unknown`` is never reused."""
    data = lead.precleared_check if isinstance(lead.precleared_check, dict) else None
    if not data or data.get("inputs") != inputs:
        return None
    verdict = data.get("verdict")
    if verdict not in DEFINITE_VERDICTS:
        return None
    try:
        checked_at = datetime.fromisoformat(str(data.get("checked_at")))
    except (TypeError, ValueError):
        return None
    if checked_at.tzinfo is not None:
        checked_at = checked_at.replace(tzinfo=None)
    if now - checked_at > timedelta(days=CACHE_DAYS):
        return None
    return CheckResult(
        verdict=verdict,
        reason=_text(data.get("reason")),
        next_step=_text(data.get("next_step")),
        receipt_id=_text(data.get("receipt_id")),
        cached=True,
    )


async def _ask(
    db: AsyncSession, payload: dict[str, Any], config: PreclearedConfig, operator_email: str
) -> CheckResult:
    api_key = config.api_key
    try:
        resp = await _post(API_URL, payload, api_key)
        if resp.status_code == 429 and _error_code(resp) == "rate_limited":
            await _sleep(_retry_after(resp))
            resp = await _post(API_URL, payload, api_key)
        if resp.status_code == 429 and not api_key and _error_code(resp) == "daily_limit":
            api_key = await _create_key(db, _json(resp), operator_email, config)
            resp = await _post(API_URL, payload, api_key)
        return _parse(resp)
    except _Unavailable as exc:
        return CheckResult(error=_redact(str(exc), api_key))


async def _post(url: str, body: dict[str, Any], api_key: str = "") -> httpx.Response:
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        if not re.fullmatch(r"[!-~]+", api_key):
            # A key is plain printable ASCII; one pasted with a space or a
            # curly quote cannot go in a header, and the error would echo it.
            raise _Unavailable(
                "PRECLEARED_API_KEY has a character that cannot be sent (a space, "
                "a quote or a line break); check the key"
            )
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport) as client:
            return await client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise _Unavailable("Precleared took too long to answer; the check will be retried")
    except httpx.HTTPError as exc:
        raise _Unavailable(f"Precleared could not be reached ({type(exc).__name__}); the check will be retried")


def _parse(resp: httpx.Response) -> CheckResult:
    status = resp.status_code
    data = _json(resp)
    if status == 200:
        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            raise _Unavailable("Precleared returned an answer without a known verdict")
        receipt = data.get("receipt")
        return CheckResult(
            verdict=verdict,
            reason=_reason(data),
            next_step=_clean(_text(data.get("next_step")))[:500],
            receipt_id=_text(receipt.get("id")) if isinstance(receipt, dict) else "",
        )
    operator_message = _clean(_text(data.get("operator_message")) or _text(data.get("message")))
    if status == 401:
        raise _Unavailable(
            "Precleared rejected the API key (PRECLEARED_API_KEY): check it, or get a "
            f"new free key at {GET_KEY_URL}"
        )
    if status == 402:
        operator_message = operator_message or "Precleared: this key has no checks left today"
        buy_url = _https_url(data.get("buy_url"))
        if buy_url and buy_url not in operator_message:
            operator_message = f"{operator_message} {buy_url}"
        raise _Unavailable(operator_message)
    if status == 429:
        raise _Unavailable(
            operator_message
            or f"Precleared's free daily allowance is used up; add a key ({GET_KEY_URL}) or wait until tomorrow"
        )
    if status == 400:
        # A bad field is about this lead's data, not about Precleared: hold
        # just this lead, with the complaint, rather than pausing every check.
        return CheckResult(
            verdict="unknown",
            reason=f"Precleared rejected the request: {operator_message or 'bad field'}"[:500],
            next_step="Check the lead's email, name, company and country fields, then resume the lead.",
        )
    raise _Unavailable(f"Precleared answered HTTP {status}; the check will be retried")


async def _create_key(
    db: AsyncSession, limit_reply: dict[str, Any], operator_email: str, config: PreclearedConfig
) -> str:
    """Ask Precleared for a free key with the operator's email, as the keyless
    429 reply's ``create_key`` says, and store it as ``precleared_api_key``.

    Done once per process.  Returns the key, or raises _Unavailable with
    the reply's operator message (which links to the free-key page).
    """
    global _key_requested, _unavailable_until
    operator_message = _clean(
        _text(limit_reply.get("operator_message")) or _text(limit_reply.get("message"))
    ) or f"Precleared's free daily allowance is used up; get a free key at {GET_KEY_URL}"
    spec = limit_reply.get("create_key")
    url = _https_url(spec.get("url")) if isinstance(spec, dict) else ""
    if (
        _key_requested
        or not _email_ok(operator_email)
        or not url
        or str(spec.get("method", "POST")).upper() != "POST"
        or not _host_allowed(url, KEY_HOSTS)
    ):
        raise _Unavailable(operator_message)
    _key_requested = True

    body = dict(spec.get("body") or {}) if isinstance(spec.get("body"), dict) else {}
    body["email"] = operator_email.strip()
    log.info("Precleared: keyless allowance used up; asking for a free key for %s", body["email"])
    resp = await _post(url, body)
    data = _json(resp)
    key = data.get("key")
    if resp.status_code not in (200, 201) or not (
        isinstance(key, str) and re.fullmatch(r"[!-~]{16,200}", key)
    ):
        detail = _clean(_text(data.get("message"))) or f"HTTP {resp.status_code}"
        raise _Unavailable(f"{operator_message} (Quickly asked for a free key; Precleared answered: {detail})")

    # The key is shown once, so it goes to the settings table straight away;
    # it is never logged.
    await put_setting(db, PRECLEARED_API_KEY_KEY, key)
    await db.commit()
    config.api_key = key
    # Another slot may have hit the limit while this key was being made.
    _unavailable_until = None
    log.info(
        "Precleared: free key created and stored in app settings (precleared_api_key); "
        "100 checks a day for 14 days, then 25"
    )
    return key


# ── Small helpers ────────────────────────────────────────────────────────────

def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_code(resp: httpx.Response) -> str:
    return _text(_json(resp).get("error"))


def _retry_after(resp: httpx.Response) -> float:
    raw = _json(resp).get("retry_after", resp.headers.get("Retry-After"))
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = 1.0
    return max(0.0, min(seconds, MAX_RETRY_AFTER))


def _reason(data: dict[str, Any]) -> str:
    """The first reason's text (with its source title), or its note."""
    reasons = data.get("reasons")
    first = reasons[0] if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict) else {}
    text = _text(first.get("text")) or _text(first.get("note"))
    source = _text(first.get("source_title"))
    if text and source:
        text = f"{text} ({source})"
    return _clean(text)[:500]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _name(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit].strip() if isinstance(value, str) else ""


def _country(value: Any) -> str:
    code = value.strip().upper() if isinstance(value, str) else ""
    if code == "UK":
        code = "GB"
    return code if len(code) == 2 and code.isascii() and code.isalpha() else ""


def _clean(text: str) -> str:
    """Control characters made spaces, so text from the API cannot drive a
    terminal or a log viewer."""
    return "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in text or "")


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "***") if api_key and text else text


def _email_ok(value: str) -> bool:
    value = (value or "").strip()
    return 0 < len(value) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None


def _https_url(value: Any) -> str:
    url = _text(value)
    if not url or len(url) > 2048 or any(ch.isspace() for ch in url):
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme == "https" and parts.hostname else ""


def _host_allowed(url: str, hosts: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)
