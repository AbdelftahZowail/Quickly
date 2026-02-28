import { useState, useEffect } from 'react';
import { api } from '../api';
import Button from '../components/ui/Button';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';

export default function Inboxes() {
  const [inboxes, setInboxes] = useState([]);
  // state used for both add and edit forms
  const initialForm = {
    provider: 'resend',
    email: '',
    display_name: '',
    max_emails_per_day: 50,
    wait_minutes_between: 5,
  };
  const [form, setForm] = useState(initialForm);
  const [message, setMessage] = useState(null);
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [redirectUri, setRedirectUri] = useState('');
  const [editing, setEditing] = useState(null); // inbox being edited
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
      await api.post('/inboxes', form);
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
    setEditMsg(null);
  };
  const closeEdit = () => {
    setEditing(null);
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
        <Button variant="primary" onClick={() => { setForm(initialForm); setMessage(null); setShowAdd(true); }}>
          Add Inbox
        </Button>
      </div>

      <h2 className="text-xl font-semibold mb-2">Inbox list</h2>
      {inboxes.length === 0 && <p className="bg-white p-4 rounded shadow">No inboxes yet. Click "Add Inbox" to create one.</p>}
      {inboxes.length > 0 && (
        <table className="w-full table-auto border-collapse bg-white rounded shadow">
          <thead>
            <tr>
              <th className="px-4 py-2">Email</th>
              <th className="px-4 py-2">Display name</th>
              <th className="px-4 py-2">Provider</th>
              <th className="px-4 py-2">Max/day</th>
              <th className="px-4 py-2">Sent today</th>
              <th className="px-4 py-2">Wait</th>
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
                  <span className="px-1 py-0.5 rounded text-sm" style={{ backgroundColor: i.provider==='gmail' ? '#cfeffd' : '#e5e7eb' }}>
                    {i.provider || 'resend'}
                  </span>
                </td>
                <td className="px-4 py-2">{i.max_emails_per_day}</td>
                <td className="px-4 py-2">{(i.sent_today||0)} of {i.max_emails_per_day}</td>
                <td className="px-4 py-2">{i.wait_minutes_between || 5}</td>
                <td className="px-4 py-2">{new Date(i.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-2 flex gap-2">
                  <Button variant="secondary" size="sm" onClick={() => openEdit(i)}>Edit</Button>
                  <Button variant="danger" size="sm" onClick={() => deleteInbox(i.id, i.email)}>Delete</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* add modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white p-6 rounded shadow max-w-md w-full">
            <h2 className="text-xl font-semibold mb-2">Add Inbox</h2>
            {message && <div className={message.type === 'error' ? 'text-red-600' : 'text-green-600'}>{message.text}</div>}
            <form onSubmit={submit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">Provider</label>
                <select name="provider" value={form.provider} onChange={handleProviderChange} className="mt-1 block w-full border-gray-300 rounded-md">
                  <option value="resend">Resend</option>
                  <option value="smtp">SMTP</option>
                  <option value="gmail">Gmail OAuth</option>
                </select>
              </div>
              {form.provider !== 'gmail' && (
              <div>
                <label className="block text-sm font-medium text-gray-700">Email *</label>
                <input type="email" name="email" value={form.email} onChange={handleChange} className="mt-1 block w-full border-gray-300 rounded-md" required />
              </div>)}
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
                <Button type="submit" disabled={!canSubmit()} variant="primary">
                  {form.provider === 'gmail' ? 'Connect with Google' : 'Add inbox'}
                </Button>
                <Button type="button" variant="secondary" onClick={() => { setShowAdd(false); setMessage(null); }}>
                  Cancel
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* edit modal */}
      {editing && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white p-6 rounded shadow max-w-md w-full">
            <h2 className="text-xl font-semibold mb-2">Edit Inbox</h2>
            {editMsg && <div className={editMsg.type === 'error' ? 'text-red-600' : 'text-green-600'}>{editMsg.text}</div>}
            <form onSubmit={saveEdit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">Email (read-only)</label>
                <input type="email" value={editing.email} disabled className="mt-1 block w-full border-gray-300 rounded-md bg-gray-100" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Display name</label>
                <input type="text" name="display_name" value={editing.display_name || ''} onChange={e => setEditing(e => ({ ...e, display_name: e.target.value }))} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Provider</label>
                <select name="provider" value={editing.provider || 'resend'} onChange={e => setEditing(e => ({ ...e, provider: e.target.value }))} className="mt-1 block w-full border-gray-300 rounded-md">
                  <option value="resend">Resend</option>
                  <option value="smtp">SMTP</option>
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
                <input type="number" name="max_emails_per_day" value={editing.max_emails_per_day} onChange={e => setEditing(e => ({ ...e, max_emails_per_day: +e.target.value }))} min={1} max={1000} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Wait between emails (minutes)</label>
                <input type="number" name="wait_minutes_between" value={editing.wait_minutes_between || 5} onChange={e => setEditing(e => ({ ...e, wait_minutes_between: +e.target.value }))} min={1} max={120} className="mt-1 block w-full border-gray-300 rounded-md" />
              </div>
              <div className="flex gap-2">
                <button type="submit" className="px-4 py-2 bg-teal-500 text-white rounded">Save</button>
                <button type="button" onClick={closeEdit} className="px-4 py-2 bg-gray-200 rounded">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}