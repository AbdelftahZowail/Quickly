/**
 * emailQuote.js
 * Gmail-style email quoting system
 *
 * Handles:
 *  - Plain-text and HTML reply quoting
 *  - Proper MIME header generation (In-Reply-To, References, Re: subject)
 *  - quoted-printable encoding for non-ASCII content
 *  - multipart/alternative assembly
 */

// ─── Quoted-Printable ────────────────────────────────────────────────────────

/**
 * Encode a UTF-8 string as quoted-printable (RFC 2045).
 * Lines are soft-wrapped at 76 chars with `=\r\n`.
 */
function encodeQuotedPrintable(str) {
  // Convert to UTF-8 bytes, encode chars outside safe ASCII range
  const encoded = str.replace(/[^\t\n\r !-<>-~]/g, (ch) => {
    return [...new TextEncoder().encode(ch)]
      .map((b) => `=${b.toString(16).toUpperCase().padStart(2, "0")}`)
      .join("");
  });

  // Soft-wrap at 76 chars
  const lines = encoded.split("\n");
  return lines
    .map((line) => {
      let result = "";
      while (line.length > 76) {
        // Find a good break point
        let cut = 76;
        // Don't break in the middle of a =XX sequence
        if (line[cut - 1] === "=") cut--;
        else if (line[cut - 2] === "=") cut -= 2;
        result += line.slice(0, cut) + "=\r\n";
        line = line.slice(cut);
      }
      return result + line;
    })
    .join("\n");
}

/**
 * Encode a string as quoted-printable for use inside HTML bodies.
 * Gmail uses `=3D` for `=`, `=20` is optional, but `=` in attributes must be encoded.
 */
function encodeQPHtml(html) {
  return encodeQuotedPrintable(html);
}

// ─── MIME Boundary ───────────────────────────────────────────────────────────

function generateBoundary() {
  const chars = "0123456789abcdef";
  let b = "";
  for (let i = 0; i < 24; i++) b += chars[Math.floor(Math.random() * 16)];
  return `000000000000${b}`;
}

// ─── Date Formatting ─────────────────────────────────────────────────────────

/**
 * Format a Date for the Date: header (RFC 2822).
 * e.g. "Wed, 4 Mar 2026 13:18:09 +0200"
 */
function formatRFC2822Date(date) {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = [
    "Jan","Feb","Mar","Apr","May","Jun",
    "Jul","Aug","Sep","Oct","Nov","Dec",
  ];
  const pad = (n) => String(n).padStart(2, "0");

  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const absOffset = Math.abs(offset);
  const tzStr = `${sign}${pad(Math.floor(absOffset / 60))}${pad(absOffset % 60)}`;

  return (
    `${days[date.getDay()]}, ${date.getDate()} ${months[date.getMonth()]} ${date.getFullYear()} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())} ${tzStr}`
  );
}

/**
 * Format a Date for the Gmail attribution line.
 * e.g. "Wed, Mar 4, 2026 at 1:18 PM"
 * Uses a narrow no-break space (U+202F) before AM/PM, matching Gmail.
 */
function formatAttributionDate(date) {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = [
    "Jan","Feb","Mar","Apr","May","Jun",
    "Jul","Aug","Sep","Oct","Nov","Dec",
  ];

  const hours24 = date.getHours();
  const hours12 = hours24 % 12 || 12;
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const ampm = hours24 < 12 ? "AM" : "PM";
  const NARROW_NBSP = "\u202F"; // U+202F narrow no-break space (Gmail uses this)

  return (
    `${days[date.getDay()]}, ${months[date.getMonth()]} ${date.getDate()}, ` +
    `${date.getFullYear()} at ${hours12}:${minutes}${NARROW_NBSP}${ampm}`
  );
}

// ─── Plain-Text Quoting ───────────────────────────────────────────────────────

/**
 * Quote plain-text body lines with `> ` prefix, matching Gmail's format.
 * Blank lines become `>` (no trailing space).
 */
function quotePlainText(text) {
  return text
    .split("\n")
    .map((line) => (line.trim() === "" ? ">" : `> ${line}`))
    .join("\n");
}

/**
 * Build the plain-text attribution header.
 * e.g. "On Wed, Mar 4, 2026 at 1:18 PM John Doe <john@example.com> wrote:"
 */
function buildPlainAttribution(originalEmail) {
  const { fromName, fromAddress, date } = originalEmail;
  const dateStr = formatAttributionDate(date instanceof Date ? date : new Date(date));
  return `On ${dateStr} ${fromName} <${fromAddress}> wrote:`;
}

/**
 * Build a complete plain-text reply body with Gmail-style quoting.
 *
 * @param {string} replyText   - The new reply content (already written by sender)
 * @param {object} originalEmail - { fromName, fromAddress, date, plainBody }
 * @returns {string} Full plain-text body
 */
function buildPlainTextReply(replyText, originalEmail) {
  const attribution = buildPlainAttribution(originalEmail);
  const quoted = quotePlainText(originalEmail.plainBody || "");
  return `${replyText}\n${attribution}\n${quoted}\n`;
}

// ─── HTML Quoting ─────────────────────────────────────────────────────────────

export function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Build the HTML attribution line.
 */
function buildHtmlAttribution(originalEmail) {
  const { fromName, fromAddress, date } = originalEmail;
  const dateStr = formatAttributionDate(date instanceof Date ? date : new Date(date));
  // Gmail uses a narrow no-break space before AM/PM — already in formatAttributionDate
  const escapedName = escapeHtml(fromName);
  const escapedAddr = escapeHtml(fromAddress);
  return (
    `<div dir="ltr" class="gmail_attr">` +
    `On ${escapeHtml(dateStr)} ${escapedName} &lt;<a href="mailto:${escapedAddr}">${escapedAddr}</a>&gt; wrote:<br>` +
    `</div>`
  );
}

/**
 * Wrap the original HTML body in Gmail's blockquote structure.
 *
 * @param {string} originalHtml - The original email's HTML body
 * @param {object} originalEmail - { fromName, fromAddress, date }
 * @returns {string} Gmail-style quoted HTML block
 */
function buildHtmlQuoteBlock(originalHtml, originalEmail) {
  const attribution = buildHtmlAttribution(originalEmail);
  return (
    `<div class="gmail_quote gmail_quote_container">` +
    attribution +
    `<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex">` +
    originalHtml +
    `\n</blockquote></div>`
  );
}

/**
 * Build a complete HTML reply body with Gmail-style quoting.
 *
 * @param {string} replyHtml     - The new reply content as HTML (e.g. "<div dir="ltr">...</div>")
 * @param {string} originalHtml  - The original email's full HTML body
 * @param {object} originalEmail - { fromName, fromAddress, date }
 * @returns {string} Full HTML body
 */
function buildHtmlReply(replyHtml, originalHtml, originalEmail) {
  const quoteBlock = buildHtmlQuoteBlock(originalHtml, originalEmail);
  return `${replyHtml}<br>${quoteBlock}\n`;
}

// ─── MIME Assembly ────────────────────────────────────────────────────────────

/**
 * Generate a RFC 2822 Message-ID.
 * Format: <random@domain>
 */
function generateMessageId(fromAddress) {
  const domain = fromAddress.split("@")[1] || "mail.example.com";
  const rand = [...crypto.getRandomValues(new Uint8Array(16))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `<${rand}@mail.${domain}>`;
}

/**
 * Build a complete MIME reply email string, matching Gmail's format.
 */
function buildReplyEmail({
  replyText,
  replyHtml,
  originalEmail,
  sender,
  references = [],
}) {
  const now = new Date();
  const boundary = generateBoundary();
  const messageId = generateMessageId(sender.address);

  // Build References chain
  const allRefs = [...references, originalEmail.messageId].join(" ");

  // Build subject
  const subject = originalEmail.subject.startsWith("Re:")
    ? originalEmail.subject
    : `Re: ${originalEmail.subject}`;

  // Build bodies
  const plainBody = buildPlainTextReply(replyText, originalEmail);
  const htmlBody = buildHtmlReply(
    replyHtml || `<div dir="ltr">${escapeHtml(replyText)}</div>`,
    originalEmail.htmlBody,
    originalEmail
  );

  // Encode bodies
  const encodedPlain = encodeQuotedPrintable(plainBody);
  const encodedHtml = encodeQPHtml(htmlBody);

  const mime = [
    `MIME-Version: 1.0`,
    `Date: ${formatRFC2822Date(now)}`,
    `References: ${allRefs}`,
    `In-Reply-To: ${originalEmail.messageId}`,
    `Message-ID: ${messageId}`,
    `Subject: ${subject}`,
    `From: ${sender.name} <${sender.address}>`,
    `To: ${originalEmail.fromName} <${originalEmail.fromAddress}>`,
    `Content-Type: multipart/alternative; boundary="${boundary}"`,
    ``,
    `--${boundary}`,
    `Content-Type: text/plain; charset="UTF-8"`,
    `Content-Transfer-Encoding: quoted-printable`,
    ``,
    encodedPlain,
    `--${boundary}`,
    `Content-Type: text/html; charset="UTF-8"`,
    `Content-Transfer-Encoding: quoted-printable`,
    ``,
    encodedHtml,
    `--${boundary}--`,
  ].join("\r\n");

  return mime;
}

// ─── Email Parser (basic MIME) ────────────────────────────────────────────────

/**
 * Decode a quoted-printable encoded string.
 */
function decodeQuotedPrintable(str) {
  // Join soft-wrapped lines
  return str
    .replace(/=\r?\n/g, "")
    .replace(/=([0-9A-Fa-f]{2})/g, (_, hex) =>
      String.fromCharCode(parseInt(hex, 16))
    );
}

/**
 * Parse raw email headers into a key/value object.
 * Handles folded headers (continuation lines starting with whitespace).
 */
function parseHeaders(headerBlock) {
  const headers = {};
  // Unfold headers (RFC 2822 folding)
  const unfolded = headerBlock.replace(/\r?\n[ \t]+/g, " ");
  for (const line of unfolded.split(/\r?\n/)) {
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const key = line.slice(0, colon).trim().toLowerCase();
    const value = line.slice(colon + 1).trim();
    headers[key] = value;
  }
  return headers;
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Extract the plain-text and HTML bodies from a raw MIME email string.
 */
function parseEmail(raw) {
  // Split headers from body
  const headerBodySplit = raw.match(/^([\s\S]*?)\r?\n\r?\n([\s\S]*)$/);
  if (!headerBodySplit) return { plainBody: "", htmlBody: "", headers: {} };

  const headers = parseHeaders(headerBodySplit[1]);
  const body = headerBodySplit[2];

  let plainBody = "";
  let htmlBody = "";

  const contentType = headers["content-type"] || "";

  if (contentType.includes("multipart/")) {
    const boundaryMatch = contentType.match(/boundary="?([^";\s]+)"?/i);
    if (!boundaryMatch) return { plainBody: body, htmlBody: "", headers };
    const boundary = boundaryMatch[1];

    const parts = body.split(new RegExp(`--${escapeRegex(boundary)}(?:--)?`));
    for (const part of parts) {
      if (!part.trim() || part.trim() === "--") continue;
      const partSplit = part.match(/^\r?\n?([\s\S]*?)\r?\n\r?\n([\s\S]*)$/);
      if (!partSplit) continue;
      const partHeaders = parseHeaders(partSplit[1]);
      let partBody = partSplit[2];
      const partCT = partHeaders["content-type"] || "";
      const partCTE = (partHeaders["content-transfer-encoding"] || "").toLowerCase();

      if (partCTE === "quoted-printable") {
        partBody = decodeQuotedPrintable(partBody);
      }

      if (partCT.includes("text/plain")) {
        plainBody = partBody.trim();
      } else if (partCT.includes("text/html")) {
        htmlBody = partBody.trim();
      }
    }
  } else if (contentType.includes("text/html")) {
    const cte = (headers["content-transfer-encoding"] || "").toLowerCase();
    htmlBody = cte === "quoted-printable" ? decodeQuotedPrintable(body) : body;
  } else {
    const cte = (headers["content-transfer-encoding"] || "").toLowerCase();
    plainBody = cte === "quoted-printable" ? decodeQuotedPrintable(body) : body;
  }

  return { plainBody, htmlBody, headers };
}

/**
 * Parse key fields out of a raw email for use as `originalEmail`.
 */
function parseOriginalEmail(raw) {
  const { plainBody, htmlBody, headers } = parseEmail(raw);

  const fromHeader = headers["from"] || "";
  const fromMatch = fromHeader.match(/^"?([^"<]*?)"?\s*<([^>]+)>/) ||
    fromHeader.match(/^([^\s@]+@[^\s]+)/);
  const fromName = fromMatch ? fromMatch[1].trim() : fromHeader;
  const fromAddress = fromMatch ? (fromMatch[2] || fromMatch[1]).trim() : fromHeader;

  const dateStr = headers["date"] || new Date().toUTCString();
  const date = new Date(dateStr);

  return {
    messageId: headers["message-id"] || `<unknown@unknown>`,
    subject: headers["subject"] || "(no subject)",
    fromName,
    fromAddress,
    date,
    plainBody,
    htmlBody,
  };
}

// ─── Public API ───────────────────────────────────────────────────────────────

export {
  // Core quoting
  buildPlainTextReply,
  buildHtmlReply,
  buildReplyEmail,

  // Parsing
  parseEmail,
  parseOriginalEmail,

  // Utilities (exported for testing/custom use)
  quotePlainText,
  buildPlainAttribution,
  buildHtmlAttribution,
  buildHtmlQuoteBlock,
  encodeQuotedPrintable,
  decodeQuotedPrintable,
  formatRFC2822Date,
  formatAttributionDate,
  generateBoundary,
  generateMessageId,
};
