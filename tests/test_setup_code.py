"""Unit tests for the one-time, in-memory setup code."""
from __future__ import annotations

import logging
import re

import pytest

from app import setup_code


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv(setup_code.ENV_VAR, raising=False)
    setup_code.reset_for_tests()
    yield
    setup_code.reset_for_tests()


def test_generated_code_is_eight_unambiguous_chars():
    code = setup_code.get_code()
    assert code is not None
    assert len(code) == 8
    assert set(code) <= set(setup_code.ALPHABET)
    # The alphabet deliberately excludes look-alike characters.
    assert not (set(code) & set("01IO"))


def test_generated_code_is_stable_within_process():
    assert setup_code.get_code() == setup_code.get_code()


def test_verify_normalizes_case_and_separators():
    code = setup_code.get_code()
    assert setup_code.verify_setup_code(code.lower()) is True
    formatted = f"{code[:4]}-{code[4:]}"
    assert setup_code.verify_setup_code(formatted) is True
    assert setup_code.verify_setup_code(f" {code.lower()} ") is True
    assert setup_code.verify_setup_code("  " + formatted + "\n") is True


def test_verify_rejects_wrong_or_missing_code():
    assert setup_code.verify_setup_code("") is False
    assert setup_code.verify_setup_code(None) is False
    assert setup_code.verify_setup_code("ZZZZ-ZZZZ") is False


def test_env_override_wins_and_is_normalized(monkeypatch):
    monkeypatch.setenv(setup_code.ENV_VAR, "test-code-1234")
    assert setup_code.get_code() == "TESTCODE1234"
    assert setup_code.verify_setup_code("test code 1234") is True
    assert setup_code.verify_setup_code("TEST-CODE-1234") is True
    assert setup_code.verify_setup_code("WRONGCODE") is False


def test_clear_invalidates_code_even_with_env_override(monkeypatch):
    monkeypatch.setenv(setup_code.ENV_VAR, "TEST-CODE-1234")
    assert setup_code.verify_setup_code("TEST-CODE-1234") is True
    setup_code.clear_setup_code()
    assert setup_code.get_code() is None
    assert setup_code.verify_setup_code("TEST-CODE-1234") is False


def test_reset_for_tests_mints_a_fresh_usable_code():
    original = setup_code.get_code()
    setup_code.clear_setup_code()
    assert setup_code.get_code() is None
    setup_code.reset_for_tests()
    fresh = setup_code.get_code()
    assert fresh is not None
    assert setup_code.verify_setup_code(fresh) is True
    # A brand new process state mints a new random code (not the old one).
    assert fresh != original


def test_log_setup_code_prints_formatted_code_once(caplog):
    caplog.set_level(logging.INFO, logger="quickly.setup")
    setup_code.log_setup_code()
    setup_code.log_setup_code()  # second call must stay silent

    code = setup_code.get_code()
    formatted = f"{code[:4]}-{code[4:]}"
    messages = [r.getMessage() for r in caplog.records if r.name == "quickly.setup"]
    assert len(messages) == 1
    assert formatted in messages[0]


def test_log_setup_code_never_prints_env_value(monkeypatch, caplog):
    monkeypatch.setenv(setup_code.ENV_VAR, "TEST-CODE-1234")
    caplog.set_level(logging.INFO, logger="quickly.setup")
    setup_code.log_setup_code()

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "TEST-CODE-1234" not in text
    assert "TESTCODE1234" not in text
    assert setup_code.ENV_VAR in text
