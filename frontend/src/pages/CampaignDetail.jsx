import { useParams } from 'react-router-dom';
import { useState, useEffect, useCallback, useRef } from 'react';
import { useNotify } from '../context/NotificationContext';
import { useLoading } from '../context/LoadingContext';
import { api } from '../api';
import Button from '../components/ui/Button';
import { useConfirm } from '../context/ConfirmContext';

export default function CampaignDetail() {
  const { id } = useParams();
  const [campaign, setCampaign] = useState(null);
  const [inboxes, setInboxes] = useState([]);
  const [sequences, setSequences] = useState([]);
  const [leads, setLeads] = useState([]);
  const [queueData, setQueueData] = useState([]);
  const [sentData, setSentData] = useState([]);
  const [queueFilter, setQueueFilter] = useState(null); // email filter
  const queueRef = useRef(null);
  const [pastExpanded, setPastExpanded] = useState(false);
  const [recalcInProgress, setRecalcInProgress] = useState(false);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showSettings, setShowSettings] = useState(false);
  const [activeTab, setActiveTab] = useState('sequences'); // 'sequences' | 'leads' | 'queue'
  const confirm = useConfirm();

  const notify = useNotify();
  const loadingCtrl = useLoading();

  const loadAll = useCallback(async () => {
    loadingCtrl.start();
    try {
      const [camp, ibxs, seqs, lds, q, s] = await Promise.all([
        api.get(`/campaigns/${id}`),
        api.get('/inboxes'),
        api.get(`/campaigns/${id}/sequences`),
        api.get(`/campaigns/${id}/leads`),
        api.get(`/campaigns/${id}/queue`),
        api.get(`/campaigns/${id}/sent`),
      ]);
      setCampaign(camp);
      setInboxes(ibxs);
      setSequences(seqs);
      setLeads(lds);
      setQueueData(q);
      setSentData(s);
    } catch (e) {
      setError(e.message);
      notify({ type: 'error', message: 'Failed to load campaign data' });
    } finally {
      loadingCtrl.stop();
      setLoading(false);
    }
  }, [id, loadingCtrl, notify]);

  // load campaign data when the id changes
  // we only depend on `id` directly so that the effect won't rerun if
  // `loadAll` function identity changes (e.g. due to context value updates).
  useEffect(() => {
    loadAll();
  }, [id]);

  /* --- queue helpers --- */
  function estimatedTime(positionInDay) {
    if (!campaign) return '';
    const start = campaign.sending_hours_start || '09:00';
    const parts = start.split(':');
    let h = parseInt(parts[0], 10) || 9;
    let m = parseInt(parts[1], 10) || 0;
    const waitMin = campaign.wait_minutes_between || 5;
    const offset = (positionInDay - 1) * waitMin;
    m += offset;
    h += Math.floor(m / 60);
    m = m % 60;
    return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
  }

  function formatSentTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  function renderQueue() {
    const filter = queueFilter;
    const sent = filter ? sentData.filter(s => s.lead_email === filter) : sentData;
    const upcoming = filter ? queueData.filter(q => q.lead_email === filter) : queueData;
    const today = new Date().toISOString().slice(0, 10);

    if (!sent.length && !upcoming.length) {
      return <p>No emails sent or scheduled.</p>;
    }

    return (
      <>
        {/* past sent */}
        {sent.length > 0 && (
          <>
            <div
              className="cursor-pointer text-gray-600 font-semibold py-1 flex items-center gap-1"
              onClick={() => setPastExpanded(pe => !pe)}
            >
              <span className={"transform transition-transform" + (pastExpanded ? ' rotate-90' : '')}>&#9654;</span>
              Sent ({sent.length})
            </div>
            {pastExpanded && (
              <table className="w-full table-auto border-collapse mb-2">
                <thead>
                  <tr>
                    <th>Date</th><th>Time</th><th>Lead</th><th>Sequence</th><th>Subject</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(sent.reduce((acc,s) => {
                    const d = s.sent_date || 'unknown';
                    acc[d] = acc[d] || [];
                    acc[d].push(s);
                    return acc;
                  }, {})).sort().map(d => (
                    sent.filter(s => (s.sent_date||'') === d).map((s,i) => (
                      <tr
                        key={s.id}
                        className={!filter ? 'cursor-pointer hover:text-teal-600' : ''}
                        onClick={() => !filter && setQueueFilter(s.lead_email)}
                      >
                        <td>{i===0 ? d : ''}</td>
                        <td>{formatSentTime(s.sent_at)}</td>
                        <td className="font-mono">{s.lead_email}</td>
                        <td>Seq {s.sequence_index + 1}</td>
                        <td>{s.subject || ''}</td>
                      </tr>
                    ))
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}

        {/* upcoming */}
        {upcoming.length > 0 ? (
          <>
            <div className="text-gray-600 font-semibold py-1">Upcoming ({upcoming.length} scheduled)</div>
            <table className="w-full table-auto border-collapse">
              <thead>
                <tr>
                  <th>Date</th><th>Est. time</th><th>#</th><th>From (inbox)</th><th>Lead</th><th>Sequence</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(upcoming.reduce((acc,q) => {
                  const d = (q.scheduled_date && q.scheduled_date.includes('T')) ? q.scheduled_date.slice(0,10) : q.scheduled_date;
                  acc[d] = acc[d] || [];
                  acc[d].push(q);
                  return acc;
                }, {})).sort().map(d => (
                  upcoming.filter(q => ((q.scheduled_date||'').slice(0,10) === d)).sort((a,b) =>
                    (a.scheduled_date||'').localeCompare(b.scheduled_date||'') || a.position_in_day - b.position_in_day
                  ).map((q,i) => {
                    const isPast = d < today;
                    const time = (q.scheduled_date && q.scheduled_date.includes('T')) ? formatSentTime(q.scheduled_date) : estimatedTime(q.position_in_day);
                    return (
                      <tr
                        key={q.id}
                        className={(isPast ? 'text-gray-500 ' : '') + (!filter ? 'cursor-pointer hover:text-teal-600' : '')}
                        onClick={() => !filter && setQueueFilter(q.lead_email)}
                      >
                        <td>{i===0 ? d : ''}</td>
                        <td>{time}</td>
                        <td>{q.position_in_day}</td>
                        <td className="font-mono">{q.inbox_email || ''}</td>
                        <td className="font-mono">{q.lead_email}</td>
                        <td>Seq {q.sequence_index + 1}</td>
                      </tr>
                    );
                  })
                ))}
              </tbody>
            </table>
          </>
        ) : (!sent.length && <p>No scheduled emails in queue.</p>)}
      </>
    );
  }

  async function recalculateQueue() {
    setRecalcInProgress(true);
    try {
      const res = await api.post(`/campaigns/${id}/recalculate-queue`);
      if (res?.slots != null) {
        notify({ type: 'success', message: `Queue recalculated (${res.slots} slots)` });
        // refresh data
        const [q, s] = await Promise.all([
          api.get(`/campaigns/${id}/queue`),
          api.get(`/campaigns/${id}/sent`),
        ]);
        setQueueData(q);
        setSentData(s);
      }
    } catch (e) {
      notify({ type: 'error', message: e.message });
    } finally {
      setRecalcInProgress(false);
    }
  }

  if (error) return <div className="p-8 text-red-600">{error}</div>;
  if (loading || !campaign) return <div className="p-8">Loading...</div>;

  const inboxList = (campaign.inbox_ids || [])
    .map(iid => {
      const i = inboxes.find(x => x.id === iid);
      return i ? `${i.email}${i.max_emails_per_day ? ` (${i.max_emails_per_day}/day)` : ''}` : `id ${iid}`;
    })
    .join(', ');

  return (
    <div className="p-8">
      <div className="flex items-center gap-4 mb-4">
        <h1 className="text-2xl font-bold flex-1">{campaign.name}</h1>
        <button
          className="px-3 py-1 bg-red-500 text-white rounded"
          onClick={async () => {
            const ok = await confirm('Delete this campaign? This cannot be undone.');
            if (!ok) return;
            api.del(`/campaigns/${id}`).then(() => { window.location.href = '/campaigns'; });
          }}
        >
          Delete
        </button>
      </div>
      <div className="bg-white p-4 rounded shadow mb-6">
        <div className="flex justify-between">
          <div>
            <p><strong>Inboxes:</strong> {inboxList || 'None'}</p>
            <p><strong>Days:</strong> {(campaign.sending_days || []).map(d => ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]).join(', ')}</p>
            <p><strong>Hours:</strong> {campaign.sending_hours_start}–{campaign.sending_hours_end}</p>
            <p><strong>Stop on reply:</strong> {campaign.stop_on_reply ? 'Yes' : 'No'}</p>
          </div>
          <button
            className="px-3 py-1 bg-gray-200 rounded self-center"
            onClick={() => setShowSettings(true)}
          >
            Edit settings
          </button>
        </div>
      </div>

      {/* tab navigation */}
      <div className="flex gap-4 mb-6">
        {['sequences','leads','queue'].map(tab => (
          <button
            key={tab}
            className={
              "px-3 py-1 rounded" +
              (activeTab === tab ? ' bg-teal-500 text-white' : ' bg-gray-200')
            }
            onClick={() => setActiveTab(tab)}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* sequences section */}
      {activeTab === 'sequences' && (
        <>
          <h2 className="text-xl font-semibold mb-2">Sequences</h2>
          <Sequences
            sequences={sequences}
            campaignId={id}
            refresh={loadAll}
          />
        </>
      )}

      {/* leads section */}
      {activeTab === 'leads' && (
        <>
          <h2 className="text-xl font-semibold mt-6 mb-2">Enrolled leads</h2>
          <Leads
            leads={leads}
            campaignId={id}
            refresh={loadAll}
            onFilter={email => {
              setQueueFilter(email);
              setActiveTab('queue');
              queueRef.current?.scrollIntoView({ behavior: 'smooth' });
            }}
          />
        </>
      )}

      {/* queue toolbar */}
      {activeTab === 'queue' && (
        <>
          <h2 className="text-xl font-semibold mt-6 mb-2">Queue</h2>
          <div className="flex flex-wrap items-center gap-2 mb-4" ref={queueRef}>
            <button
              className="px-3 py-1 bg-gray-200 rounded"
              onClick={recalculateQueue}
              disabled={recalcInProgress}
            >
              {recalcInProgress ? 'Recalculating...' : 'Recalculate queue'}
            </button>
            {queueFilter && (
              <span className="text-teal-600 font-semibold">
                Showing: {queueFilter}{' '}
                <Button variant="link" size="sm" onClick={() => setQueueFilter(null)}>
                  Show all
                </Button>
              </span>
            )}
          </div>
          <div className="bg-white p-4 rounded shadow">
            {renderQueue()}
          </div>
        </>
      )}

      {/* modals */}
      {showSettings && (
        <SettingsModal
          campaign={campaign}
          inboxes={inboxes}
          onClose={() => setShowSettings(false)}
          onSave={loadAll}
        />
      )}
      {/* render queue components helpers */}
    </div>
  );
}

// subcomponents below
function Sequences({ sequences, campaignId, refresh }) {
  const notify = useNotify();
  const loadingCtrl = useLoading();
  const [pos, setPos] = useState(sequences.length);
  const [form, setForm] = useState({ subject: '', body: '', wait_days_after_previous: 0 });
  const [msg, setMsg] = useState(null);
  const [editing, setEditing] = useState(null); // sequence being edited

  useEffect(() => { setPos(sequences.length); }, [sequences]);

  const submit = async e => {
    e.preventDefault();
    try {
      await api.post(`/campaigns/${campaignId}/sequences`, { ...form, position: pos });
      setForm({ subject: '', body: '', wait_days_after_previous: 0 });
      refresh();
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  const startEdit = seq => {
    setEditing({ ...seq });
  };
  const saveEdit = async e => {
    e.preventDefault();
    loadingCtrl.start();
    try {
      await api.patch(`/campaigns/${campaignId}/sequences/${editing.id}`, editing);
      notify({ type: 'success', message: 'Sequence updated' });
      setEditing(null);
      refresh();
    } catch (e) {
      notify({ type: 'error', message: e.message });
    } finally {
      loadingCtrl.stop();
    }
  };
  const deleteSeq = async seq => {
    const ok = await confirm('Delete sequence #' + (seq.position+1) + '?');
    if (!ok) return;
    try {
      await api.del(`/campaigns/${campaignId}/sequences/${seq.id}`);
      notify({ type: 'success', message: 'Sequence deleted' });
      refresh();
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };

  return (
    <div className="mb-4">
      {sequences.length === 0 && <div className="bg-gray-100 p-4 rounded">No sequences yet.</div>}
      {sequences.length > 0 && (
        <table className="w-full table-auto border-collapse mb-2">
          <thead>
            <tr>
              <th>#</th><th>Wait</th><th>Subject</th><th>Body</th><th></th>
            </tr>
          </thead>
          <tbody>
            {sequences.map(s => (
              <tr key={s.id}>
                <td>{s.position + 1}</td>
                <td>{s.wait_days_after_previous}d</td>
                <td>{s.subject || '(reply)'}</td>
                <td className="truncate max-w-xs">{s.body}</td>
                <td className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => startEdit(s)}>Edit</Button>
                  <Button size="sm" variant="danger" onClick={() => deleteSeq(s)}>Delete</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form onSubmit={submit} className="bg-white p-4 rounded shadow">
        <h3 className="font-semibold mb-2">Add sequence</h3>
        {msg && <div className={msg.type === 'error' ? 'text-red-600' : 'text-green-600'}>{msg.text}</div>}
        <div className="space-y-2">
          <div>
            <label>Subject</label>
            <input className="w-full border rounded p-1" name="subject" value={form.subject} onChange={e => setForm(f => ({ ...f, subject: e.target.value }))} />
          </div>
          <div>
            <label>Body *</label>
            <textarea className="w-full border rounded p-1" required name="body" value={form.body} onChange={e => setForm(f => ({ ...f, body: e.target.value }))} rows={3} />
          </div>
          <div>
            <label>Wait days</label>
            <input type="number" className="w-20 border rounded p-1" name="wait_days_after_previous" value={form.wait_days_after_previous} onChange={e => setForm(f => ({ ...f, wait_days_after_previous: +e.target.value }))} />
          </div>
          <button className="px-3 py-1 bg-teal-500 text-white rounded">Add sequence</button>
        </div>
      </form>

      {/* edit modal */}
      {editing && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white p-6 rounded shadow w-full max-w-md">
            <h3 className="font-semibold mb-2">Edit Sequence</h3>
            <form onSubmit={saveEdit} className="space-y-2">
              <div>
                <label>Subject</label>
                <input className="w-full border rounded p-1" value={editing.subject || ''} onChange={e => setEditing(ed => ({ ...ed, subject: e.target.value }))} />
              </div>
              <div>
                <label>Body *</label>
                <textarea className="w-full border rounded p-1" required value={editing.body} onChange={e => setEditing(ed => ({ ...ed, body: e.target.value }))} rows={3} />
              </div>
              <div>
                <label>Wait days</label>
                <input type="number" className="w-20 border rounded p-1" value={editing.wait_days_after_previous} onChange={e => setEditing(ed => ({ ...ed, wait_days_after_previous: +e.target.value }))} />
              </div>
              <div className="flex gap-2">
                <button className="px-3 py-1 bg-teal-500 text-white rounded">Save</button>
                <button type="button" className="px-3 py-1 bg-gray-200 rounded" onClick={() => setEditing(null)}>Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function Leads({ leads, campaignId, refresh, onFilter }) {
  const [mode, setMode] = useState('single');
  const [single, setSingle] = useState({ email: '', name: '', custom: '' });
  const [bulk, setBulk] = useState('');
  const [msg, setMsg] = useState(null);
  const notify = useNotify();

  const addSingle = async e => {
    e.preventDefault();
    setMsg(null);
    if (!single.email.trim()) {
      setMsg({ type: 'error', text: 'Email required' });
      return;
    }
    try {
      await api.post(`/campaigns/${campaignId}/leads`, [{
        email: single.email.trim(),
        name: single.name.trim() || undefined,
        custom_data: single.custom ? JSON.parse(single.custom) : undefined,
      }]);
      setSingle({ email: '', name: '', custom: '' });
      refresh();
      notify({ type: 'success', message: 'Lead added' });
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  const addBulk = async e => {
    e.preventDefault();
    const emails = bulk.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
    if (!emails.length) {
      setMsg({ type: 'error', text: 'No emails provided' });
      return;
    }
    try {
      await api.post(`/campaigns/${campaignId}/leads`, emails.map(e => ({ email: e })));
      setBulk('');
      refresh();
      notify({ type: 'success', message: emails.length + ' leads added' });
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  const removeLead = async (lid, email) => {
    const ok = await confirm(`Remove lead ${email}?`);
    if (!ok) return;
    try {
      await api.del(`/campaigns/${campaignId}/leads/${lid}`);
      refresh();
      notify({ type: 'success', message: 'Lead removed' });
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };

  return (
    <div>
      {leads.length === 0 && <div className="bg-gray-100 p-4 rounded mb-4">No leads enrolled.</div>}
      {leads.length > 0 && (
        <table className="w-full table-auto border-collapse mb-4 bg-white rounded shadow">
          <thead>
            <tr>
              <th className="px-4 py-2">Email</th>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Stage</th>
              <th className="px-4 py-2">Enrolled</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {leads.map(l => (
              <tr key={l.lead_id}>
                <td className="px-4 py-2 font-mono cursor-pointer text-teal-600 hover:underline" onClick={() => onFilter && onFilter(l.email)}>{l.email}</td>
                <td className="px-4 py-2">{l.name || '—'}</td>
                <td className="px-4 py-2">{l.status}</td>
                <td className="px-4 py-2">{l.stage || '—'}</td>
                <td className="px-4 py-2">{new Date(l.enrolled_at).toLocaleDateString()}</td>
                <td className="px-4 py-2">
                  <Button size="sm" variant="danger" onClick={() => removeLead(l.lead_id, l.email)}>Remove</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="bg-white p-4 rounded shadow">
        <div className="flex gap-2 mb-4">
          <button onClick={() => setMode('single')} className={`px-3 py-1 rounded ${mode==='single'?'bg-teal-500 text-white':'bg-gray-200'}`}>Single lead</button>
          <button onClick={() => setMode('bulk')} className={`px-3 py-1 rounded ${mode==='bulk'?'bg-teal-500 text-white':'bg-gray-200'}`}>Bulk paste</button>
        </div>
        {msg && <div className={msg.type==='error'?'text-red-600':'text-green-600'}>{msg.text}</div>}
        {mode === 'single' && (
          <form onSubmit={addSingle} className="space-y-2">
            <div>
              <label>Email *</label>
              <input className="w-full border rounded p-1" value={single.email} onChange={e => setSingle(s => ({ ...s, email: e.target.value }))} required />
            </div>
            <div>
              <label>Name</label>
              <input className="w-full border rounded p-1" value={single.name} onChange={e => setSingle(s => ({ ...s, name: e.target.value }))} />
            </div>
            <div>
              <label>Custom (JSON)</label>
              <textarea className="w-full border rounded p-1" rows={2} value={single.custom} onChange={e => setSingle(s => ({ ...s, custom: e.target.value }))} />
            </div>
            <button className="px-3 py-1 bg-teal-500 text-white rounded">Add lead</button>
          </form>
        )}
        {mode === 'bulk' && (
          <form onSubmit={addBulk} className="space-y-2">
            <div>
              <label>Emails (one per line or comma-separated)</label>
              <textarea className="w-full border rounded p-1 font-mono" rows={4} value={bulk} onChange={e => setBulk(e.target.value)} />
            </div>
            <button className="px-3 py-1 bg-teal-500 text-white rounded">Add leads</button>
          </form>
        )}
      </div>
    </div>
  );
}

function SettingsModal({ campaign, inboxes, onClose, onSave }) {
  const [form, setForm] = useState({
    name: campaign.name,
    inbox_ids: campaign.inbox_ids || [],
    sending_days: campaign.sending_days || [0,1,2,3,4],
    sending_hours_start: campaign.sending_hours_start || '09:00',
    sending_hours_end: campaign.sending_hours_end || '17:00',
    stop_on_reply: campaign.stop_on_reply,
  });
  const [msg, setMsg] = useState(null);

  const toggleDay = d => {
    setForm(f => {
      const set = new Set(f.sending_days);
      if (set.has(d)) set.delete(d); else set.add(d);
      return { ...f, sending_days: Array.from(set).sort() };
    });
  };
  const toggleInbox = id => {
    setForm(f => {
      const set = new Set(f.inbox_ids);
      if (set.has(id)) set.delete(id); else set.add(id);
      return { ...f, inbox_ids: Array.from(set) };
    });
  };

  const submit = async e => {
    e.preventDefault();
    if (!form.name.trim()) { setMsg('Name is required'); return; }
    if (form.inbox_ids.length === 0) { setMsg('Select at least one inbox'); return; }
    try {
      await api.patch(`/campaigns/${campaign.id}`, form);
      onSave();
      onClose();
    } catch (e) {
      setMsg(e.message);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white p-6 rounded shadow w-full max-w-lg">
        <h3 className="text-lg font-semibold mb-2">Edit Campaign</h3>
        {msg && <div className="text-red-600 mb-2">{msg}</div>}
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium">Name *</label>
            <input className="mt-1 block w-full border rounded p-1" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} required />
          </div>
          <div>
            <label className="block text-sm font-medium">Sending inboxes *</label>
            <div className="mt-1 space-y-1 max-h-40 overflow-y-auto p-2 border border-gray-300 rounded">
              {inboxes.map(i => (
                <label key={i.id} className="flex items-center gap-2">
                  <input type="checkbox" checked={form.inbox_ids.includes(i.id)} onChange={() => toggleInbox(i.id)} />
                  {i.email}
                </label>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium">Sending days</label>
            <div className="mt-1 flex flex-wrap gap-2">
              {[0,1,2,3,4,5,6].map(d => (
                <label key={d} className="flex items-center gap-2">
                  <input type="checkbox" checked={form.sending_days.includes(d)} onChange={() => toggleDay(d)} />
                  {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]}
                </label>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium">Window start</label>
              <input className="mt-1 block w-full border rounded p-1" value={form.sending_hours_start} onChange={e => setForm(f => ({ ...f, sending_hours_start: e.target.value }))} />
            </div>
            <div>
              <label className="block text-sm font-medium">Window end</label>
              <input className="mt-1 block w-full border rounded p-1" value={form.sending_hours_end} onChange={e => setForm(f => ({ ...f, sending_hours_end: e.target.value }))} />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" checked={form.stop_on_reply} onChange={e => setForm(f => ({ ...f, stop_on_reply: e.target.checked }))} />
            <span className="text-sm">Stop sequence on reply</span>
          </div>
          <div className="flex gap-2">
            <button className="px-3 py-1 bg-teal-500 text-white rounded">Save</button>
            <button type="button" className="px-3 py-1 bg-gray-200 rounded" onClick={onClose}>Cancel</button>
          </div>
        </form>
      </div>
    </div>
  );
}
