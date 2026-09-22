"""The uvicorn access-log token redaction filter (security hygiene).

The Gmail Pub/Sub push endpoint authenticates callers with a shared secret in
the query string.  uvicorn's access logger formats the *full* request path, so
without a filter the secret is written to the access log of every deployment.
"""
from __future__ import annotations

import io
import logging

import pytest
from uvicorn.logging import AccessFormatter

from app.log_redaction import (
    TokenRedactionFilter,
    install_log_redaction,
    redact_token_in_path,
)


def test_redact_token_in_path_keeps_rest_intact():
    assert (
        redact_token_in_path("/api/unibox/gmail/push?token=s3cret")
        == "/api/unibox/gmail/push?token=REDACTED"
    )
    assert (
        redact_token_in_path("/api/unibox/gmail/push?token=s3cret&foo=bar")
        == "/api/unibox/gmail/push?token=REDACTED&foo=bar"
    )
    # Nothing else is touched.
    assert redact_token_in_path("/api/inboxes?page=1") == "/api/inboxes?page=1"
    assert redact_token_in_path("") == ""


def _access_record(full_path: str, status: int = 200) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "access.py",
        1,
        '%s - "%s %s HTTP/%s" %s',
        ("127.0.0.1:1234", "POST", full_path, "1.1", status),
        None,
    )


def _render(record: logging.LogRecord) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(use_colors=False))
    handler.handle(record)
    return stream.getvalue().strip()


def test_filter_redacts_token_but_keeps_rest_of_line():
    record = _access_record("/api/unibox/gmail/push?token=TOPSECRET", status=401)
    assert record.args[2] == "/api/unibox/gmail/push?token=TOPSECRET"

    assert TokenRedactionFilter().filter(record) is True

    assert record.args[2] == "/api/unibox/gmail/push?token=REDACTED"
    rendered = _render(record)
    assert "TOPSECRET" not in rendered
    # The rest of the access line is intact.
    assert 'POST /api/unibox/gmail/push?token=REDACTED HTTP/1.1" 401' in rendered
    assert rendered.startswith("127.0.0.1:1234 - ")


def test_filter_leaves_unrelated_records_untouched():
    record = _access_record("/api/inboxes?page=1&per_page=50", status=200)
    original = record.args

    assert TokenRedactionFilter().filter(record) is True

    assert record.args == original


def test_filter_redacts_dict_and_scalar_args():
    f = TokenRedactionFilter()

    dict_record = logging.makeLogRecord({})
    dict_record.name = "quickly.unibox"
    dict_record.levelno = logging.INFO
    dict_record.msg = "%(url)s"
    dict_record.args = {"url": "/x?token=abc"}
    f.filter(dict_record)
    assert dict_record.args["url"] == "/x?token=REDACTED"

    str_record = logging.LogRecord(
        "quickly.unibox", logging.INFO, "x.py", 1, "%s", ("/y?token=abc",), None
    )
    # A single positional arg is stored as a 1-tuple, not a bare string.
    f.filter(str_record)
    assert str_record.args == ("/y?token=REDACTED",)


def test_install_is_idempotent_and_attaches_to_uvicorn_access():
    logger = logging.getLogger("uvicorn.access")
    before = [f for f in logger.filters if isinstance(f, TokenRedactionFilter)]

    first = install_log_redaction()
    second = install_log_redaction()

    assert first is second
    after = [f for f in logger.filters if isinstance(f, TokenRedactionFilter)]
    assert len(after) == len(before) or len(after) == 1

    # A record logged through the real logger comes out redacted.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(use_colors=False))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info(
            '%s - "%s %s HTTP/%s" %s',
            "127.0.0.1:1234",
            "POST",
            "/api/unibox/gmail/push?token=TOPSECRET",
            "1.1",
            401,
        )
    finally:
        logger.removeHandler(handler)

    assert "TOPSECRET" not in stream.getvalue()
    assert "token=REDACTED" in stream.getvalue()
