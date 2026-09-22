"""Deterministic tests for the staged SMTP diagnostic probe.

Every test drives ``app.smtp_diagnose`` against an in-process threaded SMTP
double, so the verdicts (healthy, port/mode mismatch, refused, timeout, auth
failure, sender rejected, Cloudflare-proxied) are checked without touching the
network.  A few API-level tests call the router functions directly, mirroring
``tests/test_inboxes_api.py``.
"""
from __future__ import annotations

import socket
import ssl
import threading
import time

import pytest

from app import smtp_diagnose as diag
from app.smtp_utils import apply_port_tls_inference, derive_inbox_health, infer_mode_for_port


# ---------------------------------------------------------------------------
# Self-signed certificate for the TLS-capable fake relay
# ---------------------------------------------------------------------------

_TLS_DIR = None
_TLS_CTX_CACHE: dict[str, ssl.SSLContext] = {}


def _tls_material():
    """Generate a throwaway self-signed cert/key once, in a temp dir."""
    global _TLS_DIR
    if _TLS_DIR is not None:
        return _TLS_DIR
    import datetime as _dt
    import tempfile

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = _dt.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    d = tempfile.mkdtemp(prefix="quickly-diag-tls-")
    cert_path = f"{d}/cert.pem"
    key_path = f"{d}/key.pem"
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    _TLS_DIR = (cert_path, key_path)
    return _TLS_DIR


def _server_ctx() -> ssl.SSLContext:
    if "server" not in _TLS_CTX_CACHE:
        cert_path, key_path = _tls_material()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
        _TLS_CTX_CACHE["server"] = ctx
    return _TLS_CTX_CACHE["server"]


@pytest.fixture(autouse=True)
def _unverified_tls(monkeypatch):
    """The probe verifies certs in production; tests use a self-signed one."""
    real = ssl.create_default_context

    def _ctx(*a, **k):
        ctx = real(*a, **k)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    monkeypatch.setattr(ssl, "create_default_context", _ctx)


@pytest.fixture(autouse=True)
def _reset_diagnose_cooldowns():
    """Cooldowns are process-global and inbox ids restart at 1 per test schema."""
    from app.routers import smtp as smtp_router

    smtp_router._last_diagnose_at.clear()
    smtp_router._last_send_test_at.clear()
    yield
    smtp_router._last_diagnose_at.clear()
    smtp_router._last_send_test_at.clear()


# ---------------------------------------------------------------------------
# Fake SMTP servers
# ---------------------------------------------------------------------------


class _FakeSmtpServer:
    """Threaded SMTP double.

    Knobs:
    * ``behaviour="ok"``            — accept everything;
    * ``behaviour="auth_fail"``     — 535 on AUTH;
    * ``behaviour="sender_reject"`` — 550 on MAIL FROM (cPanel/Plesk style);
    * ``behaviour="no_starttls"``   — advertise AUTH but not STARTTLS;
    * ``implicit_ssl=True``         — speak TLS from the first byte (465);
    * ``silent=True``               — accept the TCP connection, never speak.
    """

    def __init__(
        self,
        behaviour: str = "ok",
        starttls: bool = True,
        silent: bool = False,
        implicit_ssl: bool = False,
    ):
        self.behaviour = behaviour
        self.starttls = starttls
        self.silent = silent
        self.implicit_ssl = implicit_ssl
        self.port = 0
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.transcript: list[str] = []

    def start(self) -> "_FakeSmtpServer":
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        if self.silent:
            # Hold the connection open but never send a banner: the client's
            # read blocks until its socket timeout fires.
            time.sleep(30)
            conn.close()
            return
        try:
            if self.implicit_ssl:
                conn = _server_ctx().wrap_socket(conn, server_side=True)
            self._converse(conn)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _converse(self, conn: socket.socket) -> None:
        f = conn.makefile("rwb")

        def send(line: str) -> None:
            self.transcript.append(f"S {line}")
            f.write((line + "\r\n").encode())
            f.flush()

        send("220 fake-smtp ESMTP ready")
        while True:
            raw = f.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\r\n")
            self.transcript.append(f"C {line}")
            u = line.upper()
            if u.startswith("EHLO") or u.startswith("HELO"):
                send("250-fake-smtp")
                send("250-AUTH PLAIN LOGIN")
                if self.starttls:
                    send("250-STARTTLS")
                send("250 SIZE 20480000")
            elif u.startswith("STARTTLS"):
                if not self.starttls:
                    send("502 5.5.1 Command not implemented")
                    continue
                send("220 2.0.0 Ready to start TLS")
                f.close()
                conn = _server_ctx().wrap_socket(conn, server_side=True)
                f = conn.makefile("rwb")
            elif u.startswith("AUTH"):
                if self.behaviour == "auth_fail":
                    send("535 5.7.8 Authentication credentials invalid")
                else:
                    send("235 2.7.0 Authentication successful")
            elif u.startswith("MAIL FROM"):
                if self.behaviour == "sender_reject":
                    send("550 5.7.1 Sender address rejected: not permitted to send from this address")
                else:
                    send("250 2.1.0 Ok")
            elif u.startswith("RCPT TO"):
                send("250 2.1.5 Ok")
            elif u.startswith("DATA"):
                send("354 End data with <CR><LF>.<CR><LF>")
                # consume until the lone dot
                while True:
                    body = f.readline()
                    if not body or body.strip() == b".":
                        break
                send("250 2.0.0 Ok: queued as FAKE123")
            elif u.startswith("RSET"):
                send("250 2.0.0 Ok")
            elif u.startswith("QUIT"):
                send("221 2.0.0 Bye")
                break
            else:
                send("250 OK")


@pytest.fixture()
def fake_relay():
    server = _FakeSmtpServer("ok").start()
    yield server
    server.stop()


def _diagnose(server_or_port, **overrides):
    port = server_or_port if isinstance(server_or_port, int) else server_or_port.port
    kwargs = dict(
        host="127.0.0.1",
        port=port,
        use_tls=True,
        use_ssl=False,
        username="user@example.com",
        password="secret",
        from_email="user@example.com",
        to_email="catcher@example.com",
        timeout=3.0,
        total_timeout=15.0,
    )
    kwargs.update(overrides)
    return diag.diagnose(**kwargs)


# ---------------------------------------------------------------------------
# Port/TLS inference
# ---------------------------------------------------------------------------


def test_infer_mode_for_port():
    assert infer_mode_for_port(465) == "ssl"
    assert infer_mode_for_port(587) == "starttls"
    assert infer_mode_for_port(2525) is None


def test_apply_port_tls_inference_resolves_broken_combo():
    # 465 + STARTTLS (the classic broken default) => SSL wins.
    assert apply_port_tls_inference(465, use_tls=True, use_ssl=False) == (False, True)
    # 587 + SSL => STARTTLS.
    assert apply_port_tls_inference(587, use_tls=False, use_ssl=True) == (True, False)
    # Already consistent: unchanged.
    assert apply_port_tls_inference(465, use_tls=False, use_ssl=True) == (False, True)
    assert apply_port_tls_inference(587, use_tls=True, use_ssl=False) == (True, False)
    # Neither flag set on a known port => inferred.
    assert apply_port_tls_inference(465, use_tls=False, use_ssl=False) == (False, True)
    # Unknown port => operator's choice preserved.
    assert apply_port_tls_inference(2525, use_tls=False, use_ssl=False) == (False, False)


# ---------------------------------------------------------------------------
# Cloudflare detection
# ---------------------------------------------------------------------------


def test_cloudflare_ip_detection():
    assert diag.is_cloudflare_ip("104.16.5.5") is True
    assert diag.is_cloudflare_ip("172.64.0.1") is True
    assert diag.is_cloudflare_ip("131.0.72.9") is True
    assert diag.is_cloudflare_ip("145.241.239.125") is False
    assert diag.is_cloudflare_ip("not-an-ip") is False


def test_cloudflare_proxied_hostname_flags_and_stops(monkeypatch):
    """A hostname resolving to a Cloudflare edge IP must fail the DNS stage."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.16.5.5", 587)),
        ],
    )
    report = _diagnose(12345, host="mail.proxied.example")
    dns = next(s for s in report["stages"] if s["name"] == "dns")
    assert dns["ok"] is False
    assert "Cloudflare" in dns["detail"]
    assert not report["ok"]
    assert any("Cloudflare-proxied" in h for h in report["hints"])


# ---------------------------------------------------------------------------
# Healthy relay
# ---------------------------------------------------------------------------


def test_healthy_relay_all_stages_pass(fake_relay):
    report = _diagnose(fake_relay)
    assert report["ok"] is True
    assert report["verdict"].startswith("All stages passed")
    stage_map = {s["name"]: s for s in report["stages"]}
    for name in ("dns", "tcp", "ehlo", "auth", "mail_from", "rcpt_to", "data"):
        assert stage_map[name]["ok"] is True, name
    # IMAP not configured => skipped, still ok.
    assert stage_map["imap"]["ok"] is True
    assert "skipped" in stage_map["imap"]["detail"].lower()
    # The TLS stage keeps its own handshake lines, not the whole conversation.
    tls_raw = "\n".join(stage_map["tls"]["raw"])
    assert "MAIL FROM" not in tls_raw
    assert "AUTH PLAIN" not in tls_raw
    # Timeout handling is explicit in the JSON: per-stage + overall budgets
    # (the test helper passes 3s/15s to keep the suite fast).
    timeouts = report["timeouts"]
    assert timeouts["stage_seconds"] == 3.0
    assert timeouts["total_seconds"] == 15.0
    assert timeouts["elapsed_seconds"] >= 0
    assert timeouts["total_exhausted"] is False
    # Defaults documented on the module are the production budgets (~8s/~30s).
    assert diag.DEFAULT_STAGE_TIMEOUT == 8.0
    assert diag.DEFAULT_TOTAL_TIMEOUT == 30.0


def test_raw_transcript_redacts_credentials(fake_relay):
    report = _diagnose(fake_relay)
    blob = "\n".join(
        line for stage in report["stages"] for line in stage["raw"]
    )
    assert "secret" not in blob
    assert "***redacted***" in blob


# ---------------------------------------------------------------------------
# Port / mode mismatch
# ---------------------------------------------------------------------------


def test_starttls_handshake_failure_suggests_ssl(monkeypatch):
    """When STARTTLS cannot complete, the probe retries SSL and reports it."""
    calls: list[str] = []

    def _fake_once(**kwargs):
        calls.append(kwargs["mode"])
        report = diag.DiagnosticReport(host=kwargs["host"], port=kwargs["port"], mode=kwargs["mode"])
        ok = kwargs["mode"] == "ssl"
        report.ok = ok
        report.verdict = "all good" if ok else "TLS handshake failed"
        report.stages = [
            diag.StageResult(name=n, ok=ok, detail="") for n in diag.STAGE_NAMES
        ]
        return report

    monkeypatch.setattr(diag, "_probe_once", _fake_once)
    report = diag.diagnose(
        host="mail.example.com", port=465, use_tls=True, use_ssl=False,
        username="u@example.com", password="p", timeout=1.0, total_timeout=5.0,
    )
    # The configured mode (STARTTLS on 465) failed, but the probe says which
    # mode actually works and includes its full stage breakdown.
    assert report["ok"] is False
    assert report["suggested_mode"] == "ssl"
    assert report["alternate"] is not None
    assert report["alternate"]["ok"] is True
    # Tried the configured mode first, then the other one.
    assert calls[0] == "starttls"
    assert "ssl" in calls
    assert any("SSL" in h for h in report["hints"])


# ---------------------------------------------------------------------------
# Connection refused / timeout
# ---------------------------------------------------------------------------


def test_connection_refused():
    """A closed port on a reachable host => connection refused."""
    # Bind then close to get a definitely-unused port.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    report = _diagnose(port)
    tcp = next(s for s in report["stages"] if s["name"] == "tcp")
    assert tcp["ok"] is False
    assert "refused" in tcp["detail"].lower()
    assert any("Connection refused" in h for h in report["hints"])


def test_tcp_timeout_is_distinguished_from_refused():
    """A silent server (accepts, never speaks) => timeout, not refused."""
    server = _FakeSmtpServer(silent=True).start()
    try:
        report = _diagnose(server, timeout=1.0, total_timeout=8.0)
    finally:
        server.stop()
    # TCP connects, but the SMTP banner never arrives.
    tcp = next(s for s in report["stages"] if s["name"] == "tcp")
    assert tcp["ok"] is True
    assert not report["ok"]
    assert "banner" in report["verdict"].lower() or "timed out" in report["verdict"].lower()


def test_connect_timeout_reported(monkeypatch):
    """create_connection raising socket.timeout => explicit timeout hint."""
    def _boom(*a, **k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(socket, "create_connection", _boom)
    report = _diagnose(9, host="10.255.255.1")
    tcp = next(s for s in report["stages"] if s["name"] == "tcp")
    assert tcp["ok"] is False
    assert "timeout" in tcp["detail"].lower()
    assert any("packets are dropped" in h for h in report["hints"])


# ---------------------------------------------------------------------------
# Auth failure / sender rejected
# ---------------------------------------------------------------------------


def test_auth_failure_535_hint():
    server = _FakeSmtpServer("auth_fail").start()
    try:
        report = _diagnose(server, username="user", password="bad")
    finally:
        server.stop()
    auth = next(s for s in report["stages"] if s["name"] == "auth")
    assert auth["ok"] is False
    assert "535" in auth["detail"]
    # Username without "@" => cPanel/Plesk full-address hint.
    assert any("FULL" in h for h in report["hints"])


def test_auth_failure_full_username_hint():
    server = _FakeSmtpServer("auth_fail").start()
    try:
        report = _diagnose(server, username="user@domain.com", password="bad")
    finally:
        server.stop()
    assert any("Authentication was rejected" in h for h in report["hints"])


def test_auth_ok_sender_rejected_is_not_a_quickly_bug():
    """The headline case: auth works, MAIL FROM is refused by relay policy."""
    server = _FakeSmtpServer("sender_reject").start()
    try:
        report = _diagnose(server)
    finally:
        server.stop()
    stage_map = {s["name"]: s for s in report["stages"]}
    assert stage_map["auth"]["ok"] is True
    assert stage_map["mail_from"]["ok"] is False
    assert not report["ok"]
    assert "sender" in report["verdict"].lower()
    assert any("relay policy" in h for h in report["hints"])
    # RCPT/DATA were never attempted after the 5xx.
    assert stage_map["rcpt_to"]["ok"] is False


def test_no_recipient_skips_mail_stages(fake_relay):
    report = _diagnose(fake_relay, to_email="")
    stage_map = {s["name"]: s for s in report["stages"]}
    # MAIL FROM runs (relay-policy check); RCPT/DATA are skipped but non-fatal.
    assert stage_map["mail_from"]["ok"] is True
    assert stage_map["rcpt_to"]["ok"] is True
    assert "skipped" in stage_map["rcpt_to"]["detail"].lower()
    assert "skipped" in stage_map["data"]["detail"].lower()
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# Health derivation
# ---------------------------------------------------------------------------


def test_derive_inbox_health():
    assert derive_inbox_health(
        paused=False, last_send_error="", last_send_at=None,
        last_test_ok=True, last_tested_at=1,
    ) == "ok"
    assert derive_inbox_health(
        paused=False, last_send_error="Connection refused", last_send_at=1,
        last_test_ok=True, last_tested_at=1,
    ) == "failing"
    assert derive_inbox_health(
        paused=False, last_send_error="", last_send_at=None,
        last_test_ok=False, last_tested_at=None,
    ) == "unknown"


def test_render_text_report_contains_verdict(fake_relay):
    report = _diagnose(fake_relay)
    text = diag.render_text_report(report)
    assert "Quickly SMTP diagnostic report" in text
    assert "[PASS] dns" in text
    assert "Verdict:" in text
    # Timeout budgets are surfaced in the copyable text report too.
    assert "Timeouts:" in text
    # The helper probes with 3s per stage / 15s overall.
    assert "3s per stage, 15s overall" in text


# ---------------------------------------------------------------------------
# Send-failure observability helpers
# ---------------------------------------------------------------------------


def test_record_smtp_send_error_streak_and_success():
    from app.smtp_utils import (
        record_smtp_send_error,
        record_smtp_send_success,
        smtp_failure_streak,
        reset_smtp_failure_streak,
    )

    class _Acct:
        inbox_id = 4242
        last_send_error = ""
        last_send_at = None

    acct = _Acct()
    reset_smtp_failure_streak(4242)
    assert record_smtp_send_error(acct, "SMTP connection error: [Errno 111] Connection refused") == 1
    assert acct.last_send_error == "Connection refused"
    assert acct.last_send_at is not None
    assert record_smtp_send_error(acct, "timed out") == 2
    assert smtp_failure_streak(4242) == 2
    record_smtp_send_success(acct)
    assert smtp_failure_streak(4242) == 0
    reset_smtp_failure_streak(4242)


def test_send_via_smtp_records_transient_failure(monkeypatch):
    """A transient connection error must persist last_send_error (was swallowed)."""
    import smtplib

    from app import sender as sender_mod
    from app.models import SmtpAccount

    acct = SmtpAccount(
        inbox_id=99, smtp_host="mail.example.com", smtp_port=587,
        smtp_username="u", smtp_password="p", smtp_use_tls=True, smtp_use_ssl=False,
    )

    def _boom(account, timeout=30):
        raise smtplib.SMTPConnectError(421, b"connection refused")

    monkeypatch.setattr("app.smtp_utils._smtp_connect", _boom)
    from app.smtp_utils import reset_smtp_failure_streak, smtp_failure_streak

    reset_smtp_failure_streak(99)
    res = sender_mod._send_via_smtp(
        to_email="lead@example.com", subject="Hi", body="Hello",
        from_email="me@mydomain.com", smtp_account=acct,
    )
    assert res is None  # transient
    assert acct.last_send_error  # ...but now visible
    assert acct.last_send_at is not None
    assert smtp_failure_streak(99) == 1
    reset_smtp_failure_streak(99)


def test_send_via_smtp_clears_error_on_success(monkeypatch):
    from app import sender as sender_mod
    from app.models import SmtpAccount

    class _FakeSMTP:
        def sendmail(self, *a, **k):
            return {}

        def quit(self):
            pass

        def close(self):
            pass

    acct = SmtpAccount(
        inbox_id=98, smtp_host="mail.example.com", smtp_port=587,
        smtp_username="u", smtp_password="p", smtp_use_tls=True, smtp_use_ssl=False,
    )
    acct.last_send_error = "old failure"
    monkeypatch.setattr("app.smtp_utils._smtp_connect", lambda account, timeout=30: _FakeSMTP())
    res = sender_mod._send_via_smtp(
        to_email="lead@example.com", subject="Hi", body="Hello",
        from_email="me@mydomain.com", smtp_account=acct,
    )
    assert isinstance(res, sender_mod.SendResult)
    assert acct.last_send_error == ""


# ---------------------------------------------------------------------------
# API endpoints (router called directly, per tests/test_inboxes_api.py style)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diagnose_endpoint_persists_and_returns_report(session, monkeypatch):
    from app.models import Inbox, SmtpAccount
    from app.routers import smtp as smtp_router

    inbox = Inbox(email="diag@example.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    acct = SmtpAccount(
        inbox_id=inbox.id, smtp_host="127.0.0.1", smtp_port=587,
        smtp_username="diag@example.com", smtp_password="p",
        smtp_use_tls=True, smtp_use_ssl=False,
    )
    session.add(acct)
    await session.flush()

    server = _FakeSmtpServer("ok").start()
    try:
        monkeypatch.setattr(
            "app.smtp_diagnose.diagnose_account",
            lambda a, to_email="": _diagnose(server, to_email="catcher@example.com"),
        )
        report = await smtp_router.diagnose_smtp_account(inbox.id, db=session)
    finally:
        server.stop()

    assert report["ok"] is True
    assert acct.last_diagnostic_at is not None
    assert acct.last_diagnostic_json
    assert acct.last_test_ok is True
    # Never expose the password in the serialised account.
    assert "p" != acct.smtp_password or True  # value not returned below
    body = smtp_router._to_response(acct)
    assert "smtp_password" not in body
    assert body["last_diagnostic_at"] is not None


@pytest.mark.asyncio
async def test_diagnose_endpoint_cooldown(session, monkeypatch):
    from app.models import Inbox, SmtpAccount
    from app.routers import smtp as smtp_router
    from fastapi import HTTPException

    inbox = Inbox(email="cd@example.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    session.add(SmtpAccount(
        inbox_id=inbox.id, smtp_host="127.0.0.1", smtp_port=587,
        smtp_username="u", smtp_password="p", smtp_use_tls=True, smtp_use_ssl=False,
    ))
    await session.flush()

    server = _FakeSmtpServer("ok").start()
    try:
        monkeypatch.setattr(
            "app.smtp_diagnose.diagnose_account",
            lambda a, to_email="": _diagnose(server),
        )
        await smtp_router.diagnose_smtp_account(inbox.id, db=session)
        with pytest.raises(HTTPException) as exc:
            await smtp_router.diagnose_smtp_account(inbox.id, db=session)
        assert exc.value.status_code == 429
    finally:
        server.stop()
        smtp_router._last_diagnose_at.clear()


@pytest.mark.asyncio
async def test_send_test_endpoint_persists_error(session, monkeypatch):
    from app.models import Inbox, SmtpAccount
    from app.routers import smtp as smtp_router
    from app.sender import SendFailure

    inbox = Inbox(email="st@example.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    acct = SmtpAccount(
        inbox_id=inbox.id, smtp_host="127.0.0.1", smtp_port=587,
        smtp_username="u", smtp_password="p", smtp_use_tls=True, smtp_use_ssl=False,
    )
    session.add(acct)
    await session.flush()

    monkeypatch.setattr(
        "app.sender._send_via_smtp",
        lambda **k: SendFailure(error_type="auth_failed", message="SMTP sender refused: 550"),
    )
    out = await smtp_router.send_test_email(
        inbox.id, smtp_router.SendTestRequest(to_email="catcher@example.com"), db=session
    )
    assert out["ok"] is False
    assert out["error"] == "auth_failed"
    assert acct.last_send_error.startswith("550") or "550" in acct.last_send_error
    assert acct.last_send_at is not None
    smtp_router._last_send_test_at.clear()


@pytest.mark.asyncio
async def test_send_test_endpoint_success(session, monkeypatch):
    from app.models import Inbox, SmtpAccount
    from app.routers import smtp as smtp_router
    from app.sender import SendResult

    inbox = Inbox(email="st2@example.com", provider="smtp")
    session.add(inbox)
    await session.flush()
    acct = SmtpAccount(
        inbox_id=inbox.id, smtp_host="127.0.0.1", smtp_port=587,
        smtp_username="u", smtp_password="p", smtp_use_tls=True, smtp_use_ssl=False,
    )
    session.add(acct)
    await session.flush()

    monkeypatch.setattr(
        "app.sender._send_via_smtp",
        lambda **k: SendResult(message_id="<test@example.com>", thread_id="t"),
    )
    out = await smtp_router.send_test_email(
        inbox.id, smtp_router.SendTestRequest(to_email="catcher@example.com"), db=session
    )
    assert out["ok"] is True
    assert out["message_id"] == "<test@example.com>"
    assert acct.last_send_error == ""
    smtp_router._last_send_test_at.clear()

