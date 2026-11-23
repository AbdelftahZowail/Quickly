"""Privacy guarantees for application logs.

Secrets (provider API keys) and personal data (raw MIME, recipients, subjects,
lead emails) must never reach logs or log files unless the operator explicitly
opts in:

* ``QUICKLY_DEBUG_MAIL_LOGS=1``  — enables the ``logs/*_api.log`` raw mail dumps
* ``QUICKLY_DEBUG_SCHEDULE=1``   — enables the ``logs/partial.txt`` lead dump

These tests pin that default.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import app.ai_classifier as ai_classifier
from app import sender
from app.routers import schedule
from tests.conftest import (
    make_campaign,
    make_campaign_lead,
    make_email_log,
    make_lead,
)

# Sentinels that must never appear in default (flag-less) log output.
SECRET_KEY = "sk-test-SECRET-abc123"
RAW_MIME = "RAW-MIME-SENTINEL\r\nSubject: SUBJECT-SENTINEL-123\r\n\r\nBODY-SENTINEL"
RECIPIENT = "secret.lead@example.com"
SUBJECT = "SUBJECT-SENTINEL-123"


def _fake_completion(content: str = "Hello"):
    async def _completion(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return _completion


# ── Provider API keys ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_ai_key_never_prints_or_logs_key(monkeypatch, capsys, caplog):
    monkeypatch.setattr(ai_classifier, "acompletion", _fake_completion("Hello"))

    with caplog.at_level(logging.DEBUG, logger="quickly.ai_classifier"):
        result = await ai_classifier.verify_ai_key(
            provider="openai", model="gpt-4o-mini", api_key=SECRET_KEY
        )

    captured = capsys.readouterr()
    assert result == {"ok": True}
    assert SECRET_KEY not in captured.out
    assert SECRET_KEY not in captured.err
    assert SECRET_KEY not in caplog.text
    # Provider/model stay traceable at debug level.
    assert "openai" in caplog.text
    assert "gpt-4o-mini" in caplog.text


@pytest.mark.asyncio
async def test_verify_ai_key_scrubs_key_from_error(monkeypatch, capsys, caplog):
    async def _boom(**_kwargs):
        raise RuntimeError(f"Incorrect API key provided: {SECRET_KEY}")

    monkeypatch.setattr(ai_classifier, "acompletion", _boom)

    with caplog.at_level(logging.DEBUG, logger="quickly.ai_classifier"):
        result = await ai_classifier.verify_ai_key(
            provider="openai", model="gpt-4o-mini", api_key=SECRET_KEY
        )

    captured = capsys.readouterr()
    assert result["ok"] is False
    assert SECRET_KEY not in result["error"]
    assert SECRET_KEY not in caplog.text
    assert SECRET_KEY not in captured.out + captured.err


# ── Raw mail log files ───────────────────────────────────────────────────────


def _patch_mail_log_paths(monkeypatch, tmp_path):
    paths = {
        "gmail": tmp_path / "gmail_api.log",
        "o365": tmp_path / "office365_api.log",
        "smtp": tmp_path / "smtp_api.log",
    }
    monkeypatch.setattr(sender, "_GMAIL_LOG_PATH", paths["gmail"])
    monkeypatch.setattr(sender, "_O365_LOG_PATH", paths["o365"])
    monkeypatch.setattr(sender, "_SMTP_LOG_PATH", paths["smtp"])
    return paths


def _write_all_mail_logs() -> None:
    sender._log_gmail_call(
        RECIPIENT,
        "inbox@example.com",
        SUBJECT,
        RAW_MIME,
        {"raw": RAW_MIME},
        thread_id="t-1",
        status="SENDING",
    )
    sender._log_office365_call(
        RECIPIENT,
        "inbox@example.com",
        SUBJECT,
        conversation_id="c-1",
        status="SENDING",
    )
    sender._log_smtp_call(
        RECIPIENT,
        "inbox@example.com",
        SUBJECT,
        thread_id="t-1",
        status="SENDING",
    )


def test_default_mail_logging_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("QUICKLY_DEBUG_MAIL_LOGS", raising=False)
    paths = _patch_mail_log_paths(monkeypatch, tmp_path)

    _write_all_mail_logs()

    for name, path in paths.items():
        assert not path.exists(), f"{name} mail log written with debug flag off"


def test_debug_flag_enables_mail_logging(monkeypatch, tmp_path):
    monkeypatch.setenv("QUICKLY_DEBUG_MAIL_LOGS", "1")
    paths = _patch_mail_log_paths(monkeypatch, tmp_path)

    _write_all_mail_logs()

    gmail_text = paths["gmail"].read_text(encoding="utf-8")
    assert "RAW-MIME-SENTINEL" in gmail_text
    assert "BODY-SENTINEL" in gmail_text
    assert RECIPIENT in gmail_text
    assert SUBJECT in gmail_text

    for name in ("o365", "smtp"):
        text = paths[name].read_text(encoding="utf-8")
        assert RECIPIENT in text
        assert SUBJECT in text


def test_gmail_send_path_writes_no_raw_mime_by_default(monkeypatch, tmp_path):
    """End-to-end: a real Gmail send must not persist the MIME payload."""
    monkeypatch.delenv("QUICKLY_DEBUG_MAIL_LOGS", raising=False)
    paths = _patch_mail_log_paths(monkeypatch, tmp_path)

    captured: dict = {}

    class _Executable:
        def __init__(self, value):
            self._value = value

        def execute(self, num_retries=None):  # noqa: ARG002 - API signature
            return self._value

    class _Messages:
        def send(self, userId=None, body=None):  # noqa: ARG002
            captured["body"] = body
            return _Executable({"threadId": "thread-1", "id": "msg-1"})

        def get(self, **_kwargs):
            return _Executable(
                {"payload": {"headers": [{"name": "Message-Id", "value": "<real@x>"}]}}
            )

    class _Users:
        def messages(self):
            return _Messages()

    def _fake_build(service, version, **_kwargs):  # noqa: ARG001
        return SimpleNamespace(users=lambda: _Users())

    monkeypatch.setattr(sender, "build", _fake_build)

    account = SimpleNamespace(access_token="tok", refresh_token=None, token_expiry=None)
    result = sender._send_via_gmail(
        to_email=RECIPIENT,
        subject=SUBJECT,
        body="BODY-SENTINEL",
        from_email="inbox@example.com",
        access_token="tok",
        gmail_account=account,
    )

    # The send really happened and carried the raw MIME over the API …
    assert result
    assert captured["body"]["raw"]
    # … but nothing was dumped to disk.
    assert not paths["gmail"].exists()


# ── Schedule PII dump ────────────────────────────────────────────────────────


def test_schedule_dump_flag_default_off(monkeypatch):
    monkeypatch.delenv("QUICKLY_DEBUG_SCHEDULE", raising=False)
    monkeypatch.delenv("QUICKLY_TEST_LOGS_DIR", raising=False)
    assert schedule._schedule_debug_dump_enabled() is False

    monkeypatch.setenv("QUICKLY_DEBUG_SCHEDULE", "1")
    assert schedule._schedule_debug_dump_enabled() is True


@pytest.mark.asyncio
async def test_schedule_dump_written_only_with_flag(session, tmp_path, monkeypatch):
    monkeypatch.delenv("QUICKLY_DEBUG_SCHEDULE", raising=False)
    # The autouse fixture points QUICKLY_TEST_LOGS_DIR at a temp dir (which
    # implicitly enables the dump); drop it so this test sees the production
    # default.
    monkeypatch.delenv("QUICKLY_TEST_LOGS_DIR", raising=False)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(exist_ok=True)

    campaign = await make_campaign(session, name="Privacy")
    lead = await make_lead(session, email="pii.lead@example.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_email_log(session, lead.id, campaign.id, sequence_index=0)

    out_path = logs_dir / "partial.txt"

    # The ordering helper still works; it just does not dump PII.
    ordered = await schedule.order_campaign_leads_prioritizing_partials(session, [cl])
    assert ordered == [cl.id]
    assert not out_path.exists()

    # Explicit opt-in restores the dump (redirected to the temp dir).
    monkeypatch.setenv("QUICKLY_DEBUG_SCHEDULE", "1")
    monkeypatch.setenv("QUICKLY_TEST_LOGS_DIR", str(logs_dir))
    ordered = await schedule.order_campaign_leads_prioritizing_partials(session, [cl])
    assert ordered == [cl.id]
    assert "pii.lead@example.com" in out_path.read_text(encoding="utf-8")
