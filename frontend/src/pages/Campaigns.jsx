import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';

export default function Campaigns() {
  const [campaigns, setCampaigns] = useState([]);
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
    <div className="mb-4 p-4 bg-teal-50 border border-teal-200 rounded text-teal-700 text-sm flex flex-wrap items-center gap-2">
      <strong className="mr-2">Priority strategy active.</strong>
      Drag rows to set the order — <strong>#1</strong> gets inbox capacity first.
      <span className="ml-auto text-xs">
        Strategy: <Link to="/settings" className="underline">change in Settings</Link>
      </span>
    </div>
  ) : null;

  return (
    <div className="p-8 space-y-6">
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
                const sequences = stats.sequences || 1;
                const expected = totalLeads * sequences;
                const percent = expected > 0 ? Math.round((emailsSent / expected) * 100) : 0;
                const replies = stats.replies || 0;
                const replyRate = emailsSent > 0 ? Math.round((replies / emailsSent) * 100) : 0;

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
                      {c.paused ? (
                        <span className="text-gray-500">
                          {c.name}{' '}
                          <span className="inline-block text-red-600 bg-red-100 px-1 py-0.5 text-xs font-bold rounded">PAUSED</span>
                        </span>
                      ) : (
                        <Link to={`/campaigns/${c.id}`} className="text-teal-500">
                          {c.name}
                        </Link>
                      )}
                    </td>
                    <td className="py-2">
                      {c.paused ? (
                        <span className="text-red-600 font-bold">Paused</span>
                      ) : (
                        <span className="text-green-600 font-bold">Active</span>
                      )}
                    </td>
                    <td className="py-2 text-center">
                      <div className="w-32 inline-block bg-gray-200 rounded-full h-2 overflow-hidden">
                        <div
                          className="bg-teal-500 h-2"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                      <div className="text-xs mt-1">
                        {emailsSent} / {expected || 0} ({percent}%)
                      </div>
                    </td>
                    <td className="py-2 text-center">
                      {replies} ({replyRate}%)
                    </td>
                    <td className="py-2 gap-2">
                      <Button as={Link} to={`/campaigns/${c.id}`} variant="outline" size="sm">View</Button>
                      <Button variant="outline" size="sm" onClick={() => togglePause(c.id, c.paused, c.name)}>
                        {c.paused ? 'Resume' : 'Pause'}
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => duplicateCampaign(c.id, c.name)}>Duplicate</Button>
                      <Button variant="danger" size="sm" onClick={() => deleteCampaign(c.id, c.name)}>Delete</Button>
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
        <Button as={Link} to="/campaigns/add" variant="default">Create campaign</Button>
      </div>
    </div>
  );
}
