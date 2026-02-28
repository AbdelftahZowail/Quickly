import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
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

export default function Analytics() {
  const [campaigns, setCampaigns] = useState([]);
  // current dropdown choice and list of selected campaign ids
  const [currentChoice, setCurrentChoice] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [error, setError] = useState(null);
  // calendar sent data and filters
  const [allSent, setAllSent] = useState([]);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [serverToday, setServerToday] = useState(null);
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

  useEffect(() => {
    (async () => {
      try {
        const [camps, sent, offsetData] = await Promise.all([
          api.get('/campaigns'),
          api.get('/calendar/sent').catch(() => []),
          api.get('/settings/time-offset').catch(() => ({ time_offset_days: 0 })),
        ]);
        setCampaigns(camps);
        setAllSent(sent);
        // compute server today using offset
        const off = parseInt(offsetData.time_offset_days || 0, 10);
        const t = new Date();
        t.setDate(t.getDate() + off);
        setServerToday(t);
        if (!endDate) {
          const iso = t.toISOString().slice(0,10);
          setEndDate(iso);
        }
        if (!startDate) {
          const past = new Date(t);
          past.setDate(past.getDate() - 7);
          setStartDate(past.toISOString().slice(0,10));
        }
      } catch (e) {
        setError('Failed to load analytics');
      }
    })();
  }, []);

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
  const filteredSent = allSent.filter(e => {
    if (selectedIds.length && !selectedIds.includes(String(e.campaign_id))) return false;
    if (startDate && e.sent_date < startDate) return false;
    if (endDate && e.sent_date > endDate) return false;
    return true;
  });

  // build daily data
  const dailyMap = {};
  // set up containers for unique counts per day
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

  const chartData = Object.values(dailyMap).sort((a,b)=>a.date.localeCompare(b.date));

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
    // only initialize once and only after we have real data rows;
    // when chartData is empty `every` returns true which would hide
    // every series by default.
    if (!initializedRef.current && chartData.length > 0) {
      const newHide = {};
      seriesList.forEach(s => {
        newHide[s.key] = chartData.every(d => d[s.key] === 0);
      });
      setHideSeries(prev => ({ ...prev, ...newHide }));
      initializedRef.current = true;
    }
  }, [chartData]);

  const toggleSeries = key => {
    setHideSeries(prev => ({ ...prev, [key]: !prev[key] }));
  };

  // calculate totals for selected range
  const rangeSent = chartData.reduce((a,d)=>a+d.sent,0);
  const rangeReplies = chartData.reduce((a,d)=>a+d.totalReplies,0);
  const rangeClicks = chartData.reduce((a,d)=>a+d.totalClicks,0);
  const replyRateRange = rangeSent > 0 ? Math.round((rangeReplies / rangeSent) * 100) : 0;
  const clickRateRange = rangeSent > 0 ? Math.round((rangeClicks / rangeSent) * 100) : 0;

  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-semibold mb-4">Campaign Analytics</h1>
      {error && <div className="text-red-600">{error}</div>}
      {/* filters & KPI cards */}
      {serverToday && (
        <div className="text-sm text-gray-500 mb-2">
          Server date: {serverToday.toISOString().slice(0,10)}
        </div>
      )}
      {/* KPI cards based on range */}
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
      <div className="flex flex-col lg:flex-row items-center gap-4 mb-4">
        <div className="flex items-center gap-2">
          <label className="text-sm">
            From <input type="date" value={startDate} onChange={e=>setStartDate(e.target.value)} className="ml-1 border rounded p-1" />
          </label>
          <label className="text-sm">
            To <input type="date" value={endDate} onChange={e=>setEndDate(e.target.value)} className="ml-1 border rounded p-1" />
          </label>
        </div>
        {/* legend now controls series visibility, toggles below chart instead of checkboxes */}
        <div className="text-sm text-gray-500">Toggle series by clicking legend items below the chart.</div>
      </div>

      {/* timeline area chart */}
      <div style={{ width: '100%', height: 260 }}>
        {/* extra bottom space via margin so legend sits below tooltip area */}
        <ResponsiveContainer>
          <ReAreaChart data={chartData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
            <XAxis dataKey="date" />
            <YAxis />
            {/* ensure tooltip renders above legend/tools by using z-index */}
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
              wrapperStyle={{ marginTop: 16 }}
              content={() => (
                <div className="flex flex-wrap justify-center gap-4">
                  {seriesList.map(s => {
                    const inactive = hideSeries[s.key];
                    return (
                      <span
                        key={s.key}
                        onClick={() => toggleSeries(s.key)}
                        className={`flex items-center cursor-pointer select-none ${inactive ? 'opacity-50' : ''}`}
                      >
                        <svg width="10" height="10" className="mr-1">
                          <rect width="10" height="10" fill={s.stroke} />
                        </svg>
                        {s.name}
                      </span>
                    );
                  })}
                </div>
              )}
            />
          </ReAreaChart>
        </ResponsiveContainer>
      </div>
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
                <th className="text-center">Expected</th>
                <th className="text-center">Progress</th>
                <th className="text-center">Replies</th>
                <th className="text-center">Reply Rate</th>
                <th className="text-center">Open Rate</th>
                <th className="text-center">Click Rate</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(c => {
                const stats = c.stats || {};
                const totalLeads = stats.total_leads || 0;
                const sent = stats.emails_sent || 0;
                const seq = stats.sequences || 1;
                const expected = totalLeads * seq;
                const progress = expected > 0 ? Math.round((sent / expected) * 100) : 0;
                const replies = stats.replies || 0;
                // placeholder fields may not exist yet
                const openRate = stats.open_rate ?? null;
                const clickRate = stats.click_rate ?? null;
                const replyRate = sent > 0 ? Math.round((replies / sent) * 100) : 0;
                return (
                  <tr key={c.id} className="hover:bg-gray-50">
                    <td className="py-2">
                      <Link to={`/campaigns/${c.id}`} className="text-teal-500">
                        {c.name}
                      </Link>
                    </td>
                    <td className="py-2 text-center font-mono">{totalLeads}</td>
                    <td className="py-2 text-center font-mono">{sent}</td>
                    <td className="py-2 text-center font-mono">{expected}</td>
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
                    <td className="py-2 text-center">
                      {openRate !== null ? `${Math.round(openRate*100)}%` : '–'}
                    </td>
                    <td className="py-2 text-center">
                      {clickRate !== null ? `${Math.round(clickRate*100)}%` : '–'}
                    </td>
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
