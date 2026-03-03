import { useState, useEffect } from 'react';
import { api } from '../api';
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
  const [inboxes, setInboxes] = useState([]);
  const [cnameTarget, setCnameTarget] = useState('');
  // state used for both add and edit forms
  const initialForm = {
    provider: 'gmail',
    email: '',
    display_name: '',
    max_emails_per_day: 50,
    wait_minutes_between: 5,
    tracking_domain: '',
  };
  const [form, setForm] = useState(initialForm);
  const [message, setMessage] = useState(null);
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [redirectUri, setRedirectUri] = useState('');
  const [editing, setEditing] = useState(null); // inbox being edited
  const [editDirty, setEditDirty] = useState(false);
  const [showEditWarning, setShowEditWarning] = useState(false);
  const [editMsg, setEditMsg] = useState(null);
  const [showAdd, setShowAdd] = useState(false); // controls add modal
  const confirm = useConfirm();
  const notify = useNotify();

  const load = async () => {
    try {
      const data = await api.get('/inboxes');
      setInboxes(data);
    } catch (e) {
      console.error(e);
    }
  };
  useEffect(() => {
    load();
    // check oauth
    fetch('/api/gmail/status')
      .then(r => r.json())
      .then(d => {
        setOauthConfigured(d.configured);
        setRedirectUri(d.redirect_uri || '');
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
    if (form.provider === 'gmail') {
      // allow click so user receives an error message; we still show warning below
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
      // redirect to OAuth
      const params = new URLSearchParams({ display_name: form.display_name, max_per_day: form.max_emails_per_day });
      window.location.href = '/oauth/google/authorize?' + params;
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
    try {
      const body = {
        display_name: editing.display_name,
        provider: editing.provider,
        max_emails_per_day: editing.max_emails_per_day,
        wait_minutes_between: editing.wait_minutes_between,
        tracking_domain: (editing.tracking_domain || '').trim() || null,
      };
      await api.patch(`/inboxes/${editing.id}`, body);
      setEditMsg({ type: 'success', text: 'Inbox updated' });
      setTimeout(() => { closeEdit(); load(); }, 1000);
    } catch (e) {
      setEditMsg({ type: 'error', text: e.message });
    }
  };

  const deleteInbox = async (id, email) => {
    const ok = await confirm(`Delete inbox "${email}"?`);
    if (!ok) return;
    try {
      await api.del(`/inboxes/${id}`);
      load();
    } catch (e) {
      notify({ type: 'error', message: 'Error deleting inbox: ' + e.message });
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

      <h2 className="text-xl font-semibold mb-2">Inbox list</h2>
      {inboxes.length === 0 && <Card>No inboxes yet. Click "Add Inbox" to create one.</Card>}
      {inboxes.length > 0 && (
        <Card className="overflow-auto">
          <table className="w-full table-auto border-collapse">
          <thead>
            <tr>
              <th className="px-4 py-2">Email</th>
              <th className="px-4 py-2">Display name</th>
              <th className="px-4 py-2">Provider</th>
              <th className="px-4 py-2">Max/day</th>
              <th className="px-4 py-2">Sent today</th>
              <th className="px-4 py-2">Wait</th>
              <th className="px-4 py-2">Tracking domain</th>
              <th className="px-4 py-2">Created</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {inboxes.map(i => (
              <tr key={i.id} className="border-t hover:bg-gray-50">
                <td className="px-4 py-2 font-mono">{i.email || (i.provider==='gmail' ? '(Gmail account)' : '')}</td>
                <td className="px-4 py-2">{i.display_name || '—'}</td>
                <td className="px-4 py-2">
                  <span className="px-1 py-0.5 rounded text-sm" style={{ backgroundColor: '#cfeffd' }}>
                    {i.provider || 'gmail'}
                  </span>
                </td>
                <td className="px-4 py-2">{i.max_emails_per_day}</td>
                <td className="px-4 py-2">{(i.sent_today||0)} of {i.max_emails_per_day}</td>
                <td className="px-4 py-2">{i.wait_minutes_between || 5}</td>
                <td className="px-4 py-2 font-mono text-xs">
                  {i.tracking_domain
                    ? <span className="text-teal-700">{i.tracking_domain}</span>
                    : <span className="text-gray-400">app default</span>}
                </td>
                <td className="px-4 py-2">{new Date(i.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-2 whitespace-nowrap">
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => openEdit(i)}>Edit</Button>
                    <Button variant="danger" size="sm" onClick={() => deleteInbox(i.id, i.email)}>Delete</Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </Card>
      )}

      {/* add modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => { setShowAdd(false); setMessage(null); }}>
          <div data-darkreader-ignore className="p-6 rounded shadow max-w-md w-full max-h-[90vh] overflow-y-auto" style={{ backgroundColor: 'white' }} onClick={e => e.stopPropagation()}>
            <h2 className="text-xl font-semibold mb-2">Add Inbox</h2>
            {message && <div className={message.type === 'error' ? 'text-red-600' : 'text-green-600'}>{message.text}</div>}
            <form onSubmit={submit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">Provider</label>
                <select name="provider" value={form.provider} onChange={handleProviderChange} className="mt-1 block w-full border-gray-300 rounded-md">
                  <option value="gmail">Gmail OAuth</option>
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
              {/* Tracking domain */}
              <TrackingDomainField
                value={form.tracking_domain}
                onChange={val => setForm(f => ({ ...f, tracking_domain: val }))}
                cnameTarget={cnameTarget}
              />
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
              <div className="flex gap-2">
                <Button type="submit" disabled={!canSubmit()} variant="default">
                  {form.provider === 'gmail' ? 'Connect with Google' : 'Add inbox'}
                </Button>
                <Button type="button" variant="outline" onClick={() => { setShowAdd(false); setMessage(null); }}>
                  Cancel
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* edit modal */}
      {editing && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={tryCloseEdit}>
          <div data-darkreader-ignore className="p-6 rounded shadow max-w-md w-full max-h-[90vh] overflow-y-auto" style={{ backgroundColor: 'white' }} onClick={e => e.stopPropagation()}>
            <h2 className="text-xl font-semibold mb-2">Edit Inbox</h2>
            {editMsg && <div className={editMsg.type === 'error' ? 'text-red-600' : 'text-green-600'}>{editMsg.text}</div>}
            <form onSubmit={saveEdit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">Email (read-only)</label>
                <input type="email" value={editing.email} disabled className="mt-1 block w-full border-gray-300 rounded-md bg-gray-100" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Display name</label>
                <input type="text" name="display_name" value={editing.display_name || ''} onChange={e => { setEditing(prev => ({ ...prev, display_name: e.target.value })); setEditDirty(true); }} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Provider</label>
                <select name="provider" value={editing.provider || 'gmail'} onChange={e => { setEditing(prev => ({ ...prev, provider: e.target.value })); setEditDirty(true); }} className="mt-1 block w-full border-gray-300 rounded-md" disabled>
                  <option value="gmail">Gmail OAuth</option>
                </select>
              </div>
              {editing.provider === 'gmail' && (
                <div className="text-gray-500 text-sm">
                  Redirect URI: <code className="font-mono">{redirectUri}</code>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700">Max emails per day</label>
                <input type="number" name="max_emails_per_day" value={editing.max_emails_per_day} onChange={e => { setEditing(prev => ({ ...prev, max_emails_per_day: +e.target.value })); setEditDirty(true); }} min={1} max={1000} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Wait between emails (minutes)</label>
                <input type="number" name="wait_minutes_between" value={editing.wait_minutes_between || 5} onChange={e => { setEditing(prev => ({ ...prev, wait_minutes_between: +e.target.value })); setEditDirty(true); }} min={1} max={120} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              {/* Tracking domain */}
              <TrackingDomainField
                value={editing.tracking_domain || ''}
                onChange={val => { setEditing(prev => ({ ...prev, tracking_domain: val })); setEditDirty(true); }}
                cnameTarget={cnameTarget}
              />
              <div className="flex gap-2">
                <Button type="submit" size="sm" variant="default">Save</Button>
                <Button type="button" size="sm" variant="outline" onClick={tryCloseEdit}>Cancel</Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Unsaved changes warning for inbox edit */}
      {showEditWarning && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60]">
          <div data-darkreader-ignore className="rounded shadow p-6 max-w-sm w-full mx-4" style={{ backgroundColor: 'white' }}>
            <h3 className="font-semibold text-gray-800 mb-1">Discard changes?</h3>
            <p className="text-sm text-gray-500 mb-4">You have unsaved changes. Closing will discard them.</p>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="outline" onClick={() => setShowEditWarning(false)}>Keep editing</Button>
              <Button size="sm" variant="destructive" onClick={() => { setShowEditWarning(false); closeEdit(); }}>Discard</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
