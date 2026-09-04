"""Shared helpers for the generic SMTP / IMAP inbox provider.

Covers credential validation, SMTP + IMAP connection testing, and small
parsers reused by the sender (outbound) and the unibox IMAP sync (inbound).
Uses only the Python standard library (``smtplib`` / ``imaplib`` / ``email``)
so no new dependencies are required.
"""
from __future__ import annotations

import imaplib
import logging
import smtplib
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime
from email import policy as _email_policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

log = logging.getLogger("quickly.smtp")


def _verified_ssl_context() -> ssl.SSLContext:
    """Return a certificate-verifying SSL context for SMTP/IMAP TLS.

    Never pass ``context=None`` to ``smtplib``/``imaplib`` — the stdlib default
    there is an *unverified* context, which allows a MITM to intercept
    credentials even when STARTTLS/SSL is in use.
    """
    return ssl.create_default_context()


@dataclass
class SmtpTestResult:
    ok: bool
    error: str = ""
    detail: str = ""


def normalise_message_id(raw: str | None) -> str:
    """Normalise an RFC 822 Message-ID to ``<id>`` form (lower-cased id part)."""
    if not raw:
        return ""
    v = raw.strip()
    if not v:
        return ""
    # Take the first whitespace-separated token (headers are single IDs).
    v = v.split()[0]
    if not v.startswith("<"):
        v = f"<{v}"
    if not v.endswith(">"):
        v = f"{v}>"
    return v


def _assert_host_not_private(host: str) -> str | None:
    """Block hosts that resolve to loopback/private/reserved networks (SSRF guard).

    Returns an error string when the host must be refused, else ``None``.
    DNS failures are *not* rejected here — the connection test surfaces them
    with a proper error. Bypass (self-hosted relays, local test doubles) via
    ``SMTP_ALLOW_PRIVATE_HOSTS=true`` or ``TEST_MODE``.
    """
    from app.security import is_private_ip, resolve_and_check

    try:
        from app.settings_manager import settings

        if settings.test_mode:
            return None
    except Exception:  # pragma: no cover - settings always importable
        pass
    import os as _os

    if _os.getenv("SMTP_ALLOW_PRIVATE_HOSTS", "").lower() in ("1", "true", "yes"):
        return None

    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return None
    # Literal IP fast-path (also catches link-local / metadata literals).
    ok, reason = resolve_and_check(host)
    if ok:
        return None
    # Only reject when the failure is an actual private/blocked resolution;
    # a DNS lookup failure is left to the connection test to report.
    reason_low = reason.lower()
    if "private" in reason_low or "blocked hostname" in reason_low:
        return (
            f"{host} points at a private/internal address and cannot be used for "
            "SMTP/IMAP. Set SMTP_ALLOW_PRIVATE_HOSTS=true to allow self-hosted relays."
        )
    return None


def sanitize_connection_error(msg: str) -> str:
    """Reduce a raw SMTP/IMAP exception string to a safe, category-level message.

    Raw exception text can leak internal hostnames, ports and server banners,
    which turns the persisted ``last_test_error`` into a network-probing
    oracle. Full detail is kept in the application logs instead.
    """
    m = (msg or "").strip()
    if not m:
        return ""
    low = m.lower()
    if "authentication" in low or "credentials" in low or low.startswith("535"):
        return "Authentication failed — check username/password"
    if "timed out" in low or "timeout" in low:
        return "Connection timed out"
    if "refused" in low:
        return "Connection refused"
    if "certificate" in low or "ssl" in low or "tls" in low:
        return "TLS/certificate error"
    if "getaddrinfo" in low or "name or service not known" in low or "not known" in low or "no address" in low:
        return "DNS resolution failed — check the hostname"
    return "Connection failed (details in server logs)"


def validate_smtp_account_payload(data: dict, require_password: bool = True) -> str | None:
    """Return an error string when the SMTP/IMAP payload is invalid, else None."""
    smtp_host = (data.get("smtp_host") or "").strip()
    if not smtp_host:
        return "smtp_host is required"
    if len(smtp_host) > 255:
        return "smtp_host is too long"
    try:
        smtp_port = int(data.get("smtp_port", 587))
    except (TypeError, ValueError):
        return "smtp_port must be a number"
    if not 1 <= smtp_port <= 65535:
        return "smtp_port must be between 1 and 65535"
    if data.get("smtp_use_tls") and data.get("smtp_use_ssl"):
        return "Use either STARTTLS or implicit SSL, not both"
    if not (data.get("smtp_use_tls") or data.get("smtp_use_ssl")):
        # Plain SMTP would transmit the password in cleartext. Dev escape
        # hatch for local relay doubles (Mailpit, aiosmtpd): SMTP_ALLOW_PLAIN_SMTP=true.
        import os as _os

        if _os.getenv("SMTP_ALLOW_PLAIN_SMTP", "").lower() not in ("1", "true", "yes"):
            return "SMTP connection must use STARTTLS or implicit SSL"
    if not (data.get("smtp_username") or "").strip():
        return "smtp_username is required"
    if require_password and not (data.get("smtp_password") or ""):
        return "smtp_password is required"
    host_err = _assert_host_not_private(smtp_host)
    if host_err:
        return host_err

    imap_host = (data.get("imap_host") or "").strip()
    if imap_host:
        try:
            imap_port = int(data.get("imap_port", 993))
        except (TypeError, ValueError):
            return "imap_port must be a number"
        if not 1 <= imap_port <= 65535:
            return "imap_port must be between 1 and 65535"
        # When IMAP is configured, its auth must be complete as well.
        if not (data.get("imap_username") or "").strip():
            return "imap_username is required when imap_host is set"
        if not (data.get("imap_password") or ""):
            return "imap_password is required when imap_host is set"
        host_err = _assert_host_not_private(imap_host)
        if host_err:
            return host_err
    return None


def _smtp_connect(account, timeout: float = 15.0):
    """Return a connected+logged-in smtplib client for *account* (caller must quit)."""
    host = (account.smtp_host or "").strip()
    port = int(account.smtp_port or 587)
    if account.smtp_use_ssl:
        client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=_verified_ssl_context())
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)
        if account.smtp_use_tls:
            client.ehlo()
            client.starttls(context=_verified_ssl_context())
            client.ehlo()
    client.login(account.smtp_username or "", account.smtp_password or "")
    return client


def test_smtp_connection(account, timeout: float = 15.0) -> SmtpTestResult:
    """Verify SMTP connectivity + auth (EHLO/STARTTLS/LOGIN + NOOP)."""
    try:
        client = _smtp_connect(account, timeout=timeout)
    except smtplib.SMTPAuthenticationError as e:
        return SmtpTestResult(ok=False, error=f"SMTP authentication failed: {e}")
    except (smtplib.SMTPException, socket.error, OSError) as e:
        return SmtpTestResult(ok=False, error=f"SMTP connection failed: {e}")
    except Exception as e:  # pragma: no cover - defensive
        return SmtpTestResult(ok=False, error=f"SMTP connection failed: {e}")
    try:
        client.noop()
    except Exception as e:
        log.debug("SMTP NOOP failed (non-fatal): %s", e)
    try:
        client.quit()
    except Exception:
        try:
            client.close()
        except Exception:
            pass
    return SmtpTestResult(ok=True, detail="SMTP login + NOOP succeeded")


def _imap_connect(account, timeout: float = 15.0):
    """Return a logged-in, INBOX-selected imaplib client (caller must logout)."""
    host = (account.imap_host or "").strip()
    port = int(account.imap_port or 993)
    if account.imap_use_ssl:
        client = imaplib.IMAP4_SSL(host, port, ssl_context=_verified_ssl_context())
    else:
        client = imaplib.IMAP4(host, port)
    client.socket().settimeout(timeout)
    client.login(account.imap_username or "", account.imap_password or "")
    typ, _ = client.select("INBOX", readonly=True)
    if typ != "OK":
        try:
            client.logout()
        except Exception:
            pass
        raise imaplib.IMAP4.error("Could not select INBOX")
    return client


def test_imap_connection(account, timeout: float = 15.0) -> SmtpTestResult:
    """Verify IMAP connectivity + auth + INBOX select (skipped when not configured)."""
    if not (account.imap_host or "").strip():
        return SmtpTestResult(ok=True, detail="IMAP not configured — skipped")
    try:
        client = _imap_connect(account, timeout=timeout)
    except imaplib.IMAP4.error as e:
        return SmtpTestResult(ok=False, error=f"IMAP authentication failed: {e}")
    except (socket.error, OSError) as e:
        return SmtpTestResult(ok=False, error=f"IMAP connection failed: {e}")
    except Exception as e:  # pragma: no cover - defensive
        return SmtpTestResult(ok=False, error=f"IMAP connection failed: {e}")
    try:
        client.logout()
    except Exception:
        pass
    return SmtpTestResult(ok=True, detail="IMAP login + INBOX select succeeded")


def test_account_connections(account, timeout: float = 15.0) -> tuple[SmtpTestResult, SmtpTestResult]:
    """Test SMTP and (when configured) IMAP; returns ``(smtp_result, imap_result)``."""
    return test_smtp_connection(account, timeout=timeout), test_imap_connection(account, timeout=timeout)


def parse_imap_message(raw: bytes) -> dict:
    """Parse raw RFC822 bytes into plain/html bodies + headers used by sync."""
    msg = BytesParser(policy=_email_policy.default).parsebytes(raw)
    subject = str(msg.get("Subject", "") or "")
    message_id = normalise_message_id(str(msg.get("Message-ID", "") or ""))
    in_reply_to = normalise_message_id(str(msg.get("In-Reply-To", "") or ""))
    refs_raw = str(msg.get("References", "") or "")
    references = [normalise_message_id(p) for p in refs_raw.split() if p.strip()]
    from_addrs = [a for _, a in getaddresses([str(msg.get("From", "") or "")]) if a]
    to_addrs = [a.lower() for _, a in getaddresses([str(msg.get("To", "") or "")]) if a]
    from_addr = (from_addrs[0].lower() if from_addrs else "")

    body_plain, body_html = "", ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.is_multipart():
                    continue
                ctype = part.get_content_type()
                try:
                    content = part.get_content()
                except Exception:
                    continue
                if ctype == "text/plain" and not body_plain and isinstance(content, str):
                    body_plain = content
                elif ctype == "text/html" and not body_html and isinstance(content, str):
                    body_html = content
        else:
            try:
                content = msg.get_content()
            except Exception:
                content = ""
            if isinstance(content, str):
                if msg.get_content_type() == "text/html":
                    body_html = content
                else:
                    body_plain = content
    except Exception:
        log.debug("Failed to extract IMAP bodies", exc_info=True)

    date_dt = None
    date_raw = str(msg.get("Date", "") or "")
    if date_raw:
        try:
            parsed = parsedate_to_datetime(date_raw)
            if parsed is not None:
                if parsed.tzinfo is not None:
                    from datetime import timezone as _tz
                    date_dt = parsed.astimezone(_tz.utc).replace(tzinfo=None)
                else:
                    date_dt = parsed
        except Exception:
            date_dt = None

    snippet = (body_plain or "").strip().replace("\r", " ").replace("\n", " ")
    snippet = " ".join(snippet.split())[:180]

    return {
        "subject": subject,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "from": from_addr,
        "to": to_addrs,
        "body_plain": body_plain or "",
        "body_html": body_html or "",
        "date": date_dt or datetime.utcnow(),
        "snippet": snippet,
    }
