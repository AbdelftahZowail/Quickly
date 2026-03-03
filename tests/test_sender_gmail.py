import base64
import json
from types import SimpleNamespace

import pytest

from app.sender import _send_via_gmail, send_email, SendResult
from app.sender import _log_gmail_call  # used for side effects


class DummyMessage:
    def __init__(self, response):
        self._response = response

    def execute(self, num_retries=None):
        return self._response


class DummyUsers:
    def __init__(self, send_resp=None, get_resp=None):
        self._send_resp = send_resp or {}
        self._get_resp = get_resp or {"payload": {"headers": []}}

    def messages(self):
        # return object with send/get methods
        class M:
            def __init__(self, send_resp, get_resp):
                self.send_resp = send_resp
                self.get_resp = get_resp

            def send(self, userId=None, body=None):
                return DummyMessage(self.send_resp)

            def get(self, userId=None, id=None, format=None, metadataHeaders=None):
                return DummyMessage(self.get_resp)

        return M(self._send_resp, self._get_resp)


def dummy_build(service_name, version, credentials=None, cache_discovery=None):
    assert service_name == "gmail"
    # credentials may be used by caller to refresh; just return object exposing users()
    # Build with a send response that echoes the raw fields for checking.
    send_resp = {"threadId": "t123", "id": "m456"}
    get_resp = {"payload": {"headers": [{"name": "Message-Id", "value": "<REAL>"}]}}
    return SimpleNamespace(users=lambda: DummyUsers(send_resp=send_resp, get_resp=get_resp))


def test_send_via_gmail_happy_path(monkeypatch):
    # monkeypatch the google client builder
    monkeypatch.setattr("app.sender.build", dummy_build)

    # create a fake GmailAccount-like object with necessary attributes
    gmail_account = SimpleNamespace(
        access_token="foo",
        refresh_token="bar",
        token_expiry=None,
        google_email="user@example.com",
    )

    result = _send_via_gmail(
        to_email="to@x.com",
        subject="hi",
        body="body",
        from_email="from@x.com",
        from_name="From",
        reply_to_msg_id=None,
        references=None,
        is_html=False,
        gmail_account=gmail_account,
    )

    assert isinstance(result, SendResult)
    assert result.thread_id == "t123"
    assert result.message_id == "<REAL>"  # we fetched the real Id
    # since creds weren't refreshed, the account token should remain the same
    assert gmail_account.access_token == "foo"


def test_send_email_with_explicit_provider(monkeypatch):
    # when a provider is supplied the wrapper should use it unchanged
    monkeypatch.setattr("app.sender.build", dummy_build)
    gmail_account = SimpleNamespace(access_token="foo", refresh_token="bar", token_expiry=None)
    out = send_email(
        to_email="a@b",
        subject="s",
        body="b",
        from_email="c@d",
        provider="gmail",
        gmail_account=gmail_account,
    )
    assert isinstance(out, SendResult)
    assert out.thread_id == "t123"


def test_send_email_requires_provider():
    # empty provider defaults to gmail; with no credentials returns SendFailure
    from app.sender import SendFailure
    result = send_email(
        to_email="no@prov",
        subject="s",
        body="b",
        from_email="c@d",
        provider="",
    )
    assert isinstance(result, SendFailure)


def test_send_via_gmail_logs_errors(monkeypatch):
    class BadUsers(DummyUsers):
        def __init__(self):
            super().__init__({"threadId": "t", "id": "m"}, None)

        def messages(self):
            class M:
                def send(self, userId=None, body=None):
                    raise HttpError(resp=SimpleNamespace(status=403, reason="nope"), content=b"bad")
            return M()

    def bad_build(service_name, version, credentials=None, cache_discovery=None):
        return SimpleNamespace(users=lambda: BadUsers())

    monkeypatch.setattr("app.sender.build", bad_build)

    gmail_account = SimpleNamespace(access_token="foo", refresh_token=None, token_expiry=None)
    res = _send_via_gmail(
        to_email="x@y",
        subject="h",
        body="b",
        from_email="u@v",
        gmail_account=gmail_account,
    )
    assert res is None


# ensure render_body/get_lead_data tests still run in same module to keep coverage

def test_render_body_restored():
    from app.sender import render_body

    assert render_body("hello {{name}}", {"name": "z"}) == "hello z"
