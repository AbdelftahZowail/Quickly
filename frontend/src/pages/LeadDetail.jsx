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
  const [history, setHistory] = useState([]);
  const [replies, setReplies] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    loading.start();
    setError(null);
    try {
      const [l, h, r] = await Promise.all([
        api.get(`/leads/${id}`),
        api.get(`/leads/${id}/history`),
        api.get(`/leads/${id}/replies`),
      ]);
      setLead(l);
      setHistory(h);
      setReplies(r);
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
        <span className={`rounded-full px-2 py-0.5 font-medium ${statusPillClass(lead.status)}`}>
          {lead.status}
        </span>
        {lead.email_verification_status && (
          <span className="bg-gray-200 dark:bg-gray-600 text-gray-800 dark:text-gray-100 rounded-full px-2 py-0.5">
            verify: {lead.email_verification_status}
          </span>
        )}
      </div>

      <Card className="p-4">
        <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">
          Campaigns
        </h2>
        {lead.campaigns?.length ? (
          <ul className="space-y-2">
            {lead.campaigns.map((c) => (
              <li key={c.campaign_id} className="flex flex-wrap items-baseline justify-between gap-2">
                <Link
                  to={`/campaigns/${c.campaign_id}#leads`}
                  className="text-teal-600 hover:underline font-medium"
                >
                  {c.campaign_name}
                </Link>
                <span className="text-sm text-gray-500">
                  enrolled {formatDt(c.enrolled_at)}
                </span>
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="p-4 overflow-auto">
          <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">
            Email history
          </h2>
          {history.length ? (
            <ul className="space-y-3 text-sm">
              {history.map((row, i) => (
                <li key={`${row.sent_at}-${i}`} className="border-l-2 border-teal-400 pl-3">
                  <div className="font-medium">{row.subject || '(no subject)'}</div>
                  <div className="text-gray-500 text-xs mt-0.5">
                    {row.campaign_name} · step {row.sequence_index + 1} · {formatDt(row.sent_at)}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500 text-sm">No sent emails logged yet.</p>
          )}
        </Card>

        <Card className="p-4 overflow-auto">
          <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">
            Reply markers
          </h2>
          <p className="text-xs text-gray-500 mb-3">
            When a lead replies, we record a marker per campaign for stop-on-reply. Full threads live in Unibox.
          </p>
          {replies.length ? (
            <ul className="space-y-2 text-sm">
              {replies.map((r) => (
                <li key={`${r.campaign_id}-${r.replied_at}`} className="flex justify-between gap-2">
                  <Link
                    to={`/campaigns/${r.campaign_id}#leads`}
                    className="text-teal-600 hover:underline"
                  >
                    {r.campaign_name}
                  </Link>
                  <span className="text-gray-500 shrink-0">{formatDt(r.replied_at)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500 text-sm">No reply markers recorded.</p>
          )}
        </Card>
      </div>
    </div>
  );
}

function statusPillClass(status) {
  switch (status) {
    case 'active':
      return 'bg-green-100 text-green-800';
    case 'replied':
      return 'bg-blue-100 text-blue-800';
    case 'unsubscribed':
      return 'bg-gray-200 text-gray-700';
    case 'bounced':
    case 'invalid':
      return 'bg-red-100 text-red-800';
    default:
      return 'bg-gray-100 text-gray-700';
  }
}
