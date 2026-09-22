from types import SimpleNamespace

from app.sender import _build_email_message, render_body, get_lead_data


def test_render_body_replaces_known_placeholders_and_leaves_unknown():
    lead_data = {"name": "Alice", "company": "Acme"}
    tpl = "Hello {{name}}, welcome to {{company}} — missing={{missing}}"
    out = render_body(tpl, lead_data)
    assert "Hello Alice" in out
    assert "Acme" in out
    # unknown placeholder should remain unchanged
    assert "{{missing}}" in out


def test_get_lead_data_includes_custom_data_and_defaults():
    lead = SimpleNamespace(name="Bob", email="bob@example.com", custom_data={"title": "CEO"})
    data = get_lead_data(lead)
    assert data["name"] == "Bob"
    assert data["email"] == "bob@example.com"
    assert data["title"] == "CEO"


def _unsub_headers(list_unsubscribe_url="https://app.example.com/u/token", **kwargs) -> str:
    msg = _build_email_message(
        to_email="lead@example.com",
        subject="Hi",
        body="Body",
        from_email="sender@example.com",
        list_unsubscribe_url=list_unsubscribe_url,
        **kwargs,
    )
    return msg.as_string()


def test_one_click_unsubscribe_header_present_by_default():
    text = _unsub_headers()
    assert "List-Unsubscribe: <https://app.example.com/u/token>" in text
    assert "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in text


def test_one_click_unsubscribe_header_can_be_disabled():
    text = _unsub_headers(list_unsubscribe_one_click=False)
    assert "List-Unsubscribe: <https://app.example.com/u/token>" in text
    assert "List-Unsubscribe-Post" not in text


def test_unsubscribe_headers_omitted_without_url():
    text = _unsub_headers(list_unsubscribe_url=None, list_unsubscribe_one_click=False)
    assert "List-Unsubscribe" not in text
