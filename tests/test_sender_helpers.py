from types import SimpleNamespace

from app.sender import render_body, get_lead_data


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