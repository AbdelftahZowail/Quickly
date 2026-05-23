import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api, apiCache } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';

export default function Campaigns() {
  const [campaigns, setCampaigns] = useState(() => apiCache.get('/campaigns') || []);
  const [error, setError] = useState(null);

  // scheduling strategy from server ("priority" or other)
  const [strategy, setStrategy] = useState('priority');
  const [orderChanged, setOrderChanged] = useState(false);
  const dragSrcIdx = useRef(null);
  const confirm = useConfirm();
  const notify = useNotify();

  const load = useCallback(async () => {
    try {
      const [camp, strat] = await Promise.all([
        api.get('/campaigns'),
        api.get('/settings/scheduling-strategy').catch(() => ({ scheduling_strategy: 'priority' })),
      ]);
      setCampaigns(camp);
      setStrategy(strat.scheduling_strategy || 'priority');
      setOrderChanged(false);
    } catch (e) {
      setError('Failed to load campaigns');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // drag and drop helpers (only used when strategy === 'priority')
  const onDragStart = (e, idx) => {
    dragSrcIdx.current = idx;
    e.currentTarget.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  };
  const onDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    e.currentTarget.classList.add('drag-over');
  };
  const onDragLeave = (e) => {
    e.currentTarget.classList.remove('drag-over');
  };
  const onDrop = (e, idx) => {
    e.preventDefault();
    e.currentTarget.classList.remove('drag-over');
    const src = dragSrcIdx.current;
    if (src === null || src === idx) return;
    const newList = [...campaigns];
    const [moved] = newList.splice(src, 1);
    newList.splice(idx, 0, moved);
    setCampaigns(newList);
    setOrderChanged(true);
  };
  const onDragEnd = (e) => {
    e.currentTarget.classList.remove('dragging');
    dragSrcIdx.current = null;
  };

  const saveOrder = async () => {
    if (!orderChanged) return;
    try {
      await api.post('/campaigns/reorder', { campaign_ids: campaigns.map(c => c.id) });
      setOrderChanged(false);
      notify({ type: 'success', message: 'Order saved' });
    } catch (e) {
      notify({ type: 'error', message: 'Error saving order' });
    }
  };

  const togglePause = async (id, paused, name) => {
    const ok = await confirm(`${paused ? 'Resume' : 'Pause'} campaign "${name}"?`);
    if (!ok) return;
    await api.patch(`/campaigns/${id}`, { paused: !paused });
    load();
  };
  const deleteCampaign = async (id, name) => {
    const ok = await confirm(`Delete campaign "${name}"? This cannot be undone.`);
    if (!ok) return;
    await api.del(`/campaigns/${id}`);
    load();
  };
  const duplicateCampaign = async (id, name) => {
    const ok = await confirm(`Duplicate campaign "${name}"?`);
    if (!ok) return;
    const c = await api.post(`/campaigns/${id}/duplicate`);
    notify({ type: 'success', message: 'Campaign duplicated: ' + c.name });
    load();
  };

  if (error) {
    return <div className="p-8 text-red-600">{error}</div>;
  }

  const isPriority = strategy === 'priority';
  const banner = isPriority ? (
    <div className="mb-4 flex items-center gap-2 text-xs text-gray-500">
      <span className="font-medium text-gray-700">Priority</span>
      <span>·</span>
      <span>Drag rows to reorder</span>
      <span>·</span>
      <Link to="/settings#general" className="underline text-teal-500">Change strategy</Link>
    </div>
  ) : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold mb-4">Campaigns</h1>
        <Button as={Link} to="/analytics" variant="outline" size="sm">
          Analytics
        </Button>
      </div>

      {banner}

      {campaigns.length === 0 && (
        <Card>No campaigns yet. <Link className="text-teal-500" to="/campaigns/add">Create campaign</Link>.</Card>
      )}

      {campaigns.length > 0 && (
        <>
          <Card className="overflow-auto">
            <table className="w-full table-auto border-collapse">
            <thead>
              <tr>
                {isPriority && <><th className="w-8"></th><th className="w-10 text-gray-500 text-xs">#</th></>}
                <th>Name</th>
                <th>Status</th>
                <th className="text-center">Progress</th>
                <th className="text-center">Replies</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c, idx) => {
                // compute simple progress metrics using the stats object supplied by
                // the backend.  we assume stats always exists because the schema
                // defaults to zero-values.
                const stats = c.stats || {};
                const totalLeads = stats.total_leads || 0;
                const emailsSent = stats.emails_sent || 0;
                const scheduled = stats.scheduled || 0; // newly added field
                // instead of assuming every enrolled lead will receive every
                // sequence, compute progress as sent / (sent + scheduled).  slots
                // are removed when a lead replies, so replied leads immediately
                // appear "complete".
                const denom = emailsSent + scheduled;
                const percent = denom > 0 ? Math.round((emailsSent / denom) * 100) : 0;
                const replies = stats.replies || 0;
                const replyRate = emailsSent > 0 ? Math.round((replies / emailsSent) * 100) : 0;

                // Determine campaign state for display
                // When paused: scheduled slots were cleared, so denom == emailsSent
                // which makes percent = 100%, but it's misleading.
                const isCompleted = !c.paused && scheduled === 0 && emailsSent > 0;
                const isPaused = !!c.paused;

                // For paused campaigns, compute progress against total leads
                // to give a better sense of how far we got
                const pausedPercent = isPaused && totalLeads > 0
                  ? Math.round((emailsSent / totalLeads) * 100)
                  : 0;

                // Build a short reason line for paused / completed
                const reasonParts = [];
                if (replies > 0) reasonParts.push(`${replies} replied`);
                const bounced = stats.bounced || 0;
                if (bounced > 0) reasonParts.push(`${bounced} bounced`);
                const unsubscribed = stats.unsubscribed || 0;
                if (unsubscribed > 0) reasonParts.push(`${unsubscribed} unsub`);
                const needsCustom = stats.needs_custom_email || 0;
                if (needsCustom > 0) reasonParts.push(`${needsCustom} needs custom`);

                // Status display
                let statusLabel, statusClass;
                if (isPaused) {
                  statusLabel = 'Paused';
                  statusClass = 'text-amber-600 font-bold';
                } else if (isCompleted) {
                  statusLabel = 'Completed';
                  statusClass = 'text-blue-600 font-bold';
                } else if (needsCustom > 0 && totalLeads === needsCustom) {
                  statusLabel = 'Needs Writing';
                  statusClass = 'text-purple-600 font-bold';
                } else if (totalLeads === 0) {
                  statusLabel = 'Draft';
                  statusClass = 'text-gray-400 font-bold';
                } else {
                  statusLabel = 'Active';
                  statusClass = 'text-green-600 font-bold';
                }

                // Progress bar colour
                const barColor = isPaused ? 'bg-amber-400' : isCompleted ? 'bg-blue-500' : 'bg-teal-500';

                return (
                  <tr
                    key={c.id}
                    draggable={isPriority}
                    onDragStart={e => isPriority && onDragStart(e, idx)}
                    onDragOver={e => isPriority && onDragOver(e)}
                    onDragLeave={e => isPriority && onDragLeave(e)}
                    onDrop={e => isPriority && onDrop(e, idx)}
                    onDragEnd={e => isPriority && onDragEnd(e)}
                  >
                    {isPriority && (
                      <>
                        <td className="py-2">
                          <span className="cursor-grab text-gray-400">&#8942;&#8942;</span>
                        </td>
                        <td className="py-2 text-gray-500 text-xs">{idx + 1}</td>
                      </>
                    )}
                    <td className="py-2">
                      {isPaused ? (
                        <span className="text-gray-500">
                          {c.name}{' '}
                          <span className="inline-block text-amber-700 bg-amber-100 px-1 py-0.5 text-xs font-bold rounded">PAUSED</span>
                        </span>
                      ) : isCompleted ? (
                        <span>
                          <Link to={`/campaigns/${c.id}`} className="text-blue-500">{c.name}</Link>{' '}
                          <span className="inline-block text-blue-600 bg-blue-100 px-1 py-0.5 text-xs font-bold rounded">DONE</span>
                        </span>
                      ) : (
                        <Link to={`/campaigns/${c.id}`} className="text-teal-500">
                          {c.name}
                        </Link>
                      )}
                    </td>
                    <td className="py-2">
                      <span className={statusClass}>{statusLabel}</span>
                    </td>
                    <td className="py-2 text-center">
                      <div className="w-32 inline-block bg-gray-200 rounded-full h-2 overflow-hidden">
                        <div
                          className={`${barColor} h-2`}
                          style={{ width: `${isPaused ? pausedPercent : percent}%` }}
                        />
                      </div>
                      <div className="text-xs mt-1">
                        {isPaused ? (
                          <span className="text-amber-700">{emailsSent} sent of {totalLeads} lead{totalLeads !== 1 ? 's' : ''}</span>
                        ) : isCompleted ? (
                          <span className="text-blue-600">{emailsSent} sent — complete</span>
                        ) : (
                          <span>{emailsSent} / {denom} ({percent}%)</span>
                        )}
                      </div>
                      {reasonParts.length > 0 && (
                        <div className="text-[10px] text-gray-400 mt-0.5">{reasonParts.join(' · ')}</div>
                      )}
                    </td>
                    <td className="py-2 text-center">
                      {replies} ({replyRate}%)
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-2">
                        <Button as={Link} to={`/campaigns/${c.id}`} variant="outline" size="sm">View</Button>
                        <Button variant="outline" size="sm" onClick={() => togglePause(c.id, c.paused, c.name)}>
                          {c.paused ? 'Resume' : 'Pause'}
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => duplicateCampaign(c.id, c.name)}>Duplicate</Button>
                        <Button variant="danger" size="sm" onClick={() => deleteCampaign(c.id, c.name)}>Delete</Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </Card>

          {isPriority && orderChanged && (
            <div className="mt-4 flex items-center gap-4">
              <Button
                variant="default"
                size="md"
                onClick={saveOrder}
              >
                Save priority order
              </Button>
              <span className="text-green-600 text-sm">Unsaved changes</span>
            </div>
          )}
        </>
      )}

      <div className="mt-4">
        <Button as={Link} to="/campaigns/add" variant="default" className="no-underline hover:no-underline">Create campaign</Button>
      </div>
    </div>
  );
}
