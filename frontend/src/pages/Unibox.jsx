import { useCallback, useEffect, useMemo, useState } from 'react';
import Button from '../components/ui/Button';
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

export default function Unibox() {
  const notify = useNotify();
  const [conversations, setConversations] = useState([]);
  const [total, setTotal] = useState(0);
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
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [autoSyncAttempted, setAutoSyncAttempted] = useState(false);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);

  const loadConversations = useCallback(async () => {
    setListLoading(true);
    setListError('');
    try {
      const data = await uniboxRequest(`/unibox?page=${page}&page_size=${PAGE_SIZE}`);
      const items = data?.items || [];
      setConversations(items);
      setTotal(data?.total || 0);
    } catch (err) {
      setListError(err.message || 'Failed to load conversations');
    } finally {
      setListLoading(false);
    }
  }, [page]);

  const loadThread = useCallback(async (threadId, inboxId) => {
    setThreadLoading(true);
    setThreadError('');
    try {
      const params = new URLSearchParams();
      if (inboxId) params.set('inbox_id', String(inboxId));
      const data = await uniboxRequest(`/unibox/threads/${encodeURIComponent(threadId)}?${params.toString()}`);
      setSelectedThread(data);

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
    } catch (err) {
      setThreadError(err.message || 'Failed to load thread');
      setSelectedThread(null);
    } finally {
      setThreadLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

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
      }
    } catch (err) {
      notify({ type: 'error', message: err.message || 'Failed to start sync.' });
    } finally {
      setSyncing(false);
    }
  }, [notify]);

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
    const timer = setInterval(() => {
      loadConversations();
      if (selectedThread?.thread_id && selectedThread?.inbox_id) {
        loadThread(selectedThread.thread_id, selectedThread.inbox_id);
      }
    }, 12000);
    return () => clearInterval(timer);
  }, [loadConversations, loadThread, selectedThread?.inbox_id, selectedThread?.thread_id]);

  useEffect(() => {
    const source = new EventSource('/unibox/events');

    const onUpdate = () => {
      loadConversations();
      if (selectedThread?.thread_id && selectedThread?.inbox_id) {
        loadThread(selectedThread.thread_id, selectedThread.inbox_id);
      }
    };

    source.addEventListener('unibox.thread.updated', onUpdate);
    source.onerror = () => {
      source.close();
    };

    return () => {
      source.removeEventListener('unibox.thread.updated', onUpdate);
      source.close();
    };
  }, [loadConversations, loadThread, selectedThread?.inbox_id, selectedThread?.thread_id]);

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
      await loadThread(selectedThread.thread_id, selectedThread.inbox_id);
      await loadConversations();
    } catch (err) {
      notify({ type: 'error', message: err.message || 'Failed to send reply.' });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="p-8 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Unibox</h1>
        <span className="text-sm text-gray-500">Gmail thread view and replies</span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 min-h-[70vh]">
        <section className="card xl:col-span-1 flex flex-col min-h-[70vh]">
          <div className="flex items-center justify-between border-b border-gray-200 pb-3 mb-3">
            <h2 className="font-semibold">Conversations</h2>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">{total} total</span>
              <Button variant="secondary" size="sm" onClick={triggerSync} disabled={syncing}>
                {syncing ? 'Syncing...' : 'Sync now'}
              </Button>
              <Button variant="secondary" size="sm" onClick={triggerLoadOlder} disabled={loadingOlder}>
                {loadingOlder ? 'Loading older...' : 'Show older'}
              </Button>
            </div>
          </div>

          {listLoading && <p className="text-sm text-gray-500">Loading conversations...</p>}
          {listError && <p className="text-sm text-red-600">{listError}</p>}
          {!listLoading && !listError && conversations.length === 0 && (
            <p className="text-sm text-gray-500">
              No synced conversations yet. Initial sync includes recent 7 days only.
            </p>
          )}

          <div className="space-y-2 overflow-y-auto pr-1">
            {conversations.map((item) => {
              const isActive = selectedThread?.thread_id === item.thread_id && selectedThread?.inbox_id === item.inbox_id;
              return (
                <button
                  key={`${item.inbox_id}-${item.thread_id}`}
                  type="button"
                  className={`w-full text-left p-3 rounded border ${
                    isActive ? 'border-teal-400 bg-teal-50' : 'border-gray-200 hover:bg-gray-50'
                  }`}
                  onClick={() => loadThread(item.thread_id, item.inbox_id)}
                >
                  <div className="text-xs text-gray-500 mb-1">{item.gmail_account}</div>
                  <div className="font-medium truncate">{item.subject || '(no subject)'}</div>
                  <div className="text-sm text-gray-600 truncate">{item.last_message_snippet || ''}</div>
                  <div className="text-xs text-gray-400 mt-1">{formatDateTime(item.timestamp)}</div>
                </button>
              );
            })}
          </div>

          <div className="pt-3 mt-3 border-t border-gray-200 flex items-center justify-between">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1 || listLoading}
            >
              Previous
            </Button>
            <span className="text-xs text-gray-600">Page {page} of {totalPages}</span>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || listLoading}
            >
              Next
            </Button>
          </div>
        </section>

        <section className="card xl:col-span-2 flex flex-col min-h-[70vh]">
          {!selectedThread && !threadLoading && (
            <div className="text-gray-500 text-sm">Select a conversation to view messages and reply.</div>
          )}
          {threadLoading && <div className="text-gray-500 text-sm">Loading thread...</div>}
          {threadError && <div className="text-red-600 text-sm">{threadError}</div>}

          {selectedThread && !threadLoading && (
            <>
              <div className="border-b border-gray-200 pb-3 mb-3">
                <h2 className="text-lg font-semibold">{selectedThread.subject || '(no subject)'}</h2>
                <p className="text-xs text-gray-500">
                  Inbox: {selectedThread.gmail_account} | Last update: {formatDateTime(selectedThread.last_message_timestamp)}
                </p>
              </div>

              <div className="flex-1 overflow-y-auto space-y-3 pr-1">
                {(selectedThread.messages || []).map(msg => (
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
                    <div className="text-sm whitespace-pre-wrap break-words">
                      {msg.body_plain || msg.snippet || '(no message body)'}
                    </div>
                  </article>
                ))}
              </div>

              <form onSubmit={sendReply} className="mt-4 border-t border-gray-200 pt-4 space-y-3">
                <h3 className="font-semibold">Reply</h3>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">To</label>
                  <input
                    type="email"
                    value={compose.to_email}
                    onChange={e => setCompose(c => ({ ...c, to_email: e.target.value }))}
                    required
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
                  <input
                    type="text"
                    value={compose.subject}
                    onChange={e => setCompose(c => ({ ...c, subject: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Body</label>
                  <textarea
                    rows={6}
                    value={compose.body}
                    onChange={e => setCompose(c => ({ ...c, body: e.target.value }))}
                    className="block w-full rounded-md border-gray-300 shadow-sm focus:ring-teal-500 focus:border-teal-500"
                    required
                  />
                </div>
                <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={compose.is_html}
                    onChange={e => setCompose(c => ({ ...c, is_html: e.target.checked }))}
                  />
                  Send as HTML
                </label>
                <div>
                  <Button type="submit" variant="primary" disabled={sending}>
                    {sending ? 'Sending...' : 'Send reply'}
                  </Button>
                </div>
              </form>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
