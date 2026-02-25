import { useEffect, useState, useRef } from 'react';
import { api } from '../api';
import { useLoading } from '../context/LoadingContext';
import { useNotify } from '../context/NotificationContext';
import { useDarkMode } from '../context/DarkModeContext';
import { useConfirm } from '../context/ConfirmContext';

const DAY_NAMES = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
function escapeHtml(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}

export default function Settings() {
  const loading = useLoading();
  const notify = useNotify();

  const [form, setForm] = useState({});
  const [placeholders, setPlaceholders] = useState({});          // store masks like "Already configured"
  const [originalSettings, setOriginalSettings] = useState({});  // data returned from API for diffing
  const [gmailSync, setGmailSync] = useState({ push_topic:'', webhook_token:'', sync_interval_minutes:5 });
  const [gmailPlaceholders, setGmailPlaceholders] = useState({});
  const [strategy, setStrategy] = useState('priority');
  const [oauthStatus, setOauthStatus] = useState({configured:false, redirect_uri:''});

  const { darkMode, toggleDarkMode } = useDarkMode();
  const confirm = useConfirm();
  const isInitialLoad = useRef(true);
  const saveTimer = useRef();
  const gmailTimer = useRef();
  const lastSavedForm = useRef('');
  const lastSavedGmail = useRef('');

  const loadSettings = async () => {
    loading.start();
    try {
      const data = await api.get('/settings');

      // prepare placeholders for masked values
      const ph = {};
      if (data.resend_api_key_configured && (data.resend_api_key || '').includes('***')) {
        ph.resend_api_key = 'Already configured (hidden)';
      }
      if (data.smtp_password_configured && (data.smtp_password || '').includes('***')) {
        ph.smtp_password = 'Already configured (hidden)';
      }
      if (data.google_client_id_configured && (data.google_client_id || '').includes('***')) {
        ph.google_client_id = 'Already configured (hidden)';
      }
      if (data.google_client_secret_configured && (data.google_client_secret || '').includes('***')) {
        ph.google_client_secret = 'Already configured (hidden)';
      }

      setPlaceholders(ph);
      setOriginalSettings(data);

      setForm({
        base_url: data.base_url || '',
        queue_check_interval_minutes: data.queue_check_interval_minutes || 1,
        test_mode: data.test_mode || false,
        email_provider: data.email_provider || 'resend',
        // don't populate sensitive fields, keep empty so user must re-enter or trigger autosave
        resend_api_key: '',
        smtp_host: data.smtp_host || 'localhost',
        smtp_port: data.smtp_port || 1025,
        smtp_user: data.smtp_user || '',
        smtp_password: '',
        smtp_use_tls: data.smtp_use_tls || false,
        google_client_id: '',
        google_client_secret: '',
      });
    } catch (e) {
      notify({ type:'error', message:'Failed to load settings' });
    } finally { loading.stop(); }
  };

  const loadStrategy = async () => {
    try {
      const data = await api.get('/settings/scheduling-strategy');
      setStrategy(data.scheduling_strategy || 'priority');
    } catch {};
  };

  const loadGmailSync = async () => {
    try {
      const data = await api.get('/settings/gmail-sync');
      const ph = {};
      if (data.webhook_token_configured && (data.webhook_token || '').includes('***')) {
        ph.webhook_token = 'Already configured (hidden)';
      }
      setGmailPlaceholders(ph);
      setGmailSync({
        push_topic: data.push_topic || '',
        webhook_token: '',
        sync_interval_minutes: data.sync_interval_minutes || 5,
      });
    } catch {};
  };

  const checkOAuth = async () => {
    try {
      const res = await fetch('/api/gmail/status');
      const d = await res.json();
      setOauthStatus({configured:d.configured, redirect_uri:d.redirect_uri||''});
    } catch {};
  };

  useEffect(() => {
    loadSettings();
    loadStrategy();
    loadGmailSync();
    checkOAuth();

  }, []);

  const handleChange = e => {
    const { name, type, value, checked } = e.target;
    setForm(f => ({ ...f, [name]: type==='checkbox' ? checked : value }));
  };

  // use toggleDarkMode from context

  // helper used by both form submit and auto-save
  const saveSettingsNow = async () => {
    const serialized = JSON.stringify(form);
    if (serialized === lastSavedForm.current) return; // nothing changed
    loading.start();
    try {
      // start with form values
      const payload = {
        base_url: form.base_url,
        queue_check_interval_minutes: Number(form.queue_check_interval_minutes) || 1,
        test_mode: form.test_mode,
        email_provider: form.email_provider,
        resend_api_key: form.resend_api_key || '',
        smtp_host: form.smtp_host,
        smtp_port: Number(form.smtp_port) || 1025,
        smtp_user: form.smtp_user,
        smtp_password: form.smtp_password || '',
        smtp_use_tls: form.smtp_use_tls,
        google_client_id: form.google_client_id || '',
        google_client_secret: form.google_client_secret || '',
      };

      // if we left a sensitive value blank but it already existed, keep the current one
      if (!payload.resend_api_key && originalSettings.resend_api_key_configured) {
        payload.resend_api_key = originalSettings.resend_api_key;
      }
      if (!payload.smtp_password && originalSettings.smtp_password_configured) {
        payload.smtp_password = originalSettings.smtp_password;
      }
      if (!payload.google_client_id && originalSettings.google_client_id_configured) {
        payload.google_client_id = originalSettings.google_client_id;
      }
      if (!payload.google_client_secret && originalSettings.google_client_secret_configured) {
        payload.google_client_secret = originalSettings.google_client_secret;
      }

      await api.put('/settings', payload);
      notify({type:'success',message:'Settings saved'});
      // avoid triggering autosave when we re-populate the form
      isInitialLoad.current = true;
      lastSavedForm.current = serialized;
      await loadSettings(); // refresh placeholders
      await checkOAuth();
    } catch(e){notify({type:'error',message:e.message});}
    finally{loading.stop();}
  };

  const submitSettings = async e => {
    e && e.preventDefault();
    await saveSettingsNow();
  };

  const submitStrategy = async val => {
    // If the strategy is actually changing and we already have leads in
    // campaigns, warn the user before sending the request.  The backend will
    // still perform the recalculation even if there are no leads, so this is
    // only UX-level protection.
    if (val !== strategy) {
      try {
        const { has_leads } = await api.get('/campaigns/has-leads');
        if (has_leads) {
          const ok = await confirm(
            'Changing the scheduling strategy will recalculate all campaigns. ' +
            'This may take a few seconds when leads are enrolled. Continue?'
          );
          if (!ok) return;
        }
      } catch (e) {
        // ignore failures; we'll just submit and let server deal with it
      }
    }

    try {
      await api.post('/settings/scheduling-strategy', { scheduling_strategy: val });
      setStrategy(val);
      notify({type:'success',message:'Strategy saved (recalculation running in background)'});
    } catch(e){notify({type:'error',message:e.message});}
  };

  const submitGmailSync = async () => {
    const serialized = JSON.stringify(gmailSync);
    if (serialized === lastSavedGmail.current) return;
    try {
      const payload = { ...gmailSync };
      // keep existing token if we didn't change it but placeholder was shown
      if (!payload.webhook_token && gmailPlaceholders.webhook_token) {
        const existing = await api.get('/settings/gmail-sync');
        payload.webhook_token = existing.webhook_token || '';
      }
      await api.post('/settings/gmail-sync', payload);
      notify({type:'success',message:'Gmail sync settings saved'});
      lastSavedGmail.current = serialized;
      loadGmailSync();
    } catch(e){notify({type:'error',message:e.message});}
  };

  const syncNow = async () => {
    try { await fetch('/api/gmail/sync-now',{method:'POST'}); notify({type:'success',message:'Sync started'}); } catch(e){notify({type:'error',message:e.message});}
  };

  const renewWatch = async () => {
    try {
      const res = await fetch('/api/gmail/watch/renew',{method:'POST'});
      const data = await res.json();
      notify({type:'success',message:`Watch renewed for ${data.renewed||0} inbox(es)`});
    } catch(e){notify({type:'error',message:e.message});}
  };

  const provider = form.email_provider;

  // auto-save when form changes, after initial load
  useEffect(() => {
    if (isInitialLoad.current) {
      isInitialLoad.current = false;
      return;
    }
    const serialized = JSON.stringify(form);
    if (serialized === lastSavedForm.current) {
      return; // nothing to save
    }
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveSettingsNow();
    }, 800);
  }, [form]);

  // auto-save gmail sync when changed
  useEffect(() => {
    // skip initial
    if (isInitialLoad.current) return;
    const serialized = JSON.stringify(gmailSync);
    if (serialized === lastSavedGmail.current) return;
    clearTimeout(gmailTimer.current);
    gmailTimer.current = setTimeout(() => {
      submitGmailSync();
    }, 800);
  }, [gmailSync]);

  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-4">Settings</h1>
      <form onSubmit={submitSettings} className="space-y-6" autoComplete="off">
        <div className="card p-4">
          <h2 className="text-lg font-semibold mb-2">General</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium">Base URL *</label>
              <input name="base_url" value={form.base_url||''} onChange={handleChange} required className="mt-1 block w-full border-gray-300 rounded" />
              <p className="text-xs text-gray-500 mt-1">Used for OAuth redirects and email links</p>
            </div>
            <div>
              <label className="block text-sm font-medium">Queue Check Interval (minutes) *</label>
              <input type="number" name="queue_check_interval_minutes" value={form.queue_check_interval_minutes||1} onChange={handleChange} min={1} max={60} required className="mt-1 block w-24 border-gray-300 rounded" />
              <p className="text-xs text-gray-500 mt-1">How often the background job checks for emails to send</p>
            </div>
            <div className="flex items-center gap-2">
              <input type="checkbox" name="test_mode" checked={form.test_mode||false} onChange={handleChange} />
              <span>Test Mode</span>
            </div>
            <p className="text-xs text-gray-500">Redirect emails to test addresses or simulate Gmail without sending to real recipients</p>

            <div className="flex items-center gap-2 mt-4">
              <button type="button" className="btn secondary text-sm" onClick={toggleDarkMode}>
                {darkMode ? 'Light Mode' : 'Dark Mode'}
              </button>
            </div>
          </div>
        </div>

        <div className="card p-4">
          <h2 className="text-lg font-semibold mb-2">Email Provider</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium">Provider *</label>
              <select name="email_provider" value={provider||'resend'} onChange={handleChange} className="mt-1 block w-full border-gray-300 rounded">
                <option value="resend">Resend API</option>
                <option value="smtp">SMTP</option>
                <option value="gmail">Gmail OAuth</option>
              </select>
            </div>
            {provider==='resend' && (
              <div>
                <label className="block text-sm font-medium">Resend API Key</label>
                <input
                  name="resend_api_key"
                  value={form.resend_api_key||''}
                  onChange={handleChange}
                  className="mt-1 block w-full border-gray-300 rounded"
                  placeholder={placeholders.resend_api_key || 're_...'}
                  autoComplete="off"
                />
                {placeholders.resend_api_key && (
                  <p className="text-xs text-gray-500 mt-1">{placeholders.resend_api_key}</p>
                )}
                <p className="text-xs text-gray-500 mt-1">Get your API key from <a href="https://resend.com/api-keys" className="text-teal-500 underline" target="_blank" rel="noopener">resend.com/api-keys</a></p>
              </div>
            )}
            {provider==='smtp' && (
              <>
                <div>
                  <label className="block text-sm font-medium">SMTP Host *</label>
                  <input name="smtp_host" value={form.smtp_host||''} onChange={handleChange} className="mt-1 block w-full border-gray-300 rounded" required />
                </div>
                <div>
                  <label className="block text-sm font-medium">SMTP Port *</label>
                  <input type="number" name="smtp_port" value={form.smtp_port||587} onChange={handleChange} className="mt-1 block w-24 border-gray-300 rounded" required />
                </div>
                <div>
                  <label className="block text-sm font-medium">SMTP Username</label>
                  <input name="smtp_user" value={form.smtp_user||''} onChange={handleChange} className="mt-1 block w-full border-gray-300 rounded" />
                </div>
                <div>
                  <label className="block text-sm font-medium">SMTP Password</label>
                  <input
                  type="password"
                  name="smtp_password"
                  value={form.smtp_password||''}
                  onChange={handleChange}
                  className="mt-1 block w-full border-gray-300 rounded"
                  placeholder={placeholders.smtp_password || ''}
                  autoComplete="new-password"
                />
                {placeholders.smtp_password && (
                  <p className="text-xs text-gray-500 mt-1">{placeholders.smtp_password}</p>
                )}
                </div>
                <div className="flex items-center gap-2">
                  <input type="checkbox" name="smtp_use_tls" checked={form.smtp_use_tls||false} onChange={handleChange} />
                  <span>Use TLS</span>
                </div>
              </>
            )}
            {provider==='gmail' && (
              <>
                <div>
                  <label className="block text-sm font-medium">Google Client ID</label>
                  <input
                  name="google_client_id"
                  value={form.google_client_id||''}
                  onChange={handleChange}
                  className="mt-1 block w-full border-gray-300 rounded"
                  placeholder={placeholders.google_client_id || '...apps.googleusercontent.com'}
                />
                {placeholders.google_client_id && (
                  <p className="text-xs text-gray-500 mt-1">{placeholders.google_client_id}</p>
                )}
                  <p className="text-xs text-gray-500 mt-1">From Google Cloud Console OAuth 2.0 credentials</p>
                </div>
                <div>
                  <label className="block text-sm font-medium">Google Client Secret</label>
                  <input
                    type="password"
                    name="google_client_secret"
                    value={form.google_client_secret||''}
                    onChange={handleChange}
                    className="mt-1 block w-full border-gray-300 rounded"
                    placeholder={placeholders.google_client_secret || ''}
                    autoComplete="new-password"
                  />
                  {placeholders.google_client_secret && (
                    <p className="text-xs text-gray-500 mt-1">{placeholders.google_client_secret}</p>
                  )}
                </div>
                {!oauthStatus.configured && (
                  <div className="mt-2 p-3 bg-blue-50 border border-blue-200 rounded text-sm">
                    <p>OAuth not configured. Follow setup instructions below.</p>
                    <p>Redirect URI: <code className="font-mono bg-gray-100 p-1 rounded">{oauthStatus.redirect_uri || 'http://localhost:8000/oauth/google/callback'}</code></p>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div className="flex gap-3">
          <button type="submit" className="btn">Save Settings</button>
          <button type="button" className="btn secondary" onClick={loadSettings}>Reload</button>
        </div>
      </form>

      {/* scheduling strategy */}
      <div className="card p-4 mt-6">
        <h2 className="text-lg font-semibold mb-2">Scheduling Strategy</h2>
        <p className="text-xs text-gray-500 mb-2">Controls how ⚡ Recalculate All Campaigns distributes emails across campaigns.</p>
        <div className="space-y-3">
          <label className="flex gap-2 items-start cursor-pointer">
            <input type="radio" name="strategy" value="priority" checked={strategy==='priority'} onChange={()=>submitStrategy('priority')} />
            <span>
              <strong>Priority by campaign</strong><br/>
              <span className="text-xs text-gray-500">
                Campaigns are processed in ascending priority order — #1 gets inbox capacity first. <a href="/campaigns" className="text-teal-500 underline">Go to Campaigns</a> to reorder.
              </span>
            </span>
          </label>
          <label className="flex gap-2 items-start cursor-pointer">
            <input type="radio" name="strategy" value="round_robin" checked={strategy==='round_robin'} onChange={()=>submitStrategy('round_robin')} />
            <span>
              <strong>Round-robin distribution</strong><br/>
              <span className="text-xs text-gray-500">
                Inbox capacity is divided evenly across active campaigns; leads are scheduled in batches.
              </span>
            </span>
          </label>
        </div>
      </div>

      {/* gmail sync */}
      <div className="card p-4 mt-6">
        <h2 className="text-lg font-semibold mb-2">Gmail Reply Detection</h2>
        <p className="text-xs text-gray-500 mb-2">Enable near-real-time reply detection via Gmail Push + periodic fallback sync.</p>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium">Google Pub/Sub Topic (optional)</label>
            <input className="mt-1 block w-full border-gray-300 rounded" value={gmailSync.push_topic} onChange={e=>setGmailSync(g=>({...g,push_topic:e.target.value}))} />
            <p className="text-xs text-gray-500 mt-1">If set, watch renewal runs automatically and push webhook can trigger immediate sync.</p>
          </div>
          <div>
            <label className="block text-sm font-medium">Webhook Token (optional)</label>
            <input
              type="password"
              className="mt-1 block w-full border-gray-300 rounded"
              value={gmailSync.webhook_token}
              onChange={e=>setGmailSync(g=>({...g,webhook_token:e.target.value}))}
              placeholder={gmailPlaceholders.webhook_token || ''}
              autoComplete="new-password"
            />
            {gmailPlaceholders.webhook_token && (
              <p className="text-xs text-gray-500">{gmailPlaceholders.webhook_token}</p>
            )}
            <p className="text-xs text-gray-500">If set, webhook calls must include ?token=... or X-Gmail-Webhook-Token header.</p>
          </div>
          <div>
            <label className="block text-sm font-medium">Fallback Sync Interval (minutes)</label>
            <input type="number" className="mt-1 block w-24 border-gray-300 rounded" value={gmailSync.sync_interval_minutes} min={1} max={60} onChange={e=>setGmailSync(g=>({...g,sync_interval_minutes:+e.target.value}))} />
            <p className="text-xs text-gray-500">Background polling fallback interval. Takes effect after restart.</p>
          </div>
          <div className="flex gap-2 flex-wrap">
            <button className="btn secondary" onClick={submitGmailSync}>Save Gmail Sync</button>
            <button className="btn secondary" onClick={syncNow}>Run Sync Now</button>
            <button className="btn secondary" onClick={renewWatch}>Renew Watch Now</button>
          </div>
        </div>
      </div>

      {/* setup instructions placeholder */}
      {!oauthStatus.configured && provider==='gmail' && (
        <div className="card p-4 mt-6">
          <h2 className="text-lg font-semibold mb-2">Setup Instructions — Google Cloud Console</h2>
          <p className="text-xs text-gray-500 mb-2">Follow instructions in legacy settings template.</p>
          <p className="text-xs"><code className="font-mono bg-gray-100 p-1 rounded">{oauthStatus.redirect_uri || 'http://localhost:8000/oauth/google/callback'}</code></p>
        </div>
      )}
    </div>
  );
}
