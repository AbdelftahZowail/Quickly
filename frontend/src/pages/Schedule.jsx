import { useEffect, useState, useRef } from 'react';
import { api, apiCache } from '../api';
import { useLoading } from '../context/LoadingContext';
import { useNotify } from '../context/NotificationContext';
import { useConfirm } from '../context/ConfirmContext';
import { useAppMode } from '../context/AppModeContext';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';

const DAY_NAMES = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const SCHEDULE_DAYS_BACK = 7;
const SCHEDULE_DAYS_AHEAD = 3;
const SCHEDULE_LIMIT = 1000;

function buildQuery(path, params) {
  const query = new URLSearchParams(params).toString();
  return query ? `${path}?${query}` : path;
}

// ── Email Preview Modal for schedule items ────────────────────────────────────
function ScheduleEmailPreviewModal({ item, onClose }) {
  const isHtml = item.sequence_is_html || (item.sequence_body || '').trim().startsWith('<');
  const subject = item.subject || '(no subject)';
  const body = item.sequence_body || '';
  const backdropDown = useRef(false);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onMouseDown={e => { backdropDown.current = e.target === e.currentTarget; }}
      onClick={() => { if (backdropDown.current) onClose(); }}
    >
      <div
        data-darkreader-ignore
        className="rounded shadow w-full max-w-3xl max-h-[90vh] flex flex-col"
        style={{ backgroundColor: 'white' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <div>
            <h2 className="font-semibold text-gray-800">Email Preview</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              {item.type === 'sent' ? `Sent to ${item.lead_email}` : `Scheduled for ${item.lead_email}`}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">×</button>
        </div>

        {/* Meta */}
        <div className="px-6 py-3 border-b bg-gray-50 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-gray-500">
            <span className="font-medium text-gray-700">Campaign:</span> {item.campaign_name}
          </span>
          <span className="text-gray-500">
            <span className="font-medium text-gray-700">Sequence:</span> #{(item.sequence_index ?? 0) + 1}
          </span>
          {item.type === 'sent' && item.sent_at && (
            <span className="text-gray-500">
              <span className="font-medium text-gray-700">Sent:</span> {new Date(item.sent_at).toLocaleString()}
            </span>
          )}
          {item.type === 'scheduled' && item.scheduled_at && (
            <span className="text-gray-500">
              <span className="font-medium text-gray-700">Scheduled:</span> {new Date(item.scheduled_at).toLocaleString()}
            </span>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
          {/* Subject */}
          <div className="bg-gray-50 rounded-lg px-4 py-3">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-1">Subject</span>
            <p className="font-medium text-gray-800">
              {!item.subject || item.subject === '(reply in thread)'
                ? <em className="text-gray-400 font-normal">Reply in thread</em>
                : item.subject}
            </p>
          </div>
          {/* Body */}
          <div>
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide block mb-2">Body</span>
            {body ? (
              isHtml ? (
                <div
                  className="border rounded-lg p-5 bg-white prose prose-sm max-w-none"
                  dangerouslySetInnerHTML={{ __html: body }}
                />
              ) : (
                <pre className="border rounded-lg p-5 bg-gray-50 text-sm whitespace-pre-wrap font-sans text-gray-800">
                  {body}
                </pre>
              )
            ) : (
              <p className="text-gray-400 italic text-sm">No body available</p>
            )}
          </div>
        </div>

        <div className="px-6 py-3 border-t flex justify-end">
          <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}

function escapeHtml(s){
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

export default function Schedule() {
  const loading = useLoading();
  const notify = useNotify();
  const { isProduction } = useAppMode();

  const [sent, setSent] = useState(() => apiCache.get('/schedule/sent') || []);
  const [scheduled, setScheduled] = useState(() => apiCache.get('/schedule/scheduled') || []);
  const [stats, setStats] = useState(() => apiCache.get('/schedule/stats') || {});
  const [serverStatus, setServerStatus] = useState(() => apiCache.get('/status') || {});
  const [strategy, setStrategy] = useState('priority');
  const [timeToNext, setTimeToNext] = useState('');
  const confirm = useConfirm();

  const [campaignFilter, setCampaignFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [searchFilter, setSearchFilter] = useState('');

  const [pastExpanded, setPastExpanded] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [previewItem, setPreviewItem] = useState(null);

  const [daysBack, setDaysBack] = useState(SCHEDULE_DAYS_BACK);
  const [daysAhead, setDaysAhead] = useState(SCHEDULE_DAYS_AHEAD);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [initialLoaded, setInitialLoaded] = useState(false);
  const sentinelRef = useRef(null);
  const isLoadingMoreRef = useRef(false);

  // button states for recalc/validate so React can re-render correctly
  const [recalcState, setRecalcState] = useState({ busy: false, text: '⚡ Recalculate All Campaigns' });
  const [validateState, setValidateState] = useState({ busy: false, text: '🔍 Validate Queue' });

  const filterCampaignOptions = useRef([]);

  const ensureDetail = async (item) => {
    try {
      if (item.type === 'scheduled') {
        if (item.sequence_body) return item;
        const data = await api.get(`/schedule/scheduled/${item.slot_id}`);
        const merged = { ...item, ...data };
        setScheduled(prev => prev.map(s => s.slot_id === item.slot_id ? merged : s));
        return merged;
      }
      if (item.type === 'sent') {
        if (item.sequence_body || (item.opens && item.clicks)) return item;
        const data = await api.get(`/schedule/sent/${item.log_id}`);
        const merged = { ...item, ...data };
        setSent(prev => prev.map(s => s.log_id === item.log_id ? merged : s));
        return merged;
      }
    } catch (e) {
      notify({ type: 'error', message: 'Failed to load email details' });
    }
    return item;
  };

  const loadData = async (opts = {}) => {
    // loading.start();
    try {
      const effectiveBack = opts.daysBack ?? daysBack;
      const effectiveAhead = opts.daysAhead ?? daysAhead;
      const [s, sch, st, srv, stratData] = await Promise.all([
        api.get(buildQuery('/schedule/sent', {
          days_back: effectiveBack,
          limit: SCHEDULE_LIMIT,
          offset: 0,
          include_body: true,
          include_events: false,
        })).catch(() => []),
        api.get(buildQuery('/schedule/scheduled', {
          days_ahead: effectiveAhead,
          limit: SCHEDULE_LIMIT,
          offset: 0,
          include_body: false,
        })).catch(() => []),
        api.get('/schedule/stats').catch(() => ({})),
        api.get('/status').catch(() => ({})),
        api.get('/settings/scheduling-strategy').catch(() => ({})),
      ]);
      setSent(s);
      setScheduled(sch);
      setStats(st);
      setServerStatus(srv);
      setStrategy(stratData.scheduling_strategy || 'priority');
      const camps = new Map();
      // reset countdown first so stale values disappear when schedule is empty
      setTimeToNext('');
      // compute delay until next scheduled email using server timestamp (if
      // available) otherwise fall back to local clock
      if (sch && sch.length) {
        const now = srv.server_time ? new Date(srv.server_time) : new Date();
        const future = sch
          .map(i => new Date(i.scheduled_at))
          .filter(d => d > now)
          .sort((a,b) => a - b)[0];
        if (future) {
          const diff = future - now;
          const mins = Math.floor(diff / 60000);
          const hrs = Math.floor(mins / 60);
          const rem = mins % 60;
          setTimeToNext(`${hrs}h ${rem}m`);
        } else {
          setTimeToNext('none');
        }
      }
      [...s, ...sch].forEach(e => {
        if (e.campaign_id && e.campaign_name) camps.set(e.campaign_id, e.campaign_name);
      });
      filterCampaignOptions.current = [...camps.entries()].sort((a,b) => a[1].localeCompare(b[1]));
    } catch (e) {
      notify({ type: 'error', message: 'Failed to load schedule data' });
    } finally {
      // loading.stop();
    }
  };

  useEffect(() => {
    loadData().then(() => setInitialLoaded(true));
    // auto-refresh every 30s, similar to template UI
    const id = setInterval(loadData, 30000);
    return () => clearInterval(id);
  }, [daysBack, daysAhead]);

  useEffect(() => {
    if (!initialLoaded) return;
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      async ([entry]) => {
        if (!entry.isIntersecting || isLoadingMoreRef.current) return;
        isLoadingMoreRef.current = true;
        setIsLoadingMore(true);
        const nextAhead = daysAhead + SCHEDULE_DAYS_AHEAD;
        setDaysAhead(nextAhead);
        await loadData({ daysAhead: nextAhead });
        setIsLoadingMore(false);
        isLoadingMoreRef.current = false;
      },
      { threshold: 0.1 }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [daysAhead, initialLoaded]);

  const clearFilters = () => {
    setCampaignFilter('');
    setStatusFilter('');
    setSearchFilter('');
  };

  const matchesFilter = item => {
    if (campaignFilter && String(item.campaign_id) !== campaignFilter) return false;
    if (statusFilter && item.type !== statusFilter) return false;
    if (searchFilter) {
      const hay = [item.lead_email, item.lead_name, item.subject, item.campaign_name, item.inbox_email].join(' ').toLowerCase();
      if (!hay.includes(searchFilter.toLowerCase())) return false;
    }
    return true;
  };

  const filteredSent = sent.filter(matchesFilter);
  const filteredScheduled = scheduled.filter(matchesFilter);

  const fmtTime = iso => {
    if (!iso) return '';
    const d = new Date(iso);
    const base = String(d.getHours()).padStart(2,'0')+':' + String(d.getMinutes()).padStart(2,'0');
    return isProduction ? base : base + ':' + String(d.getSeconds()).padStart(2,'0');
  };
  const fmtDateTime = iso => {
    if (!iso) return '—';
    const d = new Date(iso);
    const base = String(d.getHours()).padStart(2,'0')+':' + String(d.getMinutes()).padStart(2,'0');
    const t = isProduction ? base : base + ':' + String(d.getSeconds()).padStart(2,'0');
    return d.toLocaleDateString() + ' ' + t;
  };
  const renderLastRun = iso => {
    if (!iso) return '—';
    const d = new Date(iso); const now = new Date(); const diff = Math.floor((now-d)/60000);
    return diff < 1 ? 'Just now' : diff + 'm ago';
  };
  const recalculateAll = async () => {
    setRecalcState({ busy: true, text: '⚡ Recalculating...' });
    try {
      const res = await fetch('/api/schedule/recalculate-all',{method:'POST'});
      if (res.ok) {
        const data = await res.json();
        const stratLabel = strategy==='priority'?'Priority':'Round-Robin';
        const message = `✓ Done! [${stratLabel}] (${data.campaigns_processed} campaigns, ${data.total_slots} slots)`;
        setRecalcState({ busy: true, text: message });
        setTimeout(loadData,100);
      } else {
        const t = await res.text();
        notify({ type: 'error', message: 'Error recalculating: ' + t });
        setRecalcState({ busy: false, text: '⚡ Recalculate All Campaigns' });
      }
    } catch(err) {
      notify({ type: 'error', message: 'Error: ' + err.message });
      setRecalcState({ busy: false, text: '⚡ Recalculate All Campaigns' });
    } finally {
      setTimeout(()=>{
        setRecalcState({ busy: false, text: '⚡ Recalculate All Campaigns' });
      },2000);
    }
  };
  const validateQueue = async () => {
    setValidateState({ busy: true, text: '🔍 Validating...' });
    try {
      const res = await fetch('/api/schedule/validate-queue',{method:'POST'});
      if (res.ok) {
        const data = await res.json();
        const issues = data.issues||[];
        const txt = `✓ Validated (${data.total_slots_checked} slots, ${issues.length} issue${issues.length!==1?'s':''})`;
        setValidateState({ busy: true, text: txt });
        if (issues.length) {
          notify({ type: 'error', message: `Validation completed — ${issues.length} issue(s) found. Open console for details.` });
          console.log('Validation result:', data);
        }
        loadData();
      } else {
        const t = await res.text();
        notify({ type: 'error', message: 'Validation failed: ' + t });
        setValidateState({ busy: false, text: '🔍 Validate Queue' });
      }
    } catch(err){
      notify({ type: 'error', message: 'Error: ' + err.message });
      setValidateState({ busy: false, text: '🔍 Validate Queue' });
    }
    finally {
      setTimeout(()=>{
        setValidateState({ busy: false, text: '🔍 Validate Queue' });
      },2000);
    }
  };

  const groupByDate = (items, reverse=false) => {
    const by = {};
    items.forEach(i => {
      const d = (reverse ? i.sent_date : i.scheduled_date) || 'unknown';
      if (!by[d]) by[d] = [];
      by[d].push(i);
    });
    const keys = Object.keys(by).sort();
    if (reverse) keys.reverse();
    const ordered = {};
    keys.forEach(k=>{ordered[k]=by[k];});
    return ordered;
  };

  const renderRow = (item, uid) => {
    const isSent = item.type === 'sent';
    const time = isSent ? fmtTime(item.sent_at) : fmtTime(item.scheduled_at);
    const statusCls = isSent ? 'sent' : 'scheduled';
    const statusLabel = isSent ? 'Sent' : 'Scheduled';
    const subject = item.subject || '(no subject)';
    const inboxLabel = item.inbox_email || '—';
    const isExpanded = expandedId === uid;
    return (
      <div key={uid}>
        <div
          className={`email-row${isExpanded?' expanded':''}`}
          onClick={async () => {
            if (isExpanded) {
              setExpandedId(null);
              return;
            }
            setExpandedId(uid);
            await ensureDetail(item);
          }}
        >
          <div className="time-col">{time}</div>
          <div className="status-col"><span className={`badge-status ${statusCls}`}>{statusLabel}</span></div>
          <div className="lead-col" title={item.lead_email}>
            {/* {item.lead_email}{item.lead_status && <span className={`badge ${item.lead_status}`} style={{marginLeft:'0.3rem',fontSize:'0.75rem'}}>{item.lead_status}</span>} */}
          </div>
          <div className="subj-col" title={subject}>{subject}</div>
          <div className="camp-col" title={item.campaign_name}>{item.campaign_name}</div>
          <div className="inbox-col" title={inboxLabel}>{inboxLabel}</div>
        </div>
        {isExpanded && (
          <div className="detail-panel open">
            <div className="dp-grid">
              <div><span className="dp-label">Status</span><br/><span className={`badge-status ${statusCls}`} style={{fontSize:'0.8rem'}}>{statusLabel}</span></div>
              {isSent ? (
                <div><span className="dp-label">Sent at</span><br/><span className="dp-val">{fmtDateTime(item.sent_at)}</span></div>
              ) : (
                <div><span className="dp-label">Scheduled for</span><br/><span className="dp-val">{fmtDateTime(item.scheduled_at)}</span></div>
              )}
              <div><span className="dp-label">Lead</span><br/><span className="dp-val mono">{item.lead_email}</span>{item.lead_name ? ` (${item.lead_name})` : ''}<br/><span className={`badge ${item.lead_status}`}>{item.lead_status}</span></div>
              <div><span className="dp-label">Campaign</span><br/><span className="dp-val"><a href={`/campaigns/${item.campaign_id}`}>{item.campaign_name}</a></span></div>
              <div><span className="dp-label">Sequence step</span><br/><span className="dp-val">{item.sequence_index+1}</span></div>
              <div><span className="dp-label">Wait after previous</span><br/><span className="dp-val">{item.sequence_wait_days??0} day(s)</span></div>
              <div className="dp-full"><span className="dp-label">Subject</span><br/><span className="dp-val">{subject}</span></div>
              {!isSent && (
                <>
                  <div><span className="dp-label">Inbox</span><br/><span className="dp-val mono">{inboxLabel}</span>{item.inbox_display_name ? ` (${item.inbox_display_name})` : ''}</div>
                  <div><span className="dp-label">Send method</span><br/><span className="dp-val">{(item.inbox_provider||'').toUpperCase()}</span></div>
                  <div><span className="dp-label">Inbox max/day</span><br/><span className="dp-val">{item.inbox_max_per_day??'—'}</span></div>
                  <div><span className="dp-label">Position in day</span><br/><span className="dp-val">#{item.position_in_day??'—'}</span></div>
                </>
              )}
              {isSent && item.message_id && (
                <div className="dp-full"><span className="dp-label">Message ID</span><br/><span className="dp-val mono" style={{fontSize:'0.78rem'}}>{item.message_id}</span></div>
              )}
              <div><span className="dp-label">Sending window</span><br/><span className="dp-val">{item.campaign_hours_start} – {item.campaign_hours_end}</span></div>
              <div><span className="dp-label">Sending days</span><br/><span className="dp-val">{(item.campaign_sending_days||[]).map(d=>DAY_NAMES[d]).join(', ')}</span></div>
              <div><span className="dp-label">Stop on reply</span><br/><span className="dp-val">{item.campaign_stop_on_reply?'Yes':'No'}</span></div>
              {item.sequence_body && (
                <div className="dp-full"><span className="dp-label">Email body</span>
                  <div className="flex items-center gap-2 mt-1 mb-1">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={async e => {
                        e.stopPropagation();
                        const full = await ensureDetail(item);
                        setPreviewItem(full);
                      }}
                    >
                      View full preview
                    </Button>
                  </div>
                  <div className="body-preview" dangerouslySetInnerHTML={{__html:item.sequence_body.trim().startsWith('<')?item.sequence_body:escapeHtml(item.sequence_body)}} />
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderSection = () => {
    // add header row before items
    const header = (
      <div className="email-row header" key="header">
        <div className="time-col">Time</div>
        <div className="status-col">Status</div>
        <div className="lead-col">Lead</div>
        <div className="subj-col">Subject</div>
        <div className="camp-col">Campaign</div>
        <div className="inbox-col">Inbox</div>
      </div>
    );
    const today = new Date().toISOString().slice(0,10);
    const parts = [];
    if (filteredSent.length) {
      const totalSent = stats.total_sent ?? filteredSent.length;
      parts.push(
        <div key="past">
          <div className="section-hdr" onClick={() => setPastExpanded(pe=>!pe)}>
            <span className={`arrow ${pastExpanded?'open':''}`}>&#9654;</span> Sent ({totalSent} email{totalSent!==1?'s':''})
          </div>
          {pastExpanded && Object.entries(groupByDate(filteredSent,true)).map(([d,items]) => (
            <div key={d}>
              <div className="date-hdr">{d}</div>
              {items.map(i=>renderRow(i,`sent-${i.log_id}`))}
            </div>
          ))}
        </div>
      );
    }
    if (filteredScheduled.length) {
      const totalScheduled = stats.total_scheduled ?? filteredScheduled.length;
      parts.push(
        <div key="upcoming">
          <div className="section-hdr" style={{cursor:'default'}}>
            <span className="arrow open">&#9654;</span> Scheduled ({totalScheduled} email{totalScheduled!==1?'s':''})
          </div>
          {Object.entries(groupByDate(filteredScheduled,false)).map(([d,items]) => (
            <div key={d}>
              <div className="date-hdr">{d}{d===today && <span style={{color:'var(--success)',fontWeight:400,fontSize:'0.8rem',marginLeft:'0.5rem'}}>today</span>}</div>
              {items.sort((a,b)=>(a.scheduled_at||'').localeCompare(b.scheduled_at||'')||((a.position_in_day||0)-(b.position_in_day||0))).map(i=>renderRow(i,`sched-${i.slot_id}`))}
            </div>
          ))}
        </div>
      );
    }
    if (parts.length) {
      // add header bar at top of list
      parts.unshift(header);
    }
    if (!parts.length) return <p style={{color:'var(--muted)',padding:'1rem 0'}}>No emails match your filters.</p>;
    return parts;
  };

  return (
    <div className="p-8">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">Schedule</h1>
        <span className="text-xs text-gray-400 bg-gray-100 rounded px-2 py-0.5" title="Times are stored in UTC and displayed in your local timezone below">
          🕐 Times in {Intl.DateTimeFormat().resolvedOptions().timeZone.replace(/_/g, ' ')}
        </span>
      </div>
      <Card className="flex flex-wrap justify-between items-center mb-4 p-2">
        <div className="flex flex-wrap gap-4 items-center">
          {!isProduction && (
            <>
              <div>
                <span className="text-sm text-gray-500">Test Mode:</span> <span className={serverStatus.test_mode?'text-red-600':'text-green-600'}>{serverStatus.test_mode?'ON':'OFF'}</span>
              </div>
              <div title="Backend server clock (UTC). Campaign sending windows are interpreted in their configured timezone and stored as UTC, then displayed here in your browser's local time.">
                <span className="text-sm text-gray-500">Server (UTC):</span>{' '}
                <span className="font-mono text-xs">
                  {serverStatus.server_time
                    ? new Date(serverStatus.server_time).toISOString().replace('T',' ').slice(0,19) + ' UTC'
                    : '—'}
                </span>
                {serverStatus.server_time && (
                  <span className="ml-1 text-xs text-gray-400">
                    = {new Date(serverStatus.server_time).toLocaleTimeString()} local
                  </span>
                )}
              </div>
              <div title="Time until the next scheduled email fires (calculated from server UTC time)">
                <span className="text-sm text-gray-500">Next email in:</span>{' '}
                <span className="font-semibold">{timeToNext || '—'}</span>
              </div>
            </>
          )}
          <div>
            <span className="text-sm text-gray-500">Schedule:</span> <span className={serverStatus.schedule_running?'text-green-600':'text-red-600'}>{serverStatus.schedule_running?'Running':'Stopped'}</span>
          </div>
          <div>
            <span className="text-sm text-gray-500">Last Job:</span> <span className="font-semibold">{renderLastRun(serverStatus.last_send_job_run)}</span>
          </div>
          <div>
            <span className="text-sm text-gray-500">Last Run Sent:</span> <span className="font-semibold">{serverStatus.last_send_job_sent_count??0}</span>
          </div>
          <div>
            <span className="text-sm text-gray-500">Strategy:</span> <span className={strategy==='round_robin'?'text-teal-500':'text-gray-900'} style={{cursor:'pointer',textDecoration:'underline dotted',textUnderlineOffset:'3px'}} title="Change in Settings" onClick={()=>window.location='/settings'}>{strategy==='priority'?'Priority':'Round-Robin'}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Auto-refresh: 30s</span>
          <Button size="sm" variant="outline" onClick={loadData}>↻ Refresh Now</Button>
        </div>
      </Card>
      <div className="stats-row mb-4">
        <div className="stat-card"><div className="num" id="stat-sent">{stats.total_sent||0}</div><div className="lbl">Total sent</div></div>
        <div className="stat-card"><div className="num" id="stat-sched">{stats.total_scheduled||0}</div><div className="lbl">Scheduled</div></div>
        <div className="stat-card"><div className="num" id="stat-camps">{stats.total_campaigns||0}</div><div className="lbl">Campaigns</div></div>
      </div>
      <div className="cal-toolbar mb-4 flex flex-wrap gap-2 items-center">
        <select value={campaignFilter} onChange={e=>setCampaignFilter(e.target.value)} className="border rounded p-1 text-sm">
          <option value="">All campaigns</option>
          {filterCampaignOptions.current.map(([id,name])=> <option key={id} value={id}>{name}</option>)}
        </select>
        <select value={statusFilter} onChange={e=>setStatusFilter(e.target.value)} className="border rounded p-1 text-sm">
          <option value="">All statuses</option>
          <option value="sent">Sent</option>
          <option value="scheduled">Scheduled</option>
        </select>
        <input type="text" value={searchFilter} onChange={e=>setSearchFilter(e.target.value)} placeholder="Search lead, subject…" className="border rounded p-1 text-sm" style={{maxWidth:'240px'}} />
        <Button size="sm" variant="outline" onClick={clearFilters}>Clear</Button>
        {!isProduction && (
          <>
            <Button
              id="validate-queue-btn"
              size="sm"
              variant="outline"
              onClick={validateQueue}
              disabled={validateState.busy}
            >
              {validateState.text}
            </Button>
            <Button
              id="recalc-all-btn"
              size="sm"
              variant="outline"
              onClick={recalculateAll}
              disabled={recalcState.busy}
            >
              {recalcState.text}
            </Button>
          </>
        )}
      </div>
      <Card className="p-4" id="schedule-body">
        {renderSection()}
        <div ref={sentinelRef} style={{ height: 1 }} />
        {isLoadingMore && <p style={{ textAlign: 'center', padding: '0.5rem', color: 'var(--muted)' }}>Loading more…</p>}
      </Card>

      {/* Email preview modal */}
      {previewItem && (
        <ScheduleEmailPreviewModal item={previewItem} onClose={() => setPreviewItem(null)} />
      )}
    </div>
  );
}
