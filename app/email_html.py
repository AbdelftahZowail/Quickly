"""Normalise composed HTML so it renders the same everywhere.

The rich-text editor (Quill) emits bare block tags (``<p>``, ``<ul>``,
``<h1>``, ...) with no inline styling and relies on its own stylesheet for
layout.  Email clients never load that stylesheet: they apply their own
defaults — most visibly a ~1em margin around every ``<p>`` — so a message
composed one line per paragraph arrives looking double-spaced.  The in-app
previews had the opposite problem: Tailwind's preflight zeroes those same
margins, so the very same email looked tight in the app and airy in Gmail.

``normalize_email_html`` removes the ambiguity by inlining the exact block
spacing the composer shows, inside an explicit typography container.  The
output no longer depends on the surrounding CSS, so a preview and the sent
email render identically regardless of who is doing the rendering.

The transform is deliberately conservative:

* Plain text (no HTML tags) is returned untouched.
* Full HTML documents are returned untouched (they are unwrapped downstream).
* Already-normalised HTML is returned untouched (idempotent — marked by the
  wrapper's ``data-email-normalized="1"`` attribute).
* Only the tags the editor can actually produce are styled.
"""

from __future__ import annotations

import re

__all__ = ["normalize_email_html", "NORMALIZED_MARKER"]


NORMALIZED_MARKER = 'data-email-normalized="1"'

# Base typography applied to the wrapper.  Email clients disagree about the
# default font/size/line-height, so we pin it to a common, safe stack.
_TYPOGRAPHY = (
    "font-family:Arial,Helvetica,sans-serif;"
    "font-size:14px;"
    "line-height:1.5;"
    "color:#222222;"
)

# Per-tag declarations.  Block tags mirror the spacing the Quill editor shows
# (``margin:0;padding:0`` on paragraphs etc.), so one Enter = one line and a
# blank line stays a single blank line instead of a full paragraph gap.
_BLOCK_STYLES: dict[str, str] = {
    "p": "margin:0;padding:0;",
    "h1": "font-size:2em;font-weight:bold;line-height:1.25;margin:0;padding:0;",
    "h2": "font-size:1.5em;font-weight:bold;line-height:1.25;margin:0;padding:0;",
    "h3": "font-size:1.17em;font-weight:bold;line-height:1.25;margin:0;padding:0;",
    "h4": "font-weight:bold;margin:0;padding:0;",
    "h5": "font-weight:bold;margin:0;padding:0;",
    "h6": "font-weight:bold;margin:0;padding:0;",
    "ul": "margin:0;padding-left:24px;list-style-type:disc;",
    "ol": "margin:0;padding-left:24px;list-style-type:decimal;",
    "li": "margin:0;padding:0;",
    "blockquote": "border-left:4px solid #cccccc;margin:5px 0;padding-left:16px;",
    "pre": (
        "background-color:#f0f0f0;border-radius:3px;white-space:pre-wrap;"
        "margin:5px 0;padding:5px 10px;font-family:monospace;"
    ),
    "code": "font-family:monospace;font-size:85%;",
    "a": "color:#1155cc;text-decoration:underline;",
    "strong": "font-weight:bold;",
    "b": "font-weight:bold;",
    "em": "font-style:italic;",
    "i": "font-style:italic;",
    "u": "text-decoration:underline;",
    "s": "text-decoration:line-through;",
    "strike": "text-decoration:line-through;",
    "del": "text-decoration:line-through;",
}

# Matches an opening tag of interest.  Longest alternatives come first so the
# engine never settles on a prefix (e.g. ``p`` inside ``pre``).  Attribute
# values may be quoted and contain ``>`` — the quoted branches consume their
# whole value before the pattern looks for the closing ``>``.
_TAG_RE = re.compile(
    r"<(?P<tag>blockquote|strong|strike|code|del|pre|h[1-6]|ul|ol|li|p|a|b|em|i|u|s)"
    r"(?P<attrs>(?:\s+(?:[^\s\"'<>/=]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>`=]+))?))*)"
    r"\s*(?P<self>/?)>",
    re.IGNORECASE,
)

# A real ``style`` attribute only — ``(?<![\w-])`` stops ``\bstyle`` from
# matching within names like ``data-style``.
_STYLE_ATTR_RE = re.compile(
    r"((?<![\w-])style\s*=\s*)([\"'])(.*?)\2",
    re.IGNORECASE | re.DOTALL,
)

# Plain-text probe only (is there any tag at all?).  ``[^<>]`` (not ``[^>]``)
# keeps this linear: a body full of ``<`` with no ``>`` cannot force a rescan to
# end-of-string at every position.  Attribute values with ``>``/``<`` inside
# quotes still match a neighbouring real tag, which is all this probe needs.
_HTML_TAG_RE = re.compile(r"<[a-zA-Z][^<>]*>")
_FULL_DOCUMENT_RE = re.compile(r"<html\b", re.IGNORECASE)


def _merge_style(attrs: str, declarations: str) -> str:
    """Add *declarations* to a tag's attribute string, merging any existing
    ``style`` attribute instead of producing a second one."""
    match = _STYLE_ATTR_RE.search(attrs)
    if not match:
        return f'{attrs} style="{declarations}"'

    existing = match.group(3).strip().rstrip(";")
    merged = f"{existing};{declarations}" if existing else declarations
    replacement = f'{match.group(1)}{match.group(2)}{merged}{match.group(2)}'
    return attrs[: match.start()] + replacement + attrs[match.end():]


def _style_tag(match: re.Match) -> str:
    tag = match.group("tag")
    declarations = _BLOCK_STYLES.get(tag.lower())
    if not declarations:
        return match.group(0)
    attrs = _merge_style(match.group("attrs") or "", declarations)
    return f"<{tag}{attrs}{match.group('self')}>"


def normalize_email_html(html: str | None) -> str:
    """Return *html* with inline block styles so every renderer agrees.

    Plain text and already-normalised HTML pass through unchanged.
    """
    if not html:
        return html or ""

    # Plain text (no tags at all) has nothing to normalise.
    if not _HTML_TAG_RE.search(html):
        return html

    # Idempotent: never wrap/normalise the same body twice.
    if NORMALIZED_MARKER in html:
        return html

    # A full HTML document is unwrapped later (``_strip_html_document_wrapper``);
    # wrapping it now would place the container outside ``<html>`` and corrupt
    # the fragment.  The editor never produces full documents, so leave them be.
    if _FULL_DOCUMENT_RE.search(html):
        return html

    body = _TAG_RE.sub(_style_tag, html)
    return f'<div {NORMALIZED_MARKER} style="{_TYPOGRAPHY}">{body}</div>'
