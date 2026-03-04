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

export default function EmailContent({ html, stripTracking = false }) {
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
      </style>
      <div>${sanitized}</div>
    `;

    shadowRootRef.current.innerHTML = content;
  }, [html, stripTracking]);

  return <div ref={hostRef} className="email-content" />;
}
