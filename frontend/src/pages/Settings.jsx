import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { api, apiCache } from '../api';
import { useDarkMode } from '../context/DarkModeContext';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';
import { useAppMode } from '../context/AppModeContext';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import EmailVerificationSettings from '../components/EmailVerificationSettings';

const SETTINGS_TABS = [
  { id: 'general', label: 'General' },
  { id: 'setup', label: 'Setup' },
  { id: 'features', label: 'Features' },
  { id: 'integrating', label: 'Integrating' },
  { id: 'dev', label: 'Dev' },
];

/** In-tab section anchors (DOM id = `settings-${id}`). */
const SECTIONS_BY_TAB = {
  general: [
    { id: 'scheduling', label: 'Scheduling' },
    { id: 'appearance', label: 'Appearance' },
    { id: 'account', label: 'Account & Security' },
    { id: 'known-ips', label: 'Known IPs' },
  ],
  setup: [
    { id: 'gmail-sync', label: 'Gmail sync' },
    { id: 'backup-restore', label: 'Backup & restore' },
  ],
  features: [
    { id: 'ai', label: 'AI features' },
    { id: 'other', label: 'Other' },
  ],
  integrating: [
    { id: 'api-keys', label: 'API keys' },
    { id: 'webhooks', label: 'Webhooks' },
    { id: 'mcp', label: 'MCP' },
  ],
  dev: [{ id: 'test-mode', label: 'Test mode' }],
};

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

export default function Settings() {
  const notify = useNotify();
  const { themePreference, setThemePreference } = useDarkMode();
  const confirm = useConfirm();
  const { isProduction } = useAppMode();
  const { user, logout } = useAuth();

  /* ── state ── */
  const [strategy, setStrategy] = useState('priority');
  const [testMode, setTestMode] = useState(false);

  // Account & Security
  // API Keys
  const [apiKeys, setApiKeys] = useState([]);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyExpiry, setNewKeyExpiry] = useState('');
  const [createdKey, setCreatedKey] = useState(null); // shown once after creation

  // MCP (Cursor / AI agents)
  const [mcpSetup, setMcpSetup] = useState(null);

  // Notifications
  const [notifConfig, setNotifConfig] = useState({ enabled: false, notification_email: '', events: [], rate_limit_per_hour: 10 });
  const [notifSaving, setNotifSaving] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const savedNotifRef = useRef(null); // snapshot of last-saved config

  // Webhooks (new CRUD system)
  const [webhooks, setWebhooks] = useState(() => apiCache.get('/settings/webhooks') || []);
  const [eventTypes, setEventTypes] = useState([]);
  const [newWh, setNewWh] = useState({ url: '', secret: '', description: '', events: [], active: true });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({});

  // AI settings — keyed by feature id
  const [aiFeatures, setAiFeatures] = useState({});
  const [aiExpanded, setAiExpanded] = useState({});           // { featureId: bool } — collapsed by default
  const savedAiFeaturesRef = useRef({});                      // snapshot of last-saved AI features
  const [aiProviders, setAiProviders] = useState(() => apiCache.get('/settings/ai/providers')?.providers || []);
  const [aiModels, setAiModels] = useState({});
  const [aiProviderSearch, setAiProviderSearch] = useState({});
  const [aiModelSearch, setAiModelSearch] = useState({});
  const [aiVerifying, setAiVerifying] = useState({});
  const [aiVerifyResult, setAiVerifyResult] = useState({});

  // Webhook test-event state
  const [testEventWh, setTestEventWh] = useState(null); // webhook id currently testing
  const [testEventType, setTestEventType] = useState('');
  const [testEventResult, setTestEventResult] = useState(null);

  // Gmail Sync
  const [gmailSync, setGmailSync] = useState({ push_topic: '', webhook_token: '', sync_interval_minutes: 5 });
  const [gmailSyncSaving, setGmailSyncSaving] = useState(false);
  const savedGmailSyncRef = useRef(null);

  // Backup & restore (admin — PostgreSQL)
  const [backupCfg, setBackupCfg] = useState({
    schedule_enabled: false,
    cron_expression: '0 3 * * *',
    save_local: false,
    local_relative_path: 'backups',
    send_webhook: false,
    webhook_url: '',
    webhook_auth_header: '',
  });
  const [backupMeta, setBackupMeta] = useState({
    webhook_auth_configured: false,
    webhook_auth_header_masked: '',
    local_disk_available: false,
    local_backup_resolved: null,
  });
  const [backupSaving, setBackupSaving] = useState(false);
  const [backupRunning, setBackupRunning] = useState(false);
  const [restoreFile, setRestoreFile] = useState(null);

  // Known IPs
  const [knownIps, setKnownIps] = useState(() => apiCache.get('/settings/known-ips')?.known_ips || []);
  const [knownIpsOpen, setKnownIpsOpen] = useState(false);
  const [currentIp, setCurrentIp] = useState('');
  const [newIpAddress, setNewIpAddress] = useState('');

  const [activeTab, setActiveTab] = useState('general');
  const tabContentRef = useRef(null);
  const [activeSectionDomId, setActiveSectionDomId] = useState('');

  /* ── load data ── */
  const loadAll = useCallback(async () => {
    try {
      const [stratData, tmData, whList, evtData, aiData, provData, ipData, keysData, notifData, gmailSyncData, mcpData] = await Promise.all([
        api.get('/settings/scheduling-strategy'),
        api.get('/settings/test-mode'),
        api.get('/settings/webhooks'),
        api.get('/settings/webhooks/events'),
        api.get('/settings/ai'),
        api.get('/settings/ai/providers'),
        api.get('/settings/known-ips'),
        api.get('/auth/api-keys'),
        api.get('/notifications/config').catch(() => null),
        api.get('/settings/gmail-sync').catch(() => null),
        api.get('/settings/mcp-setup').catch(() => null),
      ]);
      setStrategy(stratData.scheduling_strategy || 'priority');
      setTestMode(tmData.test_mode || false);
      setWebhooks(whList || []);
      setEventTypes(evtData.events || []);
      // default new webhook events = all
      setNewWh(prev => ({ ...prev, events: evtData.events || [] }));
      // Build per-feature map, clearing any unsaved api_key
      const featMap = {};
      for (const f of (aiData.features || [])) {
        featMap[f.id] = { ...f, api_key: '' };
      }
      setAiFeatures(featMap);
      savedAiFeaturesRef.current = featMap;
      setAiProviders(provData.providers || []);
      setKnownIps(ipData.known_ips || []);
      setCurrentIp(ipData.current_ip || '');
      setApiKeys(keysData || []);
      if (notifData) {
        setNotifConfig(notifData);
        savedNotifRef.current = notifData;
      }
      if (gmailSyncData) {
        const snap = {
          push_topic: gmailSyncData.push_topic || '',
          webhook_token: gmailSyncData.webhook_token || '',
          sync_interval_minutes: gmailSyncData.sync_interval_minutes ?? 5,
        };
        setGmailSync(snap);
        savedGmailSyncRef.current = snap;
      }
      if (user?.role === 'admin') {
        try {
          const b = await api.get('/settings/backup/config');
          const diskOn = !!b.local_disk_available;
          setBackupCfg({
            schedule_enabled: !!b.schedule_enabled,
            cron_expression: b.cron_expression || '0 3 * * *',
            save_local: diskOn && !!b.save_local,
            local_relative_path: b.local_relative_path || 'backups',
            send_webhook: !!b.send_webhook,
            webhook_url: b.webhook_url || '',
            webhook_auth_header: '',
          });
          setBackupMeta({
            webhook_auth_configured: !!b.webhook_auth_configured,
            webhook_auth_header_masked: b.webhook_auth_header_masked || '',
            local_disk_available: diskOn,
            local_backup_resolved: b.local_backup_resolved || null,
          });
        } catch {
          /* non-admin or unavailable */
        }
      }
      setMcpSetup(mcpData || null);
    } catch {}
  }, [user]);

  useEffect(() => { loadAll(); }, [loadAll]);

  const visibleTabs = useMemo(
    () => (isProduction ? SETTINGS_TABS.filter(t => t.id !== 'dev') : SETTINGS_TABS),
    [isProduction],
  );

  const sectionNav = SECTIONS_BY_TAB[activeTab] || [];

  useEffect(() => {
    const syncFromHash = () => {
      let raw = (window.location.hash || '').replace(/^#/, '');
      if (!raw) raw = 'general';
      const id = visibleTabs.some(t => t.id === raw) ? raw : 'general';
      setActiveTab(id);
      if (window.location.hash !== `#${id}`) {
        window.history.replaceState(null, '', `#${id}`);
      }
    };
    syncFromHash();
    window.addEventListener('hashchange', syncFromHash);
    return () => window.removeEventListener('hashchange', syncFromHash);
  }, [visibleTabs]);

  useEffect(() => {
    tabContentRef.current?.scrollTo(0, 0);
  }, [activeTab]);

  useEffect(() => {
    const ids = (SECTIONS_BY_TAB[activeTab] || []).map(s => `settings-${s.id}`);
    const root = tabContentRef.current;
    setActiveSectionDomId(ids[0] || '');
    if (!root || ids.length === 0) return;

    let raf = 0;
    const updateActiveFromScroll = () => {
      const rootRect = root.getBoundingClientRect();
      const margin = 20;
      let current = ids[0];
      for (const domId of ids) {
        const el = document.getElementById(domId);
        if (!el) continue;
        const top = el.getBoundingClientRect().top - rootRect.top;
        if (top <= margin) current = domId;
      }
      setActiveSectionDomId(prev => (prev === current ? prev : current));
    };

    const schedule = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        raf = 0;
        updateActiveFromScroll();
      });
    };

    root.addEventListener('scroll', schedule, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null;
    ro?.observe(root);
    schedule();

    return () => {
      root.removeEventListener('scroll', schedule);
      ro?.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [activeTab]);

  const selectTab = id => {
    if (!visibleTabs.some(t => t.id === id)) return;
    setActiveTab(id);
    window.history.replaceState(null, '', `#${id}`);
  };

  const scrollToSection = useCallback(sid => {
    const domId = `settings-${sid}`;
    setActiveSectionDomId(domId);
    const el = document.getElementById(domId);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  /* ── scheduling strategy ── */
  const submitStrategy = async val => {
    if (val !== strategy) {
      try {
        const { has_leads } = await api.get('/campaigns/has-leads');
        if (has_leads) {
          const ok = await confirm(
            'Changing the scheduling strategy will recalculate all campaigns. Continue?',
          );
          if (!ok) return;
        }
      } catch {}
    }
    try {
      await api.post('/settings/scheduling-strategy', { scheduling_strategy: val });
      setStrategy(val);
      notify({ type: 'success', message: 'Strategy saved' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  /* ── test mode ── */
  const submitTestMode = async val => {
    try {
      await api.post('/settings/test-mode', { test_mode: val });
      setTestMode(val);
      notify({ type: 'success', message: 'Test mode saved' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  /* ── webhook CRUD helpers ── */
  const createWebhook = async () => {
    if (!newWh.url.trim()) return notify({ type: 'error', message: 'URL is required' });
    try {
      const wh = await api.post('/settings/webhooks', newWh);
      setWebhooks(prev => [wh, ...prev]);
      setNewWh({ url: '', secret: '', description: '', events: eventTypes, active: true });
      notify({ type: 'success', message: 'Webhook created' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const startEdit = wh => {
    setEditingId(wh.id);
    setEditForm({ url: wh.url, secret: wh.secret, description: wh.description, events: wh.events, active: wh.active });
  };
  const cancelEdit = () => { setEditingId(null); setEditForm({}); };

  const saveEdit = async id => {
    try {
      const updated = await api.patch(`/settings/webhooks/${id}`, editForm);
      setWebhooks(prev => prev.map(w => (w.id === id ? updated : w)));
      setEditingId(null);
      notify({ type: 'success', message: 'Webhook updated' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const deleteWebhook = async id => {
    const ok = await confirm('Delete this webhook?');
    if (!ok) return;
    try {
      await api.del(`/settings/webhooks/${id}`);
      setWebhooks(prev => prev.filter(w => w.id !== id));
      notify({ type: 'success', message: 'Webhook deleted' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const testWebhook = async id => {
    try {
      await api.post(`/settings/webhooks/${id}/test`);
      notify({ type: 'success', message: 'Test event sent' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const testWebhookEvent = async (id, event) => {
    if (!event) return notify({ type: 'error', message: 'Select an event type' });
    try {
      const res = await api.post(`/settings/webhooks/${id}/test-event`, { event });
      setTestEventResult(res.payload_preview);
      notify({ type: 'success', message: `Simulated ${event} event sent` });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const toggleActive = async (id, current) => {
    try {
      const updated = await api.patch(`/settings/webhooks/${id}`, { active: !current });
      setWebhooks(prev => prev.map(w => (w.id === id ? updated : w)));
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  /* ── API Keys ── */
  const createApiKey = async () => {
    try {
      const res = await api.post('/auth/api-keys', {
        name: newKeyName,
        ...(newKeyExpiry ? { expires_in_days: parseInt(newKeyExpiry, 10) } : {}),
      });
      setCreatedKey(res.key);
      setNewKeyName(''); setNewKeyExpiry('');
      const keysData = await api.get('/auth/api-keys');
      setApiKeys(keysData || []);
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const revokeApiKey = async id => {
    const yes = await confirm('Revoke this API key? This cannot be undone.');
    if (!yes) return;
    try {
      await api.delete(`/auth/api-keys/${id}`);
      setApiKeys(prev => prev.filter(k => k.id !== id));
      notify({ type: 'success', message: 'API key revoked.' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  /* ── Notification settings ── */
  const saveNotifConfig = async () => {
    setNotifSaving(true);
    try {
      const res = await api.put('/notifications/config', notifConfig);
      setNotifConfig(res);
      savedNotifRef.current = res;
      notify({ type: 'success', message: 'Notification settings saved.' });
    } catch (e) { notify({ type: 'error', message: e.message }); }
    finally { setNotifSaving(false); }
  };

  /* ── AI settings — per-feature helpers ── */

  const loadModelsForFeature = useCallback(async (featureId, provider, apiKey) => {
    if (!provider || (!apiKey && !apiKey?.trim())) return;
    setAiModels(prev => ({ ...prev, [featureId]: { models: [], loading: true, error: '' } }));
    try {
      const params = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : '';
      const res = await api.get(`/settings/ai/providers/${provider}/models${params}`);
      if (res.error) {
        setAiModels(prev => ({ ...prev, [featureId]: { models: [], loading: false, error: res.error } }));
      } else {
        setAiModels(prev => ({ ...prev, [featureId]: { models: res.models || [], loading: false, error: '' } }));
      }
    } catch (e) {
      setAiModels(prev => ({ ...prev, [featureId]: { models: [], loading: false, error: e.message } }));
    }
  }, []);

  const saveAiFeature = async (featureId, overrides = {}) => {
    const f = { ...(aiFeatures[featureId] || {}), ...overrides };
    try {
      await api.post(`/settings/ai/${featureId}`, {
        enabled: f.enabled,
        provider: f.provider,
        model: f.model,
        api_key: f.api_key,
      });
      notify({ type: 'success', message: 'AI settings saved' });
      loadAll();
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const verifyAiFeature = async (featureId) => {
    const f = aiFeatures[featureId] || {};
    const hasProvider = f.provider;
    const hasModel = f.model;
    const hasKey = f.api_key || f.api_key_set;
    const missing = [];
    if (!hasProvider) missing.push('provider');
    if (!hasModel) missing.push('model');
    if (!hasKey) missing.push('API key');
    if (missing.length) {
      return notify({ type: 'error', message: `Please provide: ${missing.join(', ')}` });
    }
    setAiVerifying(prev => ({ ...prev, [featureId]: true }));
    setAiVerifyResult(prev => ({ ...prev, [featureId]: null }));
    try {
      const res = await api.post(`/settings/ai/${featureId}/verify`, {
        provider: f.provider,
        model: f.model,
        api_key: f.api_key,
      });
      setAiVerifyResult(prev => ({ ...prev, [featureId]: res }));
      if (res.ok) {
        // Mark as tested locally so the UI reflects the new state immediately
        setAiFeatures(prev => ({
          ...prev,
          [featureId]: { ...prev[featureId], connection_tested: true, last_error: '' },
        }));
        notify({ type: 'success', message: 'Credentials verified ✓' });
      } else {
        notify({ type: 'error', message: `Verification failed: ${res.error}` });
      }
    } catch (e) {
      setAiVerifyResult(prev => ({ ...prev, [featureId]: { ok: false, error: e.message } }));
      notify({ type: 'error', message: e.message });
    } finally {
      setAiVerifying(prev => ({ ...prev, [featureId]: false }));
    }
  };

  // Auto-fetch models whenever provider or api_key changes for any feature
  const prevAiFeaturesRef = useRef({});
  useEffect(() => {
    const prev = prevAiFeaturesRef.current;
    for (const [fid, f] of Object.entries(aiFeatures)) {
      const p = prev[fid] || {};
      const providerChanged = f.provider !== p.provider;
      const keyChanged = f.api_key !== p.api_key;
      if ((providerChanged || keyChanged) && f.provider && (f.api_key || f.api_key_set)) {
        loadModelsForFeature(fid, f.provider, f.api_key || '');
      }
    }
    prevAiFeaturesRef.current = aiFeatures;
  }, [aiFeatures, loadModelsForFeature]);

  /* ── event checkbox toggler ────────────────────────────────────────────── */
  const toggleEvent = (events, setEvents, evt) => {
    setEvents(events.includes(evt) ? events.filter(e => e !== evt) : [...events, evt]);
  };

  /* ── event sections for grouped display ─────────────────────────────── */
  const EVENT_SECTIONS = [
    { label: 'Email Events', events: eventTypes.filter(e => e.startsWith('email.')) },
    { label: 'Lead Events',  events: eventTypes.filter(e => e.startsWith('lead.')) },
    { label: 'System Events', events: eventTypes.filter(e => !e.startsWith('email.') && !e.startsWith('lead.')) },
  ].filter(s => s.events.length > 0);

  const EVENT_LABELS = {
    'email.sent': 'Email Sent',
    'email.opened': 'Email Opened',
    'email.clicked': 'Link Clicked',
    'email.bounced': 'Email Bounced',
    'lead.replied': 'Lead Replied',
    'lead.unsubscribed': 'Lead Unsubscribed',
    'lead.status_changed': 'Status Changed',
    'lead.interested': 'Lead Interested (AI)',
    'lead.not_interested': 'Lead Not Interested (AI)',
    'daily_limit': 'Daily Limit Hit',
    'rate_limit': 'Rate Limit',
    'token_expired': 'Token Expired',
  };

  const isAllEvents = (events) => eventTypes.length > 0 && events.length === eventTypes.length;
  const whEventMode = (events) => isAllEvents(events) ? 'all' : 'specific';

  const toggleSectionEvents = (sectionEvents, currentEvents, setEvents) => {
    const allSelected = sectionEvents.every(e => currentEvents.includes(e));
    if (allSelected) {
      setEvents(currentEvents.filter(e => !sectionEvents.includes(e)));
    } else {
      setEvents([...new Set([...currentEvents, ...sectionEvents])]);
    }
  };

  /* ── Reusable webhook event selector component ─────────────────────── */
  const EventSelector = ({ events, onChange }) => {
    const mode = whEventMode(events);
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-gray-600">Events:</span>
          <select
            className="border rounded-lg px-3 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-teal-300"
            value={mode}
            onChange={e => {
              if (e.target.value === 'all') onChange([...eventTypes]);
              else onChange([]);
            }}
          >
            <option value="all">All Events</option>
            <option value="specific">Specific Events</option>
          </select>
        </div>
        {mode === 'specific' && (
          <div className="border rounded-lg p-3 bg-gray-50 space-y-3">
            {EVENT_SECTIONS.map(section => {
              const allInSection = section.events.every(e => events.includes(e));
              const someInSection = section.events.some(e => events.includes(e));
              return (
                <div key={section.label}>
                  <label className="flex items-center gap-2 cursor-pointer mb-1.5">
                    <input
                      type="checkbox"
                      checked={allInSection}
                      ref={el => { if (el) el.indeterminate = someInSection && !allInSection; }}
                      onChange={() => toggleSectionEvents(section.events, events, onChange)}
                      className="rounded"
                    />
                    <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">{section.label}</span>
                  </label>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 pl-6">
                    {section.events.map(evt => (
                      <label key={evt} className="flex items-center gap-2 cursor-pointer py-0.5">
                        <input
                          type="checkbox"
                          checked={events.includes(evt)}
                          onChange={() => toggleEvent(events, onChange, evt)}
                          className="rounded"
                        />
                        <span className="text-sm text-gray-700">{EVENT_LABELS[evt] || evt}</span>
                        <span className="text-[10px] text-gray-400 font-mono">{evt}</span>
                      </label>
                    ))}
                  </div>
                </div>
              );
            })}
            {events.length === 0 && (
              <p className="text-xs text-amber-600">Select at least one event, or switch to "All Events".</p>
            )}
          </div>
        )}
        {mode === 'all' && (
          <p className="text-xs text-gray-400 pl-1">This webhook will receive all event types.</p>
        )}
      </div>
    );
  };

  /* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="shrink-0 px-6 lg:px-8 pt-6 lg:pt-8 pb-0 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-transparent">
        <h1 className="text-2xl font-bold">Settings</h1>
        <nav
          className="mt-4 flex flex-wrap gap-x-1 gap-y-0 items-end"
          aria-label="Settings sections"
        >
          {visibleTabs.map(t => (
            <button
              key={t.id}
              type="button"
              onClick={() => selectTab(t.id)}
              className={
                'px-3 py-2 text-sm font-medium leading-none transition-colors border-b-2 -mb-px ' +
                (activeTab === t.id
                  ? 'border-teal-500 text-teal-600 dark:text-teal-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600')
              }
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col lg:flex-row lg:items-stretch">
        <aside
          className="flex min-h-0 w-full shrink-0 flex-col border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900/60 lg:h-full lg:min-h-0 lg:w-44 lg:border-b-0 lg:border-r lg:bg-gray-100 lg:dark:bg-gray-900/50"
          aria-label="On this page"
        >
          <div className="flex min-h-0 flex-1 flex-col px-4 py-3 lg:py-5 lg:pl-5 lg:pr-3">
            <p className="mb-2 block shrink-0 text-[10px] font-semibold uppercase tracking-wider text-gray-700 dark:text-gray-300">
              On this page
            </p>
            <nav className="flex min-h-0 flex-1 flex-row flex-wrap content-start gap-1 overflow-y-auto lg:flex-col lg:flex-nowrap">
              {sectionNav.map(s => {
                const domId = `settings-${s.id}`;
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => scrollToSection(s.id)}
                    className={
                      'rounded-md px-2 py-1.5 text-left text-xs transition-colors lg:text-sm whitespace-nowrap lg:whitespace-normal ' +
                      (activeSectionDomId === domId
                        ? 'bg-primary/15 text-primary ring-1 ring-primary/30 dark:bg-primary/20 font-semibold'
                        : 'text-gray-700 hover:bg-gray-200/80 hover:text-primary dark:text-gray-300 dark:hover:bg-gray-800/80')
                    }
                  >
                    {s.label}
                  </button>
                );
              })}
            </nav>
          </div>
        </aside>

        <div
          ref={tabContentRef}
          className="min-h-0 min-w-0 flex-1 overflow-y-auto px-6 py-6 lg:px-8"
        >

        {activeTab === 'general' && (
          <>
        <section id="settings-scheduling" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">Scheduling</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">Controls how Recalculate All Campaigns distributes emails.</p>
          <div className="space-y-3">
            <label className="flex gap-2 items-start cursor-pointer">
              <input type="radio" name="strategy" value="priority" checked={strategy === 'priority'} onChange={() => submitStrategy('priority')} />
              <span>
                <strong>Priority by campaign</strong><br />
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  Campaigns processed in ascending priority order.{' '}
                  <a href="/campaigns" className="text-teal-500 underline">Reorder campaigns</a>
                </span>
              </span>
            </label>
            <label className="flex gap-2 items-start cursor-pointer">
              <input type="radio" name="strategy" value="round_robin" checked={strategy === 'round_robin'} onChange={() => submitStrategy('round_robin')} />
              <span>
                <strong>Round-robin distribution</strong><br />
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  Inbox capacity divided evenly across active campaigns.
                </span>
              </span>
            </label>
          </div>
        </section>

        <section id="settings-appearance" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">Appearance</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">Theme follows your choice; System uses your device light/dark setting (default).</p>
          <div className="space-y-3">
            <label className="flex gap-2 items-start cursor-pointer">
              <input
                type="radio"
                name="appearance"
                value="system"
                checked={themePreference === 'system'}
                onChange={() => setThemePreference('system')}
              />
              <span>
                <strong>System</strong>
                <br />
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  Match your OS or browser appearance automatically.
                </span>
              </span>
            </label>
            <label className="flex gap-2 items-start cursor-pointer">
              <input
                type="radio"
                name="appearance"
                value="light"
                checked={themePreference === 'light'}
                onChange={() => setThemePreference('light')}
              />
              <span>
                <strong>Light</strong>
                <br />
                <span className="text-xs text-gray-500 dark:text-gray-400">Always use light theme.</span>
              </span>
            </label>
            <label className="flex gap-2 items-start cursor-pointer">
              <input
                type="radio"
                name="appearance"
                value="dark"
                checked={themePreference === 'dark'}
                onChange={() => setThemePreference('dark')}
              />
              <span>
                <strong>Dark</strong>
                <br />
                <span className="text-xs text-gray-500 dark:text-gray-400">Always use dark theme.</span>
              </span>
            </label>
          </div>
        </section>

        <section id="settings-account" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">Account &amp; Security</h2>
          {user && (
            <div className="space-y-4">
              <div className="text-sm">
                <span className="text-gray-500 dark:text-gray-400">Logged in as </span>
                <span className="font-medium">{user.username}</span>
                <span className="ml-2 text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 uppercase">{user.role}</span>
              </div>

              <Button size="sm" variant="outline" onClick={logout}>Log Out</Button>
            </div>
          )}
        </section>

        <section id="settings-known-ips" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-2 border-b border-gray-200 dark:border-gray-700 pb-2">Known IPs</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
            Opens and clicks from these IPs are ignored for self-open filtering.
          </p>
          <Button size="sm" variant="outline" onClick={() => setKnownIpsOpen(true)}>
            Manage Known IPs
          </Button>
        </section>
          </>
        )}

        {activeTab === 'setup' && (
          <>
        {/* ──────────────── Gmail Sync ──────────────── */}
        <section id="settings-gmail-sync" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-1 border-b pb-2">Gmail Sync</h2>
          <p className="text-xs text-gray-500 mb-4">
            Configure how Quickly detects replies from Gmail inboxes. Optional — polling works
            without these settings, but push notifications make reply detection instant.
          </p>
          <div className="space-y-4">

            {/* Push Topic */}
            <div>
              <label className="block text-sm font-medium mb-1">Google Pub/Sub Topic</label>
              <input
                type="text"
                className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                placeholder="projects/your-project/topics/gmail-replies"
                value={gmailSync.push_topic}
                onChange={e => setGmailSync(prev => ({ ...prev, push_topic: e.target.value }))}
              />
              <p className="text-xs text-gray-400 mt-1">
                Create a Pub/Sub topic in Google Cloud Console and enter its full resource name here.
                Leave blank to use polling only.
              </p>
            </div>

            {/* Webhook token */}
            <div>
              <label className="block text-sm font-medium mb-1">Push Webhook Token</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  className="flex-1 border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-teal-300"
                  placeholder="auto-generated on first startup"
                  value={gmailSync.webhook_token}
                  onChange={e => setGmailSync(prev => ({ ...prev, webhook_token: e.target.value }))}
                />
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    navigator.clipboard.writeText(
                      window.location.origin + '/api/unibox/gmail/push?token=' + gmailSync.webhook_token
                    );
                    notify({ type: 'success', message: 'Push URL copied!' });
                  }}
                >
                  Copy URL
                </Button>
              </div>
              <p className="text-xs text-gray-400 mt-1">
                Append this token to your Pub/Sub push endpoint:
                {' '}<code className="bg-gray-100 dark:bg-gray-800 rounded px-1">/api/unibox/gmail/push?token=…</code>
              </p>
            </div>

            {/* Sync interval */}
            <div>
              <label className="block text-sm font-medium mb-1">Polling Interval (minutes)</label>
              <input
                type="number"
                min="1"
                max="60"
                className="w-28 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                value={gmailSync.sync_interval_minutes}
                onChange={e => setGmailSync(prev => ({ ...prev, sync_interval_minutes: Math.max(1, parseInt(e.target.value) || 1) }))}
              />
              <p className="text-xs text-gray-400 mt-1">How often to poll Gmail for new replies (fallback when push is not configured).</p>
            </div>

            <Button
              size="sm"
              disabled={gmailSyncSaving}
              onClick={async () => {
                setGmailSyncSaving(true);
                try {
                  await api.post('/settings/gmail-sync', gmailSync);
                  savedGmailSyncRef.current = { ...gmailSync };
                  notify({ type: 'success', message: 'Gmail sync settings saved' });
                } catch (e) {
                  notify({ type: 'error', message: e.message });
                } finally {
                  setGmailSyncSaving(false);
                }
              }}
            >
              {gmailSyncSaving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </section>

        {user?.role === 'admin' && (
        <section id="settings-backup-restore" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-1 border-b pb-2 dark:border-gray-700">Backup & restore</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
            Backup and restore the database containing all your leads, campaigns, and other data.
          </p>

          <div className="space-y-4 mb-6">
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  try {
                    const res = await api.download('/settings/backup/download');
                    const blob = await res.blob();
                    const dispo = res.headers.get('Content-Disposition');
                    const m = dispo && dispo.match(/filename="([^"]+)"/);
                    const name = m ? m[1] : 'quickly-backup.dump';
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = name;
                    a.click();
                    URL.revokeObjectURL(a.href);
                    notify({ type: 'success', message: 'Backup downloaded' });
                  } catch (e) {
                    notify({ type: 'error', message: e.message || 'Download failed' });
                  }
                }}
              >
                Download backup
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={backupRunning}
                onClick={async () => {
                  setBackupRunning(true);
                  try {
                    const r = await api.post('/settings/backup/run', {});
                    const parts = [];
                    if (r.local_path) parts.push(`Saved ${r.local_path}`);
                    if (r.webhook_ok) parts.push('Webhook sent');
                    if (r.webhook_error) parts.push(`Webhook error: ${r.webhook_error}`);
                    if (r.local_skipped) parts.push(r.local_skipped);
                    notify({ type: 'success', message: parts.join(' · ') || 'Backup completed' });
                  } catch (e) {
                    notify({ type: 'error', message: e.message });
                  } finally {
                    setBackupRunning(false);
                  }
                }}
              >
                {backupRunning ? 'Running…' : 'Run backup now'}
              </Button>
            </div>

            <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
              <p className="text-sm font-medium mb-2">Restore from file</p>
              <p className="text-xs text-amber-700 dark:text-amber-400 mb-2">
                Caution: This will delete all your current data and replace it with the backup.
              </p>
              <input
                type="file"
                accept=".dump,application/octet-stream"
                className="block text-sm mb-2"
                onChange={e => setRestoreFile(e.target.files?.[0] || null)}
              />
              <Button
                size="sm"
                variant="outline"
                disabled={!restoreFile}
                onClick={async () => {
                  const ok = await confirm(
                    'Restore will replace the entire database with this file. This cannot be undone. Continue?',
                  );
                  if (!ok) return;
                  try {
                    await api.upload('/settings/backup/restore', restoreFile);
                    notify({ type: 'success', message: 'Restore completed. Reloading…' });
                    setTimeout(() => window.location.reload(), 1500);
                  } catch (e) {
                    notify({ type: 'error', message: e.message });
                  }
                }}
              >
                Restore from upload
              </Button>
            </div>
          </div>

          <h3 className="text-sm font-semibold mb-2">Scheduled &amp; remote backup</h3>
          <div className="space-y-3 text-sm">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={backupCfg.schedule_enabled}
                onChange={e => setBackupCfg(prev => ({ ...prev, schedule_enabled: e.target.checked }))}
              />
              <span>Enable scheduled backup (in-app cron)</span>
            </label>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Cron (minute hour day month day-of-week, UTC)</label>
              <input
                className="w-full max-w-md border rounded-lg px-3 py-2 text-sm font-mono dark:bg-gray-900 dark:border-gray-600"
                value={backupCfg.cron_expression}
                onChange={e => setBackupCfg(prev => ({ ...prev, cron_expression: e.target.value }))}
                placeholder="0 3 * * *"
              />
            </div>
            {!backupMeta.local_disk_available && (
              <p className="text-xs text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2">
                Saving backups on the server requires the deployment to set{' '}
                <code className="text-[11px]">QUICKLY_LOCAL_DISK_BACKUPS</code> and a persistent{' '}
                <code className="text-[11px]">backups</code> folder (all Docker Compose files in this project do). On cloud
                hosts without that setup, use <strong>POST to webhook</strong> below.
              </p>
            )}
            <label className={`flex items-center gap-2 ${backupMeta.local_disk_available ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}>
              <input
                type="checkbox"
                disabled={!backupMeta.local_disk_available}
                checked={backupCfg.save_local}
                onChange={e => setBackupCfg(prev => ({ ...prev, save_local: e.target.checked }))}
              />
              <span>Save to server disk (keeps 10 newest files; older ones are removed)</span>
            </label>
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                Folder (under the app directory, default <code className="text-[11px]">backups</code>)
              </label>
              <input
                className="w-full max-w-md border rounded-lg px-3 py-2 text-sm font-mono dark:bg-gray-900 dark:border-gray-600 disabled:opacity-50"
                disabled={!backupMeta.local_disk_available}
                value={backupCfg.local_relative_path}
                onChange={e => setBackupCfg(prev => ({ ...prev, local_relative_path: e.target.value }))}
                placeholder="backups"
              />
              {backupMeta.local_disk_available && backupMeta.local_backup_resolved && (
                <p className="text-xs text-gray-400 mt-1 break-all">
                  Resolves to: {backupMeta.local_backup_resolved}
                </p>
              )}
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={backupCfg.send_webhook}
                onChange={e => setBackupCfg(prev => ({ ...prev, send_webhook: e.target.checked }))}
              />
              <span>POST backup file to webhook URL</span>
            </label>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Webhook URL</label>
              <input
                type="url"
                className="w-full border rounded-lg px-3 py-2 text-sm dark:bg-gray-900 dark:border-gray-600"
                value={backupCfg.webhook_url}
                onChange={e => setBackupCfg(prev => ({ ...prev, webhook_url: e.target.value }))}
                placeholder="https://example.com/hooks/backup"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Authorization header (optional)</label>
              <input
                type="text"
                name="backup_webhook_authorization"
                autoComplete="off"
                spellCheck={false}
                data-1p-ignore
                data-lpignore="true"
                className="w-full border rounded-lg px-3 py-2 text-sm font-mono dark:bg-gray-900 dark:border-gray-600"
                value={backupCfg.webhook_auth_header}
                onChange={e => setBackupCfg(prev => ({ ...prev, webhook_auth_header: e.target.value }))}
                placeholder={backupMeta.webhook_auth_configured ? `Leave blank to keep (${backupMeta.webhook_auth_header_masked})` : 'Bearer …'}
              />
            </div>
            <Button
              size="sm"
              disabled={backupSaving}
              onClick={async () => {
                setBackupSaving(true);
                try {
                  const r = await api.put('/settings/backup/config', backupCfg);
                  setBackupMeta({
                    webhook_auth_configured: !!r.webhook_auth_configured,
                    webhook_auth_header_masked: r.webhook_auth_header_masked || '',
                    local_disk_available: !!r.local_disk_available,
                    local_backup_resolved: r.local_backup_resolved || null,
                  });
                  setBackupCfg(prev => ({ ...prev, webhook_auth_header: '' }));
                  notify({ type: 'success', message: 'Backup settings saved' });
                } catch (e) {
                  notify({ type: 'error', message: e.message });
                } finally {
                  setBackupSaving(false);
                }
              }}
            >
              {backupSaving ? 'Saving…' : 'Save backup settings'}
            </Button>
          </div>
        </section>
        )}
          </>
        )}

        {activeTab === 'features' && (
          <>
        <section id="settings-ai" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-1 border-b pb-2">AI Features</h2>
          <p className="text-xs text-gray-500 mb-4">
            Each AI feature can use a different provider and model. Configure the provider and
            API key first — the available models will load automatically in the background.
          </p>

          {Object.values(aiFeatures).map(feature => {
            const fid = feature.id;
            const isOpen = !!aiExpanded[fid];
            const fm = aiModels[fid] || { models: [], loading: false, error: '' };
            const provSearch = aiProviderSearch[fid] ?? null; // null = not focused
            const modSearch = aiModelSearch[fid] || '';
            const verifying = aiVerifying[fid] || false;
            const verifyResult = aiVerifyResult[fid] || null;

            const setFeature = (updater) => {
              setAiFeatures(prev => ({ ...prev, [fid]: typeof updater === 'function' ? updater(prev[fid]) : { ...prev[fid], ...updater } }));
            };
            const setProvSearch = (v) => setAiProviderSearch(prev => ({ ...prev, [fid]: v }));
            const setModSearch = (v) => setAiModelSearch(prev => ({ ...prev, [fid]: v }));

            const selectedProviderLabel = feature.provider
              ? (aiProviders.find(p => p.value === feature.provider)?.label || feature.provider)
              : '';

            const filteredProviders = (provSearch || '').length > 0
              ? aiProviders.filter(p => p.label.toLowerCase().includes(provSearch.toLowerCase()) || p.value.toLowerCase().includes(provSearch.toLowerCase()))
              : aiProviders;
            const filteredModels = modSearch
              ? fm.models.filter(m => m.id.toLowerCase().includes(modSearch.toLowerCase()) || (m.name && m.name.toLowerCase().includes(modSearch.toLowerCase())))
              : fm.models;

            return (
              <Card key={fid} className="mb-4 overflow-visible">
                {/* ── Collapsed header (always visible) ── */}
                <div
                  className="flex items-center justify-between cursor-pointer select-none"
                  onClick={() => setAiExpanded(prev => ({ ...prev, [fid]: !prev[fid] }))}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className={`text-gray-400 transition-transform text-xs ${isOpen ? 'rotate-90' : ''}`}>▶</span>
                    <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 truncate">{feature.label}</h3>
                    {feature.enabled
                      ? <span className="text-[10px] bg-green-100 text-green-700 border border-green-200 rounded-full px-2 py-0.5 font-medium shrink-0">Enabled</span>
                      : <span className="text-[10px] bg-gray-100 text-gray-500 border rounded-full px-2 py-0.5 font-medium shrink-0">Disabled</span>
                    }
                  </div>
                  {/* Enable toggle — click doesn't propagate to collapse toggle */}
                  <label
                    className="flex items-center gap-1.5 cursor-pointer shrink-0 ml-4"
                    onClick={e => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={feature.enabled}
                      className="rounded"
                      onChange={e => {
                        const next = e.target.checked;
                        if (next && !feature.connection_tested) {
                          notify({ type: 'error', message: 'Test the connection successfully before enabling this feature.' });
                          return;
                        }
                        // update state without marking dirty — enabled auto-saves immediately
                        setAiFeatures(prev => ({ ...prev, [fid]: { ...prev[fid], enabled: next } }));
                        saveAiFeature(fid, { enabled: next });
                      }}
                    />
                    <span className="text-xs font-medium text-gray-600 whitespace-nowrap">
                      Enable
                    </span>
                  </label>
                </div>

                {/* ── Expanded body ── */}
                <div className={`grid transition-[grid-template-rows] duration-200 ease-in-out ${isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                  <div className={`min-h-0 ${isOpen ? 'overflow-visible' : 'overflow-hidden'}`}>
                    <div className="mt-4 space-y-4 border-t pt-4">
                    <p className="text-xs text-gray-500">{feature.description}</p>

                    {/* Step 1 — Provider */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">
                        <span className="text-gray-400 mr-1">1.</span> Provider
                      </label>
                      <div className="relative">
                        <input
                          type="text"
                          placeholder="Search providers…"
                          className="border rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-900 w-full focus:outline-none focus:ring-2 focus:ring-teal-300"
                          value={provSearch !== null ? provSearch : selectedProviderLabel}
                          onChange={e => setProvSearch(e.target.value)}
                          onFocus={() => setProvSearch('')}
                          onBlur={() => setTimeout(() => setProvSearch(null), 200)}
                        />
                        {provSearch !== null && filteredProviders.length > 0 && (
                          <div className="border rounded-lg mt-1 max-h-48 overflow-y-auto bg-white dark:bg-gray-900 shadow-lg z-20 absolute w-full top-full">
                            {filteredProviders.map(p => (
                              <button
                                key={p.value}
                                type="button"
                                className={`block w-full text-left px-3 py-1.5 text-sm hover:bg-teal-50 dark:hover:bg-gray-800 ${p.value === feature.provider ? 'bg-teal-50 dark:bg-gray-800 font-medium' : ''}`}
                                onMouseDown={e => {
                                  e.preventDefault();
                                  setFeature({ provider: p.value, model: '', connection_tested: false });
                                  setProvSearch(null);
                                  setModSearch('');
                                }}
                              >
                                {p.label}
                                <span className="text-[10px] text-gray-400 ml-2">{p.value}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Step 2 — API Key */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">
                        <span className="text-gray-400 mr-1">2.</span> API Key
                      </label>
                      <input
                        type="password"
                        placeholder={feature.api_key_set ? `Saved key: ${feature.api_key_masked}` : 'Enter your API key'}
                        className="block w-full border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                        value={feature.api_key || ''}
                        onChange={e => setFeature({ api_key: e.target.value, connection_tested: false })}
                      />
                      {feature.provider && (feature.api_key || feature.api_key_set) && (
                        <p className="text-[10px] mt-1 text-teal-600">
                          {fm.loading
                            ? '⏳ Loading available models…'
                            : fm.error
                              ? `⚠️ Could not fetch models: ${fm.error}`
                              : fm.models.length > 0
                                ? `✓ ${fm.models.length} models available — select below or type a custom name`
                                : ''}
                        </p>
                      )}
                    </div>

                    {/* Step 3 — Model */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">
                        <span className="text-gray-400 mr-1">3.</span> Model
                      </label>
                      <div className="relative">
                        <input
                          type="text"
                          placeholder={fm.loading ? 'Loading models…' : 'Search or type model name…'}
                          className="block w-full border rounded-lg p-2 text-sm bg-white dark:bg-gray-900 focus:outline-none focus:ring-2 focus:ring-teal-300"
                          value={modSearch !== '' ? modSearch : (feature.model || '')}
                          onChange={e => {
                            setModSearch(e.target.value);
                            setFeature({ model: e.target.value, connection_tested: false });
                          }}
                          onFocus={() => setModSearch(feature.model || '')}
                          onBlur={() => setTimeout(() => setModSearch(''), 200)}
                        />
                        {modSearch !== '' && filteredModels.length > 0 && (
                          <div className="border rounded-lg mt-1 max-h-48 overflow-y-auto bg-white dark:bg-gray-900 shadow-lg z-20 absolute w-full top-full">
                            {filteredModels.slice(0, 50).map(m => (
                              <button
                                key={m.id}
                                type="button"
                                className={`block w-full text-left px-3 py-1.5 text-sm hover:bg-teal-50 dark:hover:bg-gray-800 ${m.id === feature.model ? 'bg-teal-50 dark:bg-gray-800 font-medium' : ''}`}
                                onMouseDown={e => {
                                  e.preventDefault();
                                  setFeature({ model: m.id, connection_tested: false });
                                  setModSearch('');
                                }}
                              >
                                {m.id}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      {!fm.loading && fm.models.length === 0 && (
                        <p className="text-[10px] text-gray-400 mt-1">
                          Type the model name as recognized by the provider (e.g. gpt-4o, claude-sonnet-4-20250514)
                        </p>
                      )}
                    </div>

                    {/* Actions */}
                    {(() => {
                      const saved = savedAiFeaturesRef.current[fid];
                      const aiFeatDirty = saved != null && (
                        feature.provider !== saved.provider ||
                        feature.model !== saved.model ||
                        !!feature.api_key
                      );
                      return aiFeatDirty ? (
                        <p className="text-xs text-amber-600 font-medium">⚠ Unsaved changes — click Save to apply</p>
                      ) : null;
                    })()}
                    <div className="flex items-center gap-3 flex-wrap">
                      <Button
                        size="sm"
                        variant={feature.connection_tested ? 'success' : 'outline'}
                        className={feature.connection_tested ? 'bg-green-600 text-white border-green-600 hover:bg-green-700' : ''}
                        onClick={() => verifyAiFeature(fid)}
                        disabled={verifying}
                      >
                        {verifying ? 'Testing…' : feature.connection_tested ? '✓ Connection Tested' : 'Test Connection'}
                      </Button>
                      <Button size="sm" onClick={() => saveAiFeature(fid)}>Save</Button>
                      {verifyResult && !verifyResult.ok && (
                        <span className="text-sm font-medium text-red-500">
                          ✗ {verifyResult.error}
                        </span>
                      )}
                      {!feature.connection_tested && (
                        <span className="text-xs text-amber-600">Test connection before enabling</span>
                      )}
                    </div>
                    </div>
                  </div>
                </div>
              </Card>
            );
          })}

          {Object.keys(aiFeatures).length === 0 && (
            <p className="text-sm text-gray-400 italic">Loading AI features…</p>
          )}

        </section>

        <section id="settings-other" className="mb-10 scroll-mt-6">
          <h2 className="mb-1 border-b border-gray-200 pb-2 text-lg font-semibold dark:border-gray-700">Other</h2>
          <p className="mb-6 text-xs text-gray-500 dark:text-gray-400">
            Email notifications and lead verification — optional additions to your workflow.
          </p>

          <div className="mb-8">
            <h3 className="mb-2 text-base font-semibold text-gray-800 dark:text-gray-100">Email notifications</h3>
            <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
              Receive email notifications for campaign events instead of (or in addition to) webhooks.
              Notifications are sent from your own OAuth login account.
            </p>
            <Card>
              <div
                className="flex cursor-pointer select-none items-center justify-between"
                onClick={() => setNotifOpen(v => !v)}
              >
                <div className="flex min-w-0 items-center gap-3">
                  <span className={`text-xs text-gray-400 transition-transform ${notifOpen ? 'rotate-90' : ''}`}>▶</span>
                  <h4 className="truncate text-sm font-semibold text-gray-800 dark:text-gray-100">Notification settings</h4>
                  {notifConfig.enabled
                    ? <span className="shrink-0 rounded-full border border-green-200 bg-green-100 px-2 py-0.5 text-[10px] font-medium text-green-700">Enabled</span>
                    : <span className="shrink-0 rounded-full border bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">Disabled</span>
                  }
                </div>
                <label
                  className="ml-4 flex shrink-0 cursor-pointer items-center gap-1.5"
                  onClick={e => e.stopPropagation()}
                >
                  <input
                    type="checkbox"
                    checked={notifConfig.enabled}
                    className="rounded"
                    onChange={async e => {
                      const next = { ...notifConfig, enabled: e.target.checked };
                      setNotifConfig(next);
                      try {
                        await api.put('/notifications/config', next);
                        notify({ type: 'success', message: `Notifications ${next.enabled ? 'enabled' : 'disabled'}` });
                      } catch (err) { notify({ type: 'error', message: err.message }); }
                    }}
                  />
                  <span className="whitespace-nowrap text-xs font-medium text-gray-600">Enable</span>
                </label>
              </div>

              <div className={`grid transition-[grid-template-rows] duration-200 ease-in-out ${notifOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                <div className={`min-h-0 ${notifOpen ? 'overflow-visible' : 'overflow-hidden'}`}>
                  <div className="mt-4 space-y-4 border-t pt-4">
                    <div>
                      <label className="mb-1 block text-xs font-medium text-gray-600">Notification email address</label>
                      <input
                        type="email"
                        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder="you@example.com"
                        value={notifConfig.notification_email}
                        onChange={e => { setNotifConfig(prev => ({ ...prev, notification_email: e.target.value })); }}
                      />
                      <p className="mt-1 text-[11px] text-gray-400">Where notification emails will be sent to.</p>
                    </div>

                    <div>
                      <label className="mb-1 block text-xs font-medium text-gray-600">Rate limit (emails per hour)</label>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        className="w-24 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        value={notifConfig.rate_limit_per_hour}
                        onChange={e => { setNotifConfig(prev => ({ ...prev, rate_limit_per_hour: Math.max(1, Math.min(100, parseInt(e.target.value) || 1)) })); }}
                      />
                    </div>

                    <div>
                      <label className="mb-1 block text-xs font-medium text-gray-600">Events</label>
                      <EventSelector
                        events={notifConfig.events}
                        onChange={evts => { setNotifConfig(prev => ({ ...prev, events: evts })); }}
                      />
                    </div>

                    <div className="space-y-2 pt-2">
                      {savedNotifRef.current != null && (
                        notifConfig.notification_email !== savedNotifRef.current.notification_email ||
                        notifConfig.rate_limit_per_hour !== savedNotifRef.current.rate_limit_per_hour ||
                        [...notifConfig.events].sort().join(',') !== [...(savedNotifRef.current.events || [])].sort().join(',')
                      ) && (
                        <p className="text-xs font-medium text-amber-600">⚠ Unsaved changes — click Save to apply</p>
                      )}
                      <Button onClick={saveNotifConfig} disabled={notifSaving} size="sm">
                        {notifSaving ? 'Saving…' : 'Save notification settings'}
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            </Card>
          </div>

          <div>
            <h3 className="mb-2 text-base font-semibold text-gray-800 dark:text-gray-100">Email verification</h3>
            <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
              Verify new leads automatically when they are added to a campaign.
            </p>
            <EmailVerificationSettings />
          </div>
        </section>
          </>
        )}

        {activeTab === 'integrating' && (
          <>
        {/* ──────────────── API Keys ──────────────── */}
        <section id="settings-api-keys" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-1 border-b pb-2">API Keys</h2>
          <p className="text-xs text-gray-500 mb-4">
            Create keys to access the API programmatically. The full key is shown only once — copy it immediately.
          </p>

          {/* One-time key display */}
          {createdKey && (
            <Card className="mb-4 border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20">
              <h3 className="text-sm font-semibold mb-1 text-green-700 dark:text-green-400">New API Key Created</h3>
              <p className="text-xs text-gray-500 mb-2">Copy this key now — you won't be able to see it again.</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-white dark:bg-gray-800 border rounded p-2 break-all select-all">{createdKey}</code>
                <Button size="sm" variant="outline" onClick={() => { navigator.clipboard.writeText(createdKey); notify({ type: 'success', message: 'Copied!' }); }}>
                  Copy
                </Button>
              </div>
              <Button size="sm" variant="ghost" className="mt-2 text-xs" onClick={() => setCreatedKey(null)}>Dismiss</Button>
            </Card>
          )}

          {/* Create key form */}
          <Card className="mb-4">
            <h3 className="text-sm font-semibold mb-3">Create API Key</h3>
            <div className="flex gap-2 items-end">
              <div className="flex-1">
                <label className="text-xs text-gray-500">Name</label>
                <input
                  type="text"
                  placeholder="e.g. CI/CD Pipeline"
                  className="block w-full border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={newKeyName}
                  onChange={e => setNewKeyName(e.target.value)}
                />
              </div>
              <div className="w-32">
                <label className="text-xs text-gray-500">Expires (days)</label>
                <input
                  type="number"
                  placeholder="Never"
                  min="1"
                  className="block w-full border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={newKeyExpiry}
                  onChange={e => setNewKeyExpiry(e.target.value)}
                />
              </div>
              <Button size="sm" onClick={createApiKey} disabled={!newKeyName.trim()}>Create</Button>
            </div>
          </Card>

          {/* Existing keys */}
          {apiKeys.length === 0 && (
            <p className="text-sm text-gray-400 italic">No API keys yet.</p>
          )}
          {apiKeys.length > 0 && (
            <div className="space-y-2">
              {apiKeys.map(k => (
                <Card key={k.id} className="flex items-center justify-between">
                  <div>
                    <span className="text-sm font-medium">{k.name}</span>
                    <span className="ml-2 text-xs text-gray-400">{k.prefix}•••</span>
                    <span className="ml-2 text-xs text-gray-400">
                      Created {new Date(k.created_at).toLocaleDateString()}
                      {k.expires_at && <> · Expires {new Date(k.expires_at).toLocaleDateString()}</>}
                    </span>
                  </div>
                  <Button size="sm" variant="danger" onClick={() => revokeApiKey(k.id)}>Revoke</Button>
                </Card>
              ))}
            </div>
          )}
        </section>

        {/* ──────────────── Webhooks ──────────────── */}
        <section id="settings-webhooks" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-1 border-b pb-2">Webhooks</h2>
          <p className="text-xs text-gray-500 mb-4">
            Register one or more outbound webhook endpoints. Each webhook can subscribe to specific event types.
            When an event occurs every matching active webhook receives a POST request.
          </p>

          {/* New webhook form */}
          <Card className="mb-4">
            <h3 className="text-sm font-semibold mb-3">Add Webhook</h3>
            <div className="space-y-3">
              <input
                type="text"
                placeholder="https://your-endpoint.example.com/hook"
                className="block w-full border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                value={newWh.url}
                onChange={e => setNewWh(p => ({ ...p, url: e.target.value }))}
              />
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Bearer secret (optional)"
                  className="flex-1 border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={newWh.secret}
                  onChange={e => setNewWh(p => ({ ...p, secret: e.target.value }))}
                />
                <input
                  type="text"
                  placeholder="Description (optional)"
                  className="flex-1 border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={newWh.description}
                  onChange={e => setNewWh(p => ({ ...p, description: e.target.value }))}
                />
              </div>
              <EventSelector
                events={newWh.events}
                onChange={evts => setNewWh(p => ({ ...p, events: evts }))}
              />
              <Button size="sm" onClick={createWebhook}>Add Webhook</Button>
            </div>
          </Card>

          {/* Existing webhooks */}
          {webhooks.length === 0 && (
            <p className="text-sm text-gray-400 italic">No webhooks configured yet.</p>
          )}
          {webhooks.map(wh => (
            <Card key={wh.id} className="mb-3">
              {editingId === wh.id ? (
                /* editing mode */
                <div className="space-y-3">
                  <input
                    type="text"
                    className="block w-full border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                    value={editForm.url}
                    onChange={e => setEditForm(p => ({ ...p, url: e.target.value }))}
                  />
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="Bearer secret"
                      className="flex-1 border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                      value={editForm.secret}
                      onChange={e => setEditForm(p => ({ ...p, secret: e.target.value }))}
                    />
                    <input
                      type="text"
                      placeholder="Description"
                      className="flex-1 border rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                      value={editForm.description}
                      onChange={e => setEditForm(p => ({ ...p, description: e.target.value }))}
                    />
                  </div>
                  <EventSelector
                    events={editForm.events || []}
                    onChange={evts => setEditForm(p => ({ ...p, events: evts }))}
                  />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => saveEdit(wh.id)}>Save</Button>
                    <Button size="sm" variant="outline" onClick={cancelEdit}>Cancel</Button>
                  </div>
                </div>
              ) : (
                /* display mode */
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-block w-2 h-2 rounded-full ${wh.active ? 'bg-green-500' : 'bg-gray-300'}`}
                        title={wh.active ? 'Active' : 'Inactive'}
                      />
                      <span className="text-sm font-medium truncate max-w-xs" title={wh.url}>{wh.url}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button size="sm" variant="ghost" onClick={() => toggleActive(wh.id, wh.active)}>
                        Enable
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => startEdit(wh)}>Edit</Button>
                      <Button size="sm" variant="ghost" onClick={() => testWebhook(wh.id)}>Test</Button>
                      <Button size="sm" variant="ghost" onClick={() => { setTestEventWh(testEventWh === wh.id ? null : wh.id); setTestEventType(''); setTestEventResult(null); }}>
                        Simulate Event
                      </Button>
                      <Button size="sm" variant="ghost" className="text-red-500" onClick={() => deleteWebhook(wh.id)}>Delete</Button>
                    </div>
                  </div>
                  {wh.description && <p className="text-xs text-gray-500 mb-1">{wh.description}</p>}
                  <div className="flex flex-wrap gap-1">
                    {isAllEvents(wh.events || []) ? (
                      <span className="text-xs bg-teal-50 text-teal-700 border border-teal-200 rounded-full px-2 py-0.5 font-medium">All Events</span>
                    ) : (
                      (wh.events || []).map(evt => (
                        <span key={evt} className="text-[10px] bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 border rounded px-1.5 py-0.5">{EVENT_LABELS[evt] || evt}</span>
                      ))
                    )}
                  </div>
                  {/* Simulate event panel */}
                  {testEventWh === wh.id && (
                    <div className="mt-3 p-3 bg-gray-50 dark:bg-gray-800 border rounded-lg space-y-2">
                      <p className="text-xs font-semibold text-gray-600">Simulate a specific event to see the exact payload:</p>
                      <div className="flex items-center gap-2">
                        <select
                          className="border rounded-lg px-3 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-teal-300 flex-1"
                          value={testEventType}
                          onChange={e => { setTestEventType(e.target.value); setTestEventResult(null); }}
                        >
                          <option value="">— Select event type —</option>
                          {eventTypes.map(evt => (
                            <option key={evt} value={evt}>{EVENT_LABELS[evt] || evt}</option>
                          ))}
                        </select>
                        <Button size="sm" onClick={() => testWebhookEvent(wh.id, testEventType)}>
                          Send
                        </Button>
                      </div>
                      {testEventResult && (
                        <div className="mt-2">
                          <p className="text-xs font-medium text-gray-500 mb-1">Payload sent:</p>
                          <pre className="text-xs bg-white dark:bg-gray-900 border rounded p-2 overflow-auto max-h-48 font-mono">
                            {JSON.stringify(testEventResult, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </Card>
          ))}
        </section>

        {/* ──────────────── MCP (AI agents) ──────────────── */}
        <section id="settings-mcp" className="mb-10 scroll-mt-6">
          <h2 className="text-lg font-semibold mb-1 border-b pb-2">MCP (AI agents)</h2>
          <p className="text-xs text-gray-500 mb-4">
            Quickly exposes a remote MCP endpoint over HTTPS. Create an API key under API keys, then point Cursor at it with
            <code className="mx-1 text-[10px] bg-gray-100 dark:bg-gray-800 px-1 rounded">npx mcp-remote</code>
            (Node 18+). No Python install on your machine.
          </p>
          <Card className="mb-4 space-y-3">
            <h3 className="text-sm font-semibold">1. Endpoint</h3>
            <p className="text-xs text-gray-500">
              Streamable HTTP MCP URL (same auth as the REST API):
            </p>
            {mcpSetup?.mcp_http_url ? (
              <code className="block text-xs bg-gray-50 dark:bg-gray-800 border rounded-lg p-2 break-all font-mono">{mcpSetup.mcp_http_url}</code>
            ) : (
              <p className="text-xs text-amber-600">Could not load — use <code className="font-mono">{typeof window !== 'undefined' ? `${window.location.origin}/api/mcp` : '/api/mcp'}</code></p>
            )}
            <p className="text-xs text-gray-500">
              For plain HTTP (local dev only), add
              <code className="mx-1 text-[10px] bg-gray-100 dark:bg-gray-800 px-1 rounded">--allow-http</code>
              to the <code className="text-[10px] font-mono">mcp-remote</code> args after the URL.
            </p>
            <h3 className="text-sm font-semibold pt-2">2. Tools exposed to the agent</h3>
            <ul className="text-sm text-gray-600 dark:text-gray-400 list-disc pl-5 space-y-1">
              <li><code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 rounded">list_leads</code> — search / filter (q, status, bad_only)</li>
              <li><code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 rounded">get_lead</code> — one lead by id</li>
              <li><code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 rounded">update_lead</code> — patch name, status, custom_data</li>
              <li><code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 rounded">delete_lead</code> — remove a lead</li>
              <li><code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 rounded">add_campaign_leads</code> — bulk add to a campaign</li>
            </ul>
            <h3 className="text-sm font-semibold pt-2">3. Cursor MCP config</h3>
            <p className="text-xs text-gray-500">
              Merge the JSON into your MCP settings. Replace
              <code className="mx-1 text-[10px] bg-gray-100 dark:bg-gray-800 px-1 rounded">QUICKLY_MCP_API_KEY</code>
              with a key from the API keys section. To use a JWT instead, use
              <code className="mx-1 text-[10px] bg-gray-100 dark:bg-gray-800 px-1 rounded">--header</code>
              <code className="text-[10px] font-mono">{'Authorization:${QUICKLY_MCP_AUTH}'}</code>
              {' '}and set the env value to <code className="text-[10px] font-mono">Bearer …</code>
              (same as REST). App base URL:
              {mcpSetup?.api_base_url ? (
                <span className="ml-1 font-mono text-[11px]">{mcpSetup.api_base_url}</span>
              ) : (
                <span className="ml-1 text-amber-600">(load failed)</span>
              )}
            </p>
            {mcpSetup?.cursor_mcp_fragment && (
              <div className="space-y-2">
                <pre className="text-xs bg-gray-50 dark:bg-gray-800 border rounded-lg p-3 overflow-x-auto font-mono max-h-64">
                  {JSON.stringify(mcpSetup.cursor_mcp_fragment, null, 2)}
                </pre>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    navigator.clipboard.writeText(JSON.stringify(mcpSetup.cursor_mcp_fragment, null, 2));
                    notify({ type: 'success', message: 'MCP fragment copied — merge into your mcp.json' });
                  }}
                >
                  Copy MCP fragment
                </Button>
              </div>
            )}
          </Card>
        </section>
          </>
        )}

        {activeTab === 'dev' && !isProduction && (
          <section id="settings-test-mode" className="mb-10 scroll-mt-6">
            <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">Test mode</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">
              When enabled emails are simulated — no real messages are sent.
            </p>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={testMode} onChange={e => submitTestMode(e.target.checked)} />
              <span className="text-sm">Enabled</span>
            </label>
          </section>
        )}
        </div>
      </div>

      {/* ──────────────── Known IPs Dialog ──────────────── */}
      {knownIpsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-2xl mx-4 p-6 max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Known IPs</h2>
              <button
                onClick={() => setKnownIpsOpen(false)}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none"
              >✕</button>
            </div>
            <p className="text-xs text-gray-500 mb-4">
              Opens and clicks from these IPs are ignored (self-open filtering). IPs from your browser sessions are collected automatically and expire after one week. You can also add permanent IPs manually.
            </p>

            {/* Add new IP */}
            <div className="flex gap-2 mb-4">
              <input
                type="text"
                placeholder="e.g. 203.0.113.5"
                value={newIpAddress}
                onChange={e => setNewIpAddress(e.target.value)}
                className="flex-1 border rounded px-2 py-1 text-sm dark:bg-gray-800 dark:border-gray-600"
              />
              <Button size="sm" onClick={async () => {
                const ip = newIpAddress.trim();
                if (!ip) return;
                try {
                  await api.post('/settings/known-ips', { ip_address: ip, permanent: true });
                  setNewIpAddress('');
                  const d = await api.get('/settings/known-ips');
                  setKnownIps(d.known_ips || []);
                  notify('IP added', 'success');
                } catch (e) { notify(e.message, 'error'); }
              }}>Add Permanent</Button>
            </div>

            {/* IP list */}
            <div className="overflow-y-auto flex-1">
              {knownIps.length === 0 ? (
                <p className="text-sm text-gray-400 italic">No known IPs yet. Your browser IP will be registered automatically.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-500 border-b">
                      <th className="pb-1">IP Address</th>
                      <th className="pb-1">Type</th>
                      <th className="pb-1">Last Seen</th>
                      <th className="pb-1">Expires</th>
                      <th className="pb-1"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {knownIps.map(ip => (
                      <tr key={ip.id} className={`border-b ${ip.is_current ? 'bg-blue-50 dark:bg-blue-900/20' : ''}`}>
                        <td className="py-1.5">
                          {ip.ip_address}
                          {ip.is_current && <span className="ml-2 text-xs text-blue-600 dark:text-blue-400 font-medium">(you)</span>}
                        </td>
                        <td className="py-1.5">
                          {ip.permanent
                            ? <span className="text-xs bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 px-1.5 py-0.5 rounded">permanent</span>
                            : <span className="text-xs bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400 px-1.5 py-0.5 rounded">auto</span>
                          }
                        </td>
                        <td className="py-1.5 text-gray-500">{ip.last_seen_at ? new Date(ip.last_seen_at).toLocaleDateString() : '—'}</td>
                        <td className="py-1.5 text-gray-500">{ip.expires_at ? new Date(ip.expires_at).toLocaleDateString() : '—'}</td>
                        <td className="py-1.5 text-right">
                          <button
                            className="text-xs text-red-500 hover:underline"
                            onClick={async () => {
                              try {
                                await api.del(`/settings/known-ips/${ip.id}`);
                                const d = await api.get('/settings/known-ips');
                                setKnownIps(d.known_ips || []);
                                notify('IP removed', 'success');
                              } catch (e) { notify(e.message, 'error'); }
                            }}
                          >Remove</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
