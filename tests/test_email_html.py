"""Tests for app.email_html.normalize_email_html — the single transform that
keeps the delivered email, the test email and every in-app preview identical.
"""
import time

from app.email_html import NORMALIZED_MARKER, normalize_email_html
from app.sender import _build_email_message


QUILL_BODY = (
    "<p>Hi Stewart,</p>"
    "<p>Quick question about your store.</p>"
    "<p><br></p>"
    "<p>Best,<br>Antony</p>"
)


def test_plain_text_is_returned_unchanged():
    assert normalize_email_html("Just a plain sentence.\nSecond line.") == (
        "Just a plain sentence.\nSecond line."
    )


def test_text_with_non_tag_angle_brackets_is_untouched():
    # "a < b" must not be mistaken for HTML.
    assert normalize_email_html("2 < 3 and 5 > 4") == "2 < 3 and 5 > 4"


def test_plain_text_probe_stays_linear_on_many_unclosed_angle_brackets():
    # Regression: a body containing many "<" with no ">" must not make the
    # plain-text probe rescan to end-of-string at every position (quadratic).
    # Such a body has no real tag, so it is returned unchanged.
    body = "<a " * 40000
    started = time.perf_counter()
    out = normalize_email_html(body)
    assert out == body
    assert time.perf_counter() - started < 2.0


def test_empty_and_none_inputs():
    assert normalize_email_html("") == ""
    assert normalize_email_html(None) == ""


def test_paragraphs_get_zero_margin_inside_typography_wrapper():
    out = normalize_email_html("<p>one</p><p>two</p>")
    assert NORMALIZED_MARKER in out
    assert out.count("<p style=") == 2
    assert "margin:0" in out
    assert "font-family:Arial" in out


def test_blank_paragraph_is_preserved():
    out = normalize_email_html(QUILL_BODY)
    # The empty <p><br></p> spacer survives (now carrying the inline style).
    assert "><br></p>" in out
    assert out.count("<p ") == 4


def test_full_html_document_is_left_untouched():
    doc = "<html><body><p>hi</p></body></html>"
    assert normalize_email_html(doc) == doc


def test_attribute_value_containing_gt_is_not_corrupted():
    out = normalize_email_html('<a href="x" title="a>b">text</a>')
    assert 'title="a>b"' in out
    assert 'a style=' not in out  # style not injected mid-value
    assert 'style="color:#1155cc;text-decoration:underline;"' in out


def test_data_style_attribute_does_not_absorb_the_fix():
    out = normalize_email_html('<p data-style="x">hi</p>')
    assert '<p data-style="x" style="margin:0;padding:0;">' in out


def test_pre_is_not_mistaken_for_p():
    out = normalize_email_html("<pre>code</pre>")
    assert "<pre style=" in out
    assert "<p style=" not in out


def test_normalisation_is_idempotent():
    once = normalize_email_html(QUILL_BODY)
    twice = normalize_email_html(once)
    assert once == twice
    # Exactly one wrapper.
    assert twice.count(NORMALIZED_MARKER) == 1


def test_existing_style_attribute_is_merged_not_duplicated():
    out = normalize_email_html('<p style="color:red">hi</p>')
    # Merged into the existing attribute (not a second style= on the tag).
    assert '<p style="color:red;margin:0;padding:0;">' in out
    assert out.count("color:red") == 1


def test_block_tags_are_styled():
    out = normalize_email_html(
        "<h1>Title</h1><ul><li>a</li></ul><blockquote>q</blockquote>"
        "<pre>code</pre><a href=\"https://x.test\">link</a>"
    )
    assert "<h1 style=" in out
    assert "font-size:2em" in out
    assert "<ul style=" in out and "list-style-type:disc" in out
    assert "<li style=" in out
    assert "<blockquote style=" in out
    assert "<pre style=" in out
    assert "color:#1155cc" in out and "text-decoration:underline" in out
    # href must be preserved verbatim.
    assert 'href="https://x.test"' in out


def test_normalised_html_survives_mime_build_as_html_alternative():
    msg = _build_email_message(
        to_email="a@b.com",
        subject="s",
        body=normalize_email_html(QUILL_BODY),
        from_email="from@b.com",
        from_name="F",
        is_html=True,
    )
    parts = {p.get_content_type(): p.get_content() for p in msg.iter_parts()}
    html = parts["text/html"]
    assert 'style="margin:0' in html
    assert "font-family:Arial" in html
    # Plain-text alternative keeps a blank line between paragraphs.
    assert "Quick question about your store." in parts["text/plain"]
