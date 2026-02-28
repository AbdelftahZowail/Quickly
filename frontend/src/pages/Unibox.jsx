import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import DOMPurify from 'dompurify';
import Button from '../components/ui/Button';
import EmailContent from '../components/EmailContent';
import { useNotify } from '../context/NotificationContext';

const PAGE_SIZE = 25;
const OLDER_WINDOW_DAYS = 7;

async function uniboxRequest(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(text || res.statusText);
    err.status = res.status;
    throw err;
  }

  const contentType = res.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error('Unexpected response type from Unibox API. Verify backend routing/proxy for /unibox.');
  }
  return res.json();
}

function formatDateTime(value) {
  if (!value) return 'Unknown';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

function extractEmailAddress(headerValue) {
  if (!headerValue) return '';
  const angleMatch = headerValue.match(/<([^>]+)>/);
  if (angleMatch?.[1]) return angleMatch[1].trim().toLowerCase();

  const emailMatch = headerValue.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
  return emailMatch?.[0]?.trim().toLowerCase() || '';
}

function makeReplySubject(subject) {
  const clean = (subject || '').trim();
  if (!clean) return '';
  return /^re:/i.test(clean) ? clean : `Re: ${clean}`;
}

function buildHtmlDoc(html) {
  const safeHtml = DOMPurify.sanitize(html || '', {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'link', 'meta', 'base'],
  });
  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta
      http-equiv="Content-Security-Policy"
      content="default-src 'none'; img-src data: blob: https:; style-src 'unsafe-inline'; font-src data: https:;"
    />
    <style>
      body { font-family: Figtree, sans-serif; margin: 0; padding: 10px; color: #111827; }
      img { max-width: 100%; height: auto; }
      pre { white-space: pre-wrap; }
    </style>
  </head>
  <body>${safeHtml}</body>
</html>`;
}

// helper used when rendering HTML messages outside an iframe
function sanitizeHtmlContent(html) {
  return DOMPurify.sanitize(html || '', {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'link', 'meta', 'base'],
  });
}

export default function Unibox() {
  const notify = useNotify();
  const [searchParams, setSearchParams] = useSearchParams();

  const [conversations, setConversations] = useState([]);
  const [total, setTotal] = useState(0);
  // pagination state used for incremental loading only (infinite scroll)
  const [page, setPage] = useState(1);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState('');

  const [selectedThread, setSelectedThread] = useState(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState('');

  const [compose, setCompose] = useState({
    to_email: '',
    subject: '',
    body: '',
    is_html: false,
  });
  const [sending, setSending] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [initialListSyncLoading, setInitialListSyncLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [autoSyncAttempted, setAutoSyncAttempted] = useState(false);
  // view mode for each message is now determined automatically based on available content (html vs plain)
  const [replyOpen, setReplyOpen] = useState(false);
  const listRequestInFlightRef = useRef(false);
  const sseRefreshTimerRef = useRef(null);
  const threadScrollRef = useRef(null);
  const listScrollRef = useRef(null); // reference for conversation list container

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);

  // load one page of conversations.  when `append` is true we merge the
  // results onto the existing list, otherwise we replace the current
  // conversations (used for initial load and refreshing).
  const loadConversations = useCallback(
    async ({ page: requestedPage = 1, silent = false, append = false } = {}) => {
      if (listRequestInFlightRef.current) return;
      listRequestInFlightRef.current = true;
      if (!silent) {
        setListLoading(true);
        setListError('');
      }
      try {
        const data = await uniboxRequest(`
          /unibox?page=${requestedPage}&page_size=${PAGE_SIZE}`
            .trim(),
        );
        const items = data?.items || [];
        setTotal(data?.total || 0);
        if (append) {
          setConversations((prev) => [...prev, ...items]);
        } else {
          setConversations(items);
        }
        setPage(requestedPage);
      } catch (err) {
        if (!silent) {
          setListError(err.message || 'Failed to load conversations');
        }
      } finally {
        if (!silent) {
          setListLoading(false);
        }
        listRequestInFlightRef.current = false;
      }
    },
    [],
  );

  const loadThread = useCallback(async (threadId, inboxId) => {
    setThreadLoading(true);
    setThreadError('');
    try {
      const params = new URLSearchParams();
      if (inboxId) params.set('inbox_id', String(inboxId));
      const data = await uniboxRequest(`/unibox/threads/${encodeURIComponent(threadId)}?${params.toString()}`);
      setSelectedThread(data);
      // update URL so the conversation is reflected in the address bar
      const newParams = { thread: threadId };
      if (inboxId != null && inboxId !== '') newParams.inbox = String(inboxId);
      setSearchParams(newParams);

      const messages = data?.messages || [];
      const lastReceived = [...messages].reverse().find(m => m.direction === 'received');
      const fallback = messages[messages.length - 1];
      const replyTo = extractEmailAddress(lastReceived?.from || fallback?.from || '');
      setCompose({
        to_email: replyTo,
        subject: makeReplySubject(data?.subject || ''),
        body: '',
        is_html: false,
      });

      // no explicit view mode state needed; rendering will choose based on msg fields
      setReplyOpen(false);
    } catch (err) {
      setThreadError(err.message || 'Failed to load thread');
      setSelectedThread(null);
      // clear URL on error/none
      setSearchParams({});
    } finally {
      setThreadLoading(false);
    }
  }, [setSearchParams]);

  const loadSyncStatus = useCallback(async ({ silent = false } = {}) => {
    try {
      const status = await uniboxRequest('/unibox/status');
      setInitialListSyncLoading(Boolean(status?.initial_list_sync_in_progress));
    } catch (err) {
      if (!silent) {
        notify({ type: 'error', message: err.message || 'Failed to load sync status.' });
      }
    }
  }, [notify]);

  useEffect(() => {
    // initial load, clear any previously held entries
    loadConversations({ page: 1 });
    loadSyncStatus({ silent: true });
  }, [loadConversations, loadSyncStatus]);

  // monitor conversation list scrolling and load more pages lazily
  useEffect(() => {
    const el = listScrollRef.current;
    if (!el) return;
    const onScroll = () => {
      if (listLoading || page >= totalPages) return;
      if (el.scrollTop + el.clientHeight >= el.scrollHeight - 50) {
        // nearing bottom, request next page
        const next = page + 1;
        if (next <= totalPages) {
          loadConversations({ page: next, append: true });
        }
      }
    };
    el.addEventListener('scroll', onScroll);
    return () => el.removeEventListener('scroll', onScroll);
  }, [listLoading, page, totalPages, loadConversations]);

  // load thread from query params when component mounts or when params change
  useEffect(() => {
    const thread = searchParams.get('thread');
    const inbox = searchParams.get('inbox');
    if (thread) {
      // avoid unnecessary reloads if we already have the same thread selected
      if (
        !selectedThread ||
        selectedThread.thread_id !== thread ||
        String(selectedThread.inbox_id) !== inbox
      ) {
        loadThread(thread, inbox || undefined);
      }
    } else if (selectedThread) {
      // clear selection when param removed
      setSelectedThread(null);
    }
  }, [searchParams, loadThread, selectedThread]);

  const triggerSync = useCallback(async () => {
    setSyncing(true);
    try {
      const res = await uniboxRequest('/unibox/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if ((res?.queued || 0) <= 0) {
        notify({ type: 'error', message: 'No Gmail inboxes available for sync.' });
      } else {
        notify({ type: 'success', message: `Sync queued for ${res.queued} inbox(es).` });
        await loadSyncStatus({ silent: true });
      }
    } catch (err) {
      notify({ type: 'error', message: err.message || 'Failed to start sync.' });
    } finally {
      setSyncing(false);
    }
  }, [loadSyncStatus, notify]);

  const triggerLoadOlder = useCallback(async () => {
    setLoadingOlder(true);
    try {
      const body = {
        window_days: OLDER_WINDOW_DAYS,
        inbox_id: selectedThread?.inbox_id || null,
      };
      const res = await uniboxRequest('/unibox/load-more', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if ((res?.queued || 0) <= 0) {
        notify({ type: 'error', message: 'No Gmail inboxes available for backfill.' });
      } else {
        const scopeMsg = selectedThread?.inbox_id ? 'selected inbox' : 'all inboxes';
        notify({
          type: 'success',
          message: `Loading older mail (${OLDER_WINDOW_DAYS} days) for ${scopeMsg}.`,
        });
      }
    } catch (err) {
      notify({ type: 'error', message: err.message || 'Failed to load older mail.' });
    } finally {
      setLoadingOlder(false);
    }
  }, [notify, selectedThread?.inbox_id]);

  useEffect(() => {
    if (!listLoading && !listError && conversations.length === 0 && !autoSyncAttempted) {
      setAutoSyncAttempted(true);
      triggerSync();
    }
  }, [autoSyncAttempted, conversations.length, listError, listLoading, triggerSync]);

  useEffect(() => {
    if (!initialListSyncLoading) return undefined;
    const timer = setInterval(() => {
      loadConversations({ page: 1, silent: true });
      loadSyncStatus({ silent: true });
    }, 2000);
    return () => clearInterval(timer);
  }, [initialListSyncLoading, loadConversations, loadSyncStatus]);

  useEffect(() => {
    const source = new EventSource('/unibox/events');

    const onUpdate = () => {
      if (sseRefreshTimerRef.current) return;
      sseRefreshTimerRef.current = setTimeout(() => {
        sseRefreshTimerRef.current = null;
        loadConversations({ silent: true });
        if (selectedThread?.thread_id && selectedThread?.inbox_id) {
          loadThread(selectedThread.thread_id, selectedThread.inbox_id);
        }
      }, 1000);
    };

    const onSyncStatus = (evt) => {
      try {
        const data = JSON.parse(evt.data || '{}');
        if (data?.phase === 'initial-list') {
          setInitialListSyncLoading(Boolean(data?.in_progress));
          if (data?.in_progress) {
            loadConversations({ page: 1, silent: true });
          }
        } else {
          loadSyncStatus({ silent: true });
        }
      } catch {
        loadSyncStatus({ silent: true });
      }
    };

    source.addEventListener('unibox.thread.updated', onUpdate);
    source.addEventListener('unibox.sync.status', onSyncStatus);
    source.onerror = () => {
      // Keep the EventSource open so the browser can reconnect automatically.
    };

    return () => {
      source.removeEventListener('unibox.thread.updated', onUpdate);
      source.removeEventListener('unibox.sync.status', onSyncStatus);
      source.close();
      if (sseRefreshTimerRef.current) {
        clearTimeout(sseRefreshTimerRef.current);
        sseRefreshTimerRef.current = null;
      }
    };
  }, [loadConversations, loadSyncStatus, loadThread, selectedThread?.inbox_id, selectedThread?.thread_id]);

  useEffect(() => {
    if (!replyOpen || !threadScrollRef.current) return;
    threadScrollRef.current.scrollTo({
      top: threadScrollRef.current.scrollHeight,
      behavior: 'smooth',
    });
  }, [replyOpen, selectedThread?.thread_id]);

  const sendReply = async (e) => {
    e.preventDefault();
    if (!selectedThread) return;
    if (!compose.to_email.trim()) {
      notify({ type: 'error', message: 'Reply target email is required.' });
      return;
    }

    setSending(true);
    try {
      await uniboxRequest('/unibox/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          inbox_id: selectedThread.inbox_id,
          to_email: compose.to_email.trim(),
          subject: compose.subject,
          body: compose.body,
          thread_id: selectedThread.thread_id,
          is_html: compose.is_html,
        }),
      });
      notify({ type: 'success', message: 'Reply sent.' });
      setCompose(c => ({ ...c, body: '' }));
      setReplyOpen(false);
      await loadThread(selectedThread.thread_id, selectedThread.inbox_id);
      await loadConversations({ silent: true });
    } catch (err) {
      notify({ type: 'error', message: err.message || 'Failed to send reply.' });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="p-8 h-screen overflow-hidden flex flex-col gap-4">
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-2xl font-semibold">Unibox</h1>
        <span className="text-sm text-gray-500">Gmail thread view and replies</span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 flex-1 min-h-0">
        <section className="card xl:col-span-1 flex flex-col min-h-0 overflow-hidden">
          <div className="flex items-center justify-between border-b border-gray-200 pb-3 mb-3">
            <h2 className="font-semibold">Conversations</h2>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">{total} total</span>
              <Button variant="secondary" size="sm" onClick={triggerSync} disabled={syncing}>
                {syncing ? 'Syncing...' : 'Sync now'}
              </Button>
            </div>
          </div>

          {listLoading && <p className="text-sm text-gray-500">Loading conversations...</p>}
          {initialListSyncLoading && (
            <p className="text-sm text-teal-700">
              Initial sync is loading conversation list...
            </p>
          )}
          {listError && <p className="text-sm text-red-600">{listError}</p>}
          {!listLoading && !listError && conversations.length === 0 && (
            <p className="text-sm text-gray-500">
              No synced conversations yet. Initial sync includes recent 7 days only.
            </p>
          )}

          <div ref={listScrollRef} className="space-y-2 overflow-y-auto pr-1 flex-1 min-h-0">
            {conversations.map((item) => {
              const isActive = selectedThread?.thread_id === item.thread_id && selectedThread?.inbox_id === item.inbox_id;
              const toUrl = `/mailbox?thread=${encodeURIComponent(item.thread_id)}${item.inbox_id ? `&inbox=${item.inbox_id}` : ''}`;
              return (
                <Link
                  key={`${item.inbox_id}-${item.thread_id}`}
                  to={toUrl}
                  className={`w-full block text-left p-3 rounded border no-underline hover:no-underline ${
                    isActive ? 'border-teal-400 bg-teal-50' : 'border-gray-200 hover:bg-teal-50/30'
                  }`}
                >
                  <div className="text-xs text-gray-500 mb-1">{item.gmail_account}</div>
                  <div className="font-medium truncate">{item.subject || '(no subject)'}</div>
                  <div className="text-sm text-gray-600 truncate">{item.last_message_snippet || ''}</div>
                  <div className="text-xs text-gray-400 mt-1">{formatDateTime(item.timestamp)}</div>
                </Link>
              );
            })}
            {/* show older button at bottom of scrollable list */}
            <div className="flex justify-center py-2">
              <Button variant="secondary" size="sm" onClick={triggerLoadOlder} disabled={loadingOlder}>
                {loadingOlder ? 'Loading older...' : 'Show older'}
              </Button>
            </div>
          </div>
        </section>

        <section className="card xl:col-span-2 flex flex-col min-h-0 overflow-hidden">
          {!selectedThread && !threadLoading && (
            <div className="text-gray-500 text-sm flex-1">Select a conversation to view messages and reply.</div>
          )}
          {threadLoading && <div className="text-gray-500 text-sm">Loading thread...</div>}
          {threadError && <div className="text-red-600 text-sm">{threadError}</div>}

          {selectedThread && !threadLoading && (
            <>
              <div className="border-b border-gray-200 pb-3 mb-3">
                <h2 className="text-lg font-semibold truncate">{selectedThread.subject || '(no subject)'}</h2>
                <p className="text-xs text-gray-500">
                  Inbox: {selectedThread.gmail_account} | Last update: {formatDateTime(selectedThread.last_message_timestamp)}
                </p>
              </div>

              <div ref={threadScrollRef} className="flex-1 min-h-0 overflow-y-auto pr-1">
                <div className="flex flex-col gap-4 pb-1">
                  {(selectedThread.messages || []).map((msg) => (
                    <article
                      key={msg.message_id}
                      className={`p-3 rounded border ${
                        msg.direction === 'sent'
                          ? 'bg-teal-50 border-teal-200'
                          : 'bg-white border-gray-200'
                      }`}
                    >
                        <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
                          <span className="text-xs uppercase tracking-wide font-semibold text-gray-600">
                            {msg.direction === 'sent' ? 'Sent' : 'Received'}
                          </span>
                          <span className="text-xs text-gray-500">{formatDateTime(msg.timestamp)}</span>
                        </div>
                        <div className="text-xs text-gray-500 mb-2">
                          From: {msg.from || '-'} | To: {msg.to || '-'}
                        </div>
                        {msg.body_html && msg.body_html.trim() ? (
                          <div className="w-full bg-white border border-gray-200 rounded min-h-[220px] overflow-auto">
                            <EmailContent html={msg.body_html} />
                          </div>
                        ) : (
                          <div className="text-sm whitespace-pre-wrap break-words">
                            {msg.body_plain || msg.snippet || '(no message body)'}
                          </div>
                        )}
                    </article>
                  ))}

                  {replyOpen && (
                    <form onSubmit={sendReply} className="rounded-2xl border border-gray-200 bg-gradient-to-b from-white to-gray-50 p-4 shadow-sm">
                      <div className="flex items-center justify-between mb-3 gap-2">
                        <h3 className="font-semibold">Reply</h3>
                        <span className="text-xs text-gray-500 truncate">To {compose.to_email || '(unknown recipient)'}</span>
                      </div>
                      <textarea
                        value={compose.body}
                        onChange={e => setCompose(c => ({ ...c, body: e.target.value }))}
                        className="w-full min-h-[180px] resize-y rounded-xl border-gray-300 shadow-sm focus:ring-teal-500 focus:border-teal-500"
                        placeholder="Write your reply..."
                        required
                      />
                      <div className="mt-3 flex items-center justify-end gap-2">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          className="rounded-full px-3 py-1.5"
                          onClick={() => setReplyOpen(false)}
                        >
                          Cancel
                        </Button>
                        <Button type="submit" variant="primary" size="sm" disabled={sending} className="rounded-full px-4 py-1.5">
                          {sending ? 'Sending...' : 'Send reply'}
                        </Button>
                      </div>
                    </form>
                  )}
                </div>
              </div>

              <div className="pt-3 mt-3 border-t border-gray-200 flex items-center justify-between gap-3">
                <p className="text-xs text-gray-500">
                  {replyOpen ? 'Reply box added at the end of this thread.' : 'Want to continue this thread?'}
                </p>
                <Button
                  type="button"
                  variant={replyOpen ? 'secondary' : 'primary'}
                  size="sm"
                  className="rounded-full px-4 py-2 shadow-sm"
                  onClick={() => setReplyOpen((open) => !open)}
                >
                  {replyOpen ? 'Close reply' : 'Reply'}
                </Button>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
