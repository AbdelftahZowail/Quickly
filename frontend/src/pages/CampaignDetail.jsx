import { useParams } from 'react-router-dom';
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNotify } from '../context/NotificationContext';
import { useLoading } from '../context/LoadingContext';
import { api, apiCache } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import DatePicker from '../components/ui/DatePicker';
import { useConfirm } from '../context/ConfirmContext';
import { useAppMode } from '../context/AppModeContext';
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
const TABS = ['analytics', 'sequences', 'leads', 'queue', 'settings'];
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
  const [campaign, setCampaign] = useState(() => apiCache.get(`/campaigns/${id}`) || null);
  const [inboxes, setInboxes] = useState(() => apiCache.get('/inboxes') || []);
  const [sequences, setSequences] = useState(() => apiCache.get(`/campaigns/${id}/sequences`) || []);
  const [leads, setLeads] = useState(() => apiCache.get(`/campaigns/${id}/leads`) || []);
  const [queueData, setQueueData] = useState(() => apiCache.get(`/campaigns/${id}/queue`) || []);
  const [sentData, setSentData] = useState(() => apiCache.get(`/campaigns/${id}/sent`) || []);
  const [queueFilter, setQueueFilter] = useState(null);
  const queueRef = useRef(null);
  const [pastExpanded, setPastExpanded] = useState(false);
  const [recalcInProgress, setRecalcInProgress] = useState(false);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(() => !apiCache.get(`/campaigns/${id}`));
  const [activeTab, setActiveTab] = useState(() => {
    const hash = window.location.hash.replace('#', '');
    return TABS.includes(hash) ? hash : 'analytics';
  });
  const confirm = useConfirm();
  const notify = useNotify();
  const loadingCtrl = useLoading();
  const { isProduction } = useAppMode();

  // Sync hash ↔ tab
  useEffect(() => {
    window.location.hash = activeTab;
  }, [activeTab]);

  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.replace('#', '');
      if (TABS.includes(hash)) setActiveTab(hash);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

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
    const base = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
    return isProduction ? base : base + `:${String(d.getSeconds()).padStart(2,'0')}`;
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
          sentData={sentData}
          sequences={sequences}
          onRefresh={loadAll}
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
  active:         'bg-emerald-100 text-emerald-700',
  completed:      'bg-blue-100 text-blue-700',
  unsubscribed:   'bg-gray-200 text-gray-600',
  bounced:        'bg-red-100 text-red-700',
  replied:        'bg-violet-100 text-violet-700',
  opened:         'bg-amber-100 text-amber-700',
  clicked:        'bg-orange-100 text-orange-700',
  interested:     'bg-green-100 text-green-700',
  not_interested: 'bg-rose-100 text-rose-700',
  out_of_office:  'bg-sky-100 text-sky-700',
  wrong_person:   'bg-purple-100 text-purple-700',
  auto_reply:     'bg-slate-100 text-slate-600',
  paused:         'bg-yellow-100 text-yellow-700',
  // Email verification statuses
  valid:          'bg-emerald-100 text-emerald-700',
  invalid:        'bg-red-100 text-red-700',
  risky:          'bg-orange-100 text-orange-700',
  catch_all:      'bg-yellow-100 text-yellow-700',
  unknown:        'bg-gray-200 text-gray-600',
  pending:        'bg-blue-100 text-blue-600',
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
  if (lead.interest_status === 'interested') badges.push('interested');
  if (lead.interest_status === 'not_interested') badges.push('not_interested');
  if (lead.sending_paused) badges.push('paused');
  if (lead.email_verification_status) badges.push(lead.email_verification_status);
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
  const [skipDuplicates, setSkipDuplicates] = useState(true);
  const [verifyEmails, setVerifyEmails] = useState(false);
  const [emailVerifEnabled, setEmailVerifEnabled] = useState(() => {
    try { return localStorage.getItem('emailVerifEnabled') === 'true'; } catch { return false; }
  });
  const [lastDuplicates, setLastDuplicates] = useState([]);
  const [editCell, setEditCell] = useState(null); // { leadId, field }
  const [editValue, setEditValue] = useState('');
  const [importing, setImporting] = useState(false);
  const [showFormatInfo, setShowFormatInfo] = useState(false);
  const fileInputRef = useRef(null);
  const [verifying, setVerifying] = useState(false);
  const [verificationSummary, setVerificationSummary] = useState(null);
  // filters: { status, opened, replied, clicked, verification }
  const [filters, setFilters] = useState({ status: 'all', opened: 'all', replied: 'all', clicked: 'all', verification: 'all' });
  const setFilter = (key, val) => setFilters(prev => ({ ...prev, [key]: val }));
  const hasActiveFilter = Object.values(filters).some(v => v !== 'all');

  // All custom field names across all leads
  const customFields = useMemo(() => {
    const keys = new Set();
    leads.forEach(l => Object.keys(l.custom_data || {}).forEach(k => keys.add(k)));
    return [...keys].sort();
  }, [leads]);

  // Filtered leads based on all active filters
  const filteredLeads = useMemo(() => {
    return leads.filter(l => {
      if (filters.status !== 'all') {
        if (filters.status === 'active' && l.status !== 'active') return false;
        if (filters.status === 'bounced' && l.status !== 'bounced') return false;
        if (filters.status === 'unsubscribed' && l.status !== 'unsubscribed') return false;
        if (filters.status === 'completed' && l.stage !== 'Complete') return false;
      }
      if (filters.opened === 'yes' && !l.opened) return false;
      if (filters.opened === 'no' && l.opened) return false;
      if (filters.replied === 'yes' && !l.replied) return false;
      if (filters.replied === 'no' && l.replied) return false;
      if (filters.clicked === 'yes' && !l.clicked) return false;
      if (filters.clicked === 'no' && l.clicked) return false;
      if (filters.verification !== 'all') {
        if (filters.verification === 'unverified' && l.email_verification_status) return false;
        else if (filters.verification !== 'unverified' && l.email_verification_status !== filters.verification) return false;
      }
      return true;
    });
  }, [leads, filters]);

  // Load verification summary
  const loadVerificationSummary = useCallback(async () => {
    try {
      const res = await api.get(`/campaigns/${campaignId}/leads/verification-status`);
      setVerificationSummary(res);
    } catch {}
  }, [campaignId]);

  useEffect(() => { loadVerificationSummary(); }, [loadVerificationSummary]);

  useEffect(() => {
    api.get('/settings/email-verification').then(d => {
      const enabled = !!d.enabled;
      setEmailVerifEnabled(enabled);
      try { localStorage.setItem('emailVerifEnabled', enabled ? 'true' : 'false'); } catch {}
    }).catch(() => {});
  }, []);

  // Verify all unverified leads
  const verifyAllLeads = async () => {
    setVerifying(true);
    try {
      const res = await api.post(`/campaigns/${campaignId}/leads/verify`);
      notify({ type: 'success', message: `Verification queued for ${res.queued} lead(s)` });
      // Poll for completion
      setTimeout(() => { refresh(); loadVerificationSummary(); }, 3000);
    } catch (e) {
      notify({ type: 'error', message: e.message });
    } finally { setVerifying(false); }
  };

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

  /* Enhanced bulk parser – supports:
     1. email-per-line  (john@a.com)
     2. comma-separated (john@a.com, jane@b.com)
     3. tab-separated rows from Excel / Sheets copy-paste (email \t name \t company ...)
     4. CSV-style rows with headers (email,name,company\njohn@a.com,John,Acme)
  */
  const addBulk = async e => {
    e.preventDefault();
    const lines = bulk.split('\n').map(s => s.trim()).filter(Boolean);
    if (!lines.length) { setMsg({ type: 'error', text: 'No data entered' }); return; }

    // Detect if first line could be a header
    const firstLine = lines[0];
    const hasTabs = firstLine.includes('\t');
    const sep = hasTabs ? '\t' : ',';
    const cells = firstLine.split(sep).map(s => s.trim().toLowerCase());
    const looksLikeHeader = cells.includes('email');

    let payload;
    if (looksLikeHeader) {
      // Parse as tabular data with headers
      const headers = cells;
      const emailIdx = headers.indexOf('email');
      const nameIdx = headers.indexOf('name');
      payload = lines.slice(1).map(line => {
        const parts = line.split(sep).map(s => s.trim());
        const email = parts[emailIdx] || '';
        const name = nameIdx >= 0 ? (parts[nameIdx] || '') : '';
        const custom_data = {};
        headers.forEach((h, i) => {
          if (i !== emailIdx && i !== nameIdx && parts[i]) {
            custom_data[h] = parts[i];
          }
        });
        return { email, name: name || undefined, custom_data: Object.keys(custom_data).length ? custom_data : undefined };
      }).filter(r => r.email);
    } else {
      // Simple mode – emails only (comma or newline separated)
      const all = bulk.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
      payload = all.map(em => ({ email: em }));
    }

    if (!payload.length) { setMsg({ type: 'error', text: 'No valid emails found' }); return; }

    try {
      const res = await api.post(`/campaigns/${campaignId}/leads?skip_duplicates=${skipDuplicates}&verify_emails=${verifyEmails}`, payload);
      setBulk('');
      setLastDuplicates(res.duplicate_leads || []);
      const dupMsg = res.duplicate_leads?.length ? ` (${res.duplicate_leads.length} duplicate(s) skipped)` : '';
      notify({ type: 'success', message: `${res.added || payload.length} lead(s) added${dupMsg}` });
      refresh();
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  // ---- File import ----
  const handleFileImport = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setLastDuplicates([]);
    try {
      const res = await api.upload(`/campaigns/${campaignId}/leads/import?skip_duplicates=${skipDuplicates}&verify_emails=${verifyEmails}`, file);
      setLastDuplicates(res.duplicate_leads || []);
      const dupMsg = res.duplicate_leads?.length ? `, ${res.duplicate_leads.length} duplicate(s) skipped` : '';
      notify({ type: 'success', message: `Imported: ${res.added} added, ${res.already_enrolled} already enrolled, ${res.errors} errors${dupMsg}` });
      refresh();
    } catch (err) {
      notify({ type: 'error', message: err.message });
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  // ---- File export ----
  const handleExport = async () => {
    try {
      const params = new URLSearchParams();
      if (filters.status !== 'all') params.set('status', filters.status);
      if (filters.verification !== 'all' && filters.verification !== 'unverified') {
        params.set('verification_status', filters.verification);
      }
      const qs = params.toString() ? `?${params.toString()}` : '';
      const res = await api.download(`/campaigns/${campaignId}/leads/export${qs}`);
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = res.headers.get('content-disposition')?.match(/filename="?(.+?)"?$/)?.[1] || 'leads.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      notify({ type: 'error', message: err.message });
    }
  };

  return (
    <div className="space-y-6">
      {/* Import / Export toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" onClick={handleExport} disabled={!leads.length}>
          <svg className="w-4 h-4 mr-1.5 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" /></svg>
          Export{hasActiveFilter ? ' (filtered)' : ''} CSV
        </Button>
        <input ref={fileInputRef} type="file" accept=".csv,.tsv,.txt" className="hidden" onChange={handleFileImport} />
        <Button size="sm" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={importing}>
          <svg className="w-4 h-4 mr-1.5 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M16 8l-4-4m0 0L8 8m4-4v12" /></svg>
          {importing ? 'Importing…' : 'Import CSV'}
        </Button>
        {emailVerifEnabled && (
          <Button size="sm" variant="outline" onClick={verifyAllLeads} disabled={verifying}>
            {verifying ? 'Verifying…' : 'Verify All Emails'}
          </Button>
        )}
        <span className="text-xs text-gray-400 ml-1">
          {filteredLeads.length}{hasActiveFilter ? `/${leads.length}` : ''} lead{filteredLeads.length !== 1 ? 's' : ''}
        </span>
        {hasActiveFilter && (
          <button
            className="text-xs text-teal-600 hover:underline ml-1"
            onClick={() => setFilters({ status: 'all', opened: 'all', replied: 'all', clicked: 'all', verification: 'all' })}
          >Clear filters</button>
        )}
      </div>

      {/* Duplicate leads notice */}
      {lastDuplicates.length > 0 && (
        <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
          <p className="text-sm font-medium text-yellow-800 mb-1">{lastDuplicates.length} duplicate(s) skipped — already enrolled in a campaign:</p>
          <div className="flex flex-wrap gap-1 mt-1">
            {lastDuplicates.map(email => (
              <span key={email} className="font-mono text-xs bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded">{email}</span>
            ))}
          </div>
          <button className="text-xs text-yellow-600 underline mt-1" onClick={() => setLastDuplicates([])}>Dismiss</button>
        </div>
      )}

      {/* Add leads */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-gray-800">Add leads</h3>
          <button
            className="text-gray-400 hover:text-teal-600 transition-colors"
            onClick={() => setShowFormatInfo(v => !v)}
            title="Accepted data formats"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
          </button>
        </div>

        {showFormatInfo && (
          <div className="mb-4 p-3 bg-teal-50 border border-teal-200 rounded-lg text-sm text-teal-800 space-y-2">
            <p className="font-semibold">Accepted formats for bulk paste:</p>
            <ul className="list-disc pl-5 space-y-1 text-xs">
              <li><strong>Emails only</strong> — one per line or comma-separated<br/><code className="bg-teal-100 px-1 rounded">john@a.com, jane@b.com</code></li>
              <li><strong>Tab-separated (Excel / Sheets copy-paste)</strong> — first row = headers<br/><code className="bg-teal-100 px-1 rounded">email&nbsp;&nbsp;&nbsp;name&nbsp;&nbsp;&nbsp;company</code><br/><code className="bg-teal-100 px-1 rounded">john@a.com&nbsp;&nbsp;&nbsp;John&nbsp;&nbsp;&nbsp;Acme</code></li>
              <li><strong>Comma-separated with headers</strong><br/><code className="bg-teal-100 px-1 rounded">email,name,company</code><br/><code className="bg-teal-100 px-1 rounded">john@a.com,John,Acme</code></li>
            </ul>
            <p className="text-xs text-teal-600 mt-1">Columns beyond <em>email</em> and <em>name</em> are saved as custom fields.</p>
            <p className="font-semibold mt-2">CSV file import:</p>
            <p className="text-xs">Upload a <code className="bg-teal-100 px-1 rounded">.csv</code> or <code className="bg-teal-100 px-1 rounded">.tsv</code> file with an <em>email</em> header column. Extra columns become custom fields.</p>
          </div>
        )}

        <div className="flex gap-2 mb-3">
          <Button size="sm" variant={mode==='single'?'default':'outline'} onClick={()=>setMode('single')}>Single</Button>
          <Button size="sm" variant={mode==='bulk'?'default':'outline'}   onClick={()=>setMode('bulk')}>Bulk paste</Button>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none mb-2">
          <input
            type="checkbox"
            checked={skipDuplicates}
            onChange={e => { setSkipDuplicates(e.target.checked); setLastDuplicates([]); }}
            className="rounded"
          />
          Skip duplicates (checks all campaigns)
        </label>
        {emailVerifEnabled && (
          <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none mb-4">
            <input
              type="checkbox"
              checked={verifyEmails}
              onChange={e => setVerifyEmails(e.target.checked)}
              className="rounded"
            />
            Verify emails after adding
          </label>
        )}
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
              <label className="block text-sm text-gray-600 mb-1">Paste leads — emails, CSV rows, or Excel copy-paste (see ⓘ above)</label>
              <textarea
                rows={6}
                className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-teal-300"
                placeholder={"email,name,company\njohn@acme.com,John Doe,Acme Inc\njane@co.io,Jane Smith,Co"}
                value={bulk}
                onChange={e => setBulk(e.target.value)}
              />
            </div>
            <Button size="sm" variant="default">Add leads</Button>
          </form>
        )}
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-x-6 gap-y-2 p-3 bg-gray-50 dark:bg-gray-800/40 rounded-lg border border-gray-200 dark:border-gray-700 text-xs">
        {/* Status */}
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-gray-500 whitespace-nowrap">Status:</span>
          {[{v:'all',l:'All'},{v:'active',l:'Active'},{v:'bounced',l:'Bounced'},{v:'unsubscribed',l:'Unsubscribed'},{v:'completed',l:'Completed'}].map(o => (
            <button key={o.v} onClick={() => setFilter('status', o.v)}
              className={`px-2 py-0.5 rounded-full font-medium transition-colors ${
                filters.status === o.v ? 'bg-teal-500 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 hover:border-teal-300'
              }`}>{o.l}</button>
          ))}
        </div>
        {/* Opened */}
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-gray-500 whitespace-nowrap">Opened:</span>
          {[{v:'all',l:'All'},{v:'yes',l:'Yes'},{v:'no',l:'No'}].map(o => (
            <button key={o.v} onClick={() => setFilter('opened', o.v)}
              className={`px-2 py-0.5 rounded-full font-medium transition-colors ${
                filters.opened === o.v ? 'bg-amber-500 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 hover:border-amber-300'
              }`}>{o.l}</button>
          ))}
        </div>
        {/* Replied */}
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-gray-500 whitespace-nowrap">Replied:</span>
          {[{v:'all',l:'All'},{v:'yes',l:'Yes'},{v:'no',l:'No'}].map(o => (
            <button key={o.v} onClick={() => setFilter('replied', o.v)}
              className={`px-2 py-0.5 rounded-full font-medium transition-colors ${
                filters.replied === o.v ? 'bg-violet-500 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 hover:border-violet-300'
              }`}>{o.l}</button>
          ))}
        </div>
        {/* Clicked */}
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-gray-500 whitespace-nowrap">Clicked:</span>
          {[{v:'all',l:'All'},{v:'yes',l:'Yes'},{v:'no',l:'No'}].map(o => (
            <button key={o.v} onClick={() => setFilter('clicked', o.v)}
              className={`px-2 py-0.5 rounded-full font-medium transition-colors ${
                filters.clicked === o.v ? 'bg-orange-500 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 hover:border-orange-300'
              }`}>{o.l}</button>
          ))}
        </div>
        {/* Email verification */}
        {verificationSummary?.statuses && (
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-medium text-gray-500 whitespace-nowrap">Verified:</span>
            {[
              { v: 'all', l: 'All' },
              { v: 'valid', l: 'Valid' },
              { v: 'invalid', l: 'Invalid' },
              { v: 'risky', l: 'Risky' },
              { v: 'catch_all', l: 'Catch-All' },
              { v: 'unknown', l: 'Unknown' },
              { v: 'pending', l: 'Pending' },
              { v: 'unverified', l: 'Unverified' },
            ].filter(o => o.v === 'all' || (verificationSummary.statuses[o.v] || 0) > 0).map(o => (
              <button key={o.v} onClick={() => setFilter('verification', o.v)}
                className={`px-2 py-0.5 rounded-full font-medium transition-colors ${
                  filters.verification === o.v ? 'bg-teal-500 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 hover:border-teal-300'
                }`}>
                {o.l}{o.v !== 'all' ? ` (${verificationSummary.statuses[o.v] || 0})` : ''}
              </button>
            ))}
          </div>
        )}
      </div>


      {/* Leads table */}
      {filteredLeads.length === 0 ? (
        <div className="bg-gray-50 rounded-lg border border-dashed border-gray-300 p-8 text-center text-gray-400">
          {leads.length === 0
            ? 'No leads enrolled yet. Add them below or import a CSV file.'
            : 'No leads match the current filter.'}
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
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">Provider</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">Enrolled</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">Sending</th>
                {customFields.map(f => (
                  <th key={f} className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap capitalize">{f}</th>
                ))}
                <th className="px-4 py-3 w-16"></th>
              </tr>
            </thead>
            <tbody>
              {filteredLeads.map(l => (
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
                  {/* provider */}
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    {l.provider
                      ? <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                          l.provider === 'Google Workspace' ? 'bg-blue-100 text-blue-700' :
                          l.provider === 'Office 365'       ? 'bg-orange-100 text-orange-700' :
                          l.provider === 'Unknown'          ? 'bg-gray-100 text-gray-500' :
                          'bg-purple-100 text-purple-700'
                        }`}>{l.provider}</span>
                      : <span className="text-gray-300 text-xs">—</span>}
                  </td>
                  {/* enrolled date */}
                  <td className="px-4 py-2.5 text-gray-500 whitespace-nowrap">
                    {new Date(l.enrolled_at).toLocaleDateString()}
                  </td>
                  {/* Sending toggle + interest status dropdown */}
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      <button
                        className={`text-xs font-medium px-2 py-1 rounded transition-colors ${
                          l.sending_paused
                            ? 'bg-yellow-100 text-yellow-700 hover:bg-yellow-200'
                            : 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200'
                        }`}
                        onClick={async () => {
                          try {
                            await api.patch(`/campaigns/${campaignId}/leads/${l.lead_id}`, {
                              sending_paused: !l.sending_paused,
                            });
                            notify({ type: 'success', message: l.sending_paused ? 'Sending resumed' : 'Sending paused' });
                            refresh();
                          } catch (err) { notify({ type: 'error', message: err.message }); }
                        }}
                        title={l.sending_paused ? 'Click to resume sending' : 'Click to pause sending'}
                      >
                        {l.sending_paused ? 'Paused' : 'Active'}
                      </button>
                      <select
                        className={`text-[10px] font-medium rounded px-1.5 py-0.5 border cursor-pointer focus:outline-none focus:ring-1 focus:ring-teal-300 ${BADGE_STYLES[l.interest_status] || 'bg-gray-50 text-gray-500 border-gray-200'}`}
                        value={l.interest_status || ''}
                        title="AI classification — click to change or remove"
                        onChange={async (e) => {
                          const newStatus = e.target.value;
                          const pauseStatuses = new Set(['not_interested', 'wrong_person', 'out_of_office']);
                          const newPaused = pauseStatuses.has(newStatus) ? true : undefined;
                          try {
                            await api.patch(`/campaigns/${campaignId}/leads/${l.lead_id}`, {
                              interest_status: newStatus,
                              ...(newPaused !== undefined && { sending_paused: newPaused }),
                            });
                            notify({ type: 'success', message: newStatus ? `Marked as ${newStatus.replace(/_/g, ' ')}` : 'Status cleared' });
                            refresh();
                          } catch (err) { notify({ type: 'error', message: err.message }); }
                        }}
                      >
                        <option value="">— no status —</option>
                        <option value="interested">Interested</option>
                        <option value="not_interested">Not Interested</option>
                        <option value="out_of_office">Out of Office</option>
                        <option value="wrong_person">Wrong Person</option>
                        <option value="auto_reply">Auto Reply</option>
                      </select>
                    </div>
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

function CampaignAnalyticsTab({ campaignId, campaign, sentData = [], sequences = [], onRefresh }) {
  const notify = useNotify();
  const today = new Date();
  const localIso = (dt) => `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`;
  const todayStr = localIso(today);

  // Date preset helpers
  const presets = useMemo(() => {
    const d = (offset) => { const t = new Date(today); t.setDate(t.getDate() + offset); return localIso(t); };
    const monday = (dt) => { const t = new Date(dt); const day = t.getDay(); t.setDate(t.getDate() - (day === 0 ? 6 : day - 1)); return t; };
    const lastWeekEnd = new Date(monday(today)); lastWeekEnd.setDate(lastWeekEnd.getDate() - 1);
    const lastWeekStart = localIso(monday(lastWeekEnd));
    const lastMonthStart = localIso(new Date(today.getFullYear(), today.getMonth() - 1, 1));
    const lastMonthEnd = localIso(new Date(today.getFullYear(), today.getMonth(), 0));
    return [
      { label: 'Last 7 Days',  start: d(-6),          end: todayStr },
      { label: 'Last Week',    start: lastWeekStart,  end: localIso(lastWeekEnd) },
      { label: 'Last 30 Days', start: d(-29),         end: todayStr },
      { label: 'Last Month',   start: lastMonthStart, end: lastMonthEnd },
      { label: 'Last 90 Days', start: d(-89),         end: todayStr },
    ];
  }, [todayStr]);

  const [activePreset, setActivePreset] = useState('Last 7 Days');
  const defaultRange = presets.find(p => p.label === 'Last 7 Days') || presets[0];
  const [startDate, setStartDate] = useState(defaultRange.start);
  const [endDate,   setEndDate]   = useState(defaultRange.end);
  const [analyticsData, setAnalyticsData] = useState([]);
  const [hide, setHide] = useState({
    sent: false, totalOpens: false, uniqueOpens: false,
    totalReplies: false, totalClicks: false, uniqueClicks: false,
  });
  const initializedRef = useRef(false);
  const [zoomRange, setZoomRange] = useState({ start: 0, end: 0 });
  const activeChartIdxRef = useRef(null);
  const chartContainerRef = useRef(null);

  // Sub-tabs state
  const [analyticsSub, setAnalyticsSub] = useState('steps');
  const [stepStats, setStepStats] = useState([]);
  const [stepStatsLoading, setStepStatsLoading] = useState(false);
  const [sentFilter, setSentFilter] = useState('all');

  const loadStepStats = useCallback(async () => {
    setStepStatsLoading(true);
    try {
      const data = await api.get(`/campaigns/${campaignId}/analytics/steps`);
      setStepStats(data);
    } catch (e) {
      console.error('Failed to load step analytics', e);
    } finally {
      setStepStatsLoading(false);
    }
  }, [campaignId]);

  useEffect(() => { loadStepStats(); }, [loadStepStats]);

  // Re-fetch aggregated daily analytics whenever the date range changes
  useEffect(() => {
    if (!startDate || !endDate) return;
    api.get(`/analytics/daily?start_date=${startDate}&end_date=${endDate}&campaign_id=${campaignId}`)
      .then(data => setAnalyticsData(data))
      .catch(() => setAnalyticsData([]));
  }, [startDate, endDate, campaignId]);

  const toggleVariant = async (seqId, variantId, enabled) => {
    try {
      await api.patch(`/campaigns/${campaignId}/sequences/${seqId}/variants/${variantId}`, { enabled });
      await loadStepStats();
      onRefresh?.();
      notify({ type: 'success', message: enabled ? 'Variant enabled' : 'Variant disabled' });
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };

  const applyPreset = (preset) => {
    setActivePreset(preset.label);
    setStartDate(preset.start);
    setEndDate(preset.end);
  };

  const chartData = useMemo(() => {
    const map = {};
    analyticsData.forEach(row => {
      const d = row.date;
      if (!map[d]) {
        map[d] = { date: d, sent: 0, totalOpens: 0, uniqueOpens: 0, totalReplies: 0, totalClicks: 0, uniqueClicks: 0 };
      }
      map[d].sent         += row.sent;
      map[d].totalOpens   += row.total_opens;
      map[d].uniqueOpens  += row.unique_opens;
      map[d].totalReplies += row.total_replies;
      map[d].totalClicks  += row.total_clicks;
      map[d].uniqueClicks += row.unique_clicks;
    });
    const result = [];
    if (startDate && endDate) {
      const cur = new Date(startDate + 'T00:00:00');
      const end = new Date(endDate + 'T00:00:00');
      while (cur <= end) {
        const iso = `${cur.getFullYear()}-${String(cur.getMonth()+1).padStart(2,'0')}-${String(cur.getDate()).padStart(2,'0')}`;
        result.push(map[iso] || { date: iso, sent: 0, totalOpens: 0, uniqueOpens: 0, totalReplies: 0, totalClicks: 0, uniqueClicks: 0 });
        cur.setDate(cur.getDate() + 1);
      }
    }
    return result;
  }, [analyticsData, startDate, endDate]);

  useEffect(() => {
    if (!initializedRef.current && chartData.length > 0 && chartData.some(d => SERIES_LIST.some(s => d[s.key] > 0))) {
      const nh = {};
      SERIES_LIST.forEach(s => { nh[s.key] = chartData.every(d => d[s.key] === 0); });
      setHide(p => ({ ...p, ...nh }));
      initializedRef.current = true;
    }
  }, [chartData]);

  useEffect(() => {
    setZoomRange({ start: 0, end: Math.max(0, chartData.length - 1) });
  }, [chartData]);

  useEffect(() => {
    const el = chartContainerRef.current;
    if (!el) return;
    const onWheel = (e) => {
      e.preventDefault();
      setZoomRange(prev => {
        const len = chartData.length;
        if (len <= 2) return prev;
        const windowSize = prev.end - prev.start + 1;
        const pivot = activeChartIdxRef.current ?? Math.floor((prev.start + prev.end) / 2);
        const factor = e.deltaY < 0 ? 0.75 : 1.35;
        const newSize = Math.max(3, Math.min(len, Math.round(windowSize * factor)));
        const pivotRatio = windowSize > 1 ? (pivot - prev.start) / (windowSize - 1) : 0.5;
        let newStart = Math.round(pivot - pivotRatio * (newSize - 1));
        let newEnd = newStart + newSize - 1;
        if (newStart < 0) { newStart = 0; newEnd = newSize - 1; }
        if (newEnd >= len) { newEnd = len - 1; newStart = Math.max(0, len - newSize); }
        return { start: newStart, end: newEnd };
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [chartData]);

  const displayData = chartData.slice(zoomRange.start, zoomRange.end + 1);
  const handleChartMouseMove = (state) => {
    if (state?.activeTooltipIndex != null)
      activeChartIdxRef.current = zoomRange.start + state.activeTooltipIndex;
  };

  const rangeLeads   = useMemo(() => {
    const seen = new Set();
    sentData.forEach(e => {
      const d = e.sent_date || (e.sent_at ? e.sent_at.slice(0,10) : '');
      if (startDate && d < startDate) return;
      if (endDate   && d > endDate)   return;
      if (e.lead_id) seen.add(String(e.lead_id));
    });
    return seen.size;
  }, [sentData, startDate, endDate]);
  const rangeSent    = chartData.reduce((a,d)=>a+d.sent, 0);
  const rangeOpens   = chartData.reduce((a,d)=>a+d.totalOpens, 0);
  const rangeReplies = chartData.reduce((a,d)=>a+d.totalReplies, 0);
  const rangeClicks  = chartData.reduce((a,d)=>a+d.totalClicks, 0);
  const openRate     = rangeSent > 0 ? Math.round(rangeOpens   / rangeSent * 100) : 0;
  const replyRate    = rangeSent > 0 ? Math.round(rangeReplies / rangeSent * 100) : 0;
  const clickRate    = rangeSent > 0 ? Math.round(rangeClicks  / rangeSent * 100) : 0;

  const formatXDate = (d) => {
    if (!d) return '';
    const parts = d.split('-');
    return `${parseInt(parts[1])}/${parseInt(parts[2])}`;
  };

  return (
    <div className="space-y-6">
      {/* Range KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        {[
          { label: 'Leads',      value: rangeLeads },
          { label: 'Sent',       value: rangeSent },
          { label: 'Replies',    value: rangeReplies },
          { label: 'Open Rate',  value: `${openRate}%` },
          { label: 'Reply Rate', value: `${replyRate}%` },
          { label: 'Click Rate', value: `${clickRate}%` },
        ].map(({ label, value }) => (
          <Card key={label} className="p-4">
            <div className="text-xs text-gray-500 mb-1">{label}</div>
            <div className="text-2xl font-bold text-gray-800">{value}</div>
          </Card>
        ))}
      </div>

      {/* Date range presets */}
      <div className="flex flex-wrap items-center gap-2">
        {presets.map(p => (
          <button
            key={p.label}
            onClick={() => applyPreset(p)}
            className={`px-3 py-1.5 text-xs font-medium rounded-full border transition-colors ${
              activePreset === p.label
                ? 'bg-teal-500 text-white border-teal-500'
                : 'bg-white text-gray-600 border-gray-300 hover:border-teal-300 hover:bg-teal-50'
            }`}
          >
            {p.label}
          </button>
        ))}
        <button
          onClick={() => setActivePreset('custom')}
          className={`px-3 py-1.5 text-xs font-medium rounded-full border transition-colors ${
            activePreset === 'custom'
              ? 'bg-teal-500 text-white border-teal-500'
              : 'bg-white text-gray-600 border-gray-300 hover:border-teal-300 hover:bg-teal-50'
          }`}
        >
          Custom
        </button>
      </div>

      {activePreset === 'custom' && (
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            From
            <DatePicker value={startDate} onChange={v => { setStartDate(v); setActivePreset('custom'); }} />
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-600">
            To
            <DatePicker value={endDate} onChange={v => { setEndDate(v); setActivePreset('custom'); }} />
          </label>
        </div>
      )}

      {/* Chart */}
      <Card className="p-4">
        <div ref={chartContainerRef} style={{ width: '100%', height: 290 }}>
          <ResponsiveContainer>
            <ReAreaChart data={displayData} onMouseMove={handleChartMouseMove} margin={{ top: 10, right: 20, left: 0, bottom: 30 }}>
              <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatXDate} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
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

      {/* Bottom sub-tabs */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="flex border-b border-gray-200">
          {[
            { key: 'steps', label: 'Step Analytics' },
            { key: 'sent',  label: 'Sent Emails' },
          ].map(sub => (
            <button
              key={sub.key}
              onClick={() => setAnalyticsSub(sub.key)}
              className={`px-5 py-3 text-sm font-medium transition-colors border-b-2 -mb-px ${
                analyticsSub === sub.key
                  ? 'border-teal-500 text-teal-600 bg-teal-50/40'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'
              }`}
            >
              {sub.label}
            </button>
          ))}
        </div>

        <div className="p-4">
          {analyticsSub === 'steps' && (
            <StepAnalyticsPanel
              stepStats={stepStats}
              loading={stepStatsLoading}
              campaignId={campaignId}
              sequences={sequences}
              onToggleVariant={toggleVariant}
            />
          )}
          {analyticsSub === 'sent' && (
            <SentEmailsPanel
              sentData={sentData}
              filter={sentFilter}
              onFilterChange={setSentFilter}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Step Analytics Panel ─────────────────────────────────────────────────────
function StepAnalyticsPanel({ stepStats, loading, campaignId, sequences, onToggleVariant }) {
  const [expandedSteps, setExpandedSteps] = useState({});

  const toggleStep = (idx) => setExpandedSteps(p => ({ ...p, [idx]: !p[idx] }));

  const pct = (n, total) => total > 0 ? `${Math.round(n / total * 100)}%` : '—';

  if (loading) return <div className="py-8 text-center text-gray-400 text-sm">Loading step analytics…</div>;
  if (!stepStats.length) return <div className="py-8 text-center text-gray-400 text-sm">No data yet. Send some emails to see step analytics.</div>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-gray-600 text-xs font-semibold uppercase tracking-wide">
            <th className="px-3 py-2 text-left w-8"></th>
            <th className="px-3 py-2 text-left">Step</th>
            <th className="px-3 py-2 text-right">Sent</th>
            <th className="px-3 py-2 text-right">Opens</th>
            <th className="px-3 py-2 text-right">Clicks</th>
            <th className="px-3 py-2 text-right">Replies</th>
            <th className="px-3 py-2 text-right">Opportunities</th>
          </tr>
        </thead>
        <tbody>
          {stepStats.map((step) => {
            const seq = sequences.find(s => s.id === step.sequence_id);
            const hasVariants = step.variants && step.variants.length > 1; // >1 means default + at least one named
            const expanded = expandedSteps[step.sequence_index];
            return (
              <>
                <tr
                  key={step.sequence_index}
                  className={`border-b border-gray-100 ${hasVariants ? 'cursor-pointer hover:bg-gray-50' : ''}`}
                  onClick={() => hasVariants && toggleStep(step.sequence_index)}
                >
                  <td className="px-3 py-2.5 text-gray-400 text-center">
                    {hasVariants && (
                      <span className={`inline-block transition-transform text-xs ${expanded ? 'rotate-90' : ''}`}>▶</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="font-medium text-gray-800">Step {step.sequence_index + 1}</div>
                    {step.subject && <div className="text-xs text-gray-400 truncate max-w-xs">{step.subject}</div>}
                    {hasVariants && (
                      <span className="inline-flex items-center gap-1 text-[10px] bg-purple-100 text-purple-600 rounded-full px-1.5 py-0.5 mt-0.5">
                        A/B {step.variants.length - 1} variant{step.variants.length - 1 > 1 ? 's' : ''}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right font-medium">{step.total_sent}</td>
                  <td className="px-3 py-2.5 text-right">
                    {step.total_opens} <span className="text-gray-400 text-xs">({pct(step.total_opens, step.total_sent)})</span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {step.total_clicks} <span className="text-gray-400 text-xs">({pct(step.total_clicks, step.total_sent)})</span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {step.total_replies} <span className="text-gray-400 text-xs">({pct(step.total_replies, step.total_sent)})</span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <span className="font-semibold text-green-600">{step.total_opportunities}</span>
                    <span className="text-gray-400 text-xs ml-1">({pct(step.total_opportunities, step.total_sent)})</span>
                  </td>
                </tr>
                {/* Variant breakdown rows */}
                {hasVariants && expanded && step.variants.map((variant) => (
                  <tr key={`${step.sequence_index}-v${variant.variant_id ?? 'default'}`} className="bg-purple-50/50 border-b border-purple-100 text-xs">
                    <td className="px-3 py-2"></td>
                    <td className="px-3 py-2 pl-8">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-purple-700">
                          {variant.variant_label}
                        </span>
                        {variant.variant_id != null && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onToggleVariant(step.sequence_id, variant.variant_id, !variant.enabled);
                            }}
                            className={`text-[10px] px-2 py-0.5 rounded-full font-medium transition-colors ${
                              variant.enabled
                                ? 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200'
                                : 'bg-gray-200 text-gray-500 hover:bg-gray-300'
                            }`}
                          >
                            {variant.enabled ? 'Enabled' : 'Disabled'}
                          </button>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right">{variant.sent}</td>
                    <td className="px-3 py-2 text-right">
                      {variant.opens} <span className="text-gray-400">({pct(variant.opens, variant.sent)})</span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      {variant.clicks} <span className="text-gray-400">({pct(variant.clicks, variant.sent)})</span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      {variant.replies} <span className="text-gray-400">({pct(variant.replies, variant.sent)})</span>
                    </td>
                    <td className="px-3 py-2 text-right font-semibold text-green-600">
                      {variant.opportunities} <span className="text-gray-400 font-normal">({pct(variant.opportunities, variant.sent)})</span>
                    </td>
                  </tr>
                ))}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Sent Emails Panel ────────────────────────────────────────────────────────
const SENT_FILTER_OPTIONS = [
  { value: 'all',        label: 'All' },
  { value: 'opened',     label: 'Opened' },
  { value: 'clicked',    label: 'Clicked' },
  { value: 'replied',    label: 'Replied' },
  { value: 'interested', label: 'Interested' },
  { value: 'not_opened', label: 'Not Opened' },
  { value: 'bounced',    label: 'Bounced' },
  { value: 'unsubscribed', label: 'Unsubscribed' },
];

function SentEmailsPanel({ sentData = [], filter, onFilterChange }) {
  const filtered = useMemo(() => {
    switch (filter) {
      case 'opened':      return sentData.filter(e => e.opened);
      case 'clicked':     return sentData.filter(e => e.clicked);
      case 'replied':     return sentData.filter(e => e.replied);
      case 'interested':  return sentData.filter(e => e.interest_status === 'interested');
      case 'not_opened':  return sentData.filter(e => !e.opened);
      case 'bounced':     return sentData.filter(e => e.lead_status === 'bounced');
      case 'unsubscribed':return sentData.filter(e => e.lead_status === 'unsubscribed');
      default:            return sentData;
    }
  }, [sentData, filter]);

  const fmt = (isoStr) => {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="space-y-3">
      {/* Filter bar */}
      <div className="flex flex-wrap gap-1.5 items-center">
        <span className="text-xs font-medium text-gray-500 mr-1">Filter:</span>
        {SENT_FILTER_OPTIONS.map(opt => (
          <button
            key={opt.value}
            onClick={() => onFilterChange(opt.value)}
            className={`px-2.5 py-1 text-xs font-medium rounded-full border transition-colors ${
              filter === opt.value
                ? 'bg-teal-500 text-white border-teal-500'
                : 'bg-white text-gray-600 border-gray-300 hover:border-teal-300 hover:bg-teal-50'
            }`}
          >
            {opt.label}
          </button>
        ))}
        <span className="text-xs text-gray-400 ml-2">{filtered.length} email{filtered.length !== 1 ? 's' : ''}</span>
      </div>

      {filtered.length === 0 ? (
        <div className="py-8 text-center text-gray-400 text-sm">No emails match this filter.</div>
      ) : (
        <div className="overflow-x-auto max-h-[480px] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-white z-10">
              <tr className="border-b border-gray-200 bg-gray-50 text-gray-600 text-xs font-semibold uppercase tracking-wide">
                <th className="px-3 py-2.5 text-left">Sent</th>
                <th className="px-3 py-2.5 text-left">Lead</th>
                <th className="px-3 py-2.5 text-left">Step</th>
                <th className="px-3 py-2.5 text-left">Subject</th>
                <th className="px-3 py-2.5 text-center">Opened</th>
                <th className="px-3 py-2.5 text-center">Clicked</th>
                <th className="px-3 py-2.5 text-center">Replied</th>
                <th className="px-3 py-2.5 text-left">Variant</th>
                <th className="px-3 py-2.5 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(e => (
                <tr key={e.log_id} className="border-b border-gray-100 hover:bg-gray-50/60">
                  <td className="px-3 py-2 whitespace-nowrap text-gray-500 text-xs">{fmt(e.sent_at)}</td>
                  <td className="px-3 py-2 font-mono text-xs text-gray-800 max-w-[180px] truncate">{e.lead_email}</td>
                  <td className="px-3 py-2 text-xs text-gray-600 whitespace-nowrap">Step {(e.sequence_index ?? 0) + 1}</td>
                  <td className="px-3 py-2 text-xs text-gray-700 max-w-[200px] truncate">{e.subject || '—'}</td>
                  <td className="px-3 py-2 text-center">
                    {e.opened
                      ? <span className="text-amber-600 font-bold text-xs">✓</span>
                      : <span className="text-gray-300 text-xs">—</span>}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {e.clicked
                      ? <span className="text-orange-600 font-bold text-xs">✓</span>
                      : <span className="text-gray-300 text-xs">—</span>}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {e.replied
                      ? <span className="text-violet-600 font-bold text-xs">✓</span>
                      : <span className="text-gray-300 text-xs">—</span>}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {e.variant_label
                      ? <span className="bg-purple-100 text-purple-700 rounded px-1.5 py-0.5">{e.variant_label}</span>
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {e.lead_status && e.lead_status !== 'active' && (
                        <StatusBadge label={e.lead_status} />
                      )}
                      {e.interest_status && (
                        <StatusBadge label={e.interest_status} />
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
    match_lead_provider:   campaign.match_lead_provider   ?? true,
    paused:                campaign.paused               ?? false,
    timezone:              campaign.timezone              ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
  const [msg,    setMsg]    = useState(null);
  const [saving, setSaving] = useState(false);
  const [tzSearch, setTzSearch] = useState(null); // null = not focused

  // pre-compute timezone list once
  const tzList = useMemo(() => {
    return Intl.supportedValuesOf('timeZone').map(tz => {
      let offsetLabel = '';
      try {
        const parts = new Intl.DateTimeFormat('en', {
          timeZone: tz, timeZoneName: 'shortOffset',
        }).formatToParts(new Date());
        const off = parts.find(p => p.type === 'timeZoneName');
        if (off) offsetLabel = ` (${off.value})`;
      } catch (_) {}
      return { value: tz, label: `${tz.replace(/_/g, ' ')}${offsetLabel}` };
    });
  }, []);

  const filteredTz = tzSearch
    ? tzList.filter(t => t.label.toLowerCase().includes(tzSearch.toLowerCase()))
    : tzList;

  const toggleDay   = d  => setForm(f => { const s=new Set(f.sending_days); s.has(d)?s.delete(d):s.add(d); return {...f, sending_days:[...s].sort()}; });
  const toggleInbox = id => setForm(f => { const s=new Set(f.inbox_ids);   s.has(id)?s.delete(id):s.add(id); return {...f, inbox_ids:[...s]}; });

  const submit = async e => {
    e.preventDefault();
    if (!form.name.trim())          { setMsg({type:'error',text:'Name is required'});           return; }
    if (!form.inbox_ids.length)     { setMsg({type:'error',text:'Select at least one inbox'});  return; }
    setSaving(true);
    try {
      await api.patch(`/campaigns/${campaignId}`, form);
      // If the timezone was changed, trigger a queue recalculation automatically.
      const tzChanged = form.timezone !== (campaign.timezone ?? '');
      if (tzChanged) {
        try {
          await api.post(`/campaigns/${campaignId}/recalculate-queue`);
          setMsg({ type: 'success', text: 'Settings saved · Queue recalculated for new timezone' });
        } catch (_) {
          setMsg({ type: 'success', text: 'Settings saved (queue recalculation failed — run it manually if needed)' });
        }
      } else {
        setMsg({ type: 'success', text: 'Settings saved' });
      }
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
    { key: 'match_lead_provider',      label: 'Match lead provider — send from Google inboxes to Google leads, Office 365 to Office 365 (falls back to any inbox if none match)' },
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
          <div className="relative">
            <input
              type="text"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
              placeholder="Search timezones…"
              value={tzSearch !== null ? tzSearch : (form.timezone || '')}
              onChange={e => setTzSearch(e.target.value)}
              onFocus={() => setTzSearch(form.timezone || '')}
              onBlur={() => setTimeout(() => setTzSearch(null), 200)}
            />
            {tzSearch !== null && filteredTz.length > 0 && (
              <div className="absolute z-20 w-full top-full mt-1 border rounded-lg max-h-48 overflow-y-auto bg-white dark:bg-gray-900 shadow-lg">
                {filteredTz.slice(0, 100).map(t => (
                  <button
                    key={t.value}
                    type="button"
                    className={`block w-full text-left px-3 py-1.5 text-sm hover:bg-teal-50 dark:hover:bg-gray-800 ${t.value === form.timezone ? 'bg-teal-50 font-medium' : ''}`}
                    onMouseDown={e => {
                      e.preventDefault();
                      setForm(f => ({ ...f, timezone: t.value }));
                      setTzSearch(null);
                    }}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-1">
            Sending window times are interpreted in this timezone. Emails are stored internally in UTC and sent at the correct local time.
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

        {(form.send_all_as_text||form.send_first_as_text) && (form.track_opens||form.track_clicks) && (
          <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            ⚠ Plain text mode will override tracking — open/click tracking requires HTML and will be disabled for affected emails.
          </p>
        )}
        {(form.send_all_as_text||form.send_first_as_text) && !(form.track_opens||form.track_clicks) && (
          <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            ⚠ HTML settings on sequences will be ignored for affected emails.
          </p>
        )}
        {!(form.send_all_as_text||form.send_first_as_text) && (form.track_opens||form.track_clicks) && (
          <p className="text-xs text-blue-600 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
            ℹ Tracking is enabled — plain-text sequences will be automatically sent as HTML so tracking pixels and links can be injected.
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

function SequenceBodyEditor({ value, onChange, isHtml, onIsHtmlChange, previewText, onPreviewTextChange, isFirstSequence, campaign, required }) {
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
  const trackingEnabled = campaign?.track_opens || campaign?.track_clicks;
  // Tracking needs HTML: if sequence is plain text but tracking is on, it will be upgraded
  const trackingUpgrade = !isHtml && trackingEnabled && !isOverridden;
  const effectiveHtml   = (isHtml || trackingUpgrade) && !isOverridden;

  const overrideMsg = forcePlainAll
    ? 'Campaign is set to send all emails as plain text — HTML will be ignored. Tracking will also be disabled for these emails.'
    : forcePlainFirst
    ? 'Campaign sends the first email as plain text — HTML will be ignored for this sequence. Tracking will also be disabled.'
    : trackingUpgrade
    ? 'Open/click tracking is enabled — this email will be sent as HTML so tracking pixels and links can be injected.'
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
        {trackingUpgrade && (
          <span className="text-xs font-medium text-blue-600 bg-blue-50 border border-blue-200 rounded px-2 py-0.5">
            ℹ {overrideMsg}
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
      {effectiveHtml && (
        <div className="mt-2">
          <label className="block text-xs font-medium text-gray-500 mb-1">
            Preview text
            <span className="ml-1 font-normal text-gray-400">— shown as the inbox snippet and in push notifications</span>
          </label>
          <input
            type="text"
            maxLength={150}
            value={previewText || ''}
            onChange={e => onPreviewTextChange(e.target.value)}
            className="w-full border rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300 placeholder-gray-400"
            placeholder="Optional — leave blank to use the email body text"
          />
        </div>
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
function PreviewModal({ sequence, campaignId, leads, onClose, variant = null, editingOverride = null }) {
  const [leadId,    setLeadId]    = useState(leads[0]?.lead_id ?? '');
  const [preview,   setPreview]   = useState(null);
  const [loading,   setLoading]   = useState(false);
  const [err,       setErr]       = useState(null);
  const [testEmail, setTestEmail] = useState('');
  const [testState, setTestState] = useState(null); // null | 'sending' | 'success' | {error}
  const backdropDown = useRef(false);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = await api.post(`/campaigns/${campaignId}/preview`, {
        sequence_id: sequence.id,
        lead_id: leadId ? Number(leadId) : null,
        ...(variant ? { variant_id: variant.id } : {}),
        ...(editingOverride ? {
          subject_override: editingOverride.subject ?? null,
          body_override: editingOverride.body ?? null,
          is_html_override: editingOverride.is_html ?? null,
        } : {}),
      });
      setPreview(data);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }, [campaignId, sequence.id, leadId, variant, editingOverride]);

  useEffect(() => { load(); }, [load]);

  const sendTest = async () => {
    if (!testEmail.trim()) return;
    setTestState('sending');
    try {
      await api.post(`/campaigns/${campaignId}/send-test`, {
        sequence_id: sequence.id,
        lead_id: leadId ? Number(leadId) : null,
        to_email: testEmail.trim(),
        ...(variant ? { variant_id: variant.id } : {}),
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
      onMouseDown={e => { backdropDown.current = e.target === e.currentTarget; }}
      onClick={() => { if (backdropDown.current) onClose(); }}
    >
      <div
        data-darkreader-ignore
        className="rounded-xl shadow-lg w-full max-w-2xl max-h-[90vh] flex flex-col mx-auto"
        style={{ backgroundColor: 'white' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="font-semibold text-gray-800">
            Preview — Sequence #{(sequence.position ?? 0) + 1}
            {variant && <span className="ml-2 text-xs font-normal text-purple-600 bg-purple-50 border border-purple-200 rounded px-2 py-0.5">{variant.label || 'Variant'}</span>}
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
  const [form, setForm] = useState({ subject: '', body: '', wait_days_after_previous: 0, is_html: false, preview_text: '' });
  const [msg,  setMsg]  = useState(null);
  const [editing,         setEditing]         = useState(null);
  const [originalEditing, setOriginalEditing] = useState(null);
  const [editDirty,       setEditDirty]       = useState(false);
  const [showEditWarning, setShowEditWarning] = useState(false);
  const [previewSeq, setPreviewSeq] = useState(null);
  const [previewVariant, setPreviewVariant] = useState(null);
  const [previewOverride, setPreviewOverride] = useState(null);
  const [selectedIdx, setSelectedIdx] = useState(sequences.length > 0 ? 0 : null);
  const [showAddForm, setShowAddForm] = useState(sequences.length === 0);
  const [editingVariant, setEditingVariant] = useState(null);
  const [showVariantForm, setShowVariantForm] = useState(false);
  const [variantForm, setVariantForm] = useState({ label: '', subject: '', body: '', is_html: false, preview_text: '' });

  useEffect(() => { setPos(sequences.length); }, [sequences]);
  useEffect(() => {
    if (sequences.length > 0 && selectedIdx === null) setSelectedIdx(0);
    if (sequences.length === 0) { setSelectedIdx(null); setShowAddForm(true); }
  }, [sequences.length]);

  const selectedSeq = selectedIdx !== null && sequences[selectedIdx] ? sequences[selectedIdx] : null;

  const openEdit = (seq) => {
    const copy = { ...seq, is_html: seq.is_html ?? false, preview_text: seq.preview_text ?? '' };
    setEditing(copy);
    setOriginalEditing(copy);
    setEditDirty(false);
    setShowAddForm(false);
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
      setForm({ subject: '', body: '', wait_days_after_previous: 0, is_html: false, preview_text: '' });
      setMsg({ type: 'success', text: 'Sequence added' });
      setShowAddForm(false);
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
      if (selectedIdx >= sequences.length - 1) setSelectedIdx(Math.max(0, sequences.length - 2));
      refresh();
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };

  const createVariant = async () => {
    try {
      await api.post(`/campaigns/${campaignId}/sequences/${selectedSeq.id}/variants`, variantForm);
      setShowVariantForm(false);
      setVariantForm({ label: '', subject: '', body: '', is_html: false, preview_text: '' });
      refresh();
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const saveVariant = async () => {
    if (!editingVariant) return;
    try {
      await api.patch(`/campaigns/${campaignId}/sequences/${selectedSeq.id}/variants/${editingVariant.id}`, variantForm);
      setEditingVariant(null);
      setShowVariantForm(false);
      refresh();
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const deleteVariant = async (v) => {
    if (!await confirm(`Delete variant "${v.label || 'Variant'}"?`)) return;
    try {
      await api.del(`/campaigns/${campaignId}/sequences/${selectedSeq.id}/variants/${v.id}`);
      refresh();
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const toggleVariantEnabled = async (v) => {
    try {
      await api.patch(`/campaigns/${campaignId}/sequences/${selectedSeq.id}/variants/${v.id}`, { enabled: !v.enabled });
      refresh();
    } catch (e) { notify({ type: 'error', message: e.message }); }
  };

  const openEditVariant = (v) => {
    setEditingVariant(v);
    setVariantForm({ label: v.label || '', subject: v.subject || '', body: v.body || '', is_html: v.is_html ?? false, preview_text: v.preview_text || '' });
    setShowVariantForm(true);
  };

  const getCumulativeDay = (idx) => {
    let days = 0;
    for (let i = 0; i <= idx; i++) days += sequences[i]?.wait_days_after_previous || 0;
    return days;
  };

  return (
    <div className="flex gap-0 min-h-[520px] rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm">
      {/* ── Left: Step timeline ── */}
      <div className="w-72 shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col">
        <div className="px-4 py-3 border-b border-gray-200">
          <h3 className="text-sm font-semibold text-gray-700">Sequence Steps</h3>
          <p className="text-xs text-gray-400 mt-0.5">{sequences.length} step{sequences.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="flex-1 overflow-y-auto px-3 py-3">
          {sequences.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-4">No steps yet. Add one to get started.</p>
          )}
          <div className="relative">
            {sequences.map((s, idx) => {
              const isActive = editing?.id === s.id || (selectedIdx === idx && !editing && !showAddForm);
              const cumulDay = getCumulativeDay(idx);
              return (
                <div key={s.id}>
                  {/* Step row — circle column is always exactly w-8 so all cards are the same width */}
                  <div className="flex gap-3">
                    <div className="w-8 shrink-0 flex items-center justify-center">
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all z-10 ${
                        isActive ? 'bg-teal-500 border-teal-500 text-white shadow-md' : 'bg-white border-gray-300 text-gray-500'
                      }`}>{idx + 1}</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => { setSelectedIdx(idx); setEditing(null); setShowAddForm(false); }}
                      className={`flex-1 min-w-0 text-left rounded-lg px-3 py-2.5 transition-all border cursor-pointer ${
                        isActive ? 'bg-teal-50 border-teal-300 shadow-sm' : 'bg-white border-gray-200 hover:border-teal-200 hover:bg-teal-50/30'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className={`text-sm font-medium truncate ${isActive ? 'text-teal-700' : 'text-gray-800'}`}>
                          {s.subject || <em className="text-gray-400 font-normal text-xs">Reply in thread</em>}
                        </span>
                        {s.is_html && <span className="text-[9px] bg-blue-100 text-blue-600 rounded px-1 py-0.5 font-medium ml-1 shrink-0">HTML</span>}
                      </div>
                      <div className="text-[11px] text-gray-400 mt-0.5">Day {cumulDay}{idx === 0 ? ' (start)' : ''}</div>
                    </button>
                  </div>
                  {/* Connector between steps — rendered as its own row so it never affects card width */}
                  {idx < sequences.length - 1 && (
                    <div className="flex gap-3">
                      <div className="w-8 shrink-0 flex flex-col items-center">
                        <div className="w-0.5 h-4 bg-gray-300" />
                        <div className="text-[10px] font-medium text-gray-400 bg-gray-100 border border-gray-200 rounded-full px-2 py-0.5 my-0.5 whitespace-nowrap">
                          {sequences[idx + 1]?.wait_days_after_previous || 0}d wait
                        </div>
                        <div className="w-0.5 h-4 bg-gray-300" />
                      </div>
                    </div>
                  )}
                  {/* Small gap after each step */}
                  <div className="h-2" />
                </div>
              );
            })}
          </div>
        </div>
        <div className="p-3 border-t border-gray-200">
          <button
            type="button"
            onClick={() => { setShowAddForm(true); setEditing(null); setSelectedIdx(null); }}
            className={`w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border-2 border-dashed text-sm font-medium transition-colors ${
              showAddForm ? 'border-teal-400 bg-teal-50 text-teal-700' : 'border-gray-300 text-gray-500 hover:border-teal-300 hover:text-teal-600 hover:bg-teal-50/30'
            }`}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg>
            Add Step
          </button>
        </div>
      </div>

      {/* ── Right: Editor / Detail Panel ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {editing && (
          <div className="flex-1 flex flex-col overflow-y-auto">
            <div className="px-6 py-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-gray-800">Edit Step #{editing.position + 1}</h3>
                <p className="text-xs text-gray-400">Modify the email content and timing</p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => { setPreviewSeq(editing); setPreviewVariant(null); setPreviewOverride(editing); }}>Preview</Button>
                <Button size="sm" variant="destructive" onClick={() => deleteSeq(editing)}>Delete</Button>
              </div>
            </div>
            <form onSubmit={saveEdit} className="flex-1 overflow-y-auto p-6 space-y-5">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
                <input className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300" value={editing.subject || ''} onChange={e => updateEditing({ subject: e.target.value })} placeholder="Leave blank to reply in same thread" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Email Body *</label>
                <SequenceBodyEditor value={editing.body} onChange={val => updateEditing({ body: val })} isHtml={editing.is_html ?? false} onIsHtmlChange={v => updateEditing({ is_html: v })} previewText={editing.preview_text ?? ''} onPreviewTextChange={v => updateEditing({ preview_text: v })} isFirstSequence={editing.position === 0} campaign={campaign} required />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Wait days after previous step</label>
                <input type="number" min={0} className="w-28 border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300" value={editing.wait_days_after_previous} onChange={e => updateEditing({ wait_days_after_previous: +e.target.value })} />
              </div>
              <div className="flex gap-2 pt-2">
                <Button size="sm" variant="default">Save Changes</Button>
                <Button type="button" size="sm" variant="outline" onClick={tryCloseEdit}>Cancel</Button>
              </div>
            </form>
          </div>
        )}

        {!editing && !showAddForm && selectedSeq && (
          <div className="flex-1 flex flex-col overflow-y-auto">
            <div className="px-6 py-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-gray-800">Step {selectedSeq.position + 1}{selectedSeq.subject ? ` — ${selectedSeq.subject}` : ' — Reply in thread'}</h3>
                <p className="text-xs text-gray-400">{selectedSeq.wait_days_after_previous}d wait{selectedSeq.is_html ? ' · HTML' : ' · Plain text'}{' · Day ' + getCumulativeDay(selectedIdx)}</p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => { setPreviewSeq(selectedSeq); setPreviewOverride(null); }}>Preview</Button>
                <Button size="sm" variant="default" onClick={() => openEdit(selectedSeq)}>Edit</Button>
                <Button size="sm" variant="destructive" onClick={() => deleteSeq(selectedSeq)}>Delete</Button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              <div className="mb-5">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1">Subject</span>
                <p className="text-gray-800 font-medium">{selectedSeq.subject || <em className="text-gray-400 font-normal">Reply in same thread</em>}</p>
              </div>
              <div>
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-2">Body</span>
                {selectedSeq.is_html ? (
                  <div className="border rounded-lg p-5 bg-white prose prose-sm max-w-none min-h-[200px]" dangerouslySetInnerHTML={{ __html: selectedSeq.body }} />
                ) : (
                  <pre className="border rounded-lg p-5 bg-gray-50 text-sm whitespace-pre-wrap font-sans text-gray-800 min-h-[200px]">{selectedSeq.body || '(empty)'}</pre>
                )}
              </div>

              {/* ── A/B Variants ── */}
              <div className="mt-6 pt-6 border-t border-gray-200">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">A/B Variants</span>
                    <p className="text-xs text-gray-400 mt-0.5">Add alternate email content — one is chosen randomly when sending</p>
                  </div>
                  {!showVariantForm && (
                    <button
                      onClick={() => { setEditingVariant(null); setVariantForm({ label: '', subject: '', body: '', is_html: false, preview_text: '' }); setShowVariantForm(true); }}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium bg-purple-50 text-purple-700 border border-purple-200 rounded-lg hover:bg-purple-100 transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4"/></svg>
                      Add Variant
                    </button>
                  )}
                </div>

                {(selectedSeq.variants || []).length === 0 && !showVariantForm && (
                  <p className="text-xs text-gray-400 italic py-2">No variants — email always sends the default content above.</p>
                )}
                {(selectedSeq.variants || []).length > 0 && (
                  <div className="space-y-2 mb-3">
                    {(selectedSeq.variants || []).map(v => (
                      <div key={v.id} className={`flex items-center gap-3 p-3 rounded-lg border ${v.enabled ? 'border-purple-200 bg-purple-50/50' : 'border-gray-200 bg-gray-50 opacity-60'}`}>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-sm text-gray-800">{v.label || 'Variant'}</span>
                            <button
                              onClick={() => toggleVariantEnabled(v)}
                              className={`text-[10px] px-2 py-0.5 rounded-full font-medium transition-colors ${v.enabled ? 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200' : 'bg-gray-200 text-gray-500 hover:bg-gray-300'}`}
                            >
                              {v.enabled ? 'Enabled' : 'Disabled'}
                            </button>
                          </div>
                          {v.subject && <p className="text-xs text-gray-500 mt-0.5 truncate">Subject: {v.subject}</p>}
                          {v.body && <p className="text-xs text-gray-400 mt-0.5 truncate">{v.body.replace(/<[^>]+>/g, '').slice(0, 80)}…</p>}
                        </div>
                        <div className="flex gap-1.5 shrink-0">
                          <button onClick={() => { setPreviewSeq(selectedSeq); setPreviewVariant(v); setPreviewOverride(null); }} className="px-2.5 py-1 text-xs bg-white border border-teal-200 rounded hover:bg-teal-50 text-teal-700 transition-colors">Preview</button>
                          <button onClick={() => openEditVariant(v)} className="px-2.5 py-1 text-xs bg-white border border-gray-300 rounded hover:bg-gray-100 text-gray-600 transition-colors">Edit</button>
                          <button onClick={() => deleteVariant(v)} className="px-2.5 py-1 text-xs bg-white border border-red-200 rounded hover:bg-red-50 text-red-600 transition-colors">Delete</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {showVariantForm && (
                  <div className="border border-purple-200 bg-purple-50/30 rounded-lg p-4 space-y-3">
                    <h4 className="text-sm font-semibold text-gray-700">{editingVariant ? 'Edit Variant' : 'New Variant'}</h4>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Label <span className="text-gray-400">(e.g. "Variant A")</span></label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300" placeholder="Variant A" value={variantForm.label} onChange={e => setVariantForm(f => ({ ...f, label: e.target.value }))} />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Subject <span className="text-gray-400">(blank = use step's subject)</span></label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300" placeholder="Leave blank to use step subject" value={variantForm.subject} onChange={e => setVariantForm(f => ({ ...f, subject: e.target.value }))} />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Body *</label>
                      <textarea className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300 min-h-[120px] font-mono" placeholder="Email body..." value={variantForm.body} onChange={e => setVariantForm(f => ({ ...f, body: e.target.value }))} />
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" variant="default" onClick={editingVariant ? saveVariant : createVariant} type="button">
                        {editingVariant ? 'Save Variant' : 'Create Variant'}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => { setShowVariantForm(false); setEditingVariant(null); }} type="button">Cancel</Button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {!editing && showAddForm && (
          <div className="flex-1 flex flex-col overflow-y-auto">
            <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
              <h3 className="font-semibold text-gray-800">Add New Step</h3>
              <p className="text-xs text-gray-400">This will become step #{sequences.length + 1} in the sequence</p>
            </div>
            <form onSubmit={submit} className="flex-1 overflow-y-auto p-6 space-y-5">
              {msg && <div className={`rounded-lg px-3 py-2 text-sm ${msg.type === 'error' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'}`}>{msg.text}</div>}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
                <input className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300" value={form.subject} onChange={e => setForm(f => ({ ...f, subject: e.target.value }))} placeholder="Leave blank to reply in same thread" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Email Body *</label>
                <SequenceBodyEditor value={form.body} onChange={val => setForm(f => ({ ...f, body: val }))} isHtml={form.is_html} onIsHtmlChange={v => setForm(f => ({ ...f, is_html: v }))} previewText={form.preview_text ?? ''} onPreviewTextChange={v => setForm(f => ({ ...f, preview_text: v }))} isFirstSequence={pos === 0} campaign={campaign} required />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Wait days after previous step</label>
                <input type="number" min={0} className="w-28 border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300" value={form.wait_days_after_previous} onChange={e => setForm(f => ({ ...f, wait_days_after_previous: +e.target.value }))} />
              </div>
              <Button size="sm" variant="default">Add Step</Button>
            </form>
          </div>
        )}

        {!editing && !showAddForm && !selectedSeq && (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">Select a step or add a new one to get started.</div>
        )}
      </div>

      {showEditWarning && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4">
          <div data-darkreader-ignore className="rounded-xl shadow-lg p-6 w-full max-w-sm mx-auto" style={{ backgroundColor: 'white' }}>
            <h3 className="font-semibold text-gray-800 mb-1">Discard changes?</h3>
            <p className="text-sm text-gray-500 mb-4">You have unsaved changes. Closing will discard them.</p>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="outline" onClick={() => setShowEditWarning(false)}>Keep editing</Button>
              <Button size="sm" variant="destructive" onClick={() => { setShowEditWarning(false); setEditing(null); setEditDirty(false); }}>Discard</Button>
            </div>
          </div>
        </div>
      )}

      {previewSeq && (
        <PreviewModal sequence={previewSeq} campaignId={campaignId} leads={leads} variant={previewVariant} editingOverride={previewOverride} onClose={() => { setPreviewSeq(null); setPreviewVariant(null); setPreviewOverride(null); }} />
      )}
    </div>
  );
}
