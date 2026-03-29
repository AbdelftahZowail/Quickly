import { useState, useEffect, useRef } from 'react';
import { api, apiCache } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';

/** Backend stores jitter in seconds (cap 600). Forms show minutes and convert on change / save. */
const JITTER_MAX_MINUTES = 10;

/** Full Beacon setup guide in the Quickly repo (INSTALL.md). */
const BEACON_SETUP_DOCS_URL =
  'https://github.com/AbdelftahZowail/Quickly/blob/main/docs/INSTALL.md#quickly-beacon-recommended-custom-tracking-hostnames';

function CollapsibleInfo({ children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-xs font-medium text-gray-600 hover:text-gray-900 flex items-center gap-1.5 text-left"
        aria-expanded={open}
      >
        <svg
          className={`w-3.5 h-3.5 shrink-0 text-gray-500 transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        {open ? 'Hide info' : 'Show info'}
      </button>
      {open && (
        <div className="mt-2 pl-1 space-y-2 text-xs text-gray-600 border-l-2 border-gray-200 ml-0.5 py-0.5">
          {children}
        </div>
      )}
    </div>
  );
}

function RedirectUriBlock({ uri, size = 'xs' }) {
  if (!uri) return null;
  const textCls = size === 'sm' ? 'text-sm' : 'text-xs';
  const codeCls = size === 'sm' ? 'text-xs' : 'text-[11px]';
  return (
    <div className={`text-gray-500 ${textCls} mt-1 min-w-0 w-full max-w-full`}>
      <span className="block text-[10px] font-medium uppercase tracking-wide text-gray-400 mb-0.5">Redirect URI</span>
      <code
        className={`block font-mono ${codeCls} leading-snug break-all whitespace-pre-wrap bg-gray-50 rounded px-1.5 py-1 border border-gray-100 max-w-full`}
        title={uri}
      >
        {uri}
      </code>
    </div>
  );
}

function clampJitterSeconds(s) {
  const n = Number(s);
  if (!Number.isFinite(n)) return 0;
  return Math.min(600, Math.max(0, Math.round(n)));
}

function jitterInputMinutesFromSeconds(sec) {
  return clampJitterSeconds(sec ?? 0) / 60;
}

function jitterSecondsFromInputMinutes(minVal) {
  const v = parseFloat(minVal);
  if (!Number.isFinite(v) || v < 0) return 0;
  return clampJitterSeconds(Math.min(JITTER_MAX_MINUTES, v) * 60);
}

function formatJitterMinutesLabel(seconds) {
  const s = clampJitterSeconds(seconds ?? 0);
  if (s <= 0) return null;
  const min = s / 60;
  const t = Number.isInteger(min) ? String(min) : (Math.round(min * 10) / 10).toString();
  return `up to ${t} min random`;
}

/**
 * Tracking: three choices — app URL, Beacon (setup URL + Connect), or DNS setup (CNAME + Verify).
 * Long-form help is behind “Show info” for each option.
 */
function InboxTrackingOptions({
  variant,
  wrapClassName = 'space-y-4',
  radioName,
  cnameUiEnabled,
  cnameTarget,
  uiMode,
  onUiModeChange,
  trackingDomain,
  onTrackingDomainChange,
  onDnsVerifyChange,
  beaconConnected,
  beaconBaseUrl,
  beaconSetupUrl,
  onBeaconSetupUrlChange,
  onConnectBeacon,
  onDisconnectBeacon,
  beaconConnecting,
}) {
  const hostHint = cnameTarget || (typeof window !== 'undefined' ? window.location.hostname : '');
  const dnsActive = uiMode === 'dns';
  const beaconActive = uiMode === 'beacon';
  const [verifyState, setVerifyState] = useState(null);
  const [verifyMsg, setVerifyMsg] = useState('');
  const abortRef = useRef(false);

  const handleDnsValue = (v) => {
    abortRef.current = true;
    setVerifyState(null);
    setVerifyMsg('');
    onDnsVerifyChange?.(false);
    onTrackingDomainChange(v);
  };

  const verifyDns = async () => {
    const domain = (trackingDomain || '').trim();
    if (!domain) return;
    abortRef.current = false;
    setVerifyState('checking');
    setVerifyMsg('Registering domain…');
    try {
      await api.post('/settings/register-tracking-domain-pending', { domain });
    } catch (_) { /* non-fatal */ }
    if (abortRef.current) return;
    const MAX_ATTEMPTS = 5;
    const RETRY_DELAY_MS = 8000;
    let lastError = 'Timed out waiting for SSL certificate to be provisioned';
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      if (abortRef.current) return;
      setVerifyMsg(attempt === 0 ? 'Provisioning SSL certificate…' : `Waiting for SSL certificate… (attempt ${attempt + 1}/${MAX_ATTEMPTS})`);
      try {
        const data = await api.get(
          `/settings/verify-tracking-domain?domain=${encodeURIComponent(domain)}`
        );
        if (abortRef.current) return;
        if (data.ok) {
          setVerifyState('ok');
          setVerifyMsg('');
          onDnsVerifyChange?.(true);
          return;
        }
        lastError = data.error || 'Unknown error';
      } catch (e) {
        if (abortRef.current) return;
        lastError = e.message;
      }
      if (attempt < MAX_ATTEMPTS - 1) {
        await new Promise(r => setTimeout(r, RETRY_DELAY_MS));
      }
    }
    if (!abortRef.current) {
      setVerifyState({ error: lastError });
      setVerifyMsg('');
      onDnsVerifyChange?.(false);
    }
  };

  useEffect(() => {
    if (uiMode !== 'dns') {
      abortRef.current = true;
      setVerifyState(null);
      setVerifyMsg('');
    }
  }, [uiMode]);

  const dnsInputDisabled = !dnsActive;
  const beaconInputDisabled = variant === 'add' || !beaconActive || beaconConnected;
  const canVerifyDns = dnsActive && (trackingDomain || '').trim().length > 0;

  return (
    <div className={`${wrapClassName} min-w-0 max-w-full`}>
      {/* Use app domain */}
      <div className="space-y-2 min-w-0 max-w-full">
        <label className="flex items-start gap-2 cursor-pointer text-sm">
          <input
            type="radio"
            name={radioName}
            className="mt-0.5 shrink-0"
            checked={uiMode === 'app'}
            onChange={() => onUiModeChange('app')}
          />
          <span className="font-medium text-gray-800">Use app domain</span>
        </label>
        <div className="ml-6 min-w-0 max-w-full border-l-2 border-gray-200 pl-3 py-0.5">
          <CollapsibleInfo>
            <p>
              Open, click, and unsubscribe links use your Quickly app URL{' '}
              <span className="text-gray-500 font-mono break-all">({hostHint})</span>. No Beacon service and no extra DNS records are required.
            </p>
          </CollapsibleInfo>
        </div>
      </div>

      {/* Beacon */}
      <div className="space-y-2 min-w-0 max-w-full">
        <label className="flex items-start gap-2 cursor-pointer text-sm min-w-0">
          <input
            type="radio"
            name={radioName}
            className="mt-0.5 shrink-0"
            checked={uiMode === 'beacon'}
            onChange={() => onUiModeChange('beacon')}
          />
          <span className="font-medium text-gray-800">Beacon (Recommended)</span>
        </label>
        <div className="ml-6 min-w-0 max-w-full space-y-2 border-l-2 border-gray-200 pl-3 py-0.5 box-border">
          <div className="w-full min-w-0 max-w-full space-y-2">
            {beaconConnected ? (
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between w-full min-w-0">
                <p className="text-xs text-gray-700 min-w-0 break-words [overflow-wrap:anywhere]">
                  Connected: <code className="text-teal-800 break-all">{beaconBaseUrl || ''}</code>
                </p>
                <Button type="button" size="sm" variant="outline" className="shrink-0 self-start" onClick={onDisconnectBeacon}>
                  Disconnect
                </Button>
              </div>
            ) : (
              <div className="flex flex-col gap-2 w-full min-w-0 max-w-full">
                <input
                  type="url"
                  disabled={beaconInputDisabled}
                  className="w-full min-w-0 max-w-full box-border border rounded px-2 py-1.5 font-mono text-xs bg-white disabled:bg-gray-100 disabled:text-gray-500"
                  placeholder="https://track.example.com/?token=…"
                  value={beaconSetupUrl}
                  onChange={e => onBeaconSetupUrlChange(e.target.value)}
                />
                <button
                  type="button"
                  disabled={beaconInputDisabled || !beaconSetupUrl.trim() || beaconConnecting}
                  onClick={onConnectBeacon}
                  className="self-start shrink-0 px-2 py-1 text-xs rounded border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  {beaconConnecting ? 'Connecting…' : 'Connect'}
                </button>
              </div>
            )}
          </div>
          {variant === 'add' && (
            <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded px-2 py-1">
              Connect is available after the inbox exists — finish adding the inbox, then open <strong>Edit</strong> and choose Beacon (Recommended) here.
            </p>
          )}
          <CollapsibleInfo>
            <p className="font-medium text-gray-700">How Beacon works</p>
            <p>
              Run the Beacon service on the HTTPS hostname you want for tracking links. While Quickly is not connected yet, open Beacon&apos;s root URL in a browser and copy the <strong>setup URL</strong> (it includes{' '}
              <code className="bg-gray-100 px-0.5 rounded">?token=</code>
              ). Paste it above and click Connect. On Beacon, set{' '}
              <code className="bg-gray-100 px-0.5 rounded">BEACON_PUBLIC_BASE_URL</code>
              {' '}if printed links should use a specific public origin.
            </p>
            <p>
              <a
                href={BEACON_SETUP_DOCS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-700 hover:underline break-all"
              >
                Full setup guide (Quickly repo → INSTALL.md)
              </a>
            </p>
          </CollapsibleInfo>
        </div>
      </div>

      {/* DNS setup */}
      {cnameUiEnabled && (
        <div className="space-y-2 min-w-0 max-w-full">
          <label className="flex items-start gap-2 cursor-pointer text-sm min-w-0">
            <input
              type="radio"
              name={radioName}
              className="mt-0.5 shrink-0"
              checked={dnsActive}
              onChange={() => onUiModeChange('dns')}
            />
            <span className="font-medium text-gray-800">DNS setup</span>
          </label>
          <div className="ml-6 min-w-0 max-w-full space-y-2 border-l-2 border-gray-200 pl-3 py-0.5 box-border">
            <div className="flex flex-col gap-2 w-full min-w-0 max-w-full">
              <input
                type="text"
                disabled={dnsInputDisabled}
                className="w-full min-w-0 max-w-full box-border border rounded px-2 py-1.5 font-mono text-sm bg-white disabled:bg-gray-100 disabled:text-gray-500"
                placeholder="mail.yourdomain.com"
                value={trackingDomain || ''}
                onChange={e => handleDnsValue(e.target.value)}
                onFocus={() => {
                  if (!dnsActive) onUiModeChange('dns');
                }}
              />
              <button
                type="button"
                disabled={!canVerifyDns || verifyState === 'checking'}
                onClick={verifyDns}
                className="self-start shrink-0 px-2 py-1 text-xs rounded border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-50"
              >
                {verifyState === 'checking' ? 'Verifying…' : 'Verify'}
              </button>
            </div>
            {verifyState === 'checking' && verifyMsg && (
              <p className="text-xs text-blue-600 flex items-center gap-1">
                <span className="inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
                {verifyMsg}
              </p>
            )}
            {verifyState === 'ok' && (
              <p className="text-xs text-green-600 font-medium">✓ Domain is reachable and pointing to this server</p>
            )}
            {verifyState && verifyState !== 'checking' && verifyState !== 'ok' && (
              <p className="text-xs text-red-600">✗ {verifyState.error}</p>
            )}
            <CollapsibleInfo>
              <p className="font-medium text-gray-700">DNS setup (CNAME)</p>
              <p>Add a <code>CNAME</code> at your DNS host pointing your tracking hostname at this Quickly server:</p>
              <pre className="bg-white border rounded p-2 overflow-x-auto whitespace-pre-wrap break-all text-gray-700 text-[11px]">
                {`${(trackingDomain || '').trim() || 'mail.yourdomain.com'}  CNAME  ${cnameTarget || 'your-app-host'}.`}
              </pre>
              <p>
                Caddy requests a certificate on the first HTTPS hit. Select <strong>DNS setup</strong>, enter the hostname, run <strong>Verify</strong>, then save the inbox.
              </p>
            </CollapsibleInfo>
          </div>
        </div>
      )}

      {!cnameUiEnabled && (trackingDomain || '').trim() && !beaconConnected && (
        <div className="text-xs text-amber-800 space-y-2 bg-amber-50 border border-amber-200 rounded p-2">
          <p>
            Legacy CNAME tracking domain saved:{' '}
            <code className="font-mono">{(trackingDomain || '').trim()}</code>. Prefer Beacon for new setups.
          </p>
          <button
            type="button"
            className="text-xs px-2 py-1 rounded border border-amber-300 bg-white hover:bg-amber-100"
            onClick={() => { onTrackingDomainChange(''); onDnsVerifyChange?.(false); }}
          >
            Clear saved domain
          </button>
        </div>
      )}
    </div>
  );
}

export default function Inboxes() {
  const [inboxes, setInboxes] = useState(() => apiCache.get('/inboxes') || []);
  const [cnameTarget, setCnameTarget] = useState('');
  const [customTrackingCnameUiEnabled, setCustomTrackingCnameUiEnabled] = useState(true);
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
    ramp_up_start: 1,
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
  const [editWarningCloseSidebar, setEditWarningCloseSidebar] = useState(false);
  const [editMsg, setEditMsg] = useState(null);
  // Tracks the saved tracking domain at the moment editing opened, so we only
  // require re-verification when the user actually changes the domain.
  const editOriginalDomain = useRef('');
  const [editDomainVerified, setEditDomainVerified] = useState(false);
  const [beaconSetupUrl, setBeaconSetupUrl] = useState('');
  const [beaconConnecting, setBeaconConnecting] = useState(false);
  const [editTrackingMode, setEditTrackingMode] = useState('app');
  const [addTrackingMode, setAddTrackingMode] = useState('app');
  const [addDomainVerified, setAddDomainVerified] = useState(false);
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
      .then((d) => {
        setCnameTarget(d.cname_target || window.location.hostname);
        if (typeof d.custom_tracking_cname_ui_enabled === 'boolean') {
          setCustomTrackingCnameUiEnabled(d.custom_tracking_cname_ui_enabled);
        }
      })
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
      const params = new URLSearchParams({ display_name: form.display_name, max_per_day: form.max_emails_per_day, ramp_up_enabled: form.ramp_up_enabled ? 'true' : 'false', ramp_up_start: form.ramp_up_start });
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
      const params = new URLSearchParams({ display_name: form.display_name, max_per_day: form.max_emails_per_day, ramp_up_enabled: form.ramp_up_enabled ? 'true' : 'false', ramp_up_start: form.ramp_up_start });
      window.location.href = '/oauth/office365/authorize?' + params;
      return;
    }
    const addDomain = addTrackingMode === 'dns' ? form.tracking_domain.trim() : '';
    if (addDomain && !addDomainVerified) {
      setMessage({ type: 'error', text: 'Please verify the DNS tracking domain before saving.' });
      return;
    }
    try {
      await api.post('/inboxes', {
        ...form,
        tracking_domain: addDomain || null,
      });
      setMessage({ type: 'success', text: 'Inbox added' });
      setForm(initialForm);
      setAddTrackingMode('app');
      setAddDomainVerified(false);
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
    editOriginalDomain.current = inbox.tracking_domain || '';
    setEditDomainVerified(false);
    setBeaconSetupUrl('');
    setEditTrackingMode(
      inbox.beacon_connected
        ? 'beacon'
        : (customTrackingCnameUiEnabled && (inbox.tracking_domain || '').trim() ? 'dns' : 'app'),
    );
  };
  const closeEdit = () => {
    setEditing(null);
    setEditDirty(false);
  };
  const tryCloseEdit = () => {
    if (editDirty) {
      setEditWarningCloseSidebar(false);
      setShowEditWarning(true);
    } else {
      closeEdit();
    }
  };
  const tryCloseSidebar = () => {
    if (editing && editDirty) {
      setEditWarningCloseSidebar(true);
      setShowEditWarning(true);
    } else {
      closeEdit();
      setSelectedInbox(null);
    }
  };
  const applyEditTrackingMode = (mode) => {
    if (!editing) return;
    if (editing.beacon_connected && mode !== 'beacon') {
      setEditMsg({ type: 'error', text: 'Disconnect Beacon before choosing another tracking option.' });
      return;
    }
    setEditMsg(null);
    setEditTrackingMode(mode);
    if (mode === 'app' || mode === 'beacon') {
      setEditing(prev => ({ ...prev, tracking_domain: '' }));
      setEditDomainVerified(false);
    }
    setEditDirty(true);
  };

  const applyAddTrackingMode = (mode) => {
    setAddTrackingMode(mode);
    if (mode === 'app' || mode === 'beacon') {
      setForm(f => ({ ...f, tracking_domain: '' }));
      setAddDomainVerified(false);
    }
  };

  const doSave = async () => {
    if (!editing) return;
    const newDomain = editTrackingMode === 'dns' ? (editing.tracking_domain || '').trim() : '';
    const domainChanged = newDomain !== editOriginalDomain.current;
    if (editTrackingMode === 'dns' && newDomain && domainChanged && !editDomainVerified) {
      setEditMsg({ type: 'error', text: 'Please verify the DNS tracking domain before saving.' });
      return;
    }
    setEditDirty(false); // save in progress — don't treat as unsaved
    try {
      const body = {
        display_name: editing.display_name,
        provider: editing.provider,
        max_emails_per_day: editing.max_emails_per_day,
        wait_minutes_between: editing.wait_minutes_between,
        max_jitter_seconds: clampJitterSeconds(editing.max_jitter_seconds ?? 180),
        tracking_domain: newDomain || null,
        ramp_up_enabled: editing.ramp_up_enabled,
        ramp_up_period_days: editing.ramp_up_period_days,
        ramp_up_start: editing.ramp_up_start ?? 1,
      };
      await api.patch(`/inboxes/${editing.id}`, body);
      setEditMsg({ type: 'success', text: 'Inbox updated' });
      setTimeout(() => { closeEdit(); load(); }, 1000);
    } catch (err) {
      setEditMsg({ type: 'error', text: err.message });
    }
  };
  const saveEdit = async (e) => {
    e.preventDefault();
    await doSave();
  };

  const connectBeacon = async () => {
    if (!editing) return;
    const url = beaconSetupUrl.trim();
    if (!url) {
      setEditMsg({ type: 'error', text: 'Paste the full Beacon setup URL (includes ?token=…).' });
      return;
    }
    setBeaconConnecting(true);
    try {
      await api.post(`/inboxes/${editing.id}/beacon/connect`, { setup_url: url });
      setEditMsg({ type: 'success', text: 'Beacon connected. Custom Caddy tracking domain was cleared.' });
      setBeaconSetupUrl('');
      setEditTrackingMode('beacon');
      const data = await api.get('/inboxes');
      setInboxes(data);
      const fresh = data.find(i => i.id === editing.id);
      if (fresh) {
        setEditing({ ...fresh });
        setSelectedInbox(prev => (prev?.id === fresh.id ? fresh : prev));
      }
    } catch (err) {
      setEditMsg({ type: 'error', text: err.message });
    } finally {
      setBeaconConnecting(false);
    }
  };

  const disconnectBeacon = async () => {
    if (!editing) return;
    const ok = await confirm(
      customTrackingCnameUiEnabled
        ? 'Disconnect Beacon? Tracking links will use the app domain or a custom CNAME domain again.'
        : 'Disconnect Beacon? Tracking links will use the app domain until you connect Beacon again or clear any legacy domain.',
    );
    if (!ok) return;
    try {
      await api.post(`/inboxes/${editing.id}/beacon/disconnect`);
      setEditMsg({ type: 'success', text: 'Beacon disconnected.' });
      setEditTrackingMode('app');
      const data = await api.get('/inboxes');
      setInboxes(data);
      const fresh = data.find(i => i.id === editing.id);
      if (fresh) {
        setEditing({ ...fresh });
        setSelectedInbox(prev => (prev?.id === fresh.id ? fresh : prev));
      }
    } catch (err) {
      setEditMsg({ type: 'error', text: err.message });
    }
  };

  // Escape to close modals
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      if (showEditWarning) { setShowEditWarning(false); }
      else if (showAdd) { setShowAdd(false); setMessage(null); setAddTrackingMode('app'); }
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

  const reconnectInbox = (inbox) => {
    const params = new URLSearchParams({
      display_name: inbox.display_name || '',
      max_per_day: inbox.max_emails_per_day,
      ramp_up_enabled: inbox.ramp_up_enabled ? 'true' : 'false',
    });
    if (inbox.provider === 'gmail') {
      window.location.href = '/oauth/google/authorize?' + params;
    } else if (inbox.provider === 'office365') {
      window.location.href = '/oauth/office365/authorize?' + params;
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-8">
      {/* header with add button */}
      <div className="flex justify-between items-center mb-4">
        <h1 className="text-2xl font-bold">Inboxes</h1>
        <Button variant="default" onClick={() => { setForm(initialForm); setAddTrackingMode('app'); setMessage(null); setShowAdd(true); }}>
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
                  onClick={() => { if (isSelected) { tryCloseSidebar(); } else { setSelectedInbox(inbox); } }}
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
            <div className="w-[min(28rem,calc(100vw-2.5rem))] shrink-0 bg-white border border-gray-200 rounded-xl shadow-sm flex flex-col overflow-hidden sticky top-4" style={{ maxHeight: 'calc(100vh - 8rem)' }}>
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
                  <div className="px-5 py-4 overflow-y-auto flex-1 min-w-0">
                    {editMsg && <div className={`mb-3 text-sm ${editMsg.type === 'error' ? 'text-red-600' : 'text-green-600'}`}>{editMsg.text}</div>}
                    <form onSubmit={saveEdit} className="space-y-4 min-w-0 max-w-full">
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
                      {editing.provider === 'gmail' && <RedirectUriBlock uri={redirectUri} />}
                      {editing.provider === 'office365' && <RedirectUriBlock uri={o365RedirectUri} />}
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Max emails per day</label>
                        <input type="number" name="max_emails_per_day" value={editing.max_emails_per_day} onChange={e => { setEditing(prev => ({ ...prev, max_emails_per_day: +e.target.value })); setEditDirty(true); }} min={1} max={1000} className="mt-1 block w-full border-gray-300 rounded-md text-sm" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Wait between emails (minutes)</label>
                        <input type="number" name="wait_minutes_between" value={editing.wait_minutes_between || 5} onChange={e => { setEditing(prev => ({ ...prev, wait_minutes_between: +e.target.value })); setEditDirty(true); }} min={1} max={120} className="mt-1 block w-full border-gray-300 rounded-md text-sm" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Send time jitter (minutes)</label>
                        <input
                          type="number"
                          value={jitterInputMinutesFromSeconds(editing.max_jitter_seconds)}
                          onChange={e => { setEditing(prev => ({ ...prev, max_jitter_seconds: jitterSecondsFromInputMinutes(e.target.value) })); setEditDirty(true); }}
                          min={0}
                          max={JITTER_MAX_MINUTES}
                          step={0.5}
                          className="mt-1 block w-full border-gray-300 rounded-md text-sm"
                        />
                        <p className="mt-1 text-xs text-gray-400">Random 0–N minute delay per send (stored as seconds on the server). Set to 0 to disable.</p>
                      </div>
                      <div className="border rounded p-3 space-y-4 bg-gray-50 min-w-0 max-w-full overflow-hidden">
                        <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Tracking</p>
                        <InboxTrackingOptions
                          key={editing.id}
                          variant="edit"
                          wrapClassName="space-y-4"
                          radioName="inbox-tracking-edit"
                          cnameUiEnabled={customTrackingCnameUiEnabled}
                          cnameTarget={cnameTarget}
                          uiMode={editTrackingMode}
                          onUiModeChange={applyEditTrackingMode}
                          trackingDomain={editing.tracking_domain || ''}
                          onTrackingDomainChange={(val) => { setEditing(prev => ({ ...prev, tracking_domain: val })); setEditDirty(true); }}
                          onDnsVerifyChange={setEditDomainVerified}
                          beaconConnected={!!editing.beacon_connected}
                          beaconBaseUrl={editing.beacon_base_url}
                          beaconSetupUrl={beaconSetupUrl}
                          onBeaconSetupUrlChange={(v) => { setBeaconSetupUrl(v); setEditDirty(true); }}
                          onConnectBeacon={connectBeacon}
                          onDisconnectBeacon={disconnectBeacon}
                          beaconConnecting={beaconConnecting}
                        />
                        <div className="border-t border-gray-200 pt-3 space-y-2">
                          <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-gray-700">
                            <input
                              type="checkbox"
                              checked={!!editing.ramp_up_enabled}
                              onChange={e => { setEditing(prev => ({ ...prev, ramp_up_enabled: e.target.checked })); setEditDirty(true); }}
                            />
                            Enable inbox warm-up (ramp-up)
                          </label>
                          {editing.ramp_up_enabled && (
                            <div className="space-y-2">
                              <div>
                                <label className="block text-xs font-medium text-gray-700">Starting emails per day</label>
                                <input
                                  type="number"
                                  value={editing.ramp_up_start ?? 1}
                                  onChange={e => { setEditing(prev => ({ ...prev, ramp_up_start: Math.max(1, +e.target.value) })); setEditDirty(true); }}
                                  min={1}
                                  max={editing.max_emails_per_day}
                                  className="mt-1 block w-full border-gray-300 rounded-md text-sm"
                                />
                              </div>
                              <p className="text-xs text-gray-500">
                                Starts at {editing.ramp_up_start ?? 1} email{(editing.ramp_up_start ?? 1) !== 1 ? 's' : ''} on day one, adds 1 more each day, and turns off automatically once it reaches {editing.max_emails_per_day}.
                                Today's limit: <strong>{editing.effective_max_per_day ?? editing.ramp_up_start ?? 1}</strong> / {editing.max_emails_per_day}
                              </p>
                            </div>
                          )}
                        </div>
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
                          {formatJitterMinutesLabel(selectedInbox.max_jitter_seconds)
                            ?? <span className="text-gray-400">disabled</span>}
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
                            <span className="font-medium text-gray-900">{selectedInbox.max_emails_per_day - (selectedInbox.ramp_up_start || 1)} days</span>
                          </div>
                        </div>
                      </>
                    )}

                    <hr className="border-gray-100" />

                    {/* Tracking domain / Beacon */}
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-gray-600">Tracking</span>
                      <span className="font-mono text-xs text-right max-w-[180px] truncate">
                        {selectedInbox.beacon_connected && selectedInbox.beacon_base_url
                          ? <span className="text-indigo-700" title={selectedInbox.beacon_base_url}>Beacon</span>
                          : selectedInbox.tracking_domain
                            ? <span className="text-teal-700">{selectedInbox.tracking_domain}</span>
                            : <span className="text-gray-400">app default</span>}
                      </span>
                    </div>
                    {selectedInbox.beacon_connected && selectedInbox.beacon_base_url && (
                      <p className="text-xs text-gray-500 break-all">{selectedInbox.beacon_base_url}</p>
                    )}

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
                    {(selectedInbox.provider === 'gmail' || selectedInbox.provider === 'office365') && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => reconnectInbox(selectedInbox)}
                        title="Re-authenticate this inbox to refresh the OAuth login"
                      >
                        Reconnect login
                      </Button>
                    )}
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
          onClick={() => { if (addBackdropDown.current) { setShowAdd(false); setMessage(null); setAddTrackingMode('app'); } }}
        >
          <div data-darkreader-ignore className="p-6 rounded-xl shadow-lg w-full min-w-0 max-w-md max-h-[90vh] overflow-y-auto overflow-x-hidden mx-auto" style={{ backgroundColor: 'white' }} onClick={e => e.stopPropagation()}>
            <h2 className="text-xl font-semibold mb-2">Add Inbox</h2>
            {message && <div className={message.type === 'error' ? 'text-red-600' : 'text-green-600'}>{message.text}</div>}
            <form onSubmit={submit} className="space-y-4 min-w-0 max-w-full">
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
                <label className="block text-sm font-medium text-gray-700">Send time jitter (minutes)</label>
                <input
                  type="number"
                  value={jitterInputMinutesFromSeconds(form.max_jitter_seconds)}
                  onChange={e => setForm(f => ({ ...f, max_jitter_seconds: jitterSecondsFromInputMinutes(e.target.value) }))}
                  min={0}
                  max={JITTER_MAX_MINUTES}
                  step={0.5}
                  className="mt-1 block w-full border-gray-300 rounded-md"
                />
                <p className="mt-1 text-xs text-gray-400">Random 0–N minute delay per send (default 3 min). Set to 0 to disable.</p>
              </div>
              <div className="border rounded p-3 space-y-4 bg-gray-50 min-w-0 max-w-full overflow-hidden">
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Tracking</p>
                <InboxTrackingOptions
                  variant="add"
                  wrapClassName="space-y-4"
                  radioName="inbox-tracking-add"
                  cnameUiEnabled={customTrackingCnameUiEnabled}
                  cnameTarget={cnameTarget}
                  uiMode={addTrackingMode}
                  onUiModeChange={applyAddTrackingMode}
                  trackingDomain={form.tracking_domain}
                  onTrackingDomainChange={(val) => { setForm(f => ({ ...f, tracking_domain: val })); setAddDomainVerified(false); }}
                  onDnsVerifyChange={setAddDomainVerified}
                  beaconConnected={false}
                  beaconBaseUrl=""
                  beaconSetupUrl=""
                  onBeaconSetupUrlChange={() => {}}
                  onConnectBeacon={() => {}}
                  onDisconnectBeacon={() => {}}
                  beaconConnecting={false}
                />
                <div className="border-t border-gray-200 pt-3 space-y-2">
                  <label className="flex items-center gap-2 cursor-pointer text-sm font-medium text-gray-700">
                    <input
                      type="checkbox"
                      checked={!!form.ramp_up_enabled}
                      onChange={e => setForm(f => ({ ...f, ramp_up_enabled: e.target.checked }))}
                    />
                    Enable inbox warm-up (ramp-up)
                  </label>
                  {form.ramp_up_enabled && (
                    <div className="space-y-2">
                      <div>
                        <label className="block text-xs font-medium text-gray-700">Starting emails per day</label>
                        <input
                          type="number"
                          value={form.ramp_up_start}
                          onChange={e => setForm(f => ({ ...f, ramp_up_start: Math.max(1, +e.target.value) }))}
                          min={1}
                          max={form.max_emails_per_day}
                          className="mt-1 block w-full border-gray-300 rounded-md text-sm"
                        />
                      </div>
                      <p className="text-xs text-gray-500">
                        Starts at {form.ramp_up_start} email{form.ramp_up_start !== 1 ? 's' : ''} on day one, adds 1 more each day, and turns off automatically once it reaches {form.max_emails_per_day}.
                      </p>
                    </div>
                  )}
                </div>
              </div>
              {form.provider === 'gmail' && (
                <>
                  {!oauthConfigured && (
                    <div className="text-red-600">
                      Google OAuth credentials are not configured. Set the appropriate environment variables (e.g. in `.env`) and restart the server before reloading.
                    </div>
                  )}
                  <RedirectUriBlock uri={redirectUri} size="sm" />
                </>
              )}
              {form.provider === 'office365' && (
                <>
                  {!o365Configured && (
                    <div className="text-red-600">
                      Office 365 OAuth credentials are not configured. Set OFFICE365_CLIENT_ID, OFFICE365_CLIENT_SECRET, and OFFICE365_TENANT_ID in your environment and restart the server.
                    </div>
                  )}
                  <RedirectUriBlock uri={o365RedirectUri} size="sm" />
                </>
              )}
              <div className="flex gap-2">
                <Button type="submit" disabled={!canSubmit()} variant="default">
                  {form.provider === 'gmail' ? 'Connect with Google' : form.provider === 'office365' ? 'Connect with Microsoft' : 'Add inbox'}
                </Button>
                <Button type="button" variant="outline" onClick={() => { setShowAdd(false); setMessage(null); setAddTrackingMode('app'); }}>
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
            <h3 className="font-semibold text-gray-800 mb-1">Save changes?</h3>
            <p className="text-sm text-gray-500 mb-4">You have unsaved changes to this inbox.</p>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="outline" onClick={() => { setShowEditWarning(false); setEditWarningCloseSidebar(false); }}>Keep editing</Button>
              <Button size="sm" variant="destructive" onClick={() => { setShowEditWarning(false); closeEdit(); if (editWarningCloseSidebar) { setSelectedInbox(null); setEditWarningCloseSidebar(false); } }}>Discard</Button>
              <Button size="sm" variant="default" onClick={async () => { setShowEditWarning(false); setEditWarningCloseSidebar(false); await doSave(); }}>Save</Button>
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
