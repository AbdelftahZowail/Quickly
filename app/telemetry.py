"""Anonymous usage telemetry for self-hosted Quickly installs.

Reads only from this instance's own database (see ``app/telemetry_rollup.py``)
and posts a small payload to the configured hub. Beacon is not involved, and it
is fail-open: it never blocks startup, never raises into the scheduler, and does
nothing when disabled or unconfigured.

The payload is deliberately coarse. It reports install scale only — ``ram_gb``
(RAM rounded up to whole GB), ``cpu_count``, ``deployment`` (``docker``/``bare``),
``os`` family, ``arch`` family, a random per-install id (used just to de-duplicate
heartbeats), the app version, and aggregate counts (leads/campaigns/inboxes,
per-day sent/opened/clicked/reply totals, one-time milestones, feature use). It
does not send hostnames, exact OS/kernel or disk details, environment variables,
IP addresses, or any lead/person content. ``build_payload`` is the single source
of truth for the wire format; ``tests/test_telemetry.py`` pins the key allowlist.

Disable:
  * in the app  — Settings toggle (DB setting ``telemetry_enabled``)
  * environment — ``QUICKLY_TELEMETRY=false`` (wins over the toggle)
Both default to enabled (opt-out); an unset URL/key also prevents sending.
Point it at a hub (dev/testing):
  ``QUICKLY_TELEMETRY_URL`` / ``QUICKLY_TELEMETRY_KEY``
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry_rollup
from app.models import AppSetting
from app.settings_manager import settings

log = logging.getLogger("quickly.telemetry")

SCHEMA_VERSION = 1

# Baked into releases. The URL is public; the key is a public ingestion
# filter, not an admin credential or a security boundary. Release automation
# supplies the key as a build argument from repository secrets.
DEFAULT_TELEMETRY_URL = "https://telemetry.shortformfunnels.com"
DEFAULT_TELEMETRY_KEY = ""

INSTANCE_ID_KEY = "telemetry_instance_id"
SEQ_KEY = "telemetry_seq"
DAYS_PER_PAYLOAD = 7
REQUEST_TIMEOUT_SECONDS = 8.0
# Upper bounds taken from the hub wire schema (its own maxima for these fields).
# Values are reported as-is; these only stop an extreme machine from failing
# validation. RAM is rounded up to whole GB for coarseness.
MAX_CPU_COUNT = 1024
MAX_RAM_GB = 4096

# Family-level allowlists for the only two ``platform`` values on the wire.
# Anything unrecognised collapses to "other", so an unexpected platform string
# can never leak a raw, versioned value.
_OS_FAMILIES = {"linux": "linux", "windows": "windows", "darwin": "macos"}
_ARCH_FAMILIES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "x64": "x86_64",
    "x86": "x86",
    "i386": "x86",
    "i486": "x86",
    "i586": "x86",
    "i686": "x86",
    "aarch64": "arm64",
    "arm64": "arm64",
    "arm": "arm",
    "armv6l": "arm",
    "armv7l": "arm",
}

# The complete set of top-level keys ``build_payload`` may emit. Tests assert
# both that a built payload matches this set exactly and that this set matches
# the literal allowlist, so the wire format cannot drift silently.
PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "instance_id",
        "app_version",
        "seq",
        "sent_at",
        "deployment",
        "os",
        "arch",
        "cpu_count",
        "ram_gb",
        "heartbeat",
        "daily",
        "events",
        "features",
    }
)
# Only one heartbeat may be in flight per process.  The database sequence is
# still the source of truth, so this protects the common scheduler path without
# pretending to be a distributed lock.
_SEND_LOCK = asyncio.Lock()

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


def telemetry_url() -> str:
    return (settings.telemetry_url or DEFAULT_TELEMETRY_URL).strip().rstrip("/")


def telemetry_key() -> str:
    return (settings.telemetry_key or DEFAULT_TELEMETRY_KEY).strip()


def telemetry_active() -> bool:
    """True only when enabled, configured, and not disabled by the environment."""
    if not settings.telemetry_env_enabled or not settings.telemetry_enabled:
        return False
    return bool(telemetry_url() and telemetry_key())


def app_version() -> str:
    try:
        from app.backup_package import _app_version

        return _app_version()
    except Exception:
        return "unknown"


async def _get_or_create(db: AsyncSession, key: str, factory) -> str:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    row = result.scalar_one_or_none()
    if row and row.value:
        return row.value
    value = factory()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    return value


async def get_instance_id(db: AsyncSession) -> str:
    """Opaque random id, stable for the life of this install's database."""
    return await _get_or_create(db, INSTANCE_ID_KEY, lambda: uuid.uuid4().hex)


async def _next_seq(db: AsyncSession) -> int:
    raw = await _get_or_create(db, SEQ_KEY, lambda: "0")
    try:
        return int(raw)
    except ValueError:
        return 0


async def _save_seq(db: AsyncSession, seq: int) -> None:
    result = await db.execute(select(AppSetting).where(AppSetting.key == SEQ_KEY))
    row = result.scalar_one_or_none()
    if row:
        row.value = str(seq)
    else:
        db.add(AppSetting(key=SEQ_KEY, value=str(seq)))
    await db.flush()


def _deployment() -> str:
    """Container vs plain host. Deliberately coarse (no runtime or version)."""
    return "docker" if os.path.exists("/.dockerenv") else "bare"


def _os_family() -> str:
    """OS *family* only — never a version, kernel or distro. Unknown → "other"."""
    return _OS_FAMILIES.get(platform.system().strip().lower(), "other")


def _arch_family() -> str:
    """CPU *family* only — never a model or stepping. Unknown → "other"."""
    return _ARCH_FAMILIES.get(platform.machine().strip().lower(), "other")


def _capacity_gb(value: int, maximum_gb: int) -> int:
    """Round bytes up to a coarse whole-GB value, with a hard cap."""
    gb = (max(value, 0) + (1024**3) - 1) // (1024**3)
    return min(max(gb, 1), maximum_gb)


def _cpu_count() -> int | None:
    """Logical CPU count, clamped to ``MAX_CPU_COUNT``; ``None`` if unknown."""
    count = os.cpu_count()
    if not count or count < 1:
        return None
    return min(count, MAX_CPU_COUNT)


def _ram_gb() -> int | None:
    """Total RAM rounded UP to whole GB, clamped to ``MAX_RAM_GB``.

    Read from ``/proc/meminfo`` on Linux. Other hosts report ``None`` rather than
    guessing, which keeps the field optional without adding a dependency.
    """
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    return _capacity_gb(int(line.split()[1]) * 1024, MAX_RAM_GB)
    except (OSError, ValueError, IndexError):
        pass
    return None


async def build_payload(db: AsyncSession) -> dict:
    """Assemble one payload from the explicit allowlist documented above.

    This function is the single source of truth for what leaves the machine.
    Only coarse, non-identifying fields may be added here, and the privacy
    statement at the top of this module plus ``PAYLOAD_KEYS`` must be updated in
    the same change.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "instance_id": await get_instance_id(db),
        "app_version": app_version(),
        "seq": await _next_seq(db) + 1,
        "sent_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "deployment": _deployment(),
        "os": _os_family(),
        "arch": _arch_family(),
        "cpu_count": _cpu_count(),
        "ram_gb": _ram_gb(),
        "heartbeat": await telemetry_rollup.heartbeat_stats(db),
        "daily": await telemetry_rollup.daily_counters(db, days=DAYS_PER_PAYLOAD),
        "events": await telemetry_rollup.activation_events(db),
        "features": await telemetry_rollup.feature_flags(db),
    }


async def send_telemetry(db: AsyncSession) -> bool:
    """Build and POST one payload. Never raises; returns True on success.

    The sequence is advanced only after the hub has accepted the request.  A
    lost response therefore causes a replay with the same sequence, which the
    hub de-duplicates rather than double-counting.
    """
    if not telemetry_active():
        return False

    version = app_version()
    if not _VERSION_RE.match(version):
        # A dev checkout with no release version would be rejected by the hub;
        # skip rather than send something invalid.
        log.debug("telemetry: app version %r is not a release version", version)
        return False

    async with _SEND_LOCK:
        try:
            payload = await build_payload(db)
            # Persist the opaque instance id and the pre-send sequence before
            # crossing the network. If the request fails, the next attempt
            # still uses the same identity and sequence for idempotent retry.
            await db.commit()
        except Exception:
            log.debug("telemetry: could not build payload", exc_info=True)
            return False

        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                resp = await client.post(
                    f"{telemetry_url()}/v1/telemetry",
                    json=payload,
                    headers={"X-Quickly-Key": telemetry_key()},
                )
            if not 200 <= resp.status_code < 300:
                log.debug("telemetry: hub returned HTTP %s", resp.status_code)
                return False
            await _save_seq(db, int(payload["seq"]))
            await db.commit()
            return True
        except Exception:
            log.debug("telemetry: send failed", exc_info=True)
            return False
