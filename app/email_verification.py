"""Email verification provider layer.

Supports pluggable providers. Currently implements MailtesterNinja.
To add a new provider, subclass ``BaseVerificationProvider`` and register it
in ``PROVIDERS``.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

log = logging.getLogger("quickly.email_verification")

# ── Result status constants ──────────────────────────────────────────────────
VALID = "valid"
INVALID = "invalid"
CATCH_ALL = "catch_all"
UNKNOWN = "unknown"
RISKY = "risky"
PENDING = "pending"

# Statuses that should block sending
BLOCK_SEND_STATUSES = {INVALID, RISKY}


class VerificationResult:
    """Normalised output returned by every provider."""

    def __init__(self, email: str, status: str, raw: dict | None = None, message: str = ""):
        self.email = email
        self.status = status        # one of the constants above
        self.raw = raw or {}        # raw provider response for debugging
        self.message = message      # human-readable explanation


# ── Base class ───────────────────────────────────────────────────────────────
class BaseVerificationProvider(ABC):
    """Interface that every email-verification backend must implement."""

    @abstractmethod
    async def verify(self, email: str, api_key: str) -> VerificationResult:
        """Verify a single email address. Must not raise on transient errors."""
        ...


# ── MailtesterNinja provider ─────────────────────────────────────────────────
class MailtesterNinjaProvider(BaseVerificationProvider):
    """https://mailtester.ninja – single-email verification endpoint."""

    BASE_URL = "https://happy.mailtester.ninja/ninja"

    async def verify(self, email: str, api_key: str) -> VerificationResult:
        url = f"{self.BASE_URL}?key={api_key}&email={email}"
        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                try:
                    resp = await client.get(url, timeout=15)
                    if resp.status_code == 429:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    if resp.status_code != 200:
                        return VerificationResult(email, UNKNOWN, message=f"HTTP {resp.status_code}")
                    data = resp.json()
                    return self._normalise(email, data)
                except (httpx.RequestError, Exception) as exc:
                    log.warning("MailtesterNinja attempt %d failed for %s: %s", attempt + 1, email, exc)
                    await asyncio.sleep(2)
        return VerificationResult(email, UNKNOWN, message="All retries exhausted")

    @staticmethod
    def _normalise(email: str, data: dict) -> VerificationResult:
        code = data.get("code", "")
        message = data.get("message", "")

        if message == "Catch-All":
            return VerificationResult(email, CATCH_ALL, raw=data, message="Catch-all domain")

        if code == "ok" or message in ("Accepted", "Limited"):
            return VerificationResult(email, VALID, raw=data, message=message or "Valid")

        # Risky indicators
        if message in ("Low Quality", "Low Deliverability"):
            return VerificationResult(email, RISKY, raw=data, message=message)

        # Everything else is invalid
        return VerificationResult(email, INVALID, raw=data, message=message or "Invalid")


# ── Custom HTTP provider ──────────────────────────────────────────────────────

def _get_nested(data: Any, path: str) -> Any:
    """Traverse *data* following a dot-separated *path*.  Returns None if any
    segment is missing or the value is not a dict."""
    value: Any = data
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


class CustomHttpProvider(BaseVerificationProvider):
    """User-configured HTTP/S verification endpoint.

    The caller supplies a URL template containing ``{email}`` which is
    replaced with the address to verify.  A dot-notation *field_path* is
    used to extract a single value from the JSON response; that value is
    then matched against *valid_values* or *invalid_values* (case-insensitive)
    to produce the final status.  Any value not found in either list yields
    ``UNKNOWN``.
    """

    def __init__(
        self,
        url_template: str,
        field_path: str,
        valid_values: list[str],
        invalid_values: list[str],
        method: str = "GET",
    ) -> None:
        self.url_template = url_template.strip()
        self.field_path = field_path.strip()
        self.valid_values: set[str] = {v.strip().lower() for v in valid_values if v.strip()}
        self.invalid_values: set[str] = {v.strip().lower() for v in invalid_values if v.strip()}
        self.method = method.upper()

    async def verify(self, email: str, api_key: str) -> VerificationResult:  # noqa: ARG002
        if not self.url_template or "{email}" not in self.url_template:
            return VerificationResult(
                email, UNKNOWN, message="Invalid URL template — must contain {email}"
            )
        url = self.url_template.replace("{email}", email)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                if self.method == "POST":
                    resp = await client.post(url, timeout=15)
                else:
                    resp = await client.get(url, timeout=15)
                if resp.status_code != 200:
                    return VerificationResult(
                        email, UNKNOWN, message=f"HTTP {resp.status_code}"
                    )
                try:
                    data = resp.json()
                except Exception:
                    return VerificationResult(
                        email, UNKNOWN, message="Response was not valid JSON"
                    )
                return self._normalise(email, data)
            except httpx.RequestError as exc:
                return VerificationResult(email, UNKNOWN, message=f"Request error: {exc}")
            except Exception as exc:
                log.warning("CustomHttpProvider error for %s: %s", email, exc)
                return VerificationResult(email, UNKNOWN, message=f"Unexpected error: {exc}")

    def _normalise(self, email: str, data: Any) -> VerificationResult:
        raw_value = _get_nested(data, self.field_path) if self.field_path else None
        str_val = str(raw_value).strip().lower() if raw_value is not None else ""
        if str_val in self.valid_values:
            status = VALID
        elif str_val in self.invalid_values:
            status = INVALID
        else:
            status = UNKNOWN
        message = f"field '{self.field_path}' = {raw_value!r}"
        return VerificationResult(email, status, raw=data, message=message)


# ── Provider registry ────────────────────────────────────────────────────────
PROVIDERS: dict[str, BaseVerificationProvider] = {
    "mailtester_ninja": MailtesterNinjaProvider(),
}

DEFAULT_PROVIDER = "mailtester_ninja"
ALL_PROVIDER_NAMES = ["mailtester_ninja", "custom"]


def get_provider(name: str | None = None) -> BaseVerificationProvider:
    """Return the provider instance for *name*, falling back to the default.

    Note: ``"custom"`` is not in the registry since it requires runtime
    configuration — use :func:`build_custom_provider` for that case.
    """
    return PROVIDERS.get(name or DEFAULT_PROVIDER, PROVIDERS[DEFAULT_PROVIDER])


# ── High-level helpers ───────────────────────────────────────────────────────

async def verify_single(email: str, api_key: str, provider_name: str | None = None) -> VerificationResult:
    """Verify a single email using the specified (or default) provider."""
    provider = get_provider(provider_name)
    return await provider.verify(email, api_key)


async def verify_batch(
    emails: list[str],
    api_key: str,
    provider_name: str | None = None,
    concurrency: int = 5,
    delay: float = 1.0,
) -> list[VerificationResult]:
    """Verify a list of emails with bounded concurrency."""
    provider = get_provider(provider_name)
    sem = asyncio.Semaphore(concurrency)
    results: list[VerificationResult] = []

    async def _verify_one(em: str) -> VerificationResult:
        async with sem:
            result = await provider.verify(em, api_key)
            await asyncio.sleep(delay)
            return result

    tasks = [_verify_one(em) for em in emails]
    results = await asyncio.gather(*tasks)
    return list(results)
