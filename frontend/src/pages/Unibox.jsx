import { useState, useEffect, useRef } from "react";
import { api } from "../api";
import { useNotify } from "../context/NotificationContext";
import { useLoading } from "../context/LoadingContext";

function escapeHtml(s) {
  return (s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
}

function sanitizeEmailHtml(rawHtml) {
  /*
   * loosely modeled after the helper in the legacy templeate version
   * (templates/unibox.html).  we strip out dangerous elements and
   * attributes, then normalise links/images.  the React component
   * rendering the result now lives in a shadow root so that our own
   * Tailwind/global styles cannot accidentally hide or override the
   * email's markup (several users were seeing completely blank bodies
   * when their vendor templates happened to use class names such as
   * "hidden", "container" etc).
   */
  if (!rawHtml) return "";
  const parser = new DOMParser();
  const doc = parser.parseFromString(rawHtml, "text/html");

  // drop anything potentially executable or capable of loading
  // external resources we don't explicitly want.
  doc.querySelectorAll("script,style,iframe,object,embed,link,meta,base").forEach((el) => el.remove());

  // scrub attributes that start with "on" or contain javascript/data
  // URIs.  leave everything else alone (including class names) because
  // the shadow-root rendering will isolate the markup from our CSS.
  doc.querySelectorAll("*").forEach((el) => {
    Array.from(el.attributes || []).forEach((attr) => {
      const name = (attr.name || "").toLowerCase();
      const value = (attr.value || "").trim();
      if (name.startsWith("on")) {
        el.removeAttribute(attr.name);
        return;
      }
      if ((name === "href" || name === "src") && value) {
        const lower = value.toLowerCase();
        if (lower.startsWith("javascript:") || lower.startsWith("data:text/html")) {
          el.removeAttribute(attr.name);
        }
      }
    });
  });

  // open links in a new tab and remove unsafe hrefs
  doc.querySelectorAll("a").forEach((a) => {
    const href = (a.getAttribute("href") || "").trim();
    if (href && !/^(https?:|mailto:|tel:|#)/i.test(href)) {
      a.removeAttribute("href");
    }
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });

  // lazy‑load images and drop ones with unsafe sources
  doc.querySelectorAll("img").forEach((img) => {
    const src = (img.getAttribute("src") || "").trim().toLowerCase();
    if (src.startsWith("javascript:") || src.startsWith("data:text/html")) {
      img.remove();
      return;
    }
    img.setAttribute("loading", "lazy");
    img.setAttribute("referrerpolicy", "no-referrer");
  });

  return doc.body.innerHTML || "";
}


/**
 * renders HTML from an email inside a shadow root so that our global
 * styles (tailwind utilities, etc.) do not collide with class names or
 * element selectors used by the message.  the behaviour mirrors the
 * vanilla-js implementation in `templates/unibox.html`.
 */
function EmailHtml({ html, outgoing = false }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    console.log("EmailHtml effect running", { html, outgoing });

    // create shadow root once
    let root = el.shadowRoot;
    if (!root) {
      root = el.attachShadow({ mode: 'open' });
      const wrapper = document.createElement('div');
      // reset everything so the surrounding app styles can't leak in
      wrapper.style.all = 'initial';
      wrapper.style.background = 'initial';
      wrapper.style.fontFamily = 'inherit';
      wrapper.style.fontSize = 'inherit';
      wrapper.style.lineHeight = 'inherit';
      // do *not* inherit our outer color; the container may be white-on-dark
      // which leads to invisible text when the email itself has a white
      // background.  let the email decide its own colour (initial=black by
      // default).  we'll still inherit font settings so the body size is
      // reasonable.
      wrapper.style.color = 'initial';
      root.appendChild(wrapper);
    }
    const wrapper = root.firstElementChild;
    const sanitized = sanitizeEmailHtml(html);
    console.log("sanitized html length", sanitized.length, sanitized.substring(0, 200));
    wrapper.innerHTML = sanitized;

    // if sanitization produced nothing we highlight the area so it's visible
    if (!sanitized.trim()) {
      wrapper.style.border = '1px solid red';
      wrapper.textContent = '(no html content)';
    }

    // if the message doesn't specify any background colour anywhere in the
    // rendered HTML, assume we're overlaying it on a dark container and force
    // the text to white so it remains readable.  if the message is outgoing or
    // we only have plain text we explicitly keep black instead.
    let hasBg = false;
    wrapper.querySelectorAll('*').forEach((el) => {
      const style = window.getComputedStyle(el);
      if (
        style.backgroundColor &&
        style.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
        style.backgroundColor !== 'transparent'
      ) {
        hasBg = true;
      }
      if (el.hasAttribute('bgcolor')) {
        hasBg = true;
      }
    });
    if (!hasBg) {
      wrapper.style.color = outgoing ? 'black' : 'white';
    }
  }, [html, outgoing]);

  return <div className="message-body-content html" ref={containerRef} />;
}

export default function Unibox() {
  const notify = useNotify();
  const loading = useLoading();

  const [inboxes, setInboxes] = useState([]);
  const [filters, setFilters] = useState({
    inbox_id: "",
    participant_email: "",
    q: "",
    server_only: false,
    has_lead: false,
  });
  const [cursor, setCursor] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [resultsMeta, setResultsMeta] = useState("Loading conversations...");
  const [hasMore, setHasMore] = useState(false);

  const [selected, setSelected] = useState(null);
  const [threadDetail, setThreadDetail] = useState(null);
  const [replyOpen, setReplyOpen] = useState(false);
  const [replyBody, setReplyBody] = useState("");
  const replyStatusRef = useRef(null);

  const [listRefreshing, setListRefreshing] = useState(false);

  async function fetchConversations({ refresh = false, append = false, sync = false } = {}) {
    // if requested, trigger a server-side sync before pulling data. this helps
    // keep the UI up-to-date when the page is loaded or the user manually
    // refreshes.
    if (sync) {
      try {
        await api.post('/gmail/sync-now');
      } catch (err) {
        // non-fatal; we'll still attempt to load whatever the mirror currently has
        console.warn('gmail sync failed', err);
      }
    }

    setListRefreshing(refresh);
    // loading.start();
    try {
      const params = new URLSearchParams();
      if (filters.inbox_id) params.append("inbox_id", filters.inbox_id);
      if (filters.server_only) params.append("server_only", "true");
      if (filters.has_lead) params.append("has_lead", "true");
      if (filters.participant_email) params.append("participant_email", filters.participant_email);
      if (filters.q) params.append("q", filters.q);
      if (cursor && !refresh && append) params.append("cursor", cursor);
      params.append("include_inboxes", "true");
      params.append("page_size", "40");
      if (refresh) params.append("refresh", "true");

      const data = await api.get("/unibox/conversations?" + params.toString());
      setInboxes(data.inboxes || []);

      if (append) {
        setConversations((prev) => [...prev, ...(data.items || [])]);
      } else {
        setConversations(data.items || []);
      }
      setWarnings(data.warnings || []);
      setHasMore(data.has_more || false);
      setCursor(data.next_cursor);

      const metaParts = [];
      if (data.items) metaParts.push(`${data.items.length} conversation${data.items.length === 1 ? "" : "s"}`);
      if (data.cache_status) metaParts.push(`cache: ${data.cache_status}`);
      setResultsMeta(metaParts.join(" · ") || "");
    } catch (err) {
      notify({ type: 'error', message: err.message || "Failed to load conversations" });
    } finally {
      // loading.stop();
      setListRefreshing(false);
    }
  }

  async function fetchDetail(conv) {
    if (!conv) return;
    // loading.start();
    try {
      // encode segments so ids containing special characters work correctly
      const key = `${encodeURIComponent(conv.provider)}/${encodeURIComponent(
        conv.inbox_id
      )}/${encodeURIComponent(conv.thread_id)}`;
      const detail = await api.get(`/unibox/conversations/${key}`);
      setThreadDetail(detail);
    } catch (err) {
      notify({ type: 'error', message: err.message || "Failed to load thread" });
    } finally {
      // loading.stop();
    }
  }

  useEffect(() => {
    // sync the database before retrieving the first batch of threads so the
    // user sees the most recent messages when the page loads.
    fetchConversations({ sync: true });
  }, []);

  useEffect(() => {
    if (selected) {
      fetchDetail(selected);
    }
  }, [selected]);

  function onSelectConversation(conv) {
    setSelected(conv);
    setThreadDetail(null);
    setReplyOpen(false);
    setReplyBody("");
  }

  async function handleReplySubmit(e) {
    e.preventDefault();
    if (!selected || !threadDetail) return;
    if (!replyBody.trim()) return;
    // loading.start();
    try {
      const to = (threadDetail.participants || []).find((p) => p !== threadDetail.inbox_email) || "";
      await api.post("/unibox/reply", {
        provider: selected.provider,
        inbox_id: selected.inbox_id,
        thread_id: selected.thread_id,
        to_email: to,
        subject: threadDetail.subject,
        body: replyBody,
        is_html: false,
      });
      notify({ type: 'success', message: "Reply sent" });
      setReplyBody("");
      setReplyOpen(false);
      fetchDetail(selected);
    } catch (err) {
      notify({ type: 'error', message: err.message || "Failed to send reply" });
    } finally {
      // loading.stop();
    }
  }

  function clearFilters() {
    setFilters({ inbox_id: "", participant_email: "", q: "", server_only: false, has_lead: false });
    setCursor(null);
    fetchConversations({ refresh: true });
  }

  return (
    <div className="unibox-container p-8 flex flex-col">
      <h1 className="text-2xl font-bold mb-4">Unibox</h1>
      <div className="unibox-layout flex-1">
        {/* left pane */}
        <section className="left-col">
          <div className={`filter-wrap ${filters._collapsed ? "collapsed" : ""}`}>
            <div className="filter-header" onClick={() => setFilters((f) => ({ ...f, _collapsed: !f._collapsed }))}>
              <span>Filters</span>
              <button className="btn secondary small" type="button" aria-label="Toggle filters">
                ▾
              </button>
            </div>
            <div className="filter-grid">
              <div className="form-group" style={{ margin: 0 }}>
                <label>Inbox</label>
                <select
                  id="filter-inbox"
                  value={filters.inbox_id}
                  onChange={(e) => setFilters((f) => ({ ...f, inbox_id: e.target.value }))}
                >
                  <option value="">All inboxes</option>
                  {inboxes.map((ib) => (
                    <option key={ib.id} value={ib.id}>
                      {ib.email}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group" style={{ margin: 0 }}>
                <label>Conversation with email</label>
                <input
                  id="filter-participant"
                  type="email"
                  placeholder="lead@company.com"
                  value={filters.participant_email}
                  onChange={(e) => setFilters((f) => ({ ...f, participant_email: e.target.value }))}
                />
              </div>
              <div className="form-group" style={{ margin: 0 }}>
                <label>Search</label>
                <input
                  id="filter-search"
                  type="text"
                  placeholder="Subject, lead, campaign"
                  value={filters.q}
                  onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
                />
              </div>
              <label className="check-row">
                <input
                  id="filter-server-only"
                  type="checkbox"
                  checked={filters.server_only}
                  onChange={(e) => setFilters((f) => ({ ...f, server_only: e.target.checked }))}
                />
                <span>Server-sent threads only</span>
              </label>
              <label className="check-row">
                <input
                  id="filter-has-lead"
                  type="checkbox"
                  checked={filters.has_lead}
                  onChange={(e) => setFilters((f) => ({ ...f, has_lead: e.target.checked }))}
                />
                <span>Only conversations linked to saved leads</span>
              </label>
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.2rem" }}>
                <button
                  className="btn secondary"
                  id="refresh-btn"
                  type="button"
                  onClick={() => fetchConversations({ refresh: true, sync: true })}
                >
                  Refresh
                </button>
                <button
                  className="btn secondary"
                  id="clear-btn"
                  type="button"
                  onClick={clearFilters}
                >
                  Clear
                </button>
              </div>
            </div>
          </div>

          {warnings.length > 0 && (
            <ul id="warning-list" className="warning-list">
              {warnings.map((w, idx) => (
                <li key={idx}>{w}</li>
              ))}
            </ul>
          )}
          <div className="results-meta" id="results-meta">
            {listRefreshing ? "Refreshing..." : resultsMeta}
          </div>
          <div className="conversation-list" id="conversation-list">
            {conversations.map((conv) => {
              const active =
                selected &&
                conv.provider === selected.provider &&
                conv.inbox_id === selected.inbox_id &&
                conv.thread_id === selected.thread_id;

              // title for hover preview: lead email | campaigns
              const previewTitle = [
                conv.linked_lead ? conv.linked_lead.email : null,
                ...(conv.campaigns || []).map((c) => c.name),
              ]
                .filter(Boolean)
                .join(" | ");

              // ensure we always show something in the snippet area
              // try to show something useful even if snippet is missing
              let snippetText = conv.snippet || "";
              if (!snippetText) {
                if (conv.last_message_text) snippetText = conv.last_message_text;
                else if (conv.subject) snippetText = conv.subject;
              }

              return (
                <div
                  key={`${conv.provider}-${conv.inbox_id}-${conv.thread_id}`}
                  className={`conversation-item ${active ? "active" : ""}`}
                  title={previewTitle}
                  onClick={() => onSelectConversation(conv)}
                >
                  <div className="conversation-top">
                    <div className="conversation-subject">{conv.subject || "(no subject)"}</div>
                    <div className="conversation-date">
                      {conv.last_message_at ? new Date(conv.last_message_at).toLocaleString() : ""}
                    </div>
                  </div>
                  <div className="conversation-snippet">
                    {snippetText}
                  </div>
                  <div className="conversation-badges">
                    {/* show inbox email and provider as pills */}
                    {conv.inbox_email && <span className="pill">{conv.inbox_email}</span>}
                    {conv.provider && <span className="pill">{conv.provider}</span>}
                    {conv.from_server && <span className="pill server">server</span>}
                    {conv.linked_lead && <span className="pill lead">lead</span>}
                    {conv.has_unread && <span className="pill unread">unread</span>}
                    {conv.can_reply === false && <span className="pill">read-only</span>}
                  </div>
                </div>
              );
            })}
          </div>
          {hasMore && (
            <div style={{ padding: "0.7rem 0.9rem", borderTop: "1px solid var(--border)" }}>
              <button
                className="btn secondary"
                id="load-more-btn"
                type="button"
                style={{ width: "100%" }}
                onClick={() => fetchConversations({ append: true })}
              >
                Load more
              </button>
            </div>
          )}
        </section>

        {/* right pane */}
        <section className="right-col">
          {!threadDetail && (
            <div id="thread-empty" style={{ padding: "1.3rem", color: "var(--muted)" }}>
              Select a conversation to view the thread and reply.
            </div>
          )}
          {threadDetail && (
            <div id="thread-view" className="">
              <div className="thread-header">
                <div className="thread-subject">{threadDetail.subject}</div>
                <div className="thread-meta">
                  {/* show inbox email and participants */}
                  {threadDetail.inbox_email && <>{threadDetail.inbox_email} &middot; </>}
                  {threadDetail.participants?.join(", ")}
                  {threadDetail.messages?.[0]?.sent_at && (
                    <> &middot; {new Date(threadDetail.messages[0].sent_at).toLocaleString()}</>
                  )}
                  {/* lead and campaigns info if available */}
                  {(threadDetail.linked_lead || (threadDetail.campaigns || []).length) && (
                    <>
                      <br />
                      {threadDetail.linked_lead && (
                        <span className="pill lead" style={{ marginRight: '0.4rem' }}>
                          Lead: {threadDetail.linked_lead.email}
                        </span>
                      )}
                      {threadDetail.campaigns && threadDetail.campaigns.length > 0 && (
                        <span className="pill" style={{ opacity: 0.7 }}>
                          Campaigns: {threadDetail.campaigns.map((c) => c.name).join(', ')}
                        </span>
                      )}
                    </>
                  )}
                </div>
              </div>
              <div className="thread-body" id="thread-body">
                {threadDetail.messages?.map((msg) => {
                  const outgoing = msg.from_email === threadDetail.inbox_email;
                  console.log("rendering message", msg.message_id, msg.body_html && msg.body_html.length, msg.body_text && msg.body_text.length, msg.snippet && msg.snippet.length);
                  return (
                    <div key={msg.message_id || msg.id} className={`message ${outgoing ? "outgoing" : ""}`}> 
                      <div className="message-head">
                        <div className="message-from">{msg.from_header || msg.from_email}</div>
                        <div className="message-time">
                          {msg.sent_at ? new Date(msg.sent_at).toLocaleString() : ""}
                        </div>
                      </div>
                      {/* {msg.subject && <div className="message-subject">{msg.subject}</div>} */}
                      <div className="message-body">
                        {msg.body_html ? (
                        <EmailHtml html={msg.body_html} outgoing={outgoing} />
                      ) : (
                        <div className="message-body-content plain" style={{ color: 'black' }}>
                          {msg.body_text || msg.snippet}
                        </div>
                      )}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="reply-wrap">
                {!threadDetail.can_reply && (
                  <div id="reply-disabled" style={{ display: "block", color: "var(--muted)", fontSize: "0.85rem" }}>
                    Reply is currently available for Gmail-backed threads only.
                  </div>
                )}
                {threadDetail.can_reply && (
                  <form id="reply-form" onSubmit={handleReplySubmit}>
                    <div className="reply-toggle-row">
                      <button
                        className="btn secondary"
                        id="reply-toggle-btn"
                        type="button"
                        onClick={() => setReplyOpen((o) => !o)}
                      >
                        Reply
                      </button>
                    </div>
                    <div id="reply-compose" className={`reply-compose ${replyOpen ? "active" : ""}`}>
                      <div className="reply-row">
                        <label>Message</label>
                        <textarea
                          id="reply-body"
                          rows="8"
                          placeholder="Write your reply..."
                          required
                          value={replyBody}
                          onChange={(e) => setReplyBody(e.target.value)}
                        />
                      </div>
                      <div className="reply-actions">
                        <button className="btn" id="reply-send-btn" type="submit">
                          Send reply
                        </button>
                        <span id="reply-status" ref={replyStatusRef} style={{ color: "var(--muted)", fontSize: "0.82rem" }}></span>
                      </div>
                    </div>
                  </form>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}