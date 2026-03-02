import { useEffect, useState, useRef } from 'react';
import { api } from '../api';
import { useLoading } from '../context/LoadingContext';
import { useNotify } from '../context/NotificationContext';
import { useConfirm } from '../context/ConfirmContext';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';

const DAY_NAMES = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

function escapeHtml(s){
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

export default function Schedule() {
  const loading = useLoading();
  const notify = useNotify();

  const [sent, setSent] = useState([]);
  const [scheduled, setScheduled] = useState([]);
  const [stats, setStats] = useState({});
  const [serverStatus, setServerStatus] = useState({});
  const [strategy, setStrategy] = useState('priority');
  const confirm = useConfirm();

  const [campaignFilter, setCampaignFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [searchFilter, setSearchFilter] = useState('');

  const [pastExpanded, setPastExpanded] = useState(false);
  const [expandedId, setExpandedId] = useState(null);

  // button states for recalc/validate so React can re-render correctly
  const [recalcState, setRecalcState] = useState({ busy: false, text: '⚡ Recalculate All Campaigns' });
  const [validateState, setValidateState] = useState({ busy: false, text: '🔍 Validate Queue' });

  const filterCampaignOptions = useRef([]);

  const loadData = async () => {
    // loading.start();
    try {
      const [s, sch, st, srv, stratData] = await Promise.all([
        api.get('/schedule/sent').catch(() => []),
        api.get('/schedule/scheduled').catch(() => []),
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
    loadData();
    // auto-refresh every 30s, similar to template UI
    const id = setInterval(loadData, 30000);
    return () => clearInterval(id);
  }, []);

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
    return String(d.getHours()).padStart(2,'0')+':' + String(d.getMinutes()).padStart(2,'0');
  };
  const fmtDateTime = iso => {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + String(d.getHours()).padStart(2,'0')+':' + String(d.getMinutes()).padStart(2,'0');
  };
  const renderLastRun = iso => {
    if (!iso) return '—';
    const d = new Date(iso); const now = new Date(); const diff = Math.floor((now-d)/60000);
    return diff < 1 ? 'Just now' : diff + 'm ago';
  };
  const recalculateAll = async () => {
    const ok = await confirm('Recalculate queue slots for ALL campaigns? This will recompute all scheduled emails while preserving already-sent sequences.');
    if (!ok) return;
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
    const ok = await confirm('Run validation checks for scheduled emails?');
    if (!ok) return;
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
        <div className={`email-row${isExpanded?' expanded':''}`} onClick={()=>setExpandedId(isExpanded?null:uid)}>
          <div className="time-col">{time}</div>
          <div className="status-col"><span className={`badge-status ${statusCls}`}>{statusLabel}</span></div>
          <div className="lead-col" title={item.lead_email}>
            {item.lead_email}{item.lead_status && <span className={`badge ${item.lead_status}`} style={{marginLeft:'0.3rem',fontSize:'0.75rem'}}>{item.lead_status}</span>}
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
      parts.push(
        <div key="past">
          <div className="section-hdr" onClick={() => setPastExpanded(pe=>!pe)}>
            <span className={`arrow ${pastExpanded?'open':''}`}>&#9654;</span> Sent ({filteredSent.length} email{filteredSent.length!==1?'s':''})
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
      parts.push(
        <div key="upcoming">
          <div className="section-hdr" style={{cursor:'default'}}>
            <span className="arrow open">&#9654;</span> Scheduled ({filteredScheduled.length} email{filteredScheduled.length!==1?'s':''})
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
      <h1 className="text-2xl font-bold">Schedule</h1>
      <Card className="flex flex-wrap justify-between items-center mb-4 p-2">
        <div className="flex flex-wrap gap-4 items-center">
          <div>
            <span className="text-sm text-gray-500">Test Mode:</span> <span className={serverStatus.test_mode?'text-red-600':'text-green-600'}>{serverStatus.test_mode?'ON':'OFF'}</span>
          </div>
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
      </div>
      <Card className="p-4" id="schedule-body">
        {renderSection()}
      </Card>
    </div>
  );
}
