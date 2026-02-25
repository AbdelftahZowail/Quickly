import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import Button from '../components/ui/Button';

export default function Analytics() {
  const [campaigns, setCampaigns] = useState([]);
  // current dropdown choice and list of selected campaign ids
  const [currentChoice, setCurrentChoice] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [error, setError] = useState(null);

  // simple area chart for an array of percentage values
  function AreaChart({ values, width = 600, height = 120 }) {
    if (!values || values.length === 0) return null;
    const step = values.length > 1 ? width / (values.length - 1) : width;
    const pts = values.map((v, i) => {
      const x = i * step;
      const y = height - (v / 100) * height;
      return `${x},${y}`;
    });
    const path = `M${pts.join(' L ')} L${width},${height} L0,${height} Z`;
    return (
      <svg width={width} height={height} className="mb-4">
        <path d={path} fill="rgba(34, 211, 238, 0.3)" stroke="#22d3ee" strokeWidth="2" />
      </svg>
    );
  }

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get('/campaigns');
        setCampaigns(data);
      } catch (e) {
        setError('Failed to load analytics');
      }
    })();
  }, []);

  const filtered = selectedIds.length
    ? campaigns.filter(c => selectedIds.includes(String(c.id)))
    : campaigns;


  // summary metrics for the current filtered set
  const summary = filtered.reduce(
    (acc, c) => {
      const stats = c.stats || {};
      acc.leads += stats.total_leads || 0;
      acc.sent += stats.emails_sent || 0;
      acc.seq += stats.sequences || 1;
      acc.replies += stats.replies || 0;
      return acc;
    },
    { leads: 0, sent: 0, seq: 0, replies: 0 }
  );
  const expectedAll = summary.leads * summary.seq;
  const progressAll = expectedAll > 0 ? Math.round((summary.sent / expectedAll) * 100) : 0;
  const replyRateAll = summary.sent > 0 ? Math.round((summary.replies / summary.sent) * 100) : 0;

  function Bar({ percent, label }) {
    return (
      <div className="mb-4">
        <div className="text-sm mb-1">{label}: {percent}%</div>
        <div className="bg-gray-200 h-3 rounded overflow-hidden">
          <div className="bg-teal-500 h-3" style={{ width: `${percent}%` }} />
        </div>
      </div>
    );
  }

  // area chart values (progress percentages for each campaign in filtered set)
  const progressValues = filtered.map(c => {
    const stats = c.stats || {};
    const totalLeads = stats.total_leads || 0;
    const sent = stats.emails_sent || 0;
    const seq = stats.sequences || 1;
    const expected = totalLeads * seq;
    return expected > 0 ? Math.round((sent / expected) * 100) : 0;
  });

  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-semibold mb-4">Campaign Analytics</h1>
      {error && <div className="text-red-600">{error}</div>}
      <AreaChart values={progressValues} width={600} height={120} />
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
              variant="secondary"
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
        <div className="flex-1 space-y-2">
          <Bar percent={progressAll} label="Progress" />
          <Bar percent={replyRateAll} label="Reply rate" />
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="bg-white p-4 rounded shadow">
          {campaigns.length === 0 ? (
            'No campaigns to analyze.'
          ) : (
            'No matching campaigns.'
          )}
        </div>
      ) : (
        <div className="card overflow-auto">
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
        </div>
      )}
    </div>
  );
}
