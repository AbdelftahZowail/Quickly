import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { Input } from '../components/ui/Input';
import { useNotify } from '../context/NotificationContext';
import { useConfirm } from '../context/ConfirmContext';
import { useLoading } from '../context/LoadingContext';
import { cn } from '../utils/cn';

const STATUS_OPTIONS = [
  { value: 'all', label: 'All statuses' },
  { value: 'active', label: 'Enrollment: active' },
  { value: 'contacted', label: 'Enrollment: contacted' },
  { value: 'completed', label: 'Enrollment: completed' },
  { value: 'bounced', label: 'Enrollment: bounced' },
  { value: 'unsubscribed', label: 'Enrollment: unsubscribed' },
  { value: 'wrong_person', label: 'Enrollment: wrong_person' },
  { value: 'invalid', label: 'Verify: invalid' },
  { value: 'replied', label: 'Has reply' },
];

const TAB_ALL = 'all';
const TAB_BOUNCED = 'bounced';

function formatEnrolled(campaigns) {
  if (!campaigns?.length) return '—';
  const dates = campaigns.map((c) => new Date(c.enrolled_at).getTime()).filter(Number.isFinite);
  if (!dates.length) return '—';
  const earliest = new Date(Math.min(...dates));
  return earliest.toLocaleDateString();
}

function statusPillClass(status) {
  switch (status) {
    case 'active':
    case 'contacted':
      return 'bg-green-100 text-green-800';
    case 'completed':
      return 'bg-blue-100 text-blue-800';
    case 'unsubscribed':
    case 'wrong_person':
      return 'bg-gray-200 text-gray-700';
    case 'bounced':
    case 'invalid':
      return 'bg-red-100 text-red-800';
    default:
      return 'bg-gray-100 text-gray-700';
  }
}

function leadRowSummary(lead) {
  const camps = lead.campaigns || [];
  if (!camps.length) return '—';
  const bits = camps.map((c) => `${c.campaign_name?.slice(0, 12) || c.campaign_id}:${c.status || 'active'}`);
  return bits.slice(0, 3).join(' · ') + (bits.length > 3 ? '…' : '');
}

function buildLeadsQueryParams({ tab, debouncedSearch, statusFilter }) {
  const params = new URLSearchParams();
  if (debouncedSearch) params.set('q', debouncedSearch);
  if (tab === TAB_BOUNCED) {
    params.set('bad_only', 'true');
  } else if (statusFilter !== 'all') {
    params.set('status', statusFilter);
  }
  return params;
}

export default function Leads() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get('tab') === TAB_BOUNCED ? TAB_BOUNCED : TAB_ALL;

  const setTab = (next) => {
    const p = new URLSearchParams(searchParams);
    if (next === TAB_ALL) p.delete('tab');
    else p.set('tab', next);
    setSearchParams(p);
  };

  const [leads, setLeads] = useState([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selected, setSelected] = useState(() => new Set());
  const [emailDrafts, setEmailDrafts] = useState({});
  const [bulkEnrollmentStatus, setBulkEnrollmentStatus] = useState('active');
  const [debouncedSearch, setDebouncedSearch] = useState('');

  const notify = useNotify();
  const confirm = useConfirm();
  const loading = useLoading();

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 350);
    return () => clearTimeout(t);
  }, [search]);

  const loadLeads = useCallback(async () => {
    loading.start();
    try {
      const params = buildLeadsQueryParams({ tab, debouncedSearch, statusFilter });
      const qs = params.toString();
      const data = await api.get('/leads' + (qs ? `?${qs}` : ''));
      const rows = Array.isArray(data) ? data : [];
      setLeads(rows);
      setSelected(new Set());
      setEmailDrafts((prev) => {
        const next = { ...prev };
        rows.forEach((l) => {
          if (next[l.id] === undefined) next[l.id] = l.email;
        });
        return next;
      });
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Failed to load leads' });
    } finally {
      loading.stop();
    }
  }, [debouncedSearch, statusFilter, tab, loading, notify]);

  useEffect(() => {
    loadLeads();
  }, [loadLeads]);

  const visibleIds = useMemo(() => leads.map((l) => l.id), [leads]);

  const toggleOne = (id) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  };

  const toggleAll = () => {
    if (selected.size === visibleIds.length && visibleIds.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(visibleIds));
    }
  };

  const handleBulkDelete = async () => {
    if (!selected.size) return;
    const ok = await confirm(
      `Permanently delete ${selected.size} lead(s)? This removes enrollments and email history for those leads.`,
    );
    if (!ok) return;
    loading.start();
    try {
      const res = await api.post('/leads/bulk-delete', { lead_ids: [...selected] });
      const n = res.deleted ?? selected.size;
      notify({ type: 'success', message: `Deleted ${n} lead(s).` });
      await loadLeads();
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Delete failed' });
    } finally {
      loading.stop();
    }
  };

  const handleBulkStatus = async () => {
    if (!selected.size) return;
    const ok = await confirm(
      `Set enrollment to “${bulkEnrollmentStatus}” on every campaign for ${selected.size} lead(s)? The send queue will be recalculated.`,
    );
    if (!ok) return;
    loading.start();
    try {
      await api.post('/leads/bulk-status', { lead_ids: [...selected], enrollment_status: bulkEnrollmentStatus });
      notify({ type: 'success', message: 'Status updated.' });
      await loadLeads();
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Update failed' });
    } finally {
      loading.stop();
    }
  };

  const handleBulkReenroll = async () => {
    const ids = [...selected];
    const targets = ids
      .map((id) => leads.find((l) => l.id === id))
      .filter(Boolean)
      .filter(
        (l) =>
          l.email_verification_status === 'invalid' ||
          (l.campaigns || []).some((c) => c.status === 'bounced'),
      );
    if (!targets.length) {
      notify({ type: 'info', message: 'Select bounced or invalid leads to re-enroll with a corrected email.' });
      return;
    }
    const ok = await confirm(
      `Re-enroll ${targets.length} lead(s) using the “New email” values? Status becomes active; verification runs when enabled on your account.`,
    );
    if (!ok) return;
    loading.start();
    try {
      const items = targets
        .map((l) => ({
          lead_id: l.id,
          email: (emailDrafts[l.id] ?? l.email).trim(),
        }))
        .filter((row) => row.email);
      if (!items.length) {
        notify({ type: 'error', message: 'Enter an email for each selected lead.' });
        return;
      }
      const res = await api.post('/leads/bulk-recover', { items, verify_email: true });
      const n = res.recovered ?? 0;
      const errN = res.errors?.length ?? 0;
      notify({
        type: errN ? 'info' : 'success',
        message: `Recovery started for ${n} lead(s).${errN ? ` ${errN} skipped (see API errors).` : ''}`,
      });
      await loadLeads();
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Re-enroll failed' });
    } finally {
      loading.stop();
    }
  };

  const recoverOne = async (lead) => {
    const email = (emailDrafts[lead.id] ?? lead.email).trim();
    if (!email) {
      notify({ type: 'error', message: 'Enter an email address.' });
      return;
    }
    loading.start();
    try {
      await api.post(`/leads/${lead.id}/recover`, { email, verify_email: true });
      notify({ type: 'success', message: 'Lead updated. Verification or scheduling will follow your account settings.' });
      await loadLeads();
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Recovery failed' });
    } finally {
      loading.stop();
    }
  };

  const markActiveOne = async (lead) => {
    loading.start();
    try {
      await api.patch(`/leads/${lead.id}`, { enrollment_status: 'active' });
      notify({ type: 'success', message: 'All enrollments set to active; queue recalculated.' });
      await loadLeads();
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Update failed' });
    } finally {
      loading.stop();
    }
  };

  const exportCsv = async () => {
    const params = buildLeadsQueryParams({ tab, debouncedSearch, statusFilter });
    const qs = params.toString();
    loading.start();
    try {
      const res = await api.download('/leads/export' + (qs ? `?${qs}` : ''));
      const blob = await res.blob();
      let filename = tab === TAB_BOUNCED ? 'leads-bounced-invalid.csv' : 'leads-export.csv';
      const cd = res.headers.get('content-disposition');
      if (cd) {
        const m = cd.match(/filename="?([^";\n]+)"?/i);
        if (m?.[1]) filename = m[1].trim();
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      notify({ type: 'success', message: 'CSV downloaded.' });
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Export failed' });
    } finally {
      loading.stop();
    }
  };

  const importRecoverCsv = async (file) => {
    const ok = await confirm(
      'Recover leads from this CSV? The server reads id and email columns (header row or first two columns).',
    );
    if (!ok) return;
    loading.start();
    try {
      const res = await api.upload('/leads/recover-import?verify_emails=true', file);
      const n = res.recovered ?? 0;
      const errN = res.errors?.length ?? 0;
      notify({
        type: errN ? 'info' : 'success',
        message: `Recovered ${n} lead(s).${errN ? ` ${errN} row(s) skipped.` : ''}`,
      });
      await loadLeads();
    } catch (e) {
      notify({ type: 'error', message: e.message || 'Import failed — check CSV format and IDs.' });
      await loadLeads();
    } finally {
      loading.stop();
    }
  };

  return (
    <div className="p-8 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold mb-1">Leads</h1>
          <p className="text-sm text-gray-500 max-w-2xl">
            Search and manage contacts across campaigns. Use{' '}
            <strong className="font-medium text-gray-700">Bounced &amp; Invalid</strong>{' '}
            to fix addresses and put people back into the send flow (with verification when enabled).
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={exportCsv} disabled={!leads.length}>
            Export CSV
          </Button>
          <label className="inline-flex">
            <input
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = '';
                if (f) importRecoverCsv(f);
              }}
            />
            <Button as="span" variant="outline" size="sm" className="cursor-pointer">
              Import recovery CSV
            </Button>
          </label>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setTab(TAB_ALL)}
          className={cn(
            'rounded-full border text-xs font-medium px-3 py-1.5 transition-colors',
            tab === TAB_ALL
              ? 'bg-teal-500 text-white border-teal-500'
              : 'bg-white text-gray-600 border-gray-300 hover:border-teal-300 hover:bg-teal-50',
          )}
        >
          All leads
        </button>
        <button
          type="button"
          onClick={() => setTab(TAB_BOUNCED)}
          className={cn(
            'rounded-full border text-xs font-medium px-3 py-1.5 transition-colors',
            tab === TAB_BOUNCED
              ? 'bg-teal-500 text-white border-teal-500'
              : 'bg-white text-gray-600 border-gray-300 hover:border-teal-300 hover:bg-teal-50',
          )}
        >
          Bounced &amp; Invalid
        </button>
      </div>

      {tab === TAB_ALL && (
        <div className="flex flex-wrap gap-2 items-center">
          <span className="text-sm text-gray-500">Status:</span>
          {STATUS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setStatusFilter(o.value)}
              className={cn(
                'rounded-full border text-xs font-medium px-3 py-1',
                statusFilter === o.value
                  ? 'bg-teal-500 text-white border-teal-500'
                  : 'bg-white text-gray-600 border-gray-300 hover:border-teal-300 hover:bg-teal-50',
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}

      {tab === TAB_BOUNCED && (
        <Card className="p-4 bg-amber-50/80 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800">
          <p className="text-sm text-gray-800 dark:text-gray-200">
            Edit the email for each row, then <strong className="font-medium">Save &amp; recover</strong>. That updates the
            lead record (all campaigns), sets status to active, and either queues verification or reschedules immediately.
            Export bad addresses to clean them elsewhere, then import a CSV with columns <code className="font-mono text-xs">id,email</code>.
          </p>
        </Card>
      )}

      <div className="flex flex-wrap gap-4 items-end">
        <div className="flex-1 min-w-[200px] max-w-md">
          <Input
            label="Search"
            placeholder="Email or name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {selected.size > 0 && (
        <Card className="p-4 flex flex-wrap gap-3 items-center">
          <span className="text-sm font-medium text-gray-700">{selected.size} selected</span>
          <Button type="button" variant="destructive" size="sm" onClick={handleBulkDelete}>
            Delete
          </Button>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="rounded-md border-gray-300 text-sm shadow-sm focus:ring-2 focus:ring-teal-300"
              value={bulkEnrollmentStatus}
              onChange={(e) => setBulkEnrollmentStatus(e.target.value)}
            >
              <option value="active">active</option>
              <option value="contacted">contacted</option>
              <option value="completed">completed</option>
              <option value="bounced">bounced</option>
              <option value="unsubscribed">unsubscribed</option>
              <option value="wrong_person">wrong_person</option>
            </select>
            <Button type="button" variant="outline" size="sm" onClick={handleBulkStatus}>
              Apply enrollment
            </Button>
          </div>
          {(tab === TAB_BOUNCED || tab === TAB_ALL) && (
            <Button type="button" variant="default" size="sm" onClick={handleBulkReenroll}>
              Re-enroll (recover)
            </Button>
          )}
        </Card>
      )}

      <Card className="overflow-x-auto max-w-full min-w-0">
        <div className="px-4 py-2 text-sm text-gray-500 border-b border-gray-100">
          {leads.length} lead{leads.length !== 1 ? 's' : ''}
          {tab === TAB_BOUNCED ? ' (bounced or invalid)' : ''}
        </div>
        <table className="min-w-max w-full table-auto border-collapse text-sm">
          <thead>
            <tr className="text-left text-gray-600 border-b">
              <th className="p-2 w-10">
                <input
                  type="checkbox"
                  className="rounded"
                  checked={visibleIds.length > 0 && selected.size === visibleIds.length}
                  onChange={toggleAll}
                  aria-label="Select all"
                />
              </th>
              <th className="p-2">Email</th>
              <th className="p-2">Name</th>
              <th className="p-2">Verify / enrollments</th>
              <th className="p-2">Campaigns</th>
              <th className="p-2">Enrolled</th>
              {tab === TAB_BOUNCED && <th className="p-2 min-w-[200px]">New email</th>}
              {tab === TAB_BOUNCED && <th className="p-2 w-44">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {leads.length === 0 ? (
              <tr>
                <td colSpan={tab === TAB_BOUNCED ? 8 : 6} className="p-8 text-center text-gray-500">
                  No leads match this view.
                </td>
              </tr>
            ) : (
              leads.map((l) => (
                <tr key={l.id} className="even:bg-gray-50 dark:even:bg-gray-800/40 border-b border-gray-100">
                  <td className="p-2 align-top">
                    <input
                      type="checkbox"
                      className="rounded"
                      checked={selected.has(l.id)}
                      onChange={() => toggleOne(l.id)}
                      aria-label={`Select ${l.email}`}
                    />
                  </td>
                  <td className="p-2 align-top font-mono text-xs">
                    <Link to={`/leads/${l.id}`} className="text-teal-600 hover:underline">
                      {l.email}
                    </Link>
                  </td>
                  <td className="p-2 align-top">{l.name || '—'}</td>
                  <td className="p-2 align-top">
                    <div className="flex flex-wrap gap-1">
                      {l.email_verification_status && (
                        <span
                          className={cn(
                            'rounded-full px-2 py-0.5 text-xs font-medium',
                            statusPillClass(l.email_verification_status),
                          )}
                        >
                          verify: {l.email_verification_status}
                        </span>
                      )}
                      {(l.campaigns || []).slice(0, 3).map((c) => (
                        <span
                          key={c.campaign_id}
                          className={cn('rounded-full px-2 py-0.5 text-xs font-medium', statusPillClass(c.status))}
                          title={`${c.campaign_name}: ${c.status}${c.interest ? ` · ${c.interest}` : ''}`}
                        >
                          {c.status}
                        </span>
                      ))}
                      {(l.campaigns || []).length > 3 && (
                        <span className="text-xs text-gray-500">+{l.campaigns.length - 3}</span>
                      )}
                      {!(l.campaigns || []).length && !l.email_verification_status && (
                        <span className="text-gray-400 text-xs">—</span>
                      )}
                    </div>
                  </td>
                  <td className="p-2 align-top">
                    <div className="flex flex-wrap gap-1 max-w-xs">
                      {(l.campaigns || []).map((c) => (
                        <Link
                          key={c.campaign_id}
                          to={`/campaigns/${c.campaign_id}#leads`}
                          className="bg-gray-200 dark:bg-gray-600 rounded-full px-2 py-0.5 text-xs hover:bg-gray-300 dark:hover:bg-gray-500"
                        >
                          {c.campaign_name}
                        </Link>
                      ))}
                      {!(l.campaigns || []).length && <span className="text-gray-400">—</span>}
                    </div>
                  </td>
                  <td className="p-2 align-top text-gray-600">{formatEnrolled(l.campaigns)}</td>
                  {tab === TAB_BOUNCED && (
                    <td className="p-2 align-top">
                      <input
                        type="email"
                        className="w-full rounded-md border-gray-300 text-sm shadow-sm focus:ring-2 focus:ring-teal-300"
                        value={emailDrafts[l.id] ?? l.email}
                        onChange={(e) =>
                          setEmailDrafts((prev) => ({
                            ...prev,
                            [l.id]: e.target.value,
                          }))
                        }
                      />
                    </td>
                  )}
                  {tab === TAB_BOUNCED && (
                    <td className="p-2 align-top space-y-1">
                      <Button type="button" size="sm" variant="default" onClick={() => recoverOne(l)}>
                        Save &amp; recover
                      </Button>
                      <Button type="button" size="sm" variant="outline" onClick={() => markActiveOne(l)}>
                        Active only
                      </Button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
