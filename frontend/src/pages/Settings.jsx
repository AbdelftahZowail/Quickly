import { useEffect, useState, useRef, useCallback } from 'react';
import { api, apiCache } from '../api';
import { useDarkMode } from '../context/DarkModeContext';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';
import { useAppMode } from '../context/AppModeContext';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import EmailVerificationSettings from '../components/EmailVerificationSettings';

/* ────────────────────────────────────────────────────────────────────────────
   Section definitions — each object drives both the sidebar TOC and the
   scroll-spy highlight.  `id` must match the element id in the JSX below.
   ──────────────────────────────────────────────────────────────────────────── */
const SECTIONS = [
  { id: 'general',           label: 'General' },
  { id: 'webhooks',          label: 'Webhooks' },
  { id: 'ai',                label: 'AI Features' },
  { id: 'optional-features', label: 'Optional Features' },
  { id: 'scheduling',        label: 'Scheduling' },
  { id: 'test-mode',         label: 'Test Mode' },
  { id: 'utilities',         label: 'Utilities' },
];

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

export default function Settings() {
  const notify = useNotify();
  const { darkMode, toggleDarkMode } = useDarkMode();
  const confirm = useConfirm();
  const { isProduction } = useAppMode();

  /* ── state ── */
  const [strategy, setStrategy] = useState('priority');
  const [testMode, setTestMode] = useState(false);

  // Webhooks (new CRUD system)
  const [webhooks, setWebhooks] = useState(() => apiCache.get('/settings/webhooks') || []);
  const [eventTypes, setEventTypes] = useState([]);
  const [newWh, setNewWh] = useState({ url: '', secret: '', description: '', events: [], active: true });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({});

  // AI settings — keyed by feature id
  const [aiFeatures, setAiFeatures] = useState({});
  const [aiExpanded, setAiExpanded] = useState({});           // { featureId: bool } — collapsed by default
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

  // Known IPs
  const [knownIps, setKnownIps] = useState(() => apiCache.get('/settings/known-ips')?.known_ips || []);
  const [knownIpsOpen, setKnownIpsOpen] = useState(false);
  const [currentIp, setCurrentIp] = useState('');
  const [newIpAddress, setNewIpAddress] = useState('');

  // scroll-spy
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);
  const contentRef = useRef(null);

  /* ── load data ── */
  const loadAll = useCallback(async () => {
    try {
      const [stratData, tmData, whList, evtData, aiData, provData, ipData] = await Promise.all([
        api.get('/settings/scheduling-strategy'),
        api.get('/settings/test-mode'),
        api.get('/settings/webhooks'),
        api.get('/settings/webhooks/events'),
        api.get('/settings/ai'),
        api.get('/settings/ai/providers'),
        api.get('/settings/known-ips'),
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
      setAiProviders(provData.providers || []);
      setKnownIps(ipData.known_ips || []);
      setCurrentIp(ipData.current_ip || '');
    } catch {}
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  /* ── scroll-spy ── */
  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    const handleScroll = () => {
      const THRESHOLD = 120;
      const offsets = SECTIONS.map(s => {
        const el = document.getElementById(s.id);
        return el ? { id: s.id, top: el.getBoundingClientRect().top } : null;
      }).filter(Boolean);
      // Sections whose top edge has scrolled past the threshold are "active candidates"
      const past = offsets.filter(s => s.top <= THRESHOLD);
      if (past.length > 0) {
        // Pick the one closest to (but still at or above) the threshold
        const active = past.reduce((best, cur) => cur.top > best.top ? cur : best);
        setActiveSection(active.id);
      } else {
        // Nothing past threshold yet — highlight the first section
        setActiveSection(offsets[0]?.id ?? SECTIONS[0].id);
      }
    };
    container.addEventListener('scroll', handleScroll, { passive: true });
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      container.removeEventListener('scroll', handleScroll);
      window.removeEventListener('scroll', handleScroll);
    };
  }, []);

  const scrollTo = id => {
    setActiveSection(id);
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

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

  /* ── utility ── */
  const addOpensToAll = async () => {
    try {
      const resp = await api.post('/settings/add-opens');
      notify({ type: 'success', message: `Added opens to ${resp.added} email(s)` });
    } catch (e) { notify({ type: 'error', message: e.message }); }
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
      if (res.ok) notify({ type: 'success', message: 'Credentials verified ✓' });
      else notify({ type: 'error', message: `Verification failed: ${res.error}` });
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
    <div className="flex h-full">
      {/* ── sidebar TOC ── */}
      <nav className="hidden md:flex flex-col w-48 shrink-0 border-r border-gray-200 dark:border-gray-700 py-8 px-4 sticky top-0 h-screen overflow-y-auto">
        <h2 className="text-xs uppercase tracking-wider text-gray-400 mb-4 font-semibold">Contents</h2>
        {SECTIONS.map(s => (
          <button
            key={s.id}
            onClick={() => scrollTo(s.id)}
            className={`text-left text-sm py-1.5 px-2 rounded transition-colors ${
              activeSection === s.id
                ? 'bg-primary/10 text-primary font-medium'
                : 'text-gray-600 dark:text-gray-400 hover:text-primary'
            }`}
          >
            {s.label}
          </button>
        ))}
      </nav>

      {/* ── main content ── */}
      <div ref={contentRef} className="flex-1 overflow-y-auto p-8 max-w-3xl">
        <h1 className="text-2xl font-bold mb-6">Settings</h1>

        {/* ──────────────── General ──────────────── */}
        <section id="general" className="mb-10">
          <h2 className="text-lg font-semibold mb-3 border-b pb-2">General</h2>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={toggleDarkMode}>
              {darkMode ? 'Light Mode' : 'Dark Mode'}
            </Button>
            <span className="text-sm text-gray-500">(UI preference only)</span>
          </div>
        </section>

        {/* ──────────────── Webhooks ──────────────── */}
        <section id="webhooks" className="mb-10">
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
                        {wh.active ? 'Disable' : 'Enable'}
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

        {/* ──────────────── AI Features ──────────────── */}
        <section id="ai" className="mb-10">
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

            const setFeature = (updater) =>
              setAiFeatures(prev => ({ ...prev, [fid]: typeof updater === 'function' ? updater(prev[fid]) : { ...prev[fid], ...updater } }));
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
                        setFeature({ enabled: next });
                        saveAiFeature(fid, { enabled: next });
                      }}
                    />
                    <span className="text-xs font-medium text-gray-600 whitespace-nowrap">
                      {feature.enabled ? 'Disable' : 'Enable'}
                    </span>
                  </label>
                </div>

                {/* ── Expanded body ── */}
                <div className={`grid transition-[grid-template-rows] duration-200 ease-in-out ${isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
                  <div className="min-h-0 overflow-hidden">
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
                                  setFeature({ provider: p.value, model: '' });
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
                        onChange={e => setFeature({ api_key: e.target.value })}
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
                            setFeature({ model: e.target.value });
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
                                  setFeature({ model: m.id });
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
                    <div className="flex items-center gap-3 flex-wrap">
                      <Button size="sm" variant="outline" onClick={() => verifyAiFeature(fid)} disabled={verifying}>
                        {verifying ? 'Verifying…' : 'Verify'}
                      </Button>
                      <Button size="sm" onClick={() => saveAiFeature(fid)}>Save</Button>
                      {verifyResult && (
                        <span className={`text-sm font-medium ${verifyResult.ok ? 'text-green-600' : 'text-red-500'}`}>
                          {verifyResult.ok ? '✓ Credentials valid' : `✗ ${verifyResult.error}`}
                        </span>
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

        {/* ──────────────── Optional Features ──────────────── */}
        <section id="optional-features" className="mb-10">
          <h2 className="text-lg font-semibold mb-1 border-b pb-2">Optional Features</h2>
          <p className="text-xs text-gray-500 mb-4">
            Additional integrations that enhance lead processing.
          </p>

          {/* ── Email Verification ── */}
          <EmailVerificationSettings />


        </section>

        {/* ──────────────── Scheduling ──────────────── */}
        <section id="scheduling" className="mb-10">
          <h2 className="text-lg font-semibold mb-3 border-b pb-2">Scheduling Strategy</h2>
          <p className="text-xs text-gray-500 mb-3">Controls how Recalculate All Campaigns distributes emails.</p>
          <div className="space-y-3">
            <label className="flex gap-2 items-start cursor-pointer">
              <input type="radio" name="strategy" value="priority" checked={strategy === 'priority'} onChange={() => submitStrategy('priority')} />
              <span>
                <strong>Priority by campaign</strong><br />
                <span className="text-xs text-gray-500">
                  Campaigns processed in ascending priority order.{' '}
                  <a href="/campaigns" className="text-teal-500 underline">Reorder campaigns</a>
                </span>
              </span>
            </label>
            <label className="flex gap-2 items-start cursor-pointer">
              <input type="radio" name="strategy" value="round_robin" checked={strategy === 'round_robin'} onChange={() => submitStrategy('round_robin')} />
              <span>
                <strong>Round-robin distribution</strong><br />
                <span className="text-xs text-gray-500">
                  Inbox capacity divided evenly across active campaigns.
                </span>
              </span>
            </label>
          </div>
        </section>

        {/* ──────────────── Test Mode ──────────────── */}
        {!isProduction && (
          <section id="test-mode" className="mb-10">
            <h2 className="text-lg font-semibold mb-3 border-b pb-2">Test Mode</h2>
            <p className="text-xs text-gray-500 mb-2">
              When enabled emails are simulated — no real messages are sent.
            </p>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={testMode} onChange={e => submitTestMode(e.target.checked)} />
              <span className="text-sm">Enabled</span>
            </label>
          </section>
        )}

        {/* ──────────────── Utilities ──────────────── */}
        {!isProduction && (
          <section id="utilities" className="mb-10">
            <h2 className="text-lg font-semibold mb-3 border-b pb-2">Utilities</h2>
            <p className="text-xs text-gray-500 mb-2">
              Developer / test helper functions.
            </p>
            <Button size="sm" onClick={addOpensToAll}>
              Add open event to all sent emails
            </Button>
          </section>
        )}

        {/* ──────────────── Known IPs ──────────────── */}
        <section className="mb-10">
          <h2 className="text-lg font-semibold mb-2 border-b pb-2">Known IPs</h2>
          <p className="text-xs text-gray-500 mb-3">
            Opens and clicks from these IPs are ignored for self-open filtering.
          </p>
          <Button size="sm" variant="outline" onClick={() => setKnownIpsOpen(true)}>
            Manage Known IPs
          </Button>
        </section>
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
