"""Redact secrets from log output.

The Gmail Pub/Sub push endpoint authenticates callers with a shared secret in
the query string (``POST /api/unibox/gmail/push?token=<secret>``).  uvicorn's
access logger renders the *full* request path, so without this filter the
secret ends up in plain text in the access log of every deployment.

Only the ``token`` query parameter is rewritten; everything else in the record
is left byte-for-byte intact so the filter stays narrow and predictable.
"""
from __future__ import annotations

import logging
import re

__all__ = ["redact_token_in_path", "TokenRedactionFilter", "install_log_redaction"]

#: Marker substituted for the value of a ``token`` query parameter.
REDACTED = "REDACTED"

# Matches ``token=<value>`` anywhere in a request path, stopping at the next
# query separator (``&``), fragment (``#``) or whitespace. Bare values (no
# query string at all, e.g. a stray log line) are left alone.
_TOKEN_RE = re.compile(r"(?i)\btoken=[^&\s#\"']*")


def redact_token_in_path(value: str) -> str:
    """Return *value* with every ``token=…`` query value replaced.

    >>> redact_token_in_path('/api/unibox/gmail/push?token=s3cret')
    '/api/unibox/gmail/push?token=REDACTED'
    >>> redact_token_in_path('/api/unibox/gmail/push?token=s&x=1')
    '/api/unibox/gmail/push?token=REDACTED&x=1'
    """
    return _TOKEN_RE.sub(f"token={REDACTED}", value)


class TokenRedactionFilter(logging.Filter):
    """logging filter that redacts ``token=…`` in uvicorn access records.

    uvicorn's ``AccessFormatter.formatMessage`` unpacks ``record.args`` as
    ``(client_addr, method, full_path, http_version, status_code)``, so the
    secret lives in ``args[2]``.  For any other record that carries string
    arguments we redact each of them, which keeps non-uvicorn loggers that log
    a URL safe too without touching message templates.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if isinstance(args, tuple):
                if any(isinstance(a, str) and "token=" in a for a in args):
                    record.args = tuple(
                        redact_token_in_path(a) if isinstance(a, str) else a
                        for a in args
                    )
            elif isinstance(args, dict):
                if any(isinstance(v, str) and "token=" in v for v in args.values()):
                    record.args = {
                        k: (redact_token_in_path(v) if isinstance(v, str) else v)
                        for k, v in args.items()
                    }
            elif isinstance(args, str) and "token=" in args:
                record.args = redact_token_in_path(args)
        except Exception:  # pragma: no cover - a filter must never break logging
            return True
        return True


def install_log_redaction() -> TokenRedactionFilter:
    """Attach :class:`TokenRedactionFilter` to the ``uvicorn.access`` logger.

    Idempotent: repeated calls (e.g. after a dev-server reload) do not stack
    filters.  Should be called as early as possible during app startup, before
    uvicorn configures its own logging if possible.
    """
    uvicorn_access = logging.getLogger("uvicorn.access")
    for existing in uvicorn_access.filters:
        if isinstance(existing, TokenRedactionFilter):
            return existing
    log_filter = TokenRedactionFilter()
    uvicorn_access.addFilter(log_filter)
    return log_filter
