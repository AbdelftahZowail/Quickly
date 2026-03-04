import React, { useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';

// A simple component that renders a block of sanitized HTML inside
// a Shadow DOM root. Shadow DOM provides true style isolation so that
// neither our application styles nor the email's styles bleed through.
// The component exposes a small API: pass the raw html string via the
// `html` prop and it will be sanitized and rendered inside the shadow
// root whenever the value changes.
//
// When `stripTracking` is true (used in Unibox), open-tracking pixels
// (/o/<id>) are removed entirely, and click-tracking links (/c/<token>)
// are unwound to the original destination so that viewing an email in
// the app does not inflate analytics.

function sanitizeHtml(html) {
  return DOMPurify.sanitize(html || '', {
    USE_PROFILES: { html: true },
    FORBID_TAGS: [
      'script',
      'iframe',
      'object',
      'embed',
      'form',
      'input',
      'button',
      'textarea',
      'select',
      'link',
      'meta',
      'base',
    ],
  });
}

/**
 * Strip Quickly tracking artefacts from HTML:
 * 1. Remove <img> tags whose src contains /o/ (open tracking pixels).
 * 2. Replace <a> tags whose href contains /c/ (click tracking redirects)
 *    with the original destination URL, if available, or strip the href.
 */
function stripTrackingFromHtml(html) {
  if (!html) return html;
  // Remove open-tracking pixels: <img ... src="https://.../o/123" ...>
  let cleaned = html.replace(/<img\b[^>]*\bsrc\s*=\s*["'][^"']*\/o\/\d+["'][^>]*\/?>/gi, '');
  // For click tracking links (/c/<token>), we can't easily recover the
  // original URL from the HTML alone, so we rewrite them to "#" to avoid
  // triggering the redirect. The text content of the link is preserved.
  cleaned = cleaned.replace(
    /(<a\b[^>]*)\bhref\s*=\s*["'][^"']*\/c\/[A-Za-z0-9_-]+["']/gi,
    '$1href="#"'
  );
  return cleaned;
}

/**
 * After DOMPurify sanitisation, find Gmail-style quote containers and wrap
 * them in a <details>/<summary> so they render collapsed by default.
 * Works entirely with static HTML — no scripts required.
 */
function collapseGmailQuotes(sanitizedHtml) {
  if (!sanitizedHtml || !sanitizedHtml.includes('gmail_quote')) return sanitizedHtml;
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(`<div id="__root">${sanitizedHtml}</div>`, 'text/html');
    const root = doc.getElementById('__root');

    // Target top-level quote containers (not ones already nested inside another)
    const containers = root.querySelectorAll('.gmail_quote_container');
    const processed = new WeakSet();

    containers.forEach(el => {
      if (processed.has(el) || el.closest('details')) return;
      processed.add(el);

      const details = doc.createElement('details');
      details.className = 'gmail_quote_collapse';

      const summary = doc.createElement('summary');
      summary.className = 'gmail_quote_toggle';
      // The three dots button Gmail uses
      summary.setAttribute('title', 'Show trimmed content');
      summary.textContent = '\u22ef'; // ⋯ horizontal ellipsis

      details.appendChild(summary);
      el.parentNode.insertBefore(details, el);
      details.appendChild(el);
    });

    // Fallback: bare blockquote.gmail_quote not inside a container
    root.querySelectorAll('blockquote.gmail_quote').forEach(bq => {
      if (processed.has(bq) || bq.closest('details') || bq.closest('.gmail_quote_container')) return;
      const details = doc.createElement('details');
      details.className = 'gmail_quote_collapse';
      const summary = doc.createElement('summary');
      summary.className = 'gmail_quote_toggle';
      summary.setAttribute('title', 'Show trimmed content');
      summary.textContent = '\u22ef';
      details.appendChild(summary);
      bq.parentNode.insertBefore(details, bq);
      details.appendChild(bq);
    });

    return root.innerHTML;
  } catch {
    return sanitizedHtml;
  }
}


function EmailContent({ html, stripTracking }) {
  const hostRef = useRef(null);
  const shadowRootRef = useRef(null);

  useEffect(() => {
    if (!hostRef.current) return;
    if (!shadowRootRef.current) {
      shadowRootRef.current = hostRef.current.attachShadow({ mode: 'open' });
    }
    let processed = html;
    if (stripTracking) {
      processed = stripTrackingFromHtml(processed);
    }
    const sanitized = sanitizeHtml(processed);
    const withCollapsed = collapseGmailQuotes(sanitized);

    // The shadow root already keeps our app's CSS out of the email,
    // so a full "all: initial" reset is unnecessary and strips font
    // families that were defined on the body or inherited via styles.
    // Keep only a minimal reset for spacing and image behaviour.
    const content = `
      <style>
        :host { display: block; }
        body { margin: 0; padding: 0; }
        img { max-width: 100%; height: auto; }
        pre { white-space: pre-wrap; }

        /* Gmail-style collapsed quote toggle button */
        details.gmail_quote_collapse { display: block; margin: 4px 0; }
        details.gmail_quote_collapse > summary.gmail_quote_toggle {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 28px;
          height: 16px;
          background: #f1f3f4;
          border: 1px solid #dadce0;
          border-radius: 3px;
          cursor: pointer;
          font-size: 14px;
          font-weight: bold;
          color: #444746;
          letter-spacing: 1px;
          list-style: none;
          user-select: none;
          line-height: 1;
        }
        details.gmail_quote_collapse > summary.gmail_quote_toggle::-webkit-details-marker { display: none; }
        details.gmail_quote_collapse > summary.gmail_quote_toggle::marker { display: none; }
        details.gmail_quote_collapse > summary.gmail_quote_toggle:hover {
          background: #e8eaed;
          border-color: #bdc1c6;
        }
        details.gmail_quote_collapse[open] > summary.gmail_quote_toggle {
          background: #e8eaed;
        }
      </style>
      <div>${withCollapsed}</div>
    `;

    shadowRootRef.current.innerHTML = content;
  }, [html, stripTracking]);

  return <div ref={hostRef} className="email-content" />;
}

export default EmailContent;
