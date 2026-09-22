from types import SimpleNamespace
import random

from app.sender import _build_email_message, render_body, get_lead_data, RenderContext


def test_render_body_replaces_known_placeholders_and_leaves_unknown():
    lead_data = {"name": "Alice", "company": "Acme"}
    tpl = "Hello {{name}}, welcome to {{company}} — missing={{missing}}"
    out = render_body(tpl, lead_data)
    assert "Hello Alice" in out
    assert "Acme" in out
    # unknown placeholder should remain unchanged
    assert "{{missing}}" in out


def _seeded_ctx(seed: int = 0) -> RenderContext:
    return RenderContext(rng=random.Random(seed))


def test_random_picks_one_of_the_options():
    ctx = _seeded_ctx()
    out = render_body("{{random: A | B | C}}", {}, ctx)
    assert out in {"A", "B", "C"}


def test_random_options_are_trimmed_and_multi_word():
    ctx = _seeded_ctx()
    out = render_body("{{random:   good morning  |  good evening }}", {}, ctx)
    assert out in {"good morning", "good evening"}


def test_random_case_insensitive_keyword_and_spaces():
    ctx = _seeded_ctx()
    out = render_body("{{ RANDOM : yes | no }}", {}, ctx)
    assert out in {"yes", "no"}


def test_random_is_stable_within_one_context():
    ctx = _seeded_ctx()
    subject = render_body("{{random: One | Two | Three}}", {}, ctx)
    body = render_body("<p>{{random: One | Two | Three}}</p>", {}, ctx)
    assert body == f"<p>{subject}</p>"


def test_random_resolves_nested_variables():
    ctx = _seeded_ctx()
    out = render_body("{{random: Hi {{name}} | Hi there}}", {"name": "Ada"}, ctx)
    assert out in {"Hi Ada", "Hi there"}


def test_random_resolves_nested_random():
    ctx = _seeded_ctx()
    out = render_body("{{random: {{random: Hey | Hi}} | Yo}}", {}, ctx)
    assert out in {"Hey", "Hi", "Yo"}


def test_random_nested_variables_inside_two_options():
    ctx = _seeded_ctx()
    out = render_body(
        "{{random: {{name}} at {{company}} | {{company}} team}}",
        {"name": "Ada", "company": "Acme"},
        ctx,
    )
    assert out in {"Ada at Acme", "Acme team"}


def test_random_escaped_pipe_is_literal():
    ctx = _seeded_ctx()
    # Both options should be valid; never split on the escaped pipe.
    for seed in range(20):
        out = render_body("{{random: a \\| b | c}}", {}, RenderContext(rng=random.Random(seed)))
        assert out in {"a | b", "c"}


def test_random_with_no_valid_options_left_literal():
    ctx = _seeded_ctx()
    assert render_body("{{random:  |  }}", {}, ctx) == "{{random:  |  }}"


def test_random_survives_unbalanced_braces():
    ctx = _seeded_ctx()
    assert render_body("{{random: a | b", {}, ctx) == "{{random: a | b"


def test_render_body_leaves_unknown_fields_inside_random():
    # Unknown variables inside an option don't stop the random from resolving.
    ctx = _seeded_ctx()
    out = render_body("{{random: {{missing}} | ok}}", {}, ctx)
    assert out in {"{{missing}}", "ok"}


def test_unbalanced_opener_does_not_shadow_later_fields():
    # A stray "{{" must not stop later valid placeholders from rendering.
    assert render_body("Hey {{name} , {{company}} team",
                       {"name": "Al", "company": "Acme"}) == "Hey {{name} , Acme team"
    assert render_body("a {{ b {{name}}", {"name": "Al"}) == "a {{ b Al"


def test_known_field_inside_unknown_wrapper_still_renders():
    # Balanced wrapper that isn't itself a field/random must not hide nested vars.
    assert render_body("{{ outer {{name}} }}", {"name": "Al"}) == "{{ outer Al }}"


def test_triple_braces_left_untouched():
    # Ambiguous triple-brace input isn't a recognized placeholder; leave it as-is.
    assert render_body("{{{name}}}", {"name": "Al"}) == "{{{name}}}"


def test_random_no_colon_is_treated_as_plain_field():
    assert render_body("{{random}}", {"random": "VALUE"}) == "VALUE"


def test_field_value_is_not_recursed():
    # A field whose value itself looks like a placeholder must not loop.
    data = {"name": "{{name}}", "company": "Acme"}
    assert render_body("{{name}}", data) == "{{name}}"
    out = render_body("{{random: Hi {{name}} | Yo}}", data)
    assert out in {"Hi {{name}}", "Yo"}


class _CountingRng:
    """Deterministic RNG whose choices advance so we can observe cache keys."""

    def __init__(self):
        self.i = 0

    def choice(self, options):
        self.i += 1
        return options[self.i % len(options)]


def test_random_cache_is_scoped_per_option_list():
    ctx = RenderContext(rng=_CountingRng())
    first = ctx.choose(["a", "b"])
    # Same options -> cached, no new draw.
    assert ctx.choose(["a", "b"]) == first
    # Different options -> independent roll.
    assert ctx.choose(["x", "y", "z"]) != first


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
