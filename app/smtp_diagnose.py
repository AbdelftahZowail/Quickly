"""Staged SMTP/IMAP diagnostic probe for a Quickly SMTP inbox.

Standalone, stdlib-only. Runs a sequence of stages against a relay and
collects, for every stage, ``{"name", "ok", "detail", "raw"}`` where ``raw``
holds the server response lines (credentials are always redacted).

Used by:
* ``POST /api/smtp/inboxes/{id}/diagnose`` (the Inboxes UI "Diagnose" button),
* ``python -m app.smtp_diagnose --inbox-id N`` inside the container.

Why this exists: the plain "Test connection" endpoint only does
EHLO → STARTTLS/SSL → LOGIN → NOOP.  That passes on relays which authenticate
fine but *reject the sender* (cPanel/Plesk misconfiguration) and on hostnames
that are Cloudflare-proxied (where every SMTP port looks closed).  This module
actually drives the SMTP transaction and turns the raw failure into a concrete
fix.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import logging
import re
import socket
import ssl
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

log = logging.getLogger("quickly.smtp_diagnose")

# ---------------------------------------------------------------------------
# Cloudflare edge ranges.
#
# Cloudflare's proxy only handles HTTP/HTTPS.  A mail hostname that resolves to
# a Cloudflare edge IP therefore *always* looks like "all SMTP ports closed" —
# a top suspect for the "cPanel mailbox is active but nothing sends" reports.
# ---------------------------------------------------------------------------
CLOUDFLARE_RANGES = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]
_CLOUDFLARE_NETS = [ipaddress.ip_network(c) for c in CLOUDFLARE_RANGES]

# Stage names are stable identifiers — the frontend and the copy-report renderer
# key off them.
STAGE_NAMES = ("dns", "tcp", "tls", "ehlo", "auth", "mail_from", "rcpt_to", "data", "imap")

DEFAULT_STAGE_TIMEOUT = 8.0
DEFAULT_TOTAL_TIMEOUT = 30.0

# A probe sender that must never be a real address: if the relay accepts MAIL
# FROM with it, the relay does not enforce sender policy at all.
_PROBE_MESSAGE_ID_DOMAIN = "diagnose.quickly.invalid"

# Password-shaped tokens. The probe never logs the password by construction,
# but ``raw`` can echo server banners and error strings; scrub defensively.
_CRED_PATTERNS = (
    re.compile(r"(AUTH\s+(?:PLAIN|LOGIN)\s+)\S+", re.IGNORECASE),
    re.compile(r"([Pp]assword[=:\s]+)\S+"),
)


def is_cloudflare_ip(ip: str) -> bool:
    """Return True when *ip* belongs to a Cloudflare edge range."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _CLOUDFLARE_NETS)


def redact(text: str) -> str:
    """Scrub anything password-shaped out of a raw server line."""
    out = text or ""
    for pat in _CRED_PATTERNS:
        out = pat.sub(lambda m: m.group(1) + "***redacted***", out)
    return out


def infer_mode_for_port(port: int) -> str | None:
    """Infer the TLS mode implied by *port* (465 ⇒ SSL, 587 ⇒ STARTTLS).

    Returns ``None`` for ports with no implied mode so callers keep whatever
    the operator configured (2525, 25, 1025, custom relays, …).
    """
    if port == 465:
        return "ssl"
    if port == 587:
        return "starttls"
    return None


@dataclass
class StageResult:
    name: str
    ok: bool
    detail: str = ""
    raw: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosticReport:
    host: str
    port: int
    mode: str
    ok: bool = False
    verdict: str = ""
    hints: list[str] = field(default_factory=list)
    suggested_mode: str | None = None
    stages: list[StageResult] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "mode": self.mode,
            "ok": self.ok,
            "verdict": self.verdict,
            "hints": list(self.hints),
            "suggested_mode": self.suggested_mode,
            "stages": [s.to_dict() for s in self.stages],
            "duration_ms": self.duration_ms,
        }


class _Deadline:
    """Overall wall-clock budget shared by every stage."""

    def __init__(self, seconds: float):
        self.expires = time.monotonic() + max(1.0, seconds)

    def remaining(self, stage_timeout: float) -> float:
        return min(stage_timeout, max(0.5, self.expires - time.monotonic()))

    @property
    def exhausted(self) -> bool:
        return time.monotonic() >= self.expires


class _SmtpConn:
    """Minimal line-oriented SMTP client over a raw or TLS-wrapped socket."""

    def __init__(self, sock: socket.socket, stage: StageResult):
        self.sock = sock
        self.stage = stage
        self.file = sock.makefile("rb")

    def _record(self, prefix: str, line: str, redacted: bool = False) -> None:
        self.stage.raw.append(f"{prefix} {redact(line) if redacted else line}")

    def send_line(self, line: str, redacted: bool = False) -> None:
        self._record("C:", line, redacted=redacted)
        self.sock.sendall((line + "\r\n").encode())

    def read_response(self) -> list[str]:
        lines: list[str] = []
        while True:
            raw = self.file.readline()
            if not raw:
                raise ConnectionError("server closed the connection")
            line = raw.decode(errors="replace").rstrip("\r\n")
            lines.append(redact(line))
            self.stage.raw.append(f"S: {redact(line)}")
            # Multi-line replies: "250-..." continues, "250 ..." ends it.
            if len(line) >= 4 and line[3] == " ":
                break
            if len(line) == 3:
                break
        return lines

    def cmd(self, line: str, redacted: bool = False) -> list[str]:
        self.send_line(line, redacted=redacted)
        return self.read_response()

    @property
    def last(self) -> str:
        return self.stage.raw[-1].split(" ", 1)[-1] if self.stage.raw else ""

    def close(self) -> None:
        for closer in (lambda: self.file.close(), lambda: self.sock.close()):
            try:
                closer()
            except Exception:
                pass


def _code(lines: list[str]) -> int:
    """Return the numeric SMTP/IMAP status code of the last response line."""
    if not lines:
        return 0
    m = re.match(r"^(\d{3})", lines[-1].strip())
    return int(m.group(1)) if m else 0


def _tls_summary(tls_sock: ssl.SSLSocket) -> str:
    """Human-readable one-liner: protocol version + cert CN/issuer/expiry."""
    bits = [f"TLS {tls_sock.version() or '?'}"]
    try:
        cert = tls_sock.getpeercert()
    except Exception:
        cert = None
    if cert:
        subj = dict(x[0] for x in cert.get("subject", []))
        iss = dict(x[0] for x in cert.get("issuer", []))
        if subj.get("commonName"):
            bits.append(f"CN={subj['commonName']}")
        if iss.get("organizationName"):
            bits.append(f"issuer={iss['organizationName']}")
        if cert.get("notAfter"):
            bits.append(f"expires={cert['notAfter']}")
    return "  ".join(bits)


def _auth_hint(username: str, server_line: str) -> str:
    """Concrete fix for a 535, tailored to the username shape."""
    low = server_line.lower()
    if "@" not in (username or ""):
        return (
            f"Authentication was rejected. cPanel/Plesk mailboxes require the FULL "
            f"address as the username (e.g. user@domain, not user) — the configured "
            f"username is {username!r}."
        )
    if "app password" in low or "application-specific" in low:
        return "Authentication was rejected. This looks like Gmail/Workspace — generate an app password and use it instead of the account password."
    return (
        "Authentication was rejected. Check the password (Gmail needs an app password), "
        "and confirm the relay allows this mailbox to authenticate for sending."
    )


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _stage_dns(host: str, port: int, stage: StageResult) -> list[str]:
    """Resolve A/AAAA and flag Cloudflare-proxied hostnames."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        stage.ok = False
        stage.detail = f"DNS resolution failed: {e}"
        stage.raw.append(str(e))
        return ["Hostname does not resolve — check the SMTP host spelling."]
    ips = sorted({i[4][0] for i in infos})
    stage.raw.extend(f"A {ip}" for ip in ips)
    proxied = [ip for ip in ips if is_cloudflare_ip(ip)]
    if proxied:
        stage.ok = False
        stage.detail = (
            f"Resolved to Cloudflare edge IP(s): {', '.join(proxied)} — this hostname is "
            "proxied (orange cloud)."
        )
        return [
            "Mail hostname is Cloudflare-proxied. Cloudflare proxies only HTTP/HTTPS, so "
            "every SMTP/IMAP port looks 'closed' from here. Set that DNS record to "
            "DNS-only (grey cloud), or use the provider's canonical mail host "
            "(e.g. mail.privateemail.com).",
        ]
    stage.ok = True
    stage.detail = f"Resolved to {', '.join(ips)}"
    return []


def _stage_tcp(host: str, port: int, timeout: float, stage: StageResult) -> tuple[socket.socket | None, list[str]]:
    """Connect, distinguishing a dropped packet (timeout) from a refused port."""
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout)
    except socket.timeout:
        stage.ok = False
        stage.detail = f"TCP timeout after {timeout:.0f}s — packets are being dropped."
        return None, [
            "TCP timeout: the machine running Quickly cannot reach that port and the "
            "packets are dropped (firewall / egress block / cloud provider SMTP "
            "restriction). Check the host's outbound firewall rules.",
        ]
    except ConnectionRefusedError:
        stage.ok = False
        stage.detail = "Connection refused — host reachable but nothing is listening on this port."
        return None, [
            "Connection refused: wrong port, or the mail service is down. Confirm the "
            "port (587 STARTTLS / 465 SSL) in the mail host's documentation.",
        ]
    except OSError as e:
        stage.ok = False
        stage.detail = f"TCP error: {e}"
        return None, [f"TCP connection failed: {e}"]
    elapsed = time.monotonic() - t0
    stage.ok = True
    stage.detail = f"Connected in {elapsed:.2f}s"
    return sock, []


def _stage_tls_implicit(
    sock: socket.socket, host: str, mode: str, port: int, stage: StageResult
) -> tuple[socket.socket | None, list[str]]:
    """Wrap an already-connected socket in implicit TLS."""
    ctx = ssl.create_default_context()
    try:
        tls = ctx.wrap_socket(sock, server_hostname=host)
    except ssl.SSLError as e:
        stage.ok = False
        stage.detail = f"Implicit TLS handshake failed: {e}"
        hints = []
        if mode != "ssl":
            hints.append(
                f"TLS handshake failed on port {port} in STARTTLS mode. Port 465 is "
                "implicit SSL — switch the inbox to SSL (465)."
            )
        else:
            hints.append(
                f"Implicit TLS handshake failed: {e}. Check the port (465 is SSL, 587 "
                "is STARTTLS) and that the certificate is valid."
            )
        return None, hints
    except OSError as e:
        stage.ok = False
        stage.detail = f"TLS socket error: {e}"
        return None, [f"TLS socket error: {e}"]
    stage.ok = True
    stage.detail = _tls_summary(tls)
    return tls, []


def _stage_tls_starttls(
    conn: _SmtpConn, host: str, mode: str, port: int, stage: StageResult
) -> tuple[_SmtpConn | None, list[str]]:
    """Negotiate STARTTLS after EHLO, using the capabilities already gathered."""
    caps = " ".join(conn.stage.raw).upper() if conn.stage.raw else ""
    if "STARTTLS" not in caps:
        # Some servers only advertise STARTTLS after the *first* EHLO; the caller
        # already sent EHLO, so this is authoritative.
        stage.ok = False
        stage.detail = "Server does not advertise STARTTLS."
        return None, [
            f"The relay does not offer STARTTLS on port {port}. Use implicit SSL (465) "
            "instead, or a port that supports STARTTLS."
        ]
    resp = conn.cmd("STARTTLS")
    if _code(resp) != 220:
        stage.ok = False
        stage.detail = f"STARTTLS refused: {conn.last}"
        return None, ["The relay refused STARTTLS. Try implicit SSL (465) instead."]
    ctx = ssl.create_default_context()
    try:
        tls = ctx.wrap_socket(conn.sock, server_hostname=host)
    except ssl.SSLError as e:
        stage.ok = False
        stage.detail = f"STARTTLS handshake failed: {e}"
        return None, [f"STARTTLS handshake failed: {e}"]
    stage.ok = True
    stage.detail = _tls_summary(tls)
    conn.close()
    return _SmtpConn(tls, stage), []


def _stage_ehlo(conn: _SmtpConn, stage: StageResult) -> list[str]:
    """Capture the advertised capabilities (AUTH mechanisms, STARTTLS, SIZE)."""
    resp = conn.cmd("EHLO quickly-diagnose.local")
    stage.raw.extend(conn.stage.raw[-len(resp) - 1:])
    code = _code(resp)
    if code != 250:
        stage.ok = False
        stage.detail = f"EHLO rejected: {resp[-1] if resp else '(no response)'}"
        return [f"The relay rejected EHLO: {resp[-1] if resp else 'no response'}"]
    caps = [ln[4:].strip() for ln in resp if len(ln) > 4]
    auth_line = next((c for c in caps if c.upper().startswith("AUTH")), "")
    size_line = next((c for c in caps if c.upper().startswith("SIZE")), "")
    stage.ok = True
    parts = []
    if auth_line:
        parts.append(auth_line)
    if size_line:
        parts.append(size_line)
    if "STARTTLS" in " ".join(caps).upper():
        parts.append("STARTTLS")
    stage.detail = "Advertised: " + ", ".join(parts) if parts else "EHLO accepted (no AUTH capability advertised)"
    return []


def _stage_auth(conn: _SmtpConn, username: str, password: str, stage: StageResult) -> list[str]:
    """Authenticate with PLAIN (falling back to LOGIN when not advertised)."""
    caps = " ".join(stage.raw).upper()
    mechanisms = "PLAIN LOGIN"
    m = re.search(r"AUTH[ =]([A-Z0-9 _-]+)", caps)
    if m:
        mechanisms = m.group(1).strip()
    if "PLAIN" in mechanisms:
        token = base64.b64encode(f"\0{username}\0{password}".encode()).decode()
        resp = conn.cmd(f"AUTH PLAIN {token}", redacted=True)
    elif "LOGIN" in mechanisms:
        conn.cmd("AUTH LOGIN", redacted=True)
        conn.cmd(base64.b64encode(username.encode()).decode(), redacted=True)
        resp = conn.cmd(base64.b64encode(password.encode()).decode(), redacted=True)
    else:
        stage.ok = False
        stage.detail = f"No supported AUTH mechanism (advertised: {mechanisms or 'none'})"
        return ["The relay advertises no AUTH PLAIN/LOGIN mechanism — it may require a different TLS port or not allow relaying."]
    stage.raw = list(conn.stage.raw[-2 * len(resp) - 1:])
    code = _code(resp)
    if code == 235:
        stage.ok = True
        stage.detail = "Authenticated OK"
        return []
    stage.ok = False
    stage.detail = f"Authentication failed ({code}): {conn.last}"
    return [_auth_hint(username, conn.last)]


def _stage_send_probe(
    conn: _SmtpConn,
    stages: dict[str, StageResult],
    from_email: str,
    to_email: str,
    host: str,
) -> list[str]:
    """MAIL FROM / RCPT TO / DATA with a real (tiny) probe message."""
    hints: list[str] = []

    mf = stages["mail_from"]
    resp = conn.cmd(f"MAIL FROM:<{from_email}>")
    code = _code(resp)
    if code != 250:
        mf.ok = False
        mf.detail = f"MAIL FROM rejected ({code}): {conn.last}"
        if 500 <= code < 600:
            hints.append(
                "The relay rejected your sender address (MAIL FROM) with a 5xx. This is "
                "relay policy — the sender is not permitted / is not a configured "
                "mailbox on the mail host, not a Quickly bug. Use an address that "
                "belongs to the SMTP account (usually the same as the username)."
            )
        else:
            hints.append(f"MAIL FROM was refused ({code}): {conn.last}")
        return hints
    mf.ok = True
    mf.detail = "Sender accepted"

    rc = stages["rcpt_to"]
    if not to_email:
        rc.ok = False
        rc.detail = "Skipped — no recipient supplied for the probe."
        stages["data"].ok = False
        stages["data"].detail = "Skipped — no recipient supplied for the probe."
        return hints
    resp = conn.cmd(f"RCPT TO:<{to_email}>")
    code = _code(resp)
    if code != 250 and code != 251:
        rc.ok = False
        rc.detail = f"RCPT TO rejected ({code}): {conn.last}"
        hints.append(
            f"The relay rejected the recipient ({to_email}). Check the address, or use "
            "a mailbox that this relay is allowed to deliver to."
        )
        conn.cmd("RSET")
        return hints
    rc.ok = True
    rc.detail = f"Recipient accepted ({to_email})"

    data = stages["data"]
    resp = conn.cmd("DATA")
    if _code(resp) != 354:
        data.ok = False
        data.detail = f"DATA refused: {conn.last}"
        hints.append(f"The relay refused DATA: {conn.last}")
        return hints
    body = (
        f"From: {from_email}\r\n"
        f"To: {to_email}\r\n"
        f"Subject: Quickly SMTP diagnostic probe\r\n"
        f"Message-ID: <diagnose.{int(time.time())}@{host}>\r\n"
        f"X-Quickly-Diagnose: 1\r\n"
        "\r\n"
        "This is an automated deliverability probe sent by the Quickly "
        "\"Diagnose inbox\" feature. No action is required.\r\n"
        ".\r\n"
    )
    conn.sock.sendall(body.encode())
    resp = conn.read_response()
    code = _code(resp)
    if code == 250:
        data.ok = True
        data.detail = "Message accepted by the relay"
        return hints
    data.ok = False
    data.detail = f"Message rejected ({code}): {conn.last}"
    if 500 <= code < 600:
        hints.append(
            f"The message was rejected at DATA ({code}): {conn.last}. A 5xx here is a "
            "permanent content/policy rejection by the relay, not a transport problem."
        )
    else:
        hints.append(f"The relay returned {code} at DATA: {conn.last} — try again later.")
    return hints


def _stage_imap_connect(
    host: str,
    port: int,
    use_ssl: bool,
    username: str,
    password: str,
    timeout: float,
    stage: StageResult,
) -> list[str]:
    """IMAP connect/login/select INBOX. Only 993/SSL is supported today."""
    import imaplib

    hints: list[str] = []
    if not host:
        stage.ok = True
        stage.detail = "IMAP not configured — skipped"
        return hints
    if not use_ssl:
        hints.append(
            "IMAP is configured without SSL. Quickly currently supports SSL IMAP "
            "only (typically port 993) — STARTTLS on 143 is not implemented, so "
            "reply sync will not work with the current settings."
        )
    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            client = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=timeout)
        else:
            client = imaplib.IMAP4(host, port, timeout=timeout)
    except (imaplib.IMAP4.error, OSError, socket.timeout) as e:
        stage.ok = False
        stage.detail = f"IMAP connection failed: {e}"
        hints.append(f"IMAP connection to {host}:{port} failed: {e}")
        return hints
    stage.raw.append(f"C: LOGIN {username} ***redacted***")
    try:
        client.login(username, password)
        typ, _ = client.select("INBOX", readonly=True)
        if typ != "OK":
            stage.ok = False
            stage.detail = "Login succeeded but INBOX could not be selected."
            hints.append("IMAP login worked but selecting INBOX failed — check mailbox permissions.")
        else:
            stage.ok = True
            stage.detail = "Login + INBOX select succeeded"
    except imaplib.IMAP4.error as e:
        stage.ok = False
        stage.detail = f"IMAP login failed: {e}"
        hints.append(_auth_hint(username, str(e)))
    except OSError as e:
        stage.ok = False
        stage.detail = f"IMAP error: {e}"
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return hints


# ---------------------------------------------------------------------------
# Top-level probe
# ---------------------------------------------------------------------------


def _probe_once(
    *,
    host: str,
    port: int,
    mode: str,
    username: str,
    password: str,
    from_email: str,
    to_email: str,
    timeout: float,
    deadline: _Deadline,
    imap: dict[str, Any] | None,
    stage_names: tuple[str, ...] = STAGE_NAMES,
) -> DiagnosticReport:
    """Run one full attempt with a single TLS mode."""
    report = DiagnosticReport(host=host, port=port, mode=mode)
    stages = {name: StageResult(name=name, ok=False) for name in stage_names}
    report.stages = list(stages.values())
    all_hints: list[str] = []

    def budget() -> float:
        return deadline.remaining(timeout)

    # ── DNS ────────────────────────────────────────────────────────────────
    dns_hints = _stage_dns(host, port, stages["dns"])
    all_hints.extend(dns_hints)
    if not stages["dns"].ok and "resolve" in stages["dns"].detail.lower():
        report.verdict = "The SMTP hostname does not resolve."
        report.hints = all_hints
        return report

    # ── TCP ────────────────────────────────────────────────────────────────
    sock, tcp_hints = _stage_tcp(host, port, budget(), stages["tcp"])
    all_hints.extend(tcp_hints)
    if sock is None:
        report.verdict = (
            f"Could not open a TCP connection to {host}:{port} — the relay is unreachable "
            "from this machine."
        )
        report.hints = all_hints
        return report

    # The conversation stage collects the transcript; the protocol stages have
    # their own raw (TLS handshake, advertised caps).  Attaching the same stage
    # object to the conversation would duplicate EHLO/AUTH/DATA output.
    convo = StageResult(name="conversation", ok=False)
    conn: _SmtpConn | None = None
    try:
        if mode == "ssl":
            tls, tls_hints = _stage_tls_implicit(sock, host, mode, port, stages["tls"])
            all_hints.extend(tls_hints)
            if tls is None:
                report.verdict = f"TLS handshake failed on {host}:{port} (mode={mode})."
                report.hints = all_hints
                return report
            conn = _SmtpConn(tls, convo)
            # Banner
            try:
                conn.read_response()
            except Exception:
                pass
            ehlo_hints = _stage_ehlo(conn, stages["ehlo"])
            all_hints.extend(ehlo_hints)
            if not stages["ehlo"].ok:
                report.verdict = f"Connected over SSL, but the relay rejected EHLO on {host}:{port}."
                report.hints = all_hints
                return report
            convo.ok = True
            convo.detail = "SMTP session completed"
        else:
            conn = _SmtpConn(sock, convo)
            try:
                conn.read_response()
            except Exception as e:
                stages["ehlo"].detail = f"No SMTP banner: {e}"
                report.verdict = "The port answered but did not send an SMTP banner — it may not be an SMTP service."
                report.hints = all_hints
                return report
            ehlo_hints = _stage_ehlo(conn, stages["ehlo"])
            all_hints.extend(ehlo_hints)
            if not stages["ehlo"].ok:
                report.verdict = f"Connected, but the relay rejected EHLO on {host}:{port}."
                report.hints = all_hints
                return report
            conn, tls_hints = _stage_tls_starttls(conn, host, mode, port, stages["tls"])
            all_hints.extend(tls_hints)
            if conn is None:
                report.verdict = f"STARTTLS could not be established on {host}:{port}."
                report.hints = all_hints
                return report
            # Re-EHLO over the encrypted channel (required after STARTTLS).
            conn.cmd("EHLO quickly-diagnose.local")
            convo.ok = True
            convo.detail = "SMTP session completed"

        # ── AUTH ───────────────────────────────────────────────────────────
        if username:
            auth_hints = _stage_auth(conn, username, password, stages["auth"])
            all_hints.extend(auth_hints)
            if not stages["auth"].ok:
                report.verdict = f"Authentication failed against {host}:{port} — the credentials are rejected."
                report.hints = all_hints
                return report
        else:
            stages["auth"].ok = False
            stages["auth"].detail = "Skipped — no username configured."

        # ── MAIL FROM / RCPT TO / DATA ─────────────────────────────────────
        send_hints = _stage_send_probe(conn, stages, from_email, to_email, host)
        all_hints.extend(send_hints)
        if not stages["mail_from"].ok:
            report.verdict = (
                "Authentication works, but the relay rejects the sender address — "
                "sending is blocked by relay policy, not by Quickly."
            )
            report.hints = all_hints
            return report
        if to_email and not stages["rcpt_to"].ok:
            report.verdict = "The relay accepts the sender but rejects the test recipient."
            report.hints = all_hints
            return report
        if to_email and not stages["data"].ok:
            report.verdict = "The relay rejected the probe message at DATA — check content/policy rules."
            report.hints = all_hints
            return report

        try:
            conn.cmd("QUIT")
        except Exception:
            pass
    finally:
        if conn is not None:
            conn.close()

    # ── IMAP (optional) ────────────────────────────────────────────────────
    if imap is not None:
        imap_hints = _stage_imap_connect(
            host=imap.get("host") or "",
            port=int(imap.get("port") or 993),
            use_ssl=bool(imap.get("use_ssl", True)),
            username=imap.get("username") or "",
            password=imap.get("password") or "",
            timeout=budget(),
            stage=stages["imap"],
        )
        all_hints.extend(imap_hints)
    else:
        stages["imap"].ok = True
        stages["imap"].detail = "IMAP not configured — skipped"

    report.ok = all(s.ok for s in report.stages)
    if report.ok:
        report.verdict = (
            "All stages passed — Quickly's SMTP path will work with these settings. "
            "If mail still does not arrive, the problem is downstream of the relay "
            "(e.g. the relay's own port-25 egress)."
        )
    report.hints = all_hints
    return report


def diagnose(
    *,
    host: str,
    port: int,
    use_tls: bool = True,
    use_ssl: bool = False,
    username: str = "",
    password: str = "",
    from_email: str = "",
    to_email: str = "",
    imap_host: str = "",
    imap_port: int = 993,
    imap_username: str = "",
    imap_password: str = "",
    imap_use_ssl: bool = True,
    timeout: float = DEFAULT_STAGE_TIMEOUT,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
) -> dict[str, Any]:
    """Run the staged probe and return the structured report as a plain dict.

    The mode/port pair is inferred (465 ⇒ SSL, 587 ⇒ STARTTLS).  When the first
    attempt fails on TLS/EHLO, the other mode is tried once so the report can
    say which one actually works.
    """
    host = (host or "").strip()
    port = int(port or 587)
    mode = "ssl" if use_ssl else ("starttls" if use_tls else "plain")
    inferred = infer_mode_for_port(port)
    notes: list[str] = []
    if inferred and inferred != mode and mode != "plain":
        notes.append(
            f"Port {port} normally uses {'SSL' if inferred == 'ssl' else 'STARTTLS'}, "
            f"but this inbox is set to {'SSL' if mode == 'ssl' else 'STARTTLS'}. "
            "Testing both modes."
        )
    from_email = (from_email or username or "").strip()
    to_email = (to_email or "").strip()
    imap = None
    if (imap_host or "").strip():
        imap = {
            "host": (imap_host or "").strip(),
            "port": int(imap_port or 993),
            "use_ssl": bool(imap_use_ssl),
            "username": (imap_username or "").strip(),
            "password": imap_password or "",
        }

    t0 = time.monotonic()
    deadline = _Deadline(total_timeout)
    primary = _probe_once(
        host=host, port=port, mode=mode, username=username, password=password,
        from_email=from_email, to_email=to_email, timeout=timeout,
        deadline=deadline, imap=imap,
    )

    suggested_mode: str | None = None
    if not primary.ok and not deadline.exhausted:
        other = "starttls" if mode == "ssl" else "ssl"
        # Only worth retrying when the failure could plausibly be the TLS mode
        # (i.e. we got as far as a connection, or the port implies the other mode).
        failed_stages = {s.name for s in primary.stages if not s.ok}
        worth_retry = bool(failed_stages & {"tls", "ehlo"}) or inferred == other
        if worth_retry and not deadline.exhausted:
            alt = _probe_once(
                host=host, port=port, mode=other, username=username, password=password,
                from_email=from_email, to_email=to_email, timeout=timeout,
                deadline=deadline, imap=imap,
            )
            if alt.ok:
                suggested_mode = other
                notes.append(
                    f"These settings work with {'implicit SSL' if other == 'ssl' else 'STARTTLS'} "
                    f"on port {port}. Switch the inbox to that mode."
                )
                primary.hints.append(
                    f"The same relay works with {'SSL' if other == 'ssl' else 'STARTTLS'}. "
                    f"Change this inbox to {'SSL (465)' if other == 'ssl' else 'STARTTLS (587)'}."
                )

    report = primary.to_dict()
    report["ok"] = primary.ok
    report["suggested_mode"] = suggested_mode
    report["duration_ms"] = int((time.monotonic() - t0) * 1000)
    if notes:
        report["hints"] = notes + report["hints"]
    log.info(
        "SMTP diagnose host=%s port=%s mode=%s ok=%s suggested=%s",
        host, port, mode, report["ok"], suggested_mode,
    )
    return report


def diagnose_account(account, to_email: str = "") -> dict[str, Any]:
    """Run :func:`diagnose` against an ORM ``SmtpAccount``/inbox pair.

    Accepts anything exposing the ``smtp_*`` / ``imap_*`` attributes, so both
    the ORM model and a lightweight stub work in tests.
    """
    report = diagnose(
        host=account.smtp_host,
        port=account.smtp_port,
        use_tls=bool(account.smtp_use_tls),
        use_ssl=bool(account.smtp_use_ssl),
        username=getattr(account, "smtp_username", "") or "",
        password=getattr(account, "smtp_password", "") or "",
        from_email=getattr(account, "smtp_username", "") or "",
        to_email=to_email,
        imap_host=getattr(account, "imap_host", "") or "",
        imap_port=getattr(account, "imap_port", 993) or 993,
        imap_username=getattr(account, "imap_username", "") or "",
        imap_password=getattr(account, "imap_password", "") or "",
        imap_use_ssl=bool(getattr(account, "imap_use_ssl", True)),
    )
    return report


def render_text_report(report: dict[str, Any]) -> str:
    """Render the report as plain text for pasting into a support thread."""
    lines = [
        "Quickly SMTP diagnostic report",
        f"Target: {report.get('host')}:{report.get('port')}  mode={report.get('mode')}",
        f"Result: {'PASS' if report.get('ok') else 'FAIL'}  ({report.get('duration_ms', 0)} ms)",
        "",
    ]
    for stage in report.get("stages", []):
        mark = "PASS" if stage.get("ok") else "FAIL"
        lines.append(f"[{mark}] {stage.get('name')}: {stage.get('detail') or ''}")
        for raw in stage.get("raw", []) or []:
            lines.append(f"       {raw}")
    lines.append("")
    lines.append(f"Verdict: {report.get('verdict') or ''}")
    if report.get("suggested_mode"):
        lines.append(
            f"Suggested mode: {'SSL' if report['suggested_mode'] == 'ssl' else 'STARTTLS'}"
        )
    if report.get("hints"):
        lines.append("Hints:")
        for hint in report["hints"]:
            lines.append(f"  - {hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI: python -m app.smtp_diagnose --inbox-id N
# ---------------------------------------------------------------------------


def _cli_manual(args) -> int:
    report = diagnose(
        host=args.host,
        port=args.port,
        use_tls=(args.mode == "starttls"),
        use_ssl=(args.mode == "ssl"),
        username=args.user or "",
        password=args.password or "",
        from_email=args.from_address or args.user or "",
        to_email=args.to or "",
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text_report(report))
    return 0 if report["ok"] else 1


async def _cli_inbox(args) -> int:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models import Inbox, SmtpAccount

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SmtpAccount, Inbox)
            .join(Inbox, SmtpAccount.inbox_id == Inbox.id)
            .where(Inbox.id == args.inbox_id)
        )
        row = result.one_or_none()
        if row is None:
            print(f"No SMTP account configured for inbox {args.inbox_id}", file=sys.stderr)
            return 1
        account, _inbox = row
        report = diagnose_account(account, to_email=args.to or "")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text_report(report))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.smtp_diagnose",
        description="Staged SMTP/IMAP diagnostic for a Quickly SMTP inbox.",
    )
    parser.add_argument("--inbox-id", type=int, help="Run against this inbox's stored credentials.")
    parser.add_argument("--host", help="Ad-hoc host (when not using --inbox-id).")
    parser.add_argument("--port", type=int, default=587)
    parser.add_argument("--mode", choices=["starttls", "ssl", "plain"], default="starttls")
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--from-address", dest="from_address")
    parser.add_argument("--to", help="Recipient for the MAIL FROM/RCPT TO/DATA probe.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    if args.inbox_id:
        import asyncio

        return asyncio.run(_cli_inbox(args))
    if not args.host:
        parser.error("provide --inbox-id or --host")
    return _cli_manual(args)


if __name__ == "__main__":
    sys.exit(main())
