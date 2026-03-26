import { useEffect, useState, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { useNotify } from '../context/NotificationContext';
import { useLoading } from '../context/LoadingContext';

function formatDt(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function LeadDetail() {
  const { id } = useParams();
  const notify = useNotify();
  const loading = useLoading();
  const [lead, setLead] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    loading.start();
    setError(null);
    try {
      const l = await api.get(`/leads/${id}`);
      setLead(l);
    } catch (e) {
      setError(e.message || 'Failed to load lead');
      notify({ type: 'error', message: 'Could not load lead' });
    } finally {
      loading.stop();
    }
  }, [id, loading, notify]);

  useEffect(() => {
    load();
  }, [load]);

  if (error && !lead) {
    return (
      <div className="p-8 space-y-6">
        <h1 className="text-2xl font-semibold mb-4">Lead</h1>
        <p className="text-red-600">{error}</p>
        <Button as={Link} to="/leads" variant="outline">Back to Leads</Button>
      </div>
    );
  }

  if (!lead) {
    return (
      <div className="p-8">
        <p className="text-gray-500">Loading…</p>
      </div>
    );
  }

  const customEntries = Object.entries(lead.custom_data || {});

  return (
    <div className="p-8 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="text-sm text-gray-500 mb-1">
            <Link to="/leads" className="text-teal-500 hover:underline">Leads</Link>
            <span className="mx-2">/</span>
            <span className="font-mono text-xs">#{lead.id}</span>
          </div>
          <h1 className="text-2xl font-semibold">{lead.name || lead.email}</h1>
          <p className="text-gray-600 mt-1 font-mono text-sm">{lead.email}</p>
        </div>
        <Button as={Link} to="/unibox" variant="outline" size="sm">
          Open Unibox
        </Button>
      </div>

      <div className="flex flex-wrap gap-2 text-sm">
        {lead.email_verification_status && (
          <span className="bg-gray-200 dark:bg-gray-600 text-gray-800 dark:text-gray-100 rounded-full px-2 py-0.5">
            verify: {lead.email_verification_status}
          </span>
        )}
        {lead.provider && (
          <span className="bg-slate-100 text-slate-700 rounded-full px-2 py-0.5 text-xs">
            {lead.provider}
          </span>
        )}
      </div>

      <Card className="p-4">
        <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">
          Campaigns
        </h2>
        {lead.campaigns?.length ? (
          <ul className="space-y-3">
            {lead.campaigns.map((c) => (
              <li
                key={c.campaign_id}
                className="flex flex-col sm:flex-row sm:flex-wrap sm:items-baseline sm:justify-between gap-2 border-b border-gray-100 dark:border-gray-700 pb-2 last:border-0"
              >
                <div>
                  <Link
                    to={`/campaigns/${c.campaign_id}#leads`}
                    className="text-teal-600 hover:underline font-medium"
                  >
                    {c.campaign_name}
                  </Link>
                  <span className="text-xs text-gray-400 font-mono ml-2">{c.campaign_public_id}</span>
                </div>
                <div className="text-xs text-gray-600 flex flex-wrap gap-2">
                  <span className="rounded-full bg-gray-100 px-2 py-0.5">status: {c.status || 'active'}</span>
                  {c.interest && (
                    <span className="rounded-full bg-violet-50 text-violet-800 px-2 py-0.5">
                      interest: {c.interest}
                    </span>
                  )}
                  <span className="rounded-full bg-gray-50 px-2 py-0.5">
                    opened {c.opened ? 'yes' : 'no'} · clicked {c.clicked ? 'yes' : 'no'} · replied{' '}
                    {c.replied ? 'yes' : 'no'}
                  </span>
                  {c.sending_paused && (
                    <span className="rounded-full bg-amber-100 text-amber-900 px-2 py-0.5">paused</span>
                  )}
                </div>
                <span className="text-sm text-gray-500">enrolled {formatDt(c.enrolled_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-gray-500 text-sm">Not enrolled in any campaign.</p>
        )}
      </Card>

      <Card className="p-4">
        <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">
          Custom data
        </h2>
        {customEntries.length ? (
          <table className="w-full table-auto border-collapse text-sm">
            <tbody>
              {customEntries.map(([k, v]) => (
                <tr key={k} className="even:bg-gray-50 dark:even:bg-gray-800/50">
                  <td className="border border-gray-200 dark:border-gray-600 px-2 py-1 font-medium text-gray-700 dark:text-gray-300">
                    {k}
                  </td>
                  <td className="border border-gray-200 dark:border-gray-600 px-2 py-1 font-mono text-xs break-all">
                    {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-gray-500 text-sm">No custom fields.</p>
        )}
      </Card>

      <Card className="p-4 overflow-auto">
        <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">
          Interactions
        </h2>
        <p className="text-xs text-gray-500 mb-3">
          Outbound sends and inbound messages we can associate (mirrored mail + reply markers). Full threads: Unibox.
        </p>
        {lead.interactions?.length ? (
          <ul className="space-y-3 text-sm">
            {lead.interactions.map((row, i) => (
              <li
                key={`${row.at}-${row.kind}-${i}`}
                className={`border-l-2 pl-3 ${
                  row.direction === 'outbound' ? 'border-teal-400' : 'border-violet-400'
                }`}
              >
                <div className="font-medium">
                  {row.direction === 'outbound' ? 'Sent' : 'Received'}{' '}
                  {row.kind && row.kind !== 'sent' ? `· ${row.kind.replace(/_/g, ' ')}` : ''}
                </div>
                <div className="text-gray-500 text-xs mt-0.5">
                  {row.campaign_name && <span>{row.campaign_name} · </span>}
                  {row.subject && <span className="font-medium text-gray-600">{row.subject} · </span>}
                  {formatDt(row.at)}
                </div>
                {row.snippet && (
                  <p className="text-xs text-gray-600 mt-1 line-clamp-3">{row.snippet}</p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-gray-500 text-sm">No interaction history yet.</p>
        )}
      </Card>
    </div>
  );
}
