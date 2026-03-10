import { useState, useEffect, useRef, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { api, apiCache } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import DatePicker from '../components/ui/DatePicker';
// Recharts for charts
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

// Fill every day in [start, end] with zeros where no data exists
function fillDateRange(dataMap, startDate, endDate) {
  const result = [];
  if (!startDate || !endDate) return Object.values(dataMap).sort((a,b)=>a.date.localeCompare(b.date));
  const cur = new Date(startDate + 'T00:00:00');
  const end = new Date(endDate + 'T00:00:00');
  while (cur <= end) {
    const iso = localIso(cur);
    result.push(dataMap[iso] || { date: iso, sent: 0, totalOpens: 0, uniqueOpens: 0, totalReplies: 0, totalClicks: 0, uniqueClicks: 0 });
    cur.setDate(cur.getDate() + 1);
  }
  return result;
}

function localIso(dt) {
  return `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`;
}

function buildPresets(serverToday) {
  const t = serverToday || new Date();
  const todayStr = localIso(t);
  const d = (offset) => { const dt = new Date(t); dt.setDate(dt.getDate() + offset); return localIso(dt); };
  const monday = (dt) => { const x = new Date(dt); const day = x.getDay(); x.setDate(x.getDate() - (day === 0 ? 6 : day - 1)); return x; };
  const lastWeekEnd = new Date(monday(t)); lastWeekEnd.setDate(lastWeekEnd.getDate() - 1);
  const lastWeekStart = localIso(monday(lastWeekEnd));
  const lastMonthStart = localIso(new Date(t.getFullYear(), t.getMonth() - 1, 1));
  const lastMonthEnd = localIso(new Date(t.getFullYear(), t.getMonth(), 0));
  return [
    { label: 'Last 7 Days',  start: d(-6),          end: todayStr },
    { label: 'Last Week',    start: lastWeekStart,  end: localIso(lastWeekEnd) },
    { label: 'Last 30 Days', start: d(-29),         end: todayStr },
    { label: 'Last Month',   start: lastMonthStart, end: lastMonthEnd },
    { label: 'Last 90 Days', start: d(-89),         end: todayStr },
  ];
}

export default function Analytics() {
  const [campaigns, setCampaigns] = useState(() => apiCache.get('/campaigns') || []);
  // current dropdown choice and list of selected campaign ids
  const [currentChoice, setCurrentChoice] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [error, setError] = useState(null);
  // schedule sent data and filters
  const [allSent, setAllSent] = useState(() => apiCache.get('/schedule/sent') || []);
  const [serverToday, setServerToday] = useState(null);

  const [presets, setPresets] = useState(() => buildPresets(new Date()));
  const [activePreset, setActivePreset] = useState('Last 7 Days');
  const defaultRange = presets.find(p => p.label === 'Last 7 Days') || presets[0];
  const [startDate, setStartDate] = useState(defaultRange.start);
  const [endDate, setEndDate] = useState(defaultRange.end);

  // state for whether each series should be hidden
  const [hideSeries, setHideSeries] = useState({
    sent: false,
    totalOpens: false,
    uniqueOpens: false,
    totalReplies: false,
    totalClicks: false,
    uniqueClicks: false,
  });
  // initialize hide flags for any series that are all zero; run only once when data arrives
  const initializedRef = useRef(false);
  const [zoomRange, setZoomRange] = useState({ start: 0, end: 0 });
  const activeChartIdxRef = useRef(null);
  const chartContainerRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const [camps, sent, offsetData] = await Promise.all([
          api.get('/campaigns'),
          api.get('/schedule/sent').catch(() => []),
          api.get('/settings/time-offset').catch(() => ({ time_offset_days: 0 })),
        ]);
        setCampaigns(camps);
        setAllSent(sent);
        // compute server today using offset
        const off = parseInt(offsetData.time_offset_days || 0, 10);
        const t = new Date();
        t.setDate(t.getDate() + off);
        setServerToday(t);
        // Rebuild presets using server time
        const newPresets = buildPresets(t);
        setPresets(newPresets);
        const last7 = newPresets.find(p => p.label === 'Last 7 Days') || newPresets[0];
        setStartDate(last7.start);
        setEndDate(last7.end);
        setActivePreset('Last 7 Days');
      } catch (e) {
        setError('Failed to load analytics');
      }
    })();
  }, []);

  const applyPreset = (preset) => {
    setActivePreset(preset.label);
    setStartDate(preset.start);
    setEndDate(preset.end);
  };

  const filtered = selectedIds.length
    ? campaigns.filter(c => selectedIds.includes(String(c.id)))
    : campaigns;

  // compute aggregated stats for open/click rates (used to approximate uniques)
  const totalSentForRates = filtered.reduce((acc,c)=>acc + ((c.stats?.emails_sent)||0),0);
  const totalOpened = filtered.reduce((acc,c)=>acc + ((c.stats?.open_rate||0) * ((c.stats?.emails_sent)||0)),0);
  const totalClicked = filtered.reduce((acc,c)=>acc + ((c.stats?.click_rate||0) * ((c.stats?.emails_sent)||0)),0);
  const openRateAll = totalSentForRates>0 ? Math.round((totalOpened/totalSentForRates)*100) : 0;
  const clickRateAll = totalSentForRates>0 ? Math.round((totalClicked/totalSentForRates)*100) : 0;

  // filtered sent events by campaign and date range
  const filteredSent = useMemo(() => allSent.filter(e => {
    if (selectedIds.length && !selectedIds.includes(String(e.campaign_id))) return false;
    if (startDate && e.sent_date < startDate) return false;
    if (endDate && e.sent_date > endDate) return false;
    return true;
  }), [allSent, selectedIds, startDate, endDate]);
  // per-campaign range stats aggregated from filteredSent
  const filteredStatsByCampaign = useMemo(() => {
    const map = {};
    const seenLeads = {};
    filteredSent.forEach(e => {
      const cid = String(e.campaign_id);
      if (!map[cid]) {
        map[cid] = { sent: 0, replies: 0, totalOpens: 0, totalClicks: 0 };
        seenLeads[cid] = new Set();
      }
      map[cid].sent += 1;
      if (e.lead_id) seenLeads[cid].add(String(e.lead_id));
      if (e.lead_status === 'replied') map[cid].replies += 1;
      (e.opens || []).forEach(() => map[cid].totalOpens += 1);
      (e.clicks || []).forEach(() => map[cid].totalClicks += 1);
    });
    Object.keys(map).forEach(cid => { map[cid].uniqueLeads = seenLeads[cid].size; });
    return map;
  }, [filteredSent]);
  // build daily data
  const chartData = useMemo(() => {
    const dailyMap = {};
    const seenOpen = {};
    const seenClick = {};
    filteredSent.forEach(e => {
      const d = e.sent_date || (e.sent_at ? e.sent_at.slice(0,10) : '');
      if (!d) return;
      if (!dailyMap[d]) {
        dailyMap[d] = { date: d, sent: 0, totalOpens: 0, uniqueOpens: 0, totalReplies: 0, totalClicks: 0, uniqueClicks: 0 };
        seenOpen[d] = new Set();
        seenClick[d] = new Set();
      }
      dailyMap[d].sent += 1;
      if (e.lead_status === 'replied') dailyMap[d].totalReplies += 1;
      if (e.opens && e.opens.length) {
        dailyMap[d].totalOpens += e.opens.length;
        e.opens.forEach(o => {
          if (o && o.ip) seenOpen[d].add(o.ip);
        });
      }
      if (e.clicks && e.clicks.length) {
        dailyMap[d].totalClicks += e.clicks.length;
        e.clicks.forEach(c => {
          if (c && c.ip) seenClick[d].add(c.ip);
        });
      }
    });
    // derive unique counts
    Object.keys(dailyMap).forEach(d => {
      dailyMap[d].uniqueOpens = seenOpen[d].size;
      dailyMap[d].uniqueClicks = seenClick[d].size;
    });
    return fillDateRange(dailyMap, startDate, endDate);
  }, [filteredSent, startDate, endDate]);

  // series metadata for chart and legend
  const seriesList = [
    { key: 'sent', name: 'Sent', stroke: 'rgba(59,130,246,0.8)', fill: 'rgba(59,130,246,0.4)' },
    { key: 'totalOpens', name: 'Total Opens', stroke: 'rgba(234,179,8,0.8)', fill: 'rgba(234,179,8,0.4)' },
    { key: 'uniqueOpens', name: 'Unique Opens', stroke: 'rgba(16,185,129,0.8)', fill: 'rgba(16,185,129,0.4)' },
    { key: 'totalReplies', name: 'Total Replies', stroke: 'rgba(45,212,191,0.8)', fill: 'rgba(45,212,191,0.4)' },
    { key: 'totalClicks', name: 'Total Clicks', stroke: 'rgba(234,88,12,0.8)', fill: 'rgba(234,88,12,0.4)' },
    { key: 'uniqueClicks', name: 'Unique Clicks', stroke: 'rgba(236,72,153,0.8)', fill: 'rgba(236,72,153,0.4)' },
  ];

  // once chartData is computed, initialize hide state for any all-zero series (only first time)
  useEffect(() => {
    if (!initializedRef.current && chartData.length > 0 && chartData.some(d => seriesList.some(s => d[s.key] > 0))) {
      const newHide = {};
      seriesList.forEach(s => {
        newHide[s.key] = chartData.every(d => d[s.key] === 0);
      });
      setHideSeries(prev => ({ ...prev, ...newHide }));
      initializedRef.current = true;
    }
  }, [chartData]);

  // Reset zoom when chartData changes (new date range selected)
  useEffect(() => {
    setZoomRange({ start: 0, end: Math.max(0, chartData.length - 1) });
  }, [chartData]);

  // Attach wheel listener with passive:false so we can preventDefault
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

  const toggleSeries = key => {
    setHideSeries(prev => ({ ...prev, [key]: !prev[key] }));
  };

  // calculate totals for selected range
  const rangeSent = chartData.reduce((a,d)=>a+d.sent,0);
  const rangeReplies = chartData.reduce((a,d)=>a+d.totalReplies,0);
  const rangeClicks = chartData.reduce((a,d)=>a+d.totalClicks,0);
  const totalRepliesFromStats = filtered.reduce((a,c)=>a+(c.stats?.replies||0),0);
  const totalSentFromStats = filtered.reduce((a,c)=>a+(c.stats?.emails_sent||0),0);
  const replyRateRange = totalSentFromStats > 0 ? Math.round((totalRepliesFromStats / totalSentFromStats) * 100) : 0;
  const clickRateRange = rangeSent > 0 ? Math.round((rangeClicks / rangeSent) * 100) : 0;

  // Format x-axis dates short
  const formatXDate = (d) => {
    if (!d) return '';
    const parts = d.split('-');
    return `${parseInt(parts[1])}/${parseInt(parts[2])}`;
  };

  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-semibold mb-4">Campaign Analytics</h1>
      {error && <div className="text-red-600">{error}</div>}

      {/* KPI cards */}
      <div className="flex flex-wrap gap-4 mb-4">
        <Card className="p-4">
          <div className="text-sm text-gray-500">Total Sent</div>
          <div className="text-2xl font-bold">{rangeSent}</div>
        </Card>
        <Card className="p-4">
          <div className="text-sm text-gray-500">Reply Rate</div>
          <div className="text-2xl font-bold">{replyRateRange}%</div>
        </Card>
        <Card className="p-4">
          <div className="text-sm text-gray-500">Click Rate</div>
          <div className="text-2xl font-bold">{clickRateRange}%</div>
        </Card>
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
        <div className="flex items-center gap-4">
          <label className="text-sm flex items-center gap-1">From <DatePicker value={startDate} onChange={v => { setStartDate(v); setActivePreset('custom'); }} /></label>
          <label className="text-sm flex items-center gap-1">To <DatePicker value={endDate} onChange={v => { setEndDate(v); setActivePreset('custom'); }} /></label>
        </div>
      )}

      {/* timeline area chart — scroll to zoom, centered on hovered day */}
      <Card className="p-4">
        <div ref={chartContainerRef} style={{ width: '100%', height: 290 }}>
          <ResponsiveContainer>
            <ReAreaChart data={displayData} onMouseMove={handleChartMouseMove} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
              <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatXDate} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip wrapperStyle={{ zIndex: 1000 }} />
              <CartesianGrid strokeDasharray="3 3" />
              {seriesList.map(s => (
                <Area
                  key={s.key}
                  name={s.name}
                  type="monotone"
                  dataKey={s.key}
                  stroke={s.stroke}
                  fill={s.fill}
                  hide={hideSeries[s.key]}
                />
              ))}
              <Legend
                verticalAlign="bottom"
                align="center"
                content={() => (
                  <div className="flex flex-wrap justify-center gap-3 mt-2">
                    {seriesList.map(s => (
                      <span
                        key={s.key}
                        onClick={() => toggleSeries(s.key)}
                        className={`flex items-center gap-1 cursor-pointer select-none text-xs transition-opacity ${hideSeries[s.key] ? 'opacity-40' : ''}`}
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
      <div className="flex flex-col lg:flex-row items-start lg:items-center gap-4">
        <div className="flex-1">
          <label htmlFor="campaign-select" className="sr-only">Campaigns</label>
          <div className="flex items-center gap-2">
            <select
              id="campaign-select"
              value={currentChoice}
              onChange={e => setCurrentChoice(e.target.value)}
              className="border rounded px-2 py-1"
            >
              <option value="">Add a campaign…</option>
              {campaigns
                .filter(c => !selectedIds.includes(String(c.id)))
                .map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
            </select>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (currentChoice && !selectedIds.includes(currentChoice)) {
                  setSelectedIds([...selectedIds, currentChoice]);
                }
                setCurrentChoice('');
              }}
              disabled={!currentChoice}
            >
              Add
            </Button>
          </div>
          {selectedIds.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {selectedIds.map(id => {
                const camp = campaigns.find(c => String(c.id) === id);
                return (
                  <span
                    key={id}
                    className="inline-flex items-center bg-gray-200 rounded-full px-2 py-0.5 text-sm"
                  >
                    {camp?.name || id}
                    <button
                      className="ml-1 text-gray-500 hover:text-gray-700"
                      onClick={() => setSelectedIds(selectedIds.filter(x => x !== id))}
                    >
                      ×
                    </button>
                  </span>
                );
              })}
            </div>
          )}
        </div>
        {/* reply-rate bar chart removed per request */}
        <div className="flex-1 space-y-2"></div>
      </div>

      {filtered.length === 0 ? (
        <Card>
          {campaigns.length === 0 ? (
            'No campaigns to analyze.'
          ) : (
            'No matching campaigns.'
          )}
        </Card>
      ) : (
        <Card className="overflow-auto">
          <table className="w-full table-auto border-collapse">
            <thead>
              <tr>
                <th>Name</th>
                <th className="text-center">Leads</th>
                <th className="text-center">Sent</th>
                <th className="text-center">Pending</th>
                <th className="text-center">Progress</th>
                <th className="text-center">Replies</th>
                <th className="text-center">Reply Rate</th>
                <th className="text-center">Open Rate</th>
                <th className="text-center">Click Rate</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(c => {
                const allTimeStats = c.stats || {};
                const rStats = filteredStatsByCampaign[String(c.id)] || {};
                const sent = rStats.sent || 0;
                const leads = rStats.uniqueLeads || 0;
                const replies = rStats.replies || 0;
                const openRate = sent > 0 ? Math.round((rStats.totalOpens || 0) / sent * 100) : 0;
                const replyRate = sent > 0 ? Math.round(replies / sent * 100) : 0;
                const clickRate = sent > 0 ? Math.round((rStats.totalClicks || 0) / sent * 100) : 0;
                // progress is all-time (scheduled vs sent) — no range equivalent
                const allTimeSent = allTimeStats.emails_sent || 0;
                const scheduled = allTimeStats.scheduled || 0;
                const denom = allTimeSent + scheduled;
                const progress = denom > 0 ? Math.round((allTimeSent / denom) * 100) : 0;
                return (
                  <tr key={c.id} className="hover:bg-gray-50">
                    <td className="py-2">
                      <Link to={`/campaigns/${c.id}#analytics`} className="text-teal-500">
                        {c.name}
                      </Link>
                    </td>
                    <td className="py-2 text-center font-mono">{leads}</td>
                    <td className="py-2 text-center font-mono">{sent}</td>
                    <td className="py-2 text-center font-mono">{scheduled}</td>
                    <td className="py-2 text-center">
                      <div className="bg-gray-200 h-2 rounded overflow-hidden mx-auto w-32">
                        <div className="bg-teal-500 h-2" style={{width: `${progress}%`}} />
                      </div>
                      <div className="text-xs mt-1">{progress}%</div>
                    </td>
                    <td className="py-2 text-center font-mono">{replies}</td>
                    <td className="py-2 text-center">
                      <div className="bg-gray-200 h-2 rounded overflow-hidden mx-auto w-24">
                        <div className="bg-teal-500 h-2" style={{width: `${replyRate}%`}} />
                      </div>
                      <div className="text-xs mt-1">{replyRate}%</div>
                    </td>
                    <td className="py-2 text-center">{openRate}%</td>
                    <td className="py-2 text-center">{clickRate}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
