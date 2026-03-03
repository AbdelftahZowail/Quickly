import { useEffect, useState, useRef, useCallback } from 'react';
import { api } from '../api';
import { useDarkMode } from '../context/DarkModeContext';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';
import { useAppMode } from '../context/AppModeContext';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';

/* ────────────────────────────────────────────────────────────────────────────
   Section definitions — each object drives both the sidebar TOC and the
   scroll-spy highlight.  `id` must match the element id in the JSX below.
   ──────────────────────────────────────────────────────────────────────────── */
const SECTIONS = [
  { id: 'general',    label: 'General' },
  { id: 'webhooks',   label: 'Webhooks' },
  { id: 'scheduling', label: 'Scheduling' },
  { id: 'test-mode',  label: 'Test Mode' },
  { id: 'utilities',  label: 'Utilities' },
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
  const [webhooks, setWebhooks] = useState([]);
  const [eventTypes, setEventTypes] = useState([]);
  const [newWh, setNewWh] = useState({ url: '', secret: '', description: '', events: [], active: true });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({});

  // scroll-spy
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);
  const contentRef = useRef(null);

  /* ── load data ── */
  const loadAll = useCallback(async () => {
    try {
      const [stratData, tmData, whList, evtData] = await Promise.all([
        api.get('/settings/scheduling-strategy'),
        api.get('/settings/test-mode'),
        api.get('/settings/webhooks'),
        api.get('/settings/webhooks/events'),
      ]);
      setStrategy(stratData.scheduling_strategy || 'priority');
      setTestMode(tmData.test_mode || false);
      setWebhooks(whList || []);
      setEventTypes(evtData.events || []);
      // default new webhook events = all
      setNewWh(prev => ({ ...prev, events: evtData.events || [] }));
    } catch {}
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  /* ── scroll-spy ── */
  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    const handleScroll = () => {
      const offsets = SECTIONS.map(s => {
        const el = document.getElementById(s.id);
        return el ? { id: s.id, top: el.getBoundingClientRect().top } : null;
      }).filter(Boolean);
      // pick the one closest to the top of the viewport (with a small margin)
      const active = offsets.reduce((best, cur) =>
        (cur.top <= 120 && cur.top > (best?.top ?? -Infinity)) ? cur : best,
        offsets[0],
      );
      if (active) setActiveSection(active.id);
    };
    container.addEventListener('scroll', handleScroll, { passive: true });
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      container.removeEventListener('scroll', handleScroll);
      window.removeEventListener('scroll', handleScroll);
    };
  }, []);

  const scrollTo = id => {
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

  /* ── event checkbox toggler ────────────────────────────────────────────── */
  const toggleEvent = (events, setEvents, evt) => {
    setEvents(events.includes(evt) ? events.filter(e => e !== evt) : [...events, evt]);
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
            <h3 className="text-sm font-semibold mb-2">Add Webhook</h3>
            <div className="space-y-2">
              <input
                type="text"
                placeholder="https://your-endpoint.example.com/hook"
                className="block w-full border rounded p-1.5 text-sm"
                value={newWh.url}
                onChange={e => setNewWh(p => ({ ...p, url: e.target.value }))}
              />
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Bearer secret (optional)"
                  className="flex-1 border rounded p-1.5 text-sm"
                  value={newWh.secret}
                  onChange={e => setNewWh(p => ({ ...p, secret: e.target.value }))}
                />
                <input
                  type="text"
                  placeholder="Description (optional)"
                  className="flex-1 border rounded p-1.5 text-sm"
                  value={newWh.description}
                  onChange={e => setNewWh(p => ({ ...p, description: e.target.value }))}
                />
              </div>
              <div>
                <span className="text-xs font-medium text-gray-600">Events:</span>
                <div className="flex flex-wrap gap-2 mt-1">
                  {eventTypes.map(evt => (
                    <label key={evt} className="flex items-center gap-1 text-xs">
                      <input
                        type="checkbox"
                        checked={newWh.events.includes(evt)}
                        onChange={() => toggleEvent(newWh.events, evts => setNewWh(p => ({ ...p, events: evts })), evt)}
                      />
                      {evt}
                    </label>
                  ))}
                </div>
              </div>
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
                <div className="space-y-2">
                  <input
                    type="text"
                    className="block w-full border rounded p-1.5 text-sm"
                    value={editForm.url}
                    onChange={e => setEditForm(p => ({ ...p, url: e.target.value }))}
                  />
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="Bearer secret"
                      className="flex-1 border rounded p-1.5 text-sm"
                      value={editForm.secret}
                      onChange={e => setEditForm(p => ({ ...p, secret: e.target.value }))}
                    />
                    <input
                      type="text"
                      placeholder="Description"
                      className="flex-1 border rounded p-1.5 text-sm"
                      value={editForm.description}
                      onChange={e => setEditForm(p => ({ ...p, description: e.target.value }))}
                    />
                  </div>
                  <div>
                    <span className="text-xs font-medium text-gray-600">Events:</span>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {eventTypes.map(evt => (
                        <label key={evt} className="flex items-center gap-1 text-xs">
                          <input
                            type="checkbox"
                            checked={(editForm.events || []).includes(evt)}
                            onChange={() => toggleEvent(editForm.events || [], evts => setEditForm(p => ({ ...p, events: evts })), evt)}
                          />
                          {evt}
                        </label>
                      ))}
                    </div>
                  </div>
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
                      <Button size="sm" variant="ghost" className="text-red-500" onClick={() => deleteWebhook(wh.id)}>Delete</Button>
                    </div>
                  </div>
                  {wh.description && <p className="text-xs text-gray-500 mb-1">{wh.description}</p>}
                  <div className="flex flex-wrap gap-1">
                    {(wh.events || []).map(evt => (
                      <span key={evt} className="text-[10px] bg-gray-100 dark:bg-gray-800 border rounded px-1.5 py-0.5">{evt}</span>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          ))}
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
      </div>
    </div>
  );
}
