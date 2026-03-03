import { useParams } from 'react-router-dom';
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNotify } from '../context/NotificationContext';
import { useLoading } from '../context/LoadingContext';
import { api } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import DatePicker from '../components/ui/DatePicker';
import { useConfirm } from '../context/ConfirmContext';
import ReactQuill from 'react-quill';
import 'react-quill/dist/quill.snow.css';
import {
  ResponsiveContainer,
  AreaChart as ReAreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts';

// ─── tabs ─────────────────────────────────────────────────────────────────────
const TABS = ['sequences', 'leads', 'analytics', 'queue', 'settings'];
const TAB_LABELS = {
  sequences: 'Sequences',
  leads: 'Leads',
  analytics: 'Analytics',
  queue: 'Queue',
  settings: 'Settings',
};

// ─── Main page ────────────────────────────────────────────────────────────────
export default function CampaignDetail() {
  const { id } = useParams();
  const [campaign, setCampaign] = useState(null);
  const [inboxes, setInboxes] = useState([]);
  const [sequences, setSequences] = useState([]);
  const [leads, setLeads] = useState([]);
  const [queueData, setQueueData] = useState([]);
  const [sentData, setSentData] = useState([]);
  const [allSentGlobal, setAllSentGlobal] = useState([]);
  const [queueFilter, setQueueFilter] = useState(null);
  const queueRef = useRef(null);
  const [pastExpanded, setPastExpanded] = useState(false);
  const [recalcInProgress, setRecalcInProgress] = useState(false);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('sequences');
  const confirm = useConfirm();
  const notify = useNotify();
  const loadingCtrl = useLoading();

  const loadAll = useCallback(async () => {
    loadingCtrl.start();
    try {
      const [camp, ibxs, seqs, lds, q, s, globalSent] = await Promise.all([
        api.get(`/campaigns/${id}`),
        api.get('/inboxes'),
        api.get(`/campaigns/${id}/sequences`),
        api.get(`/campaigns/${id}/leads`),
        api.get(`/campaigns/${id}/queue`),
        api.get(`/campaigns/${id}/sent`),
        api.get('/schedule/sent').catch(() => []),
      ]);
      setCampaign(camp);
      setInboxes(ibxs);
      setSequences(seqs);
      setLeads(lds);
      setQueueData(q);
      setSentData(s);
      setAllSentGlobal(globalSent);
    } catch (e) {
      setError(e.message);
      notify({ type: 'error', message: 'Failed to load campaign data' });
    } finally {
      loadingCtrl.stop();
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { loadAll(); }, [id]);

  /* ── queue helpers ── */
  function estimatedTime(positionInDay) {
    if (!campaign) return '';
    const start = campaign.sending_hours_start || '09:00';
    const [hStr, mStr] = start.split(':');
    let h = parseInt(hStr, 10) || 9;
    let m = parseInt(mStr, 10) || 0;
    const offset = (positionInDay - 1) * (campaign.wait_minutes_between || 5);
    m += offset;
    h += Math.floor(m / 60);
    m = m % 60;
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
  }

  function formatTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  }

  function renderQueue() {
    const filter = queueFilter;
    const sent     = filter ? sentData.filter(s => s.lead_email === filter) : sentData;
    const upcoming = filter ? queueData.filter(q => q.lead_email === filter) : queueData;
    const today    = new Date().toISOString().slice(0, 10);

    if (!sent.length && !upcoming.length)
      return <p className="text-gray-500">No emails sent or scheduled.</p>;

    return (
      <>
        {sent.length > 0 && (
          <>
            <div
              className="cursor-pointer text-gray-600 font-semibold py-2 flex items-center gap-1 select-none"
              onClick={() => setPastExpanded(p => !p)}
            >
              <span className={`inline-block transition-transform ${pastExpanded ? 'rotate-90' : ''}`}>▶</span>
              Sent ({sent.length})
            </div>
            {pastExpanded && (
              <table className="w-full text-sm border-collapse mb-3">
                <thead>
                  <tr className="bg-gray-50">
                    {['Date','Time','Lead','Sequence','Subject'].map(h => (
                      <th key={h} className="px-3 py-2 text-left font-semibold text-gray-600">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(sent.reduce((acc,s)=>{const d=s.sent_date||'?'; (acc[d]=acc[d]||[]).push(s); return acc;},{})).sort()
                    .flatMap(d => sent.filter(s=>(s.sent_date||'')=== d).map((s,i)=>(
                      <tr key={s.id} className={`border-b ${!filter?'cursor-pointer hover:bg-gray-50':''}`}
                        onClick={()=>!filter&&setQueueFilter(s.lead_email)}>
                        <td className="px-3 py-1.5">{i===0?d:''}</td>
                        <td className="px-3 py-1.5">{formatTime(s.sent_at)}</td>
                        <td className="px-3 py-1.5 font-mono">{s.lead_email}</td>
                        <td className="px-3 py-1.5">Seq {s.sequence_index+1}</td>
                        <td className="px-3 py-1.5">{s.subject||''}</td>
                      </tr>
                    )))}
                </tbody>
              </table>
            )}
          </>
        )}
        {upcoming.length > 0 && (
          <>
            <div className="text-gray-600 font-semibold py-2">Upcoming ({upcoming.length} scheduled)</div>
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-50">
                  {['Date','Est. time','#','From','Lead','Sequence'].map(h=>(
                    <th key={h} className="px-3 py-2 text-left font-semibold text-gray-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.keys(upcoming.reduce((acc,q)=>{const d=(q.scheduled_date||'').slice(0,10); (acc[d]=acc[d]||[]).push(q); return acc;},{})).sort()
                  .flatMap(d => upcoming
                    .filter(q=>(q.scheduled_date||'').slice(0,10)===d)
                    .sort((a,b)=>(a.scheduled_date||'').localeCompare(b.scheduled_date||'')||a.position_in_day-b.position_in_day)
                    .map((q,i)=>{
                      const isPast = d < today;
                      const t = q.scheduled_date?.includes('T') ? formatTime(q.scheduled_date) : estimatedTime(q.position_in_day);
                      return (
                        <tr key={q.id} className={`border-b ${isPast?'text-gray-400':''} ${!filter?'cursor-pointer hover:bg-gray-50':''}`}
                          onClick={()=>!filter&&setQueueFilter(q.lead_email)}>
                          <td className="px-3 py-1.5">{i===0?d:''}</td>
                          <td className="px-3 py-1.5">{t}</td>
                          <td className="px-3 py-1.5">{q.position_in_day}</td>
                          <td className="px-3 py-1.5 font-mono">{q.inbox_email||''}</td>
                          <td className="px-3 py-1.5 font-mono">{q.lead_email}</td>
                          <td className="px-3 py-1.5">Seq {q.sequence_index+1}</td>
                        </tr>
                      );
                    }))}
              </tbody>
            </table>
          </>
        )}
        {!upcoming.length && !sent.length && (
          <p className="text-gray-500">No scheduled emails in queue.</p>
        )}
      </>
    );
  }

  async function recalculateQueue() {
    setRecalcInProgress(true);
    try {
      const res = await api.post(`/campaigns/${id}/recalculate-queue`);
      if (res?.slots != null) {
        notify({ type: 'success', message: `Queue recalculated (${res.slots} slots)` });
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

  if (error)            return <div className="p-8 text-red-600">{error}</div>;
  if (loading||!campaign) return <div className="p-8 text-gray-400">Loading…</div>;

  return (
    <div className="p-6">
      {/* header */}
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold flex-1 truncate">{campaign.name}</h1>
        {campaign.paused && (
          <span className="px-2 py-0.5 bg-yellow-100 text-yellow-700 rounded-full text-xs font-semibold">Paused</span>
        )}
      </div>

      {/* tab bar */}
      <div className="flex gap-1 mb-6 border-b border-gray-200">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={
              'px-5 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ' +
              (activeTab === tab
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300')
            }
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {/* Sequences tab */}
      {activeTab === 'sequences' && (
        <SequencesTab
          sequences={sequences}
          campaignId={id}
          campaign={campaign}
          leads={leads}
          refresh={loadAll}
        />
      )}

      {/* Leads tab */}
      {activeTab === 'leads' && (
        <LeadsTab
          leads={leads}
          campaignId={id}
          refresh={loadAll}
          onViewQueue={email => {
            setQueueFilter(email);
            setActiveTab('queue');
            setTimeout(() => queueRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
          }}
        />
      )}

      {/* Analytics tab */}
      {activeTab === 'analytics' && (
        <CampaignAnalyticsTab
          campaignId={Number(id)}
          campaign={campaign}
          allSent={allSentGlobal}
        />
      )}

      {/* Queue tab */}
      {activeTab === 'queue' && (
        <div>
          <div className="flex flex-wrap items-center gap-2 mb-4" ref={queueRef}>
            <Button variant="outline" size="sm" onClick={recalculateQueue} disabled={recalcInProgress}>
              {recalcInProgress ? 'Recalculating…' : 'Recalculate queue'}
            </Button>
            {queueFilter && (
              <span className="text-sm text-teal-600 font-medium flex items-center gap-1">
                Showing: {queueFilter}
                <button className="ml-1 text-gray-400 hover:text-gray-600" onClick={() => setQueueFilter(null)}>✕</button>
              </span>
            )}
          </div>
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            {renderQueue()}
          </div>
        </div>
      )}

      {/* Settings tab */}
      {activeTab === 'settings' && (
        <SettingsTab
          campaign={campaign}
          inboxes={inboxes}
          onSave={loadAll}
          campaignId={id}
        />
      )}
    </div>
  );
}

// ─── Status badges ────────────────────────────────────────────────────────────
const BADGE_STYLES = {
  active:       'bg-emerald-100 text-emerald-700',
  completed:    'bg-blue-100 text-blue-700',
  unsubscribed: 'bg-gray-200 text-gray-600',
  bounced:      'bg-red-100 text-red-700',
  replied:      'bg-violet-100 text-violet-700',
  opened:       'bg-amber-100 text-amber-700',
  clicked:      'bg-orange-100 text-orange-700',
};

function StatusBadge({ label }) {
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${BADGE_STYLES[label] || 'bg-gray-100 text-gray-600'}`}>
      {label}
    </span>
  );
}

function deriveStatuses(lead) {
  const badges = [];
  if (lead.status === 'unsubscribed') badges.push('unsubscribed');
  else if (lead.status === 'bounced')  badges.push('bounced');
  else if (lead.stage  === 'Complete') badges.push('completed');
  else                                 badges.push('active');
  if (lead.replied) badges.push('replied');
  if (lead.opened)  badges.push('opened');
  if (lead.clicked) badges.push('clicked');
  return badges;
}

// ─── Leads Tab ────────────────────────────────────────────────────────────────
function LeadsTab({ leads, campaignId, refresh, onViewQueue }) {
  const notify  = useNotify();
  const confirm = useConfirm();
  const [mode, setMode]   = useState('single');
  const [single, setSingle] = useState({ email: '', name: '', custom: '' });
  const [bulk, setBulk]   = useState('');
  const [msg, setMsg]     = useState(null);
  const [editCell, setEditCell] = useState(null); // { leadId, field }
  const [editValue, setEditValue] = useState('');

  // All custom field names across all leads
  const customFields = useMemo(() => {
    const keys = new Set();
    leads.forEach(l => Object.keys(l.custom_data || {}).forEach(k => keys.add(k)));
    return [...keys].sort();
  }, [leads]);

  const startEdit = (leadId, field, val) => {
    setEditCell({ leadId, field });
    setEditValue(val == null ? '' : String(val));
  };
  const cancelEdit = () => setEditCell(null);

  const commitEdit = async (leadId) => {
    if (!editCell) return;
    const lead = leads.find(l => l.lead_id === leadId);
    if (!lead) { cancelEdit(); return; }
    const newCustom = { ...(lead.custom_data || {}), [editCell.field]: editValue };
    try {
      await api.patch(`/leads/${leadId}`, { custom_data: newCustom });
      notify({ type: 'success', message: 'Saved' });
      refresh();
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
    cancelEdit();
  };

  const removeLead = async (lid, email) => {
    const ok = await confirm(`Remove ${email} from this campaign?`);
    if (!ok) return;
    try {
      await api.del(`/campaigns/${campaignId}/leads/${lid}`);
      notify({ type: 'success', message: 'Lead removed' });
      refresh();
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };

  const addSingle = async e => {
    e.preventDefault();
    setMsg(null);
    if (!single.email.trim()) { setMsg({ type: 'error', text: 'Email required' }); return; }
    let custom_data;
    if (single.custom.trim()) {
      try { custom_data = JSON.parse(single.custom); }
      catch { setMsg({ type: 'error', text: 'Custom data must be valid JSON' }); return; }
    }
    try {
      await api.post(`/campaigns/${campaignId}/leads`, [{
        email: single.email.trim(),
        name: single.name.trim() || undefined,
        custom_data,
      }]);
      setSingle({ email: '', name: '', custom: '' });
      notify({ type: 'success', message: 'Lead added' });
      refresh();
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  const addBulk = async e => {
    e.preventDefault();
    const emails = bulk.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
    if (!emails.length) { setMsg({ type: 'error', text: 'No emails' }); return; }
    try {
      await api.post(`/campaigns/${campaignId}/leads`, emails.map(em => ({ email: em })));
      setBulk('');
      notify({ type: 'success', message: `${emails.length} lead(s) added` });
      refresh();
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  return (
    <div className="space-y-6">
      {/* Leads table */}
      {leads.length === 0 ? (
        <div className="bg-gray-50 rounded-lg border border-dashed border-gray-300 p-8 text-center text-gray-400">
          No leads enrolled yet. Add them below.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 shadow-sm">
          <table className="min-w-full text-sm bg-white">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">Email</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Name</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Status</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Stage</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">Enrolled</th>
                {customFields.map(f => (
                  <th key={f} className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap capitalize">{f}</th>
                ))}
                <th className="px-4 py-3 w-16"></th>
              </tr>
            </thead>
            <tbody>
              {leads.map(l => (
                <tr key={l.lead_id} className="border-b border-gray-100 hover:bg-gray-50/60 transition-colors">
                  {/* email */}
                  <td className="px-4 py-2.5">
                    <button
                      className="font-mono text-teal-600 hover:underline text-left"
                      onClick={() => onViewQueue?.(l.email)}
                      title="View queue for this lead"
                    >
                      {l.email}
                    </button>
                  </td>
                  {/* name */}
                  <td className="px-4 py-2.5 text-gray-700">{l.name || <span className="text-gray-300">—</span>}</td>
                  {/* status badges */}
                  <td className="px-4 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {deriveStatuses(l).map(s => <StatusBadge key={s} label={s} />)}
                    </div>
                  </td>
                  {/* stage */}
                  <td className="px-4 py-2.5 text-gray-600 whitespace-nowrap">{l.stage || '—'}</td>
                  {/* enrolled date */}
                  <td className="px-4 py-2.5 text-gray-500 whitespace-nowrap">
                    {new Date(l.enrolled_at).toLocaleDateString()}
                  </td>
                  {/* custom data columns — inline editable */}
                  {customFields.map(f => {
                    const isEditing = editCell?.leadId === l.lead_id && editCell?.field === f;
                    const val = (l.custom_data || {})[f];
                    return (
                      <td key={f} className="px-4 py-2.5 max-w-[200px]">
                        {isEditing ? (
                          <input
                            autoFocus
                            className="w-full border border-teal-400 rounded-md px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                            value={editValue}
                            onChange={e => setEditValue(e.target.value)}
                            onBlur={() => commitEdit(l.lead_id)}
                            onKeyDown={e => {
                              if (e.key === 'Enter')  commitEdit(l.lead_id);
                              if (e.key === 'Escape') cancelEdit();
                            }}
                          />
                        ) : (
                          <button
                            className="w-full text-left px-2 py-1 rounded-md border border-transparent hover:border-teal-200 hover:bg-teal-50 transition-colors group"
                            onClick={() => startEdit(l.lead_id, f, val)}
                            title="Click to edit"
                          >
                            {val != null
                              ? <span className="text-gray-800">{String(val)}</span>
                              : <span className="text-gray-300 italic group-hover:text-teal-300 text-xs">empty</span>
                            }
                          </button>
                        )}
                      </td>
                    );
                  })}
                  {/* remove */}
                  <td className="px-4 py-2.5 text-right">
                    <button
                      className="text-red-400 hover:text-red-600 text-xs font-medium transition-colors"
                      onClick={() => removeLead(l.lead_id, l.email)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add leads */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <h3 className="font-semibold text-gray-800 mb-3">Add leads</h3>
        <div className="flex gap-2 mb-4">
          <Button size="sm" variant={mode==='single'?'default':'outline'} onClick={()=>setMode('single')}>Single</Button>
          <Button size="sm" variant={mode==='bulk'?'default':'outline'}   onClick={()=>setMode('bulk')}>Bulk paste</Button>
        </div>
        {msg && <div className={`mb-2 text-sm ${msg.type==='error'?'text-red-600':'text-green-600'}`}>{msg.text}</div>}
        {mode === 'single' && (
          <form onSubmit={addSingle} className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm text-gray-600 mb-1">Email *</label>
                <input
                  type="email" required
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={single.email}
                  onChange={e => setSingle(s=>({...s, email: e.target.value}))}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-600 mb-1">Name</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={single.name}
                  onChange={e => setSingle(s=>({...s, name: e.target.value}))}
                />
              </div>
            </div>
            <div>
              <label className="block text-sm text-gray-600 mb-1">Custom data (JSON)</label>
              <textarea
                rows={2}
                className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-teal-300"
                placeholder='{"company": "Acme", "title": "CEO"}'
                value={single.custom}
                onChange={e => setSingle(s=>({...s, custom: e.target.value}))}
              />
            </div>
            <Button size="sm" variant="default">Add lead</Button>
          </form>
        )}
        {mode === 'bulk' && (
          <form onSubmit={addBulk} className="space-y-3">
            <div>
              <label className="block text-sm text-gray-600 mb-1">Emails (one per line or comma-separated)</label>
              <textarea
                rows={5}
                className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-teal-300"
                value={bulk}
                onChange={e => setBulk(e.target.value)}
              />
            </div>
            <Button size="sm" variant="default">Add leads</Button>
          </form>
        )}
      </div>
    </div>
  );
}

// ─── Analytics Tab ────────────────────────────────────────────────────────────
const SERIES_LIST = [
  { key: 'sent',         name: 'Sent',          stroke: 'rgba(59,130,246,0.8)',  fill: 'rgba(59,130,246,0.15)' },
  { key: 'totalOpens',   name: 'Total Opens',   stroke: 'rgba(234,179,8,0.8)',   fill: 'rgba(234,179,8,0.15)' },
  { key: 'uniqueOpens',  name: 'Unique Opens',  stroke: 'rgba(16,185,129,0.8)',  fill: 'rgba(16,185,129,0.15)' },
  { key: 'totalReplies', name: 'Replies',        stroke: 'rgba(45,212,191,0.8)',  fill: 'rgba(45,212,191,0.15)' },
  { key: 'totalClicks',  name: 'Total Clicks',  stroke: 'rgba(234,88,12,0.8)',   fill: 'rgba(234,88,12,0.15)' },
  { key: 'uniqueClicks', name: 'Unique Clicks', stroke: 'rgba(236,72,153,0.8)',  fill: 'rgba(236,72,153,0.15)' },
];

function CampaignAnalyticsTab({ campaignId, campaign, allSent }) {
  const today = new Date().toISOString().slice(0, 10);
  const past30 = new Date(); past30.setDate(past30.getDate()-30);

  const [startDate, setStartDate] = useState(past30.toISOString().slice(0,10));
  const [endDate,   setEndDate]   = useState(today);
  const [hide, setHide] = useState({
    sent: false, totalOpens: false, uniqueOpens: false,
    totalReplies: false, totalClicks: false, uniqueClicks: false,
  });
  const initializedRef = useRef(false);

  const filteredSent = useMemo(() =>
    allSent.filter(e => {
      if (String(e.campaign_id) !== String(campaignId)) return false;
      if (startDate && (e.sent_date||'') < startDate) return false;
      if (endDate   && (e.sent_date||'') > endDate)   return false;
      return true;
    }),
    [allSent, campaignId, startDate, endDate]
  );

  const chartData = useMemo(() => {
    const map = {};
    const seenOpen = {}, seenClick = {};
    filteredSent.forEach(e => {
      const d = e.sent_date || (e.sent_at?.slice(0,10) || '');
      if (!d) return;
      if (!map[d]) {
        map[d] = { date: d, sent: 0, totalOpens: 0, uniqueOpens: 0, totalReplies: 0, totalClicks: 0, uniqueClicks: 0 };
        seenOpen[d] = new Set(); seenClick[d] = new Set();
      }
      map[d].sent += 1;
      if (e.lead_status === 'replied') map[d].totalReplies += 1;
      (e.opens||[]).forEach(o => { map[d].totalOpens += 1; if (o?.ip) seenOpen[d].add(o.ip); });
      (e.clicks||[]).forEach(c => { map[d].totalClicks += 1; if (c?.ip) seenClick[d].add(c.ip); });
    });
    Object.keys(map).forEach(d => {
      map[d].uniqueOpens  = seenOpen[d].size;
      map[d].uniqueClicks = seenClick[d].size;
    });
    return Object.values(map).sort((a,b)=>a.date.localeCompare(b.date));
  }, [filteredSent]);

  useEffect(() => {
    if (!initializedRef.current && chartData.length > 0) {
      const nh = {};
      SERIES_LIST.forEach(s => { nh[s.key] = chartData.every(d => d[s.key] === 0); });
      setHide(p => ({ ...p, ...nh }));
      initializedRef.current = true;
    }
  }, [chartData]);

  const stats = campaign?.stats || {};
  const rangeSent    = chartData.reduce((a,d)=>a+d.sent, 0);
  const rangeOpens   = chartData.reduce((a,d)=>a+d.totalOpens, 0);
  const rangeReplies = chartData.reduce((a,d)=>a+d.totalReplies, 0);
  const rangeClicks  = chartData.reduce((a,d)=>a+d.totalClicks, 0);
  const openRate     = rangeSent > 0 ? Math.round(rangeOpens   / rangeSent * 100) : 0;
  const replyRate    = rangeSent > 0 ? Math.round(rangeReplies / rangeSent * 100) : 0;
  const clickRate    = rangeSent > 0 ? Math.round(rangeClicks  / rangeSent * 100) : 0;

  return (
    <div className="space-y-6">
      {/* All-time KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {[
          { label: 'Total Leads',   value: stats.total_leads  || 0 },
          { label: 'Emails Sent',   value: stats.emails_sent  || 0 },
          { label: 'Replies',       value: stats.replies      || 0 },
          { label: 'Pending',       value: stats.scheduled    || 0 },
        ].map(({ label, value }) => (
          <Card key={label} className="p-4">
            <div className="text-xs text-gray-500 mb-1">{label}</div>
            <div className="text-2xl font-bold text-gray-800">{value}</div>
          </Card>
        ))}
      </div>

      {/* Date range */}
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-sm text-gray-600">
          From
          <DatePicker value={startDate} onChange={setStartDate} />
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          To
          <DatePicker value={endDate} onChange={setEndDate} />
        </label>
      </div>

      {/* Range KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {[
          { label: 'Sent (range)',   value: rangeSent },
          { label: 'Open Rate',      value: `${openRate}%` },
          { label: 'Reply Rate',     value: `${replyRate}%` },
          { label: 'Click Rate',     value: `${clickRate}%` },
        ].map(({ label, value }) => (
          <Card key={label} className="p-4">
            <div className="text-xs text-gray-500 mb-1">{label}</div>
            <div className="text-2xl font-bold text-gray-800">{value}</div>
          </Card>
        ))}
      </div>

      {/* Chart */}
      {chartData.length === 0 ? (
        <Card className="p-10 text-center text-gray-400">No data for selected date range.</Card>
      ) : (
        <Card className="p-4">
          <div style={{ width: '100%', height: 290 }}>
            <ResponsiveContainer>
              <ReAreaChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 30 }}>
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip wrapperStyle={{ zIndex: 1000 }} />
                <CartesianGrid strokeDasharray="3 3" />
                {SERIES_LIST.map(s => (
                  <Area key={s.key} name={s.name} type="monotone" dataKey={s.key}
                    stroke={s.stroke} fill={s.fill} hide={hide[s.key]} />
                ))}
                <Legend
                  verticalAlign="bottom"
                  content={() => (
                    <div className="flex flex-wrap justify-center gap-3 mt-2">
                      {SERIES_LIST.map(s => (
                        <span
                          key={s.key}
                          onClick={() => setHide(p => ({ ...p, [s.key]: !p[s.key] }))}
                          className={`flex items-center gap-1 cursor-pointer select-none text-xs transition-opacity ${hide[s.key]?'opacity-40':''}`}
                        >
                          <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: s.stroke }} />
                          {s.name}
                        </span>
                      ))}
                    </div>
                  )}
                />
              </ReAreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}
    </div>
  );
}

// ─── Settings Tab ─────────────────────────────────────────────────────────────
function SettingsTab({ campaign, inboxes, onSave, campaignId }) {
  const confirm = useConfirm();
  const notify  = useNotify();
  const [form, setForm] = useState({
    name:                  campaign.name,
    inbox_ids:             campaign.inbox_ids         || [],
    sending_days:          campaign.sending_days      || [0,1,2,3,4],
    sending_hours_start:   campaign.sending_hours_start || '09:00',
    sending_hours_end:     campaign.sending_hours_end   || '17:00',
    stop_on_reply:         campaign.stop_on_reply,
    track_opens:           campaign.track_opens           ?? false,
    track_clicks:          campaign.track_clicks          ?? false,
    add_unsubscribe_header:campaign.add_unsubscribe_header ?? true,
    send_first_as_text:    campaign.send_first_as_text    ?? false,
    send_all_as_text:      campaign.send_all_as_text      ?? false,
    paused:                campaign.paused               ?? false,
    timezone:              campaign.timezone              ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
  const [msg,    setMsg]    = useState(null);
  const [saving, setSaving] = useState(false);

  const toggleDay   = d  => setForm(f => { const s=new Set(f.sending_days); s.has(d)?s.delete(d):s.add(d); return {...f, sending_days:[...s].sort()}; });
  const toggleInbox = id => setForm(f => { const s=new Set(f.inbox_ids);   s.has(id)?s.delete(id):s.add(id); return {...f, inbox_ids:[...s]}; });

  const submit = async e => {
    e.preventDefault();
    if (!form.name.trim())          { setMsg({type:'error',text:'Name is required'});           return; }
    if (!form.inbox_ids.length)     { setMsg({type:'error',text:'Select at least one inbox'});  return; }
    setSaving(true);
    try {
      await api.patch(`/campaigns/${campaignId}`, form);
      setMsg({ type: 'success', text: 'Settings saved' });
      onSave();
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const deleteCampaign = async () => {
    if (!await confirm('Delete this campaign? This cannot be undone.')) return;
    try {
      await api.del(`/campaigns/${campaignId}`);
      window.location.href = '/campaigns';
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };

  const TOGGLE_OPTIONS = [
    { key: 'stop_on_reply',            label: 'Stop sequence on reply' },
    { key: 'paused',                   label: 'Pause this campaign' },
    { key: 'track_opens',              label: 'Track email opens' },
    { key: 'track_clicks',             label: 'Track link clicks' },
    { key: 'add_unsubscribe_header',   label: 'Add List-Unsubscribe header (recommended)' },
    { key: 'send_first_as_text',       label: 'Send first email as plain text', disabled: form.send_all_as_text },
    { key: 'send_all_as_text',         label: 'Send all emails as plain text' },
  ];

  return (
    <div className="max-w-2xl space-y-8">
      <form onSubmit={submit} className="bg-white rounded-lg border border-gray-200 p-6 space-y-5">
        <h2 className="text-lg font-semibold text-gray-800">Campaign settings</h2>
        {msg && (
          <div className={`rounded-lg px-3 py-2 text-sm ${msg.type==='error'?'bg-red-50 text-red-700':'bg-green-50 text-green-700'}`}>
            {msg.text}
          </div>
        )}

        {/* Name */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
          <input
            required
            className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
            value={form.name}
            onChange={e => setForm(f=>({...f, name: e.target.value}))}
          />
        </div>

        {/* Inboxes */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Sending inboxes *</label>
          <div className="border rounded-lg p-3 max-h-48 overflow-y-auto space-y-1.5">
            {inboxes.length === 0 && <p className="text-sm text-gray-400">No inboxes configured.</p>}
            {inboxes.map(i => (
              <label key={i.id} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={form.inbox_ids.includes(i.id)} onChange={()=>toggleInbox(i.id)} />
                <span className="text-sm">{i.email}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Sending days */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Sending days</label>
          <div className="flex flex-wrap gap-3">
            {[0,1,2,3,4,5,6].map(d => (
              <label key={d} className="flex items-center gap-1.5 cursor-pointer text-sm">
                <input type="checkbox" checked={form.sending_days.includes(d)} onChange={()=>toggleDay(d)} />
                {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]}
              </label>
            ))}
          </div>
        </div>

        {/* Hours */}
        <div className="grid grid-cols-2 gap-4">
          {[
            { key: 'sending_hours_start', label: 'Window start' },
            { key: 'sending_hours_end',   label: 'Window end' },
          ].map(({key, label}) => (
            <div key={key}>
              <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
              <input
                type="time"
                className="w-full border rounded-lg px-3 py-2 text-sm"
                value={form[key]}
                onChange={e => setForm(f=>({...f, [key]: e.target.value}))}
              />
            </div>
          ))}
        </div>

        {/* Timezone */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Timezone</label>
          <select
            className="w-full border rounded-lg px-3 py-2 text-sm"
            value={form.timezone}
            onChange={e => setForm(f=>({...f, timezone: e.target.value}))}
          >
            {Intl.supportedValuesOf('timeZone').map(tz => (
              <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>
            ))}
          </select>
          <p className="text-xs text-gray-400 mt-1">
            Sending window times are interpreted in this timezone.
          </p>
        </div>

        {/* Toggle options */}
        <div>
          <p className="text-sm font-medium text-gray-700 mb-2">Options</p>
          <div className="space-y-2.5">
            {TOGGLE_OPTIONS.map(({ key, label, disabled }) => (
              <label key={key} className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  disabled={disabled}
                  checked={form[key]}
                  onChange={e => {
                    const v = e.target.checked;
                    setForm(f => ({
                      ...f, [key]: v,
                      ...(key==='send_all_as_text'&&v ? {send_first_as_text:false} : {}),
                    }));
                  }}
                />
                <span className={`text-sm ${disabled?'text-gray-400':'text-gray-700'}`}>{label}</span>
              </label>
            ))}
          </div>
        </div>

        {(form.send_all_as_text||form.send_first_as_text) && (
          <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            ⚠ HTML settings on sequences will be ignored for affected emails.
          </p>
        )}

        <Button variant="default" disabled={saving}>
          {saving ? 'Saving…' : 'Save settings'}
        </Button>
      </form>

      {/* Danger zone */}
      <div className="bg-white rounded-lg border border-red-200 p-6">
        <h3 className="font-semibold text-red-700 mb-1">Danger zone</h3>
        <p className="text-sm text-gray-500 mb-4">Permanently delete this campaign and all its data. This cannot be undone.</p>
        <Button variant="destructive" onClick={deleteCampaign}>Delete campaign</Button>
      </div>
    </div>
  );
}

// ─── Quill editor config ──────────────────────────────────────────────────────
const QUILL_MODULES = {
  toolbar: [
    [{ header: [1,2,3,false] }],
    ['bold','italic','underline','strike'],
    [{ list:'ordered' },{ list:'bullet' }],
    ['link','blockquote','code-block'],
    ['clean'],
  ],
};
const QUILL_FORMATS = [
  'header','bold','italic','underline','strike',
  'list','bullet','link','blockquote','code-block',
];

function SequenceBodyEditor({ value, onChange, isHtml, onIsHtmlChange, isFirstSequence, campaign, required }) {
  const [copiedVar, setCopiedVar] = useState(null);
  const notify = useNotify();

  const copyVar = (v) => {
    navigator.clipboard?.writeText(v);
    setCopiedVar(v);
    notify({ type: 'success', message: `Copied ${v}`, duration: 1500 });
    setTimeout(() => setCopiedVar(null), 1500);
  };

  const forcePlainAll   = campaign?.send_all_as_text;
  const forcePlainFirst = campaign?.send_first_as_text && isFirstSequence;
  const isOverridden    = forcePlainAll || forcePlainFirst;
  const effectiveHtml   = isHtml && !isOverridden;

  const overrideMsg = forcePlainAll
    ? 'Campaign is set to send all emails as plain text — HTML will be ignored.'
    : forcePlainFirst
    ? 'Campaign sends the first email as plain text — HTML will be ignored for this sequence.'
    : null;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input type="checkbox" checked={isHtml} onChange={e=>onIsHtmlChange(e.target.checked)} />
          <span className="text-sm font-medium">Send as HTML</span>
        </label>
        {isOverridden && isHtml && (
          <span className="text-xs font-medium text-amber-600 bg-amber-50 border border-amber-200 rounded px-2 py-0.5">
            ⚠ {overrideMsg}
          </span>
        )}
      </div>
      {effectiveHtml ? (
        <div className="border rounded overflow-hidden">
          <ReactQuill
            theme="snow" value={value} onChange={onChange}
            modules={QUILL_MODULES} formats={QUILL_FORMATS}
            style={{ minHeight: '160px' }}
          />
        </div>
      ) : (
        <textarea
          className="w-full border rounded-lg p-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
          required={required}
          value={value}
          onChange={e => onChange(e.target.value)}
          rows={5}
          placeholder="Email body…"
        />
      )}
      <p className="mt-1 text-xs text-gray-500 flex flex-wrap items-center gap-1">
        <span>Variables:</span>
        {['{{name}}','{{email}}','{{unsubscribe_link}}'].map(v => (
          <span key={v} className="relative inline-flex items-center">
            <code
              className="px-1 bg-gray-100 rounded cursor-pointer hover:bg-teal-100 transition-colors select-none"
              title="Click to copy"
              onClick={() => copyVar(v)}
            >
              {v}
            </code>
            {copiedVar === v && (
              <span className="absolute -top-6 left-1/2 -translate-x-1/2 bg-gray-800 text-white text-xs rounded px-1.5 py-0.5 whitespace-nowrap pointer-events-none z-10 animate-pulse">
                Copied!
              </span>
            )}
          </span>
        ))}
        <span className="text-gray-400">+ custom fields like <code className="px-1 bg-gray-100 rounded">{'{{company}}'}</code></span>
      </p>
    </div>
  );
}

// ─── Preview Modal ────────────────────────────────────────────────────────────
function PreviewModal({ sequence, campaignId, leads, onClose }) {
  const [leadId,    setLeadId]    = useState(leads[0]?.lead_id ?? '');
  const [preview,   setPreview]   = useState(null);
  const [loading,   setLoading]   = useState(false);
  const [err,       setErr]       = useState(null);
  const [testEmail, setTestEmail] = useState('');
  const [testState, setTestState] = useState(null); // null | 'sending' | 'success' | {error}

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = await api.post(`/campaigns/${campaignId}/preview`, {
        sequence_id: sequence.id,
        lead_id: leadId ? Number(leadId) : null,
      });
      setPreview(data);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }, [campaignId, sequence.id, leadId]);

  useEffect(() => { load(); }, [load]);

  const sendTest = async () => {
    if (!testEmail.trim()) return;
    setTestState('sending');
    try {
      await api.post(`/campaigns/${campaignId}/send-test`, {
        sequence_id: sequence.id,
        lead_id: leadId ? Number(leadId) : null,
        to_email: testEmail.trim(),
      });
      setTestState('success');
      setTimeout(() => setTestState(null), 3000);
    } catch (e) {
      setTestState({ error: e.message });
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        data-darkreader-ignore
        className="rounded shadow w-full max-w-3xl max-h-[90vh] flex flex-col"
        style={{ backgroundColor: 'white' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="font-semibold text-gray-800">
            Preview — Sequence #{(sequence.position ?? 0) + 1}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">×</button>
        </div>

        {/* Lead picker */}
        <div className="px-6 py-3 border-b bg-gray-50 flex flex-wrap items-center gap-3">
          <label className="text-sm font-medium text-gray-600">Preview as:</label>
          <select
            className="border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
            value={leadId}
            onChange={e => setLeadId(e.target.value)}
          >
            <option value="">No lead — show placeholders</option>
            {leads.map(l => (
              <option key={l.lead_id} value={l.lead_id}>
                {l.email}{l.name ? ` — ${l.name}` : ''}
              </option>
            ))}
          </select>
          {preview?.tracking_note && (
            <span className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-2 py-0.5">
              ℹ {preview.tracking_note}
            </span>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {loading && (
            <div className="flex items-center justify-center py-12 text-gray-400">Loading preview…</div>
          )}
          {err && <div className="text-red-600 text-sm">{err}</div>}
          {preview && !loading && (
            <div className="space-y-4">
              {/* Subject */}
              <div className="bg-gray-50 rounded-lg px-4 py-3">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1">Subject</span>
                <p className="font-medium text-gray-800">
                  {preview.subject || <em className="text-gray-400 font-normal">Reply in thread</em>}
                </p>
              </div>
              {/* Body */}
              <div>
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-2">Body</span>
                {preview.is_html ? (
                  <div
                    className="border rounded-lg p-5 bg-white prose prose-sm max-w-none"
                    dangerouslySetInnerHTML={{ __html: preview.body }}
                  />
                ) : (
                  <pre className="border rounded-lg p-5 bg-gray-50 text-sm whitespace-pre-wrap font-sans text-gray-800">
                    {preview.body}
                  </pre>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Footer: test email + close */}
        <div className="px-6 py-3 border-t space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-gray-600 whitespace-nowrap">Send test to:</span>
            <input
              type="email"
              value={testEmail}
              onChange={e => setTestEmail(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && sendTest()}
              placeholder="you@example.com"
              className="flex-1 min-w-0 border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
            />
            <Button
              size="sm"
              variant="default"
              onClick={sendTest}
              disabled={testState === 'sending' || !testEmail.trim()}
            >
              {testState === 'sending' ? 'Sending…' : 'Send test'}
            </Button>
            {testState === 'success' && (
              <span className="text-xs text-green-600 font-medium">✓ Sent!</span>
            )}
            {testState?.error && (
              <span className="text-xs text-red-600">{testState.error}</span>
            )}
          </div>
          <div className="flex justify-end">
            <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Sequences Tab ────────────────────────────────────────────────────────────
function SequencesTab({ sequences, campaignId, campaign, leads, refresh }) {
  const notify      = useNotify();
  const confirm     = useConfirm();
  const loadingCtrl = useLoading();
  const [pos, setPos] = useState(sequences.length);
  const [form, setForm] = useState({ subject: '', body: '', wait_days_after_previous: 0, is_html: false });
  const [msg,  setMsg]  = useState(null);
  const [editing,         setEditing]         = useState(null);
  const [originalEditing, setOriginalEditing] = useState(null); // snapshot for change detection
  const [editDirty,       setEditDirty]       = useState(false);
  const [showEditWarning, setShowEditWarning] = useState(false);
  const [previewSeq, setPreviewSeq] = useState(null);

  useEffect(() => { setPos(sequences.length); }, [sequences]);

  const openEdit = (seq) => {
    const copy = { ...seq, is_html: seq.is_html ?? false };
    setEditing(copy);
    setOriginalEditing(copy);
    setEditDirty(false);
  };

  const updateEditing = (patch) => {
    setEditing(ed => ({ ...ed, ...patch }));
    setEditDirty(true);
  };

  const tryCloseEdit = () => {
    if (editDirty) {
      setShowEditWarning(true);
    } else {
      setEditing(null);
    }
  };

  const submit = async e => {
    e.preventDefault();
    try {
      await api.post(`/campaigns/${campaignId}/sequences`, { ...form, position: pos });
      setForm({ subject: '', body: '', wait_days_after_previous: 0, is_html: false });
      setMsg({ type: 'success', text: 'Sequence added' });
      refresh();
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  const saveEdit = async e => {
    e.preventDefault();
    loadingCtrl.start();
    try {
      await api.patch(`/campaigns/${campaignId}/sequences/${editing.id}`, editing);
      notify({ type: 'success', message: 'Sequence updated' });
      setEditing(null);
      setEditDirty(false);
      refresh();
    } catch (e) {
      notify({ type: 'error', message: e.message });
    } finally {
      loadingCtrl.stop();
    }
  };

  const deleteSeq = async seq => {
    if (!await confirm(`Delete sequence #${seq.position+1}?`)) return;
    try {
      await api.del(`/campaigns/${campaignId}/sequences/${seq.id}`);
      notify({ type: 'success', message: 'Sequence deleted' });
      refresh();
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };

  return (
    <div className="space-y-6">
      {sequences.length === 0 ? (
        <div className="bg-gray-50 rounded-lg border border-dashed border-gray-300 p-8 text-center text-gray-400">
          No sequences yet. Add one below.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 shadow-sm">
          <table className="min-w-full text-sm bg-white">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-3 text-left font-semibold text-gray-600">#</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Wait</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Subject</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Body preview</th>
                <th className="px-4 py-3 text-center font-semibold text-gray-600">HTML</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {sequences.map(s => (
                <tr key={s.id} className="border-b border-gray-100 hover:bg-gray-50/60">
                  <td className="px-4 py-3 font-medium text-gray-700">{s.position+1}</td>
                  <td className="px-4 py-3 text-gray-500 whitespace-nowrap">{s.wait_days_after_previous}d</td>
                  <td className="px-4 py-3 text-gray-800">
                    {s.subject || <em className="text-gray-400">reply</em>}
                  </td>
                  <td className="px-4 py-3 text-gray-500 max-w-sm">
                    <span className="truncate block">{s.is_html ? '〈HTML〉' : s.body}</span>
                  </td>
                  <td className="px-4 py-3 text-center text-gray-500">{s.is_html ? '✓' : '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1.5 justify-end">
                      <Button size="sm" variant="outline" onClick={()=>setPreviewSeq(s)}>Preview</Button>
                      <Button size="sm" variant="outline" onClick={()=>openEdit(s)}>Edit</Button>
                      <Button size="sm" variant="destructive"  onClick={()=>deleteSeq(s)}>Delete</Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add sequence */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <h3 className="font-semibold text-gray-800 mb-3">Add sequence</h3>
        {msg && <div className={`mb-2 text-sm ${msg.type==='error'?'text-red-600':'text-green-600'}`}>{msg.text}</div>}
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-sm text-gray-600 mb-1">Subject</label>
            <input
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
              value={form.subject}
              onChange={e => setForm(f=>({...f, subject: e.target.value}))}
              placeholder="Leave blank to reply in same thread"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-600 mb-1">Body *</label>
            <SequenceBodyEditor
              value={form.body}
              onChange={val => setForm(f=>({...f, body: val}))}
              isHtml={form.is_html}
              onIsHtmlChange={v => setForm(f=>({...f, is_html: v}))}
              isFirstSequence={pos===0}
              campaign={campaign}
              required
            />
          </div>
          <div>
            <label className="block text-sm text-gray-600 mb-1">Wait days after previous</label>
            <input
              type="number" min={0}
              className="w-24 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
              value={form.wait_days_after_previous}
              onChange={e => setForm(f=>({...f, wait_days_after_previous: +e.target.value}))}
            />
          </div>
          <Button size="sm" variant="default">Add sequence</Button>
        </form>
      </div>

      {/* Edit modal */}
      {editing && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={tryCloseEdit}
        >
          <div
            data-darkreader-ignore
            className="p-6 rounded shadow w-full max-w-2xl max-h-[90vh] overflow-y-auto"
            style={{ backgroundColor: 'white' }}
            onClick={e=>e.stopPropagation()}
          >
            <h3 className="font-semibold text-gray-800 mb-4">Edit Sequence #{editing.position+1}</h3>
            <form onSubmit={saveEdit} className="space-y-4">
              <div>
                <label className="block text-sm text-gray-600 mb-1">Subject</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={editing.subject||''}
                  onChange={e=>updateEditing({ subject: e.target.value })}
                  placeholder="Leave blank to reply in same thread"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-600 mb-1">Body *</label>
                <SequenceBodyEditor
                  value={editing.body}
                  onChange={val=>updateEditing({ body: val })}
                  isHtml={editing.is_html??false}
                  onIsHtmlChange={v=>updateEditing({ is_html: v })}
                  isFirstSequence={editing.position===0}
                  campaign={campaign}
                  required
                />
              </div>
              <div>
                <label className="block text-sm text-gray-600 mb-1">Wait days</label>
                <input
                  type="number" min={0}
                  className="w-24 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                  value={editing.wait_days_after_previous}
                  onChange={e=>updateEditing({ wait_days_after_previous: +e.target.value })}
                />
              </div>
              <div className="flex gap-2 pt-1">
                <Button size="sm" variant="default">Save</Button>
                <Button type="button" size="sm" variant="outline" onClick={tryCloseEdit}>Cancel</Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Unsaved changes warning */}
      {showEditWarning && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60]">
          <div data-darkreader-ignore className="rounded shadow p-6 max-w-sm w-full mx-4" style={{ backgroundColor: 'white' }}>
            <h3 className="font-semibold text-gray-800 mb-1">Discard changes?</h3>
            <p className="text-sm text-gray-500 mb-4">You have unsaved changes. Closing will discard them.</p>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="outline" onClick={() => setShowEditWarning(false)}>Keep editing</Button>
              <Button size="sm" variant="destructive" onClick={() => { setShowEditWarning(false); setEditing(null); setEditDirty(false); }}>Discard</Button>
            </div>
          </div>
        </div>
      )}

      {/* Preview modal */}
      {previewSeq && (
        <PreviewModal
          sequence={previewSeq}
          campaignId={campaignId}
          leads={leads}
          onClose={()=>setPreviewSeq(null)}
        />
      )}
    </div>
  );
}
