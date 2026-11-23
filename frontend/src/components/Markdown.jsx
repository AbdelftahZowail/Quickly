import { useEffect, useMemo, useRef } from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.setOptions({ gfm: true, breaks: true });

/** Render Markdown (release notes, announcements) as sanitized HTML. */
export default function Markdown({ text, className = '' }) {
  const ref = useRef(null);
  const html = useMemo(() => {
    if (!text) return '';
    return DOMPurify.sanitize(marked.parse(String(text)), {
      USE_PROFILES: { html: true },
    });
  }, [text]);

  useEffect(() => {
    ref.current?.querySelectorAll('a').forEach(link => {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    });
  }, [html]);

  if (!html) return null;
  return (
    <div
      ref={ref}
      className={`markdown-body ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
