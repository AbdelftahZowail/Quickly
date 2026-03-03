"""Tests for app.tracking — inject_tracking_html and helpers.

The module is intentionally free of DB/async dependencies so all tests are
plain synchronous unit tests with no fixtures required.
"""
from __future__ import annotations

import re

import pytest

from app.tracking import (
    PIXEL_GIF,
    inject_tracking_html,
    make_tracking_token,
    _inject_bare_url_tracking,
)

BASE = "https://track.example.com"


# ---------------------------------------------------------------------------
# PIXEL_GIF constant
# ---------------------------------------------------------------------------


class TestPixelGif:
    def test_is_bytes(self):
        assert isinstance(PIXEL_GIF, bytes)

    def test_starts_with_gif_header(self):
        # All GIF files start with "GIF8"
        assert PIXEL_GIF[:4] == b"GIF8"

    def test_non_empty(self):
        assert len(PIXEL_GIF) > 0


# ---------------------------------------------------------------------------
# make_tracking_token
# ---------------------------------------------------------------------------


class TestMakeTrackingToken:
    def test_returns_string(self):
        assert isinstance(make_tracking_token(), str)

    def test_non_empty(self):
        assert len(make_tracking_token()) > 0

    def test_url_safe_characters_only(self):
        token = make_tracking_token()
        assert re.match(r'^[A-Za-z0-9_\-]+$', token), f"Unsafe chars in token: {token}"

    def test_unique_each_call(self):
        tokens = {make_tracking_token() for _ in range(50)}
        assert len(tokens) == 50, "Collision detected in 50 tokens"


# ---------------------------------------------------------------------------
# inject_tracking_html — click tracking
# ---------------------------------------------------------------------------


class TestClickTracking:
    def test_rewrites_single_href(self):
        html = '<a href="https://example.com/page">Click</a>'
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert len(pairs) == 1
        token, original = pairs[0]
        assert original == "https://example.com/page"
        assert f"{BASE}/c/{token}" in new_html
        # Original URL gone from href attribute
        assert 'href="https://example.com/page"' not in new_html

    def test_rewrites_single_quoted_href(self):
        html = "<a href='https://example.com/page'>Click</a>"
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert len(pairs) == 1
        token, original = pairs[0]
        assert original == "https://example.com/page"
        assert f"{BASE}/c/{token}" in new_html

    def test_multiple_links_each_unique_token(self):
        html = (
            '<a href="https://one.com">One</a> '
            '<a href="https://two.com">Two</a>'
        )
        _, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert len(pairs) == 2
        tokens = [p[0] for p in pairs]
        assert tokens[0] != tokens[1], "Two links should have distinct tokens"
        assert {p[1] for p in pairs} == {"https://one.com", "https://two.com"}

    def test_does_not_rewrite_mailto(self):
        html = '<a href="mailto:user@example.com">Email</a>'
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert pairs == []
        assert 'href="mailto:user@example.com"' in new_html

    def test_does_not_rewrite_anchor_hash(self):
        html = '<a href="#section">Jump</a>'
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert pairs == []
        assert 'href="#section"' in new_html

    def test_does_not_rewrite_tracking_base_url(self):
        """Links already served by this server (e.g. unsubscribe) must not be wrapped."""
        unsub_url = f"{BASE}/u/sometoken"
        html = f'<a href="{unsub_url}">Unsubscribe</a>'
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert pairs == []
        assert f'href="{unsub_url}"' in new_html

    def test_tracking_base_trailing_slash_normalised(self):
        """Passing a trailing slash on tracking_base must not break redirect URLs."""
        html = '<a href="https://example.com">Click</a>'
        new_html, pairs = inject_tracking_html(html, 1, BASE + "/", track_opens=False)

        assert len(pairs) == 1
        token, _ = pairs[0]
        # Should produce /c/<token>, not //c/<token>
        assert f"{BASE}/c/{token}" in new_html

    def test_track_clicks_false_leaves_hrefs_unchanged(self):
        html = '<a href="https://example.com">Click</a>'
        new_html, pairs = inject_tracking_html(
            html, 1, BASE, track_opens=False, track_clicks=False
        )

        assert pairs == []
        assert new_html == html

    def test_link_pairs_token_matches_html(self):
        """Every token in link_pairs must actually appear in the rewritten HTML."""
        html = (
            '<a href="https://a.com">A</a>'
            '<a href="https://b.com">B</a>'
        )
        new_html, pairs = inject_tracking_html(html, 5, BASE, track_opens=False)

        for token, _ in pairs:
            assert token in new_html, f"Token {token} not found in HTML"

    def test_extra_attributes_on_anchor_preserved(self):
        """Other attributes on the <a> tag must be kept intact."""
        html = '<a class="btn" href="https://example.com" target="_blank">Click</a>'
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert len(pairs) == 1
        assert 'class="btn"' in new_html
        assert 'target="_blank"' in new_html


# ---------------------------------------------------------------------------
# inject_tracking_html — bare URL tracking
# ---------------------------------------------------------------------------


class TestBareUrlTracking:
    def test_bare_url_in_text_node_wrapped(self):
        """A plain https:// URL in body text should be wrapped in a click-tracking link."""
        html = "<p>Visit https://example.com for details</p>"
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert len(pairs) == 1
        token, original = pairs[0]
        assert original == "https://example.com"
        assert f"{BASE}/c/{token}" in new_html

    def test_bare_url_inside_anchor_not_double_wrapped(self):
        """A URL that is the text content of an <a> tag must not be re-wrapped."""
        html = '<a href="https://example.com">https://example.com</a>'
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        # Only one pair from the href rewrite; the visible URL text is untouched
        assert len(pairs) == 1
        # The link text should still be the original URL (not another redirect)
        assert ">https://example.com<" in new_html

    def test_bare_url_trailing_punctuation_stripped(self):
        """Trailing punctuation should not be included in the tracked URL."""
        html = "<p>See https://example.com/page.</p>"
        _, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert len(pairs) == 1
        _, original = pairs[0]
        assert original == "https://example.com/page"
        assert not original.endswith(".")

    def test_bare_tracking_base_url_not_rewrapped(self):
        """Bare URLs that already belong to the tracking server should be left alone."""
        unsub_url = f"{BASE}/u/abc123"
        html = f"<p>Click {unsub_url} to unsubscribe</p>"
        _, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        # The unsubscribe URL should not appear in pairs
        assert all(unsub_url != orig for _, orig in pairs)

    def test_bare_url_in_plain_text_node(self):
        """Bare URL detection works even when not wrapped in any block element."""
        html = "Hello https://example.com world"
        new_html, pairs = inject_tracking_html(html, 1, BASE, track_opens=False)

        assert len(pairs) == 1
        token, original = pairs[0]
        assert original == "https://example.com"
        assert f"{BASE}/c/{token}" in new_html


# ---------------------------------------------------------------------------
# inject_tracking_html — open tracking pixel
# ---------------------------------------------------------------------------


class TestOpenTrackingPixel:
    def test_pixel_injected_before_body_close(self):
        html = "<html><body><p>Hello</p></body></html>"
        new_html, _ = inject_tracking_html(html, 42, BASE, track_clicks=False)

        pixel_url = f"{BASE}/o/42"
        assert pixel_url in new_html
        # Pixel must come before </body>
        pixel_pos = new_html.lower().index(pixel_url.lower())
        body_close_pos = new_html.lower().index("</body>")
        assert pixel_pos < body_close_pos

    def test_pixel_appended_when_no_body_tag(self):
        html = "<p>No body tag here</p>"
        new_html, _ = inject_tracking_html(html, 7, BASE, track_clicks=False)

        pixel_url = f"{BASE}/o/7"
        assert pixel_url in new_html
        # Pixel appended at the end
        assert new_html.endswith(f'<img src="{pixel_url}" width="1" height="1" border="0" style="display:none" alt="" />')

    def test_pixel_url_uses_correct_log_id(self):
        html = "<p>Test</p>"
        new_html, _ = inject_tracking_html(html, 999, BASE, track_clicks=False)

        assert f"{BASE}/o/999" in new_html

    def test_pixel_url_uses_tracking_base(self):
        custom_base = "https://mail.client.com"
        html = "<p>Test</p>"
        new_html, _ = inject_tracking_html(html, 1, custom_base, track_clicks=False)

        assert f"{custom_base}/o/1" in new_html

    def test_pixel_tag_has_display_none(self):
        """The pixel must be invisible — style="display:none"."""
        html = "<p>Test</p>"
        new_html, _ = inject_tracking_html(html, 1, BASE, track_clicks=False)

        assert 'style="display:none"' in new_html

    def test_track_opens_false_no_pixel(self):
        html = "<p>Test</p></body>"
        new_html, _ = inject_tracking_html(
            html, 1, BASE, track_opens=False, track_clicks=False
        )

        assert "/o/" not in new_html
        assert new_html == html

    def test_pixel_injected_before_uppercase_body_close(self):
        """Case-insensitive search for </BODY> must still work."""
        html = "<html><body><p>Hello</p></BODY></html>"
        new_html, _ = inject_tracking_html(html, 3, BASE, track_clicks=False)

        pixel_url = f"{BASE}/o/3"
        assert pixel_url in new_html
        pixel_pos = new_html.lower().index("o/3")
        body_close_pos = new_html.lower().index("</body>")
        assert pixel_pos < body_close_pos


# ---------------------------------------------------------------------------
# inject_tracking_html — combined (both clicks + opens)
# ---------------------------------------------------------------------------


class TestCombined:
    def test_both_enabled(self):
        html = (
            "<html><body>"
            '<p>Visit <a href="https://example.com">here</a></p>'
            "</body></html>"
        )
        new_html, pairs = inject_tracking_html(html, 10, BASE)

        # One link rewritten
        assert len(pairs) == 1
        token, original = pairs[0]
        assert original == "https://example.com"
        assert f"{BASE}/c/{token}" in new_html

        # Pixel present before </body>
        assert f"{BASE}/o/10" in new_html
        pixel_pos = new_html.lower().index("/o/10")
        body_close_pos = new_html.lower().index("</body>")
        assert pixel_pos < body_close_pos

    def test_multiple_links_all_rewritten_pixel_present(self):
        html = (
            "<html><body>"
            '<a href="https://a.com">A</a>'
            '<a href="https://b.com">B</a>'
            '<a href="https://c.com">C</a>'
            "</body></html>"
        )
        new_html, pairs = inject_tracking_html(html, 20, BASE)

        assert len(pairs) == 3
        distinct_tokens = {p[0] for p in pairs}
        assert len(distinct_tokens) == 3

        assert f"{BASE}/o/20" in new_html

    def test_returns_original_urls_not_tokens_in_pairs(self):
        """link_pairs must store the *original* URL — not the tracking redirect."""
        html = '<a href="https://real.com/path">Link</a>'
        _, pairs = inject_tracking_html(html, 1, BASE)

        assert len(pairs) == 1
        _, original = pairs[0]
        assert original == "https://real.com/path"
        assert BASE not in original  # Must not be the redirect URL

    def test_custom_tracking_domain(self):
        """Per-inbox custom tracking domain must be used throughout."""
        custom = "https://mail.myclient.com"
        html = (
            "<html><body>"
            '<a href="https://example.com">Link</a>'
            "</body></html>"
        )
        new_html, pairs = inject_tracking_html(html, 55, custom)

        token, _ = pairs[0]
        assert f"{custom}/c/{token}" in new_html
        assert f"{custom}/o/55" in new_html
        # Default base must NOT appear
        assert BASE not in new_html
