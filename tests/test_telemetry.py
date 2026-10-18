"""Telemetry privacy, gating, aggregation, and retry/idempotency tests.

This module is the guardrail for the anonymous-telemetry wire contract in
``app/telemetry.py``. Besides the behavioural tests, it pins the exact
top-level allowlist, recursively proves no fingerprinting key can appear
anywhere in a payload, and statically forbids the device-identity APIs so a
future change cannot quietly reintroduce fingerprinting.
"""
from __future__ import annotations

import ast
import builtins
import io
import json
import pathlib
import re
from datetime import datetime

import httpx
import pytest

from app import telemetry, telemetry_rollup
from app.app_settings import get_telemetry_enabled, set_telemetry_enabled
from app.models import (
    AppSetting,
    Campaign,
    CampaignLead,
    EmailClick,
    EmailLog,
    EmailOpen,
    Inbox,
    Lead,
    LeadReply,
    Sequence,
    User,
)
from app.settings_manager import initialize_settings, settings

# The complete literal wire contract. ``build_payload`` must emit exactly these
# keys and ``telemetry.PAYLOAD_KEYS`` must equal this set.
EXPECTED_PAYLOAD_KEYS = {
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

# Key NAMES that would indicate device/host/user fingerprinting if they ever
# appeared anywhere in a payload (checked recursively, including list items).
# ``instance_id`` is an explicitly allowed random per-install id and is NOT in
# this list — we match keys, never naive value substrings.
FORBIDDEN_KEYS = frozenset(
    {
        "disk_gb",
        "disk_usage",
        "disk_total",
        "disk_percent",
        "hostname",
        "host",
        "fqdn",
        "machine_id",
        "machine-id",
        "dmi",
        "serial",
        "board",
        "product_uuid",
        "mac",
        "mac_address",
        "uuid",
        "kernel",
        "os_version",
        "platform_version",
        "distro",
        "uname",
        "cpu_model",
        "ip",
        "ip_address",
        "username",
        "user",
        "home",
        "path",
        "cwd",
        "file",
        "env",
        "environment",
        "secret",
        "token",
        "password",
    }
)

OS_FAMILIES = {"linux", "windows", "macos", "other"}
ARCH_FAMILIES = {"x86_64", "x86", "arm64", "arm", "other"}
HEARTBEAT_KEYS = {"leads_count", "campaigns_count", "inboxes_count", "uptime_days"}
DAILY_ROW_KEYS = {
    "date",
    "emails_sent",
    "opened",
    "clicked",
    "replies",
    "replies_positive",
    "replies_negative",
    "bounced",
    "unsubscribes",
    "send_failures",
}


def _configure_telemetry(*, enabled: bool = True, env_enabled: bool = True) -> None:
    settings.telemetry_enabled = enabled
    settings.telemetry_env_enabled = env_enabled
    settings.telemetry_url = "https://telemetry.example"
    settings.telemetry_key = "test-write-key"


def _collect_keys(obj) -> set[str]:
    """Every dict key reachable in *obj*, recursing through dicts and lists."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(key)
            keys |= _collect_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            keys |= _collect_keys(item)
    return keys


def _code_without_docstrings(source: str) -> str:
    """Source with every docstring removed, leaving only executable code.

    The module's own privacy statement deliberately *names* the signals it does
    not collect (e.g. ``/etc/machine-id``, ``uname``), so the raw text cannot be
    scanned for those tokens. Comments never reach the AST, so this yields the
    real code while preserving string literals that appear in code.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
    return ast.unparse(tree)


def test_telemetry_active_is_opt_out_with_configuration_and_hard_kill(monkeypatch):
    _configure_telemetry(enabled=True)
    assert telemetry.telemetry_active() is True

    _configure_telemetry(enabled=False)
    assert telemetry.telemetry_active() is False

    _configure_telemetry(enabled=True, env_enabled=False)
    assert telemetry.telemetry_active() is False

    _configure_telemetry(enabled=True, env_enabled=True)
    assert telemetry.telemetry_active() is True

    monkeypatch.setattr(telemetry, "DEFAULT_TELEMETRY_URL", "")
    settings.telemetry_url = ""
    assert telemetry.telemetry_active() is False
    settings.telemetry_url = "https://telemetry.example"
    settings.telemetry_key = ""
    assert telemetry.telemetry_active() is False


def test_release_version_is_required_and_optional_v_prefix_is_normalized(monkeypatch):
    monkeypatch.setenv("QUICKLY_VERSION", "v2.4.0")
    assert telemetry.app_version() == "2.4.0"

    monkeypatch.setenv("QUICKLY_VERSION", "unknown")
    monkeypatch.setattr(telemetry, "_VERSION_RE", re.compile(r"^never$"))
    assert telemetry.app_version() == "unknown"


@pytest.mark.asyncio
async def test_build_payload_contains_only_allowlisted_aggregate_metadata(session, monkeypatch):
    monkeypatch.setenv("QUICKLY_VERSION", "2.4.0")
    _configure_telemetry()

    user = User(username="admin", email="private@example.test", password_hash="x")
    lead = Lead(email="lead@example.test", name="Private Lead")
    campaign = Campaign(name="Private Campaign Name")
    inbox = Inbox(email="private-inbox@example.test", display_name="Private Inbox", provider="gmail")
    session.add_all([user, lead, campaign, inbox])
    await session.flush()
    campaign_lead = CampaignLead(campaign_id=campaign.id, lead_id=lead.id)
    email = EmailLog(
        lead_id=lead.id,
        campaign_id=campaign.id,
        inbox_id=inbox.id,
        subject="Private Subject",
        body="Private Message Body",
        sent_at=datetime.utcnow(),
    )
    session.add_all([campaign_lead, email])
    await session.flush()
    session.add_all([
        EmailOpen(email_log_id=email.id, ip_address="203.0.113.10"),
        EmailClick(email_log_id=email.id, ip_address="203.0.113.11"),
    ])
    reply = LeadReply(lead_id=lead.id, campaign_id=campaign.id, replied_at=datetime.utcnow())
    session.add(reply)
    campaign_lead.interest_status = "interested"
    await session.flush()

    payload = await telemetry.build_payload(session)

    # --- Exact top-level allowlist, pinned against the literal contract. ---
    assert set(payload) == telemetry.PAYLOAD_KEYS
    assert telemetry.PAYLOAD_KEYS == EXPECTED_PAYLOAD_KEYS
    assert len(telemetry.PAYLOAD_KEYS) == 14
    # disk_gb was removed as over-collection and must never come back.
    assert "disk_gb" not in payload

    assert payload["instance_id"] and payload["seq"] == 1
    assert payload["cpu_count"] is None or 1 <= payload["cpu_count"] <= telemetry.MAX_CPU_COUNT
    assert payload["ram_gb"] is None or 1 <= payload["ram_gb"] <= telemetry.MAX_RAM_GB
    # Bounds are the hub schema's own maxima (not a tighter product cap).
    assert telemetry.MAX_CPU_COUNT == 1024
    assert telemetry.MAX_RAM_GB == 4096

    # --- Family allowlists, never a raw/versioned platform string. ---
    assert payload["os"] in OS_FAMILIES
    assert payload["arch"] in ARCH_FAMILIES
    assert payload["deployment"] in {"docker", "bare"}

    # --- Exact nested shapes. ---
    assert set(payload["heartbeat"]) == HEARTBEAT_KEYS
    assert payload["daily"], "daily rollup must contain at least today"
    for row in payload["daily"]:
        assert set(row) == DAILY_ROW_KEYS
    for row in payload["events"]:
        assert {"name", "occurred_at"} <= set(row) <= {"name", "occurred_at", "provider"}
    for row in payload["features"]:
        assert set(row) == {"name", "used"}

    # --- No forbidden key may appear at any depth. ---
    assert FORBIDDEN_KEYS.isdisjoint(_collect_keys(payload))

    # --- No planted private value may survive serialization. ---
    serialized = json.dumps(payload, default=str).lower()
    forbidden_values = [
        "private@example.test",
        "lead@example.test",
        "private-inbox@example.test",
        "private lead",
        "private campaign name",
        "private subject",
        "private message body",
        "203.0.113.10",
        "203.0.113.11",
        "password_hash",
    ]
    assert not any(value in serialized for value in forbidden_values)

    assert payload["heartbeat"]["leads_count"] == 1
    assert payload["daily"][-1]["emails_sent"] == 1
    assert payload["daily"][-1]["opened"] == 1
    assert payload["daily"][-1]["clicked"] == 1
    assert payload["daily"][-1]["replies"] == 1
    assert payload["daily"][-1]["replies_positive"] == 1


def test_forbidden_key_scanner_catches_nested_fingerprinting_keys():
    """The recursive scanner itself detects planted keys at every depth."""
    planted = {
        "os": "linux",
        "instance_id": "abc123",  # allowed random id, not a hardware id
        "heartbeat": {"leads_count": 1, "hostname": "private-host"},
        "daily": [{"date": "2026-01-01", "disk_gb": 80}],
        "events": [{"name": "x", "ip": "203.0.113.9"}],
        "features": [{"name": "y", "used": True}],
    }
    assert FORBIDDEN_KEYS & _collect_keys(planted) == {"hostname", "disk_gb", "ip"}
    assert FORBIDDEN_KEYS.isdisjoint(_collect_keys({"os": "linux", "arch": "x86_64"}))
    # A uuid VALUE is fine; only a "uuid" KEY would be flagged.
    assert "uuid" not in _collect_keys(planted)


def test_capacity_gb_rounds_up_and_clamps():
    gib = 1024**3
    assert telemetry._capacity_gb(0, 1024) == 1
    assert telemetry._capacity_gb(1, 1024) == 1
    assert telemetry._capacity_gb(1024, 1024) == 1
    assert telemetry._capacity_gb(gib - 1, 1024) == 1
    assert telemetry._capacity_gb(gib, 1024) == 1  # exact GiB stays
    assert telemetry._capacity_gb(gib + 1, 1024) == 2  # one byte over rounds up
    assert telemetry._capacity_gb(2 * gib, 1024) == 2
    assert telemetry._capacity_gb(2 * gib + 1, 1024) == 3
    # Hard cap, including values far above it.
    assert telemetry._capacity_gb(7 * gib, 4) == 4
    assert telemetry._capacity_gb(10**15, telemetry.MAX_RAM_GB) == telemetry.MAX_RAM_GB


def test_cpu_count_clamps_and_handles_unknown(monkeypatch):
    monkeypatch.setattr(telemetry.os, "cpu_count", lambda: 4)
    assert telemetry._cpu_count() == 4

    monkeypatch.setattr(telemetry.os, "cpu_count", lambda: telemetry.MAX_CPU_COUNT * 4)
    assert telemetry._cpu_count() == telemetry.MAX_CPU_COUNT

    monkeypatch.setattr(telemetry.os, "cpu_count", lambda: 0)
    assert telemetry._cpu_count() is None

    monkeypatch.setattr(telemetry.os, "cpu_count", lambda: None)
    assert telemetry._cpu_count() is None


def _patch_meminfo(monkeypatch, text: str | Exception) -> None:
    """Replace only the ``/proc/meminfo`` read; other opens pass through."""
    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes)) and str(file) == "/proc/meminfo":
            if isinstance(text, Exception):
                raise text
            return io.StringIO(text)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_ram_gb_rounds_up_caps_and_tolerates_missing_meminfo(monkeypatch):
    kib = 1024
    gib_in_kb = 1024**3 // kib  # 1048576 kB == exactly 1 GiB

    _patch_meminfo(monkeypatch, f"MemTotal:       {gib_in_kb} kB\nMemFree: 1 kB\n")
    assert telemetry._ram_gb() == 1  # exact GiB stays 1

    _patch_meminfo(monkeypatch, f"MemTotal:       {gib_in_kb + 1} kB\n")
    assert telemetry._ram_gb() == 2  # 1 GiB + 1 KiB rounds up

    _patch_meminfo(monkeypatch, f"MemTotal:       {gib_in_kb * 4} kB\n")
    assert telemetry._ram_gb() == 4

    huge = (telemetry.MAX_RAM_GB * 4) * gib_in_kb
    _patch_meminfo(monkeypatch, f"MemTotal:       {huge} kB\n")
    assert telemetry._ram_gb() == telemetry.MAX_RAM_GB

    _patch_meminfo(monkeypatch, "MemFree: 123 kB\nSwapTotal: 0 kB\n")
    assert telemetry._ram_gb() is None  # no MemTotal line

    _patch_meminfo(monkeypatch, FileNotFoundError("no /proc/meminfo"))
    assert telemetry._ram_gb() is None


def test_os_and_arch_families_collapse_to_allowlist(monkeypatch):
    for raw, expected in [
        ("Linux", "linux"),
        ("Windows", "windows"),
        ("Darwin", "macos"),
        ("Linux9.4", "other"),  # version-like string must not leak
        ("FreeBSD", "other"),
        ("", "other"),
    ]:
        monkeypatch.setattr(telemetry.platform, "system", lambda raw=raw: raw)
        assert telemetry._os_family() == expected, raw
        assert telemetry._os_family() in OS_FAMILIES

    for raw, expected in [
        ("x86_64", "x86_64"),
        ("AMD64", "x86_64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("armv7l", "arm"),
        ("i686", "x86"),
        ("riscv64", "other"),
        ("", "other"),
    ]:
        monkeypatch.setattr(telemetry.platform, "machine", lambda raw=raw: raw)
        assert telemetry._arch_family() == expected, raw
        assert telemetry._arch_family() in ARCH_FAMILIES


def test_telemetry_source_contains_no_device_fingerprinting_calls():
    """Static regression guard: device/host identity APIs must never return.

    The guard scans executable code (docstrings stripped) in both telemetry
    modules, so reintroducing a device/host-identity API fails CI even if it is
    only called dynamically named/partially.
    """
    banned = [
        "gethostname",
        "gethostbyaddr",
        "gethostbyname",
        "getnode(",
        "uuid.getnode",
        "machine-id",
        "machine_id",
        "platform.version",
        "platform.release",
        "platform.node",
        "platform.processor",
        "platform.fqdn",
        "platform.mac_ver",
        "platform.libc_ver",
        "platform.uname",
        "uname(",
        "shutil.disk_usage",
        "os.environ",
        "os.getlogin",
        "getpass",
        "getuser",
        "pwd.getpwuid",
        "hostid",
        "dmidecode",
    ]
    scanned = []
    for module in (telemetry, telemetry_rollup):
        source = pathlib.Path(module.__file__).read_text()
        code = _code_without_docstrings(source)
        scanned.append(module.__name__)
        for token in banned:
            assert token not in code, (
                f"{module.__name__}: fingerprinting API reintroduced: {token!r}"
            )
    assert scanned == ["app.telemetry", "app.telemetry_rollup"]
    # Sanity: the scanner must still see real code.
    code = _code_without_docstrings(pathlib.Path(telemetry.__file__).read_text())
    assert "platform.system" in code
    assert "platform.machine" in code


@pytest.mark.asyncio
async def test_send_advances_sequence_only_after_2xx(session, monkeypatch):
    monkeypatch.setenv("QUICKLY_VERSION", "2.4.0")
    _configure_telemetry()
    monkeypatch.setattr(telemetry, "build_payload", _stub_payload)
    monkeypatch.setattr(telemetry, "get_instance_id", _stub_instance_id)

    statuses = iter([500, 202])
    sent_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_payloads.append(json.loads(request.content))
        return httpx.Response(next(statuses), request=request)

    transport = httpx.MockTransport(handler)
    original_client = telemetry.httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(telemetry.httpx, "AsyncClient", client_factory)

    assert await telemetry.send_telemetry(session) is False
    assert await _stored_seq(session) == 0

    assert await telemetry.send_telemetry(session) is True
    assert await _stored_seq(session) == 1
    assert len(sent_payloads) == 2
    assert sent_payloads[0]["seq"] == sent_payloads[1]["seq"] == 1

    session.expire_all()
    instance = (
        await session.execute(
            __import__("sqlalchemy").select(AppSetting).where(
                AppSetting.key == telemetry.INSTANCE_ID_KEY
            )
        )
    ).scalar_one_or_none()
    assert instance is None or instance.value == "stub-instance"


async def _stub_payload(_db):
    # Matches the frozen wire contract (no disk_gb).
    return {
        "schema_version": 1,
        "instance_id": "stub-instance",
        "app_version": "2.4.0",
        "seq": 1,
        "sent_at": datetime.utcnow().isoformat() + "Z",
        "deployment": "docker",
        "os": "linux",
        "arch": "x86_64",
        "cpu_count": 4,
        "ram_gb": 8,
        "heartbeat": {},
        "daily": [],
        "events": [],
        "features": [],
    }


async def _stub_instance_id(_db):
    return "stub-instance"


async def _stored_seq(session) -> int:
    from sqlalchemy import select

    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == telemetry.SEQ_KEY))
    ).scalar_one_or_none()
    return int(row.value) if row else 0


@pytest.mark.asyncio
async def test_sqlite_daily_rollup_uses_the_same_date_keys_as_output(session, monkeypatch):
    now = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    campaign = Campaign(name="Aggregate only")
    lead = Lead(email="aggregate@example.test")
    session.add_all([campaign, lead])
    await session.flush()
    session.add(EmailLog(lead_id=lead.id, campaign_id=campaign.id, sent_at=now))
    await session.flush()
    rows = await telemetry_rollup.daily_counters(session, days=1)

    assert rows == [
        {
            "date": now.date().isoformat(),
            "emails_sent": 1,
            "opened": 0,
            "clicked": 0,
            "replies": 0,
            "replies_positive": None,
            "replies_negative": None,
            "bounced": None,
            "unsubscribes": None,
            "send_failures": None,
        }
    ]
    # And the row shape is the frozen one, not just this one instance of it.
    assert set(rows[0]) == DAILY_ROW_KEYS


@pytest.mark.asyncio
async def test_telemetry_setting_is_opt_out_and_reloads_cache(session):
    original_enabled = settings.telemetry_enabled
    original_env = settings.telemetry_env_enabled
    try:
        settings.telemetry_env_enabled = True
        await initialize_settings(session)
        assert await get_telemetry_enabled(session) is True
        assert settings.telemetry_enabled is True

        await set_telemetry_enabled(session, False)
        assert await get_telemetry_enabled(session) is False
        assert settings.telemetry_enabled is False

        await set_telemetry_enabled(session, True)
        assert await get_telemetry_enabled(session) is True
        assert settings.telemetry_enabled is True
    finally:
        settings.telemetry_enabled = original_enabled
        settings.telemetry_env_enabled = original_env
