import { useState, useEffect, useRef } from 'react';
import { api, apiCache } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';

/**
 * Reusable tracking-domain form section.
 * Lets the user choose between the app's default domain or a custom one,
 * and shows DNS setup instructions with the real CNAME target.
 */
function TrackingDomainField({ value, onChange, cnameTarget }) {
  const isCustom = Boolean(value && value.trim());
  const [verifyState, setVerifyState] = useState(null); // null | 'checking' | 'ok' | {error}

  // Reset verification whenever the domain value changes
  const handleChange = (v) => {
    setVerifyState(null);
    onChange(v);
  };

  const verify = async () => {
    const domain = value.trim();
    if (!domain) return;
    setVerifyState('checking');
    try {
      const res = await fetch(`/api/settings/verify-tracking-domain?domain=${encodeURIComponent(domain)}`);
      const data = await res.json();
      setVerifyState(data.ok ? 'ok' : { error: data.error || 'Unknown error' });
    } catch (e) {
      setVerifyState({ error: e.message });
    }
  };

  return (
    <div className="border rounded p-3 space-y-2 bg-gray-50">
      <p className="text-sm font-medium text-gray-700">Tracking domain</p>
      <label className="flex items-center gap-2 cursor-pointer text-sm">
        <input
          type="radio"
          checked={!isCustom}
          onChange={() => handleChange('')}
        />
        <span>
          Use app domain <span className="text-gray-400 font-mono text-xs">({cnameTarget || window.location.hostname})</span>
        </span>
      </label>
      <label className="flex items-center gap-2 cursor-pointer text-sm">
        <input
          type="radio"
          checked={isCustom}
          onChange={() => { if (!isCustom) handleChange(''); }}
        />
        <span>Custom domain</span>
      </label>
      {isCustom || (
        <p className="text-xs text-gray-400">
          Open and click tracking links will use the app's own URL.
        </p>
      )}
      {(isCustom || value !== undefined) && (
        <div className="space-y-2 pl-5">
          <div className="flex gap-2 items-center">
            <input
              type="text"
              className="block flex-1 border rounded p-1 font-mono text-sm"
              placeholder="mail.yourdomain.com"
              value={value}
              onChange={e => handleChange(e.target.value)}
              onFocus={() => { if (!isCustom) handleChange(' '); }}
            />
            {isCustom && (
              <button
                type="button"
                onClick={verify}
                disabled={verifyState === 'checking'}
                className="shrink-0 px-2 py-1 text-xs rounded border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-50"
              >
                {verifyState === 'checking' ? 'Checking…' : 'Verify'}
              </button>
            )}
          </div>
          {verifyState === 'ok' && (
            <p className="text-xs text-green-600 font-medium">✓ Domain is reachable and pointing to this server</p>
          )}
          {verifyState && verifyState !== 'checking' && verifyState !== 'ok' && (
            <p className="text-xs text-red-600">✗ {verifyState.error}</p>
          )}
          {isCustom && cnameTarget && (
            <div className="bg-white border rounded p-2 text-xs text-gray-500 space-y-1">
              <p className="font-medium text-gray-700">DNS setup (one-time)</p>
              <p>Add a <code>CNAME</code> record at your registrar:</p>
              <pre className="bg-gray-50 rounded p-1 overflow-x-auto whitespace-pre-wrap break-all">
{`${value.trim() || 'mail.yourdomain.com'}  CNAME  ${cnameTarget}.`}
              </pre>
              <p>
                Caddy auto-provisions an SSL certificate on the first HTTPS request —
                no manual cert management needed.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Inboxes() {
  const [inboxes, setInboxes] = useState(() => apiCache.get('/inboxes') || []);
  const [cnameTarget, setCnameTarget] = useState('');
  // state used for both add and edit forms
  const initialForm = {
    provider: 'gmail',
    email: '',
    display_name: '',
    max_emails_per_day: 50,
    wait_minutes_between: 5,
    max_jitter_seconds: 180,
    tracking_domain: '',
    ramp_up_enabled: false,
    ramp_up_period_days: 42,
  };
  const [form, setForm] = useState(initialForm);
  const [message, setMessage] = useState(null);
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [redirectUri, setRedirectUri] = useState('');
  const [o365Configured, setO365Configured] = useState(false);
  const [o365RedirectUri, setO365RedirectUri] = useState('');
  const [editing, setEditing] = useState(null); // inbox being edited
  const [editDirty, setEditDirty] = useState(false);
  const [showEditWarning, setShowEditWarning] = useState(false);
  const [editMsg, setEditMsg] = useState(null);
  const [showAdd, setShowAdd] = useState(false); // controls add modal
  const confirm = useConfirm();
  const addBackdropDown = useRef(false);
  const notify = useNotify();

  // ---- Pause modal state ----
  const [showPauseModal, setShowPauseModal] = useState(false);
  const [pausingInbox, setPausingInbox] = useState(null);
  const [pauseAction, setPauseAction] = useState('pause_leads');

  // ---- Detail panel state ----
  const [selectedInbox, setSelectedInbox] = useState(null);

  const load = async () => {
    try {
      const data = await api.get('/inboxes');
      setInboxes(data);
      setSelectedInbox(prev => prev ? (data.find(i => i.id === prev.id) || null) : null);
    } catch (e) {
      console.error(e);
    }
  };
  useEffect(() => {
    load();
    // check Gmail OAuth
    fetch('/api/gmail/status')
      .then(r => r.json())
      .then(d => {
        setOauthConfigured(d.configured);
        setRedirectUri(d.redirect_uri || '');
      })
      .catch(() => {});
    // check Office 365 OAuth
    fetch('/api/office365/status')
      .then(r => r.json())
      .then(d => {
        setO365Configured(d.configured);
        setO365RedirectUri(d.redirect_uri || '');
      })
      .catch(() => {});
    // get server hostname for DNS instructions
    fetch('/api/settings/server-info')
      .then(r => r.json())
      .then(d => setCnameTarget(d.cname_target || window.location.hostname))
      .catch(() => setCnameTarget(window.location.hostname));
  }, []);

  const handleChange = (e) => {
    const { name, value, type } = e.target;
    setForm(f => ({ ...f, [name]: type === 'number' ? +value : value }));
  };

  const handleProviderChange = (e) => {
    handleChange(e);
  };

  const canSubmit = () => {
    if (form.provider === 'gmail' || form.provider === 'office365') {
      // allow click so user receives an error message if OAuth is not configured
      return true;
    }
    return form.email.trim() !== '';
  };

  const submit = async (e) => {
    e.preventDefault();
    setMessage(null);
    if (form.provider === 'gmail') {
      if (!oauthConfigured) {
        setMessage({
          type: 'error',
          text: 'Google OAuth is not configured. Define GOOGLE_CLIENT_ID/SECRET in your environment and restart the server.',
        });
        return;
      }
      // redirect to Gmail OAuth
      const params = new URLSearchParams({ display_name: form.display_name, max_per_day: form.max_emails_per_day, ramp_up_enabled: form.ramp_up_enabled ? 'true' : 'false' });
      window.location.href = '/oauth/google/authorize?' + params;
      return;
    }
    if (form.provider === 'office365') {
      if (!o365Configured) {
        setMessage({
          type: 'error',
          text: 'Office 365 OAuth is not configured. Define OFFICE365_CLIENT_ID/SECRET/TENANT_ID in your environment and restart the server.',
        });
        return;
      }
      // redirect to Office 365 OAuth
      const params = new URLSearchParams({ display_name: form.display_name, max_per_day: form.max_emails_per_day, ramp_up_enabled: form.ramp_up_enabled ? 'true' : 'false' });
      window.location.href = '/oauth/office365/authorize?' + params;
      return;
    }
    try {
      await api.post('/inboxes', {
        ...form,
        tracking_domain: form.tracking_domain.trim() || null,
      });
      setMessage({ type: 'success', text: 'Inbox added' });
      setForm(initialForm);
      load();
      setShowAdd(false);
    } catch (e) {
      setMessage({ type: 'error', text: e.message });
    }
  };

  const openEdit = (inbox) => {
    setEditing({ ...inbox });
    setEditDirty(false);
    setEditMsg(null);
  };
  const closeEdit = () => {
    setEditing(null);
    setEditDirty(false);
  };
  const tryCloseEdit = () => {
    if (editDirty) {
      setShowEditWarning(true);
    } else {
      closeEdit();
    }
  };
  const saveEdit = async (e) => {
    e.preventDefault();
    if (!editing) return;
    setEditDirty(false); // save in progress — don't treat as unsaved
    try {
      const body = {
        display_name: editing.display_name,
        provider: editing.provider,
        max_emails_per_day: editing.max_emails_per_day,
        wait_minutes_between: editing.wait_minutes_between,
        max_jitter_seconds: editing.max_jitter_seconds ?? 180,
        tracking_domain: (editing.tracking_domain || '').trim() || null,
        ramp_up_enabled: editing.ramp_up_enabled,
        ramp_up_period_days: editing.ramp_up_period_days,
      };
      await api.patch(`/inboxes/${editing.id}`, body);
      setEditMsg({ type: 'success', text: 'Inbox updated' });
      setTimeout(() => { closeEdit(); load(); }, 1000);
    } catch (e) {
      setEditMsg({ type: 'error', text: e.message });
    }
  };

  // Escape to close modals
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      if (showEditWarning) { setShowEditWarning(false); }
      else if (showAdd) { setShowAdd(false); setMessage(null); }
      else if (editing) tryCloseEdit();
      else if (selectedInbox) setSelectedInbox(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showEditWarning, showAdd, editing]); // eslint-disable-line react-hooks/exhaustive-deps

  const deleteInbox = async (id, email) => {
    const ok = await confirm(`Delete inbox "${email}"?`);
    if (!ok) return;

    const tryDelete = async (reassign = false) => {
      const url = `/inboxes/${id}` + (reassign ? '?reassign=true' : '');
      // helper exposes `del` method for DELETE
      await api.del(url);
    };

    try {
      await tryDelete();
      notify({ type: 'success', message: `Inbox "${email}" deleted` });
      load();
    } catch (e) {
      const msg = e.message || '';
      if (msg.includes('assigned to one or more campaigns') || msg.includes('pending queue slots')) {
        const again = await confirm(
          'Inbox is currently in use.\n' +
          'Assigned leads will be reassigned to other inboxes.'
        );
        if (again) {
          try {
            await tryDelete(true);
            notify({ type: 'success', message: `Inbox "${email}" paused & deleted` });
            load();
            return;
          } catch (e2) {
            notify({ type: 'error', message: 'Error deleting inbox after reassign: ' + e2.message });
            return;
          }
        }
      }
      notify({ type: 'error', message: 'Error deleting inbox: ' + msg });
    }
  };

  const openPauseModal = async (inbox) => {
    if (!inbox.pending_leads) {
      // No pending leads to handle — pause silently without a dialog
      try {
        await api.post(`/inboxes/${inbox.id}/pause`, { action: 'pause_leads' });
        notify({ type: 'success', message: `Inbox "${inbox.email}" paused` });
        load();
      } catch (e) {
        notify({ type: 'error', message: 'Error pausing inbox: ' + e.message });
      }
      return;
    }
    setPausingInbox(inbox);
    setPauseAction('reassign');
    setShowPauseModal(true);
  };

  const confirmPause = async () => {
    if (!pausingInbox) return;
    try {
      await api.post(`/inboxes/${pausingInbox.id}/pause`, {
        action: pauseAction,
      });
      notify({ type: 'success', message: `Inbox "${pausingInbox.email}" paused` });
      setShowPauseModal(false);
      load();
    } catch (e) {
      notify({ type: 'error', message: 'Error pausing inbox: ' + e.message });
    }
  };

  const resumeInbox = async (id, email) => {
    try {
      await api.post(`/inboxes/${id}/unpause`, {});
      notify({ type: 'success', message: `Inbox "${email}" resumed` });
      load();
    } catch (e) {
      notify({ type: 'error', message: 'Error resuming inbox: ' + e.message });
    }
  };

  return (
    <div className="p-8">
      {/* header with add button */}
      <div className="flex justify-between items-center mb-4">
        <h1 className="text-2xl font-bold">Inboxes</h1>
        <Button variant="default" onClick={() => { setForm(initialForm); setMessage(null); setShowAdd(true); }}>
          Add Inbox
        </Button>
      </div>

      {inboxes.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="w-14 h-14 rounded-full bg-gray-100 flex items-center justify-center mb-4">
            <svg className="w-7 h-7 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <p className="text-gray-500 text-sm">No inboxes yet. Click <span className="font-medium text-gray-700">Add Inbox</span> to get started.</p>
        </div>
      )}
      {inboxes.length > 0 && (
        <div className="flex gap-5 items-start" style={{ alignItems: 'flex-start' }}>
          {/* ── Inbox card list ── */}
          <div className="flex-1 min-w-0 space-y-2">
            {inboxes.map(inbox => {
              const isSelected = selectedInbox?.id === inbox.id;
              const sentToday = inbox.sent_today || 0;
              const maxToday = inbox.effective_max_per_day || inbox.max_emails_per_day;
              const warmupActive = inbox.ramp_up_enabled && inbox.effective_max_per_day < inbox.max_emails_per_day;
              const avatarLetter = (inbox.email || inbox.display_name || 'I')[0].toUpperCase();
              return (
                <button
                  key={inbox.id}
                  onClick={() => setSelectedInbox(isSelected ? null : inbox)}
                  className={`w-full text-left rounded-xl border px-5 py-4 transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-blue-400 ${
                    isSelected
                      ? 'border-blue-400 bg-blue-50 shadow-sm'
                      : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
                  }`}
                >
                  <div className="flex items-center justify-between gap-4">
                    {/* Left: avatar + email */}
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center text-white text-sm font-semibold shrink-0">
                        {avatarLetter}
                      </div>
                      <div className="min-w-0">
                        <p className="font-medium text-gray-900 text-sm leading-tight truncate">
                          {inbox.email || '(Connected account)'}
                        </p>
                        {inbox.display_name && (
                          <p className="text-xs text-gray-500 leading-tight mt-0.5 truncate">{inbox.display_name}</p>
                        )}
                      </div>
                    </div>
                    {/* Right: badges + sent count */}
                    <div className="flex items-center gap-2 shrink-0">
                      {warmupActive && (
                        <span className="text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">
                          Stage {inbox.effective_max_per_day}/{inbox.max_emails_per_day}
                        </span>
                      )}
                      <span className="text-xs text-gray-500">
                        <span className="font-semibold text-gray-800">{sentToday}</span>
                        <span className="text-gray-400"> / {maxToday} sent</span>
                      </span>
                      {inbox.paused
                        ? <span className="text-xs bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full font-medium">Paused</span>
                        : <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">Active</span>
                      }
                      <svg className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${isSelected ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* ── Detail panel ── */}
          {selectedInbox && (
            <div className="w-72 shrink-0 bg-white border border-gray-200 rounded-xl shadow-sm flex flex-col overflow-hidden sticky top-4" style={{ maxHeight: 'calc(100vh - 8rem)' }}>
              {editing && editing.id === selectedInbox.id ? (
                <>
                  {/* Edit panel header */}
                  <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                    <h3 className="font-semibold text-gray-900 text-sm">Edit Inbox</h3>
                    <button
                      onClick={tryCloseEdit}
                      className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
                      aria-label="Cancel edit"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>

                  {/* Edit form */}
                  <div className="px-5 py-4 overflow-y-auto flex-1">
                    {editMsg && <div className={`mb-3 text-sm ${editMsg.type === 'error' ? 'text-red-600' : 'text-green-600'}`}>{editMsg.text}</div>}
                    <form onSubmit={saveEdit} className="space-y-4">
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Email (read-only)</label>
                        <input type="email" value={editing.email} disabled className="mt-1 block w-full border-gray-300 rounded-md bg-gray-100 text-sm" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Display name</label>
                        <input type="text" name="display_name" value={editing.display_name || ''} onChange={e => { setEditing(prev => ({ ...prev, display_name: e.target.value })); setEditDirty(true); }} className="mt-1 block w-full border-gray-300 rounded-md text-sm" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Provider</label>
                        <select name="provider" value={editing.provider || 'gmail'} className="mt-1 block w-full border-gray-300 rounded-md bg-gray-100 text-sm" disabled>
                          <option value="gmail">Gmail / Google Workspace</option>
                          <option value="office365">Office 365 / Outlook</option>
                        </select>
                      </div>
                      {editing.provider === 'gmail' && redirectUri && (
                        <div className="text-gray-500 text-xs">
                          Redirect URI: <code className="font-mono">{redirectUri}</code>
                        </div>
                      )}
                      {editing.provider === 'office365' && o365RedirectUri && (
                        <div className="text-gray-500 text-xs">
                          Redirect URI: <code className="font-mono">{o365RedirectUri}</code>
                        </div>
                      )}
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Max emails per day</label>
                        <input type="number" name="max_emails_per_day" value={editing.max_emails_per_day} onChange={e => { setEditing(prev => ({ ...prev, max_emails_per_day: +e.target.value })); setEditDirty(true); }} min={1} max={1000} className="mt-1 block w-full border-gray-300 rounded-md text-sm" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Wait between emails (minutes)</label>
                        <input type="number" name="wait_minutes_between" value={editing.wait_minutes_between || 5} onChange={e => { setEditing(prev => ({ ...prev, wait_minutes_between: +e.target.value })); setEditDirty(true); }} min={1} max={120} className="mt-1 block w-full border-gray-300 rounded-md text-sm" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Send time jitter (seconds)</label>
                        <input type="number" name="max_jitter_seconds" value={editing.max_jitter_seconds ?? 180} onChange={e => { setEditing(prev => ({ ...prev, max_jitter_seconds: +e.target.value })); setEditDirty(true); }} min={0} max={600} className="mt-1 block w-full border-gray-300 rounded-md text-sm" />
                        <p className="mt-1 text-xs text-gray-400">Adds a random 0–N second delay to each send time. Breaks predictable sending patterns. Set to 0 to disable.</p>
                      </div>
                      <TrackingDomainField
                        value={editing.tracking_domain || ''}
                        onChange={val => { setEditing(prev => ({ ...prev, tracking_domain: val })); setEditDirty(true); }}
                        cnameTarget={cnameTarget}
                      />
                      <div className="border rounded p-3 space-y-2 bg-gray-50">
                        <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-gray-700">
                          <input
                            type="checkbox"
                            checked={!!editing.ramp_up_enabled}
                            onChange={e => { setEditing(prev => ({ ...prev, ramp_up_enabled: e.target.checked })); setEditDirty(true); }}
                          />
                          Enable inbox warm-up (ramp-up)
                        </label>
                        {editing.ramp_up_enabled && (
                          <p className="text-xs text-gray-500">
                            Sends 1 email on day one, 2 on day two, and so on until it reaches {editing.max_emails_per_day}, then turns off automatically.
                            Today's limit: <strong>{editing.effective_max_per_day ?? 1}</strong> / {editing.max_emails_per_day}
                          </p>
                        )}
                      </div>
                      <div className="flex gap-2 pt-1">
                        <Button type="submit" size="sm" variant="default">Save</Button>
                        <Button type="button" size="sm" variant="outline" onClick={tryCloseEdit}>Cancel</Button>
                      </div>
                    </form>
                  </div>
                </>
              ) : (
                <>
                  {/* Panel header */}
                  <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center text-white text-sm font-semibold shrink-0">
                        {(selectedInbox.email || selectedInbox.display_name || 'I')[0].toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <p className="font-semibold text-gray-900 text-sm leading-tight truncate">{selectedInbox.email || '(Connected account)'}</p>
                        {selectedInbox.display_name && (
                          <p className="text-xs text-gray-500 truncate leading-tight mt-0.5">{selectedInbox.display_name}</p>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={() => setSelectedInbox(null)}
                      className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
                      aria-label="Close panel"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>

                  {/* Scrollable content */}
                  <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1">
                    {/* Status + Provider row */}
                    <div className="flex items-center justify-between">
                      {selectedInbox.paused
                        ? <span className="text-xs bg-orange-100 text-orange-700 px-2.5 py-1 rounded-full font-medium">Paused</span>
                        : <span className="text-xs bg-green-100 text-green-700 px-2.5 py-1 rounded-full font-medium">Active</span>
                      }
                      <span className="text-xs bg-sky-100 text-sky-700 px-2.5 py-1 rounded-full font-medium capitalize">{selectedInbox.provider || 'gmail'}</span>
                    </div>

                    {/* Sent today */}
                    <div>
                      <div className="flex justify-between items-center mb-1.5">
                        <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Today</span>
                        <span className="text-sm font-semibold text-gray-900">
                          {selectedInbox.sent_today || 0}
                          <span className="text-gray-400 font-normal"> / {selectedInbox.effective_max_per_day || selectedInbox.max_emails_per_day}</span>
                        </span>
                      </div>
                      <div className="w-full bg-gray-100 rounded-full h-1.5">
                        <div
                          className="bg-blue-500 h-1.5 rounded-full transition-all"
                          style={{ width: `${Math.min(100, ((selectedInbox.sent_today || 0) / (selectedInbox.effective_max_per_day || selectedInbox.max_emails_per_day || 1)) * 100)}%` }}
                        />
                      </div>
                    </div>

                    <hr className="border-gray-100" />

                    {/* Settings */}
                    <div className="space-y-2.5">
                      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Settings</p>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-600">Max per day</span>
                        <span className="font-medium text-gray-900">{selectedInbox.max_emails_per_day}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-600">Wait between sends</span>
                        <span className="font-medium text-gray-900">{selectedInbox.wait_minutes_between || 5} min</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-600">Send jitter</span>
                        <span className="font-medium text-gray-900">
                          {(selectedInbox.max_jitter_seconds ?? 180) > 0
                            ? `up to ${selectedInbox.max_jitter_seconds ?? 180}s random`
                            : <span className="text-gray-400">disabled</span>}
                        </span>
                      </div>
                    </div>

                    {/* Warm-up */}
                    {selectedInbox.ramp_up_enabled && (
                      <>
                        <hr className="border-gray-100" />
                        <div className="space-y-2.5">
                          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Warm-up</p>
                          {selectedInbox.effective_max_per_day < selectedInbox.max_emails_per_day ? (
                            <>
                              <div className="flex justify-between text-sm">
                                <span className="text-gray-600">Today's stage</span>
                                <span className="font-medium text-amber-700">{selectedInbox.effective_max_per_day} / {selectedInbox.max_emails_per_day}</span>
                              </div>
                              <div className="w-full bg-amber-100 rounded-full h-1.5">
                                <div
                                  className="bg-amber-500 h-1.5 rounded-full"
                                  style={{ width: `${Math.min(100, (selectedInbox.effective_max_per_day / (selectedInbox.max_emails_per_day || 1)) * 100)}%` }}
                                />
                              </div>
                            </>
                          ) : (
                            <p className="text-sm text-green-700 font-medium">Complete ✓</p>
                          )}
                          <div className="flex justify-between text-sm">
                            <span className="text-gray-600">Ramp period</span>
                            <span className="font-medium text-gray-900">{selectedInbox.ramp_up_period_days || 42} days</span>
                          </div>
                        </div>
                      </>
                    )}

                    <hr className="border-gray-100" />

                    {/* Tracking domain */}
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-gray-600">Tracking domain</span>
                      <span className="font-mono text-xs text-right max-w-[140px] truncate">
                        {selectedInbox.tracking_domain
                          ? <span className="text-teal-700">{selectedInbox.tracking_domain}</span>
                          : <span className="text-gray-400">app default</span>}
                      </span>
                    </div>

                    {/* Created */}
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-gray-600">Added</span>
                      <span className="text-gray-900">{new Date(selectedInbox.created_at).toLocaleDateString()}</span>
                    </div>
                  </div>

                  {/* Action buttons */}
                  <div className="px-5 py-4 border-t border-gray-100 space-y-2">
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" className="flex-1" onClick={() => openEdit(selectedInbox)}>Edit</Button>
                      {selectedInbox.paused
                        ? <Button variant="outline" size="sm" className="flex-1 bg-green-50 text-green-700 border-green-300 hover:bg-green-100" onClick={() => resumeInbox(selectedInbox.id, selectedInbox.email)}>Resume</Button>
                        : <Button variant="outline" size="sm" className="flex-1 bg-orange-50 text-orange-700 border-orange-300 hover:bg-orange-100" onClick={() => openPauseModal(selectedInbox)}>Pause</Button>
                      }
                    </div>
                    <Button variant="danger" size="sm" className="w-full" onClick={() => deleteInbox(selectedInbox.id, selectedInbox.email)}>Delete inbox</Button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* add modal */}
      {showAdd && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onMouseDown={e => { addBackdropDown.current = e.target === e.currentTarget; }}
          onClick={() => { if (addBackdropDown.current) { setShowAdd(false); setMessage(null); } }}
        >
          <div data-darkreader-ignore className="p-6 rounded-xl shadow-lg w-full max-w-md max-h-[90vh] overflow-y-auto mx-auto" style={{ backgroundColor: 'white' }} onClick={e => e.stopPropagation()}>
            <h2 className="text-xl font-semibold mb-2">Add Inbox</h2>
            {message && <div className={message.type === 'error' ? 'text-red-600' : 'text-green-600'}>{message.text}</div>}
            <form onSubmit={submit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">Provider</label>
                <select name="provider" value={form.provider} onChange={handleProviderChange} className="mt-1 block w-full border-gray-300 rounded-md">
                  <option value="gmail">Gmail / Google Workspace</option>
                  <option value="office365">Office 365 / Outlook</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700">Display name</label>
                <input type="text" name="display_name" value={form.display_name} onChange={handleChange} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Max emails per day</label>
                <input type="number" name="max_emails_per_day" value={form.max_emails_per_day} onChange={handleChange} min={1} max={1000} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Wait between emails (minutes)</label>
                <input type="number" name="wait_minutes_between" value={form.wait_minutes_between} onChange={handleChange} min={1} max={120} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Send time jitter (seconds)</label>
                <input type="number" name="max_jitter_seconds" value={form.max_jitter_seconds} onChange={handleChange} min={0} max={600} className="mt-1 block w-full border-gray-300 rounded-md" />
                <p className="mt-1 text-xs text-gray-400">Adds a random 0–N second delay per send. Breaks predictable sending patterns. Default 180s. Set to 0 to disable.</p>
              </div>
              {/* Tracking domain */}
              <TrackingDomainField
                value={form.tracking_domain}
                onChange={val => setForm(f => ({ ...f, tracking_domain: val }))}
                cnameTarget={cnameTarget}
              />
              {/* Ramp-up / warm-up */}
              <div className="border rounded p-3 space-y-2 bg-gray-50">
                <label className="flex items-center gap-2 cursor-pointer text-sm font-medium text-gray-700">
                  <input
                    type="checkbox"
                    checked={!!form.ramp_up_enabled}
                    onChange={e => setForm(f => ({ ...f, ramp_up_enabled: e.target.checked }))}
                  />
                  Enable inbox warm-up (ramp-up)
                </label>
                {form.ramp_up_enabled && (
                  <p className="text-xs text-gray-500">
                    Starts at 1 email on day one, adds 1 more each day, and turns off automatically once it reaches {form.max_emails_per_day}.
                  </p>
                )}
              </div>
              {form.provider === 'gmail' && (
                <>
                  {!oauthConfigured && (
                    <div className="text-red-600">
                      Google OAuth credentials are not configured. Set the appropriate environment variables (e.g. in `.env`) and restart the server before reloading.
                    </div>
                  )}
                  {redirectUri && (
                    <div className="text-gray-500 text-sm mt-1">
                      Redirect URI: <code className="font-mono">{redirectUri}</code>
                    </div>
                  )}
                </>
              )}
              {form.provider === 'office365' && (
                <>
                  {!o365Configured && (
                    <div className="text-red-600">
                      Office 365 OAuth credentials are not configured. Set OFFICE365_CLIENT_ID, OFFICE365_CLIENT_SECRET, and OFFICE365_TENANT_ID in your environment and restart the server.
                    </div>
                  )}
                  {o365RedirectUri && (
                    <div className="text-gray-500 text-sm mt-1">
                      Redirect URI: <code className="font-mono">{o365RedirectUri}</code>
                    </div>
                  )}
                </>
              )}
              <div className="flex gap-2">
                <Button type="submit" disabled={!canSubmit()} variant="default">
                  {form.provider === 'gmail' ? 'Connect with Google' : form.provider === 'office365' ? 'Connect with Microsoft' : 'Add inbox'}
                </Button>
                <Button type="button" variant="outline" onClick={() => { setShowAdd(false); setMessage(null); }}>
                  Cancel
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}



      {/* Unsaved changes warning for inbox edit */}
      {showEditWarning && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4">
          <div data-darkreader-ignore className="rounded-xl shadow-lg p-6 w-full max-w-sm mx-auto" style={{ backgroundColor: 'white' }}>
            <h3 className="font-semibold text-gray-800 mb-1">Discard changes?</h3>
            <p className="text-sm text-gray-500 mb-4">You have unsaved changes. Closing will discard them.</p>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="outline" onClick={() => setShowEditWarning(false)}>Keep editing</Button>
              <Button size="sm" variant="destructive" onClick={() => { setShowEditWarning(false); closeEdit(); }}>Discard</Button>
            </div>
          </div>
        </div>
      )}

      {/* Pause inbox modal */}
      {showPauseModal && pausingInbox && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div data-darkreader-ignore className="bg-white p-6 rounded-xl shadow-lg w-full max-w-md mx-auto">
            <h2 className="text-xl font-semibold mb-1">Pause Inbox</h2>
            <p className="text-sm text-gray-500 mb-4">
              Pausing <span className="font-mono font-medium">{pausingInbox.email}</span>. What should happen to leads currently assigned to this inbox?
            </p>

            <div className="space-y-3 mb-5">
              <label className="flex items-start gap-3 cursor-pointer p-3 border rounded-lg hover:bg-gray-50 transition-colors">
                <input
                  type="radio"
                  className="mt-0.5 shrink-0"
                  checked={pauseAction === 'reassign'}
                  onChange={() => setPauseAction('reassign')}
                />
                <div>
                  <p className="text-sm font-medium text-gray-800">Reassign to another inbox</p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    The queue will be recalculated and leads will be automatically redistributed across remaining active inboxes.
                  </p>
                </div>
              </label>

              <label className="flex items-start gap-3 cursor-pointer p-3 border rounded-lg hover:bg-gray-50 transition-colors">
                <input
                  type="radio"
                  className="mt-0.5 shrink-0"
                  checked={pauseAction === 'pause_leads'}
                  onChange={() => setPauseAction('pause_leads')}
                />
                <div>
                  <p className="text-sm font-medium text-gray-800">Pause all assigned leads</p>
                  <p className="text-xs text-gray-500 mt-0.5">Sending will be paused for every lead whose next email is scheduled through this inbox. You can resume them individually later.</p>
                </div>
              </label>
            </div>

            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="outline" onClick={() => setShowPauseModal(false)}>Cancel</Button>
              <Button
                size="sm"
                variant="default"
                onClick={confirmPause}
              >
                Pause Inbox
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
