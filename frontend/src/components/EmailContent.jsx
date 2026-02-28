import React, { useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';

// A simple component that renders a block of sanitized HTML inside
// a Shadow DOM root. Shadow DOM provides true style isolation so that
// neither our application styles nor the email's styles bleed through.
// The component exposes a small API: pass the raw html string via the
// `html` prop and it will be sanitized and rendered inside the shadow
// root whenever the value changes.

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

export default function EmailContent({ html }) {
  const hostRef = useRef(null);
  const shadowRootRef = useRef(null);

  useEffect(() => {
    if (!hostRef.current) return;
    if (!shadowRootRef.current) {
      shadowRootRef.current = hostRef.current.attachShadow({ mode: 'open' });
    }
    const sanitized = sanitizeHtml(html);

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
  }, [html]);

  return <div ref={hostRef} className="email-content" />;
}
