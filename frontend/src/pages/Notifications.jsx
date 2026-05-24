import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useNotifications } from '../context/NotificationsContext';
import { useNotify } from '../context/NotificationContext';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import {
  RiMailOpenLine,
  RiMailSendLine,
  RiMailCheckLine,
  RiMailCloseLine,
  RiUserReceivedLine,
  RiUserUnfollowLine,
  RiErrorWarningLine,
  RiTimerLine,
  RiSpeedLine,
  RiKey2Line,
  RiDeleteBinLine,
  RiCheckDoubleLine,
  RiSparklingLine,
  RiEyeLine,
  RiCursorLine,
  RiSearchLine,
} from 'react-icons/ri';

const EVENT_ICONS = {
  'email.sent': <RiMailSendLine size={20} />,
  'email.opened': <RiEyeLine size={20} />,
  'email.clicked': <RiCursorLine size={20} />,
  'email.bounced': <RiMailCloseLine size={20} />,
  'lead.replied': <RiMailCheckLine size={20} />,
  'lead.unsubscribed': <RiUserUnfollowLine size={20} />,
  'lead.status_changed': <RiUserReceivedLine size={20} />,
  'lead.interested': <RiSparklingLine size={20} />,
  'lead.not_interested': <RiUserUnfollowLine size={20} />,
  'lead.out_of_office': <RiTimerLine size={20} />,
  'lead.wrong_person': <RiUserUnfollowLine size={20} />,
  'lead.auto_reply': <RiTimerLine size={20} />,
  'feature.error': <RiErrorWarningLine size={20} />,
  'daily_limit': <RiSpeedLine size={20} />,
  'rate_limit': <RiSpeedLine size={20} />,
  'token_expired': <RiKey2Line size={20} />,
};

const EVENT_LABELS = {
  'email.sent': 'Email Sent',
  'email.opened': 'Email Opened',
  'email.clicked': 'Link Clicked',
  'email.bounced': 'Email Bounced',
  'lead.replied': 'Lead Replied',
  'lead.unsubscribed': 'Lead Unsubscribed',
  'lead.status_changed': 'Status Changed',
  'lead.interested': 'Lead Interested (AI)',
  'lead.not_interested': 'Lead Not Interested (AI)',
  'lead.out_of_office': 'Out of Office (AI)',
  'lead.wrong_person': 'Wrong Person (AI)',
  'lead.auto_reply': 'Auto Reply (AI)',
  'feature.error': 'Feature Error',
  'daily_limit': 'Daily Limit Hit',
  'rate_limit': 'Rate Limit',
  'token_expired': 'Token Expired',
};

const EVENT_CATEGORIES = {
  'email': ['email.sent', 'email.opened', 'email.clicked', 'email.bounced'],
  'lead': ['lead.replied', 'lead.unsubscribed', 'lead.status_changed', 'lead.interested', 'lead.not_interested', 'lead.out_of_office', 'lead.wrong_person', 'lead.auto_reply'],
  'system': ['daily_limit', 'rate_limit', 'token_expired', 'feature.error'],
};

function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function NotificationItem({ n, onRead, onDelete, navigate }) {
  const handleClick = () => {
    if (!n.read_at) onRead(n.id);
    if (n.lead_id) navigate(`/leads/${n.lead_id}`);
    else if (n.campaign_id) navigate(`/campaigns/${n.campaign_id}`);
    else if (n.inbox_id) navigate(`/inboxes/${n.inbox_id}`);
    else if (n.event_type.startsWith('email.')) navigate('/analytics');
    else if (['daily_limit', 'rate_limit', 'token_expired'].includes(n.event_type)) navigate('/inboxes');
    else navigate('/notifications');
  };

  return (
    <div
      onClick={handleClick}
      className={`group flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
        n.read_at
          ? 'bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800'
          : 'bg-teal-50 dark:bg-teal-900/20 hover:bg-teal-100 dark:hover:bg-teal-900/30'
      }`}
    >
      <div className="mt-0.5 text-teal-500 flex-shrink-0">
        {EVENT_ICONS[n.event_type] || <RiMailOpenLine size={20} />}
      </div>
      <div className="flex-1 min-w-0">
        <p className={`text-sm ${n.read_at ? 'text-gray-700 dark:text-gray-300' : 'text-gray-900 dark:text-gray-100 font-semibold'}`}>
          {n.title}
        </p>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">{n.message}</p>
        <div className="flex items-center gap-2 mt-1">
          <p className="text-[10px] text-gray-400">{timeAgo(n.created_at)}</p>
          {!n.read_at && (
            <span className="rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[10px] font-semibold px-1.5 py-0.5 leading-none">
              New
            </span>
          )}
        </div>
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(n.id); }}
        className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-opacity"
        title="Dismiss"
      >
        <RiDeleteBinLine size={16} />
      </button>
    </div>
  );
}

export default function Notifications() {
  const navigate = useNavigate();
  const { refresh: refreshBadge } = useNotifications();
  const notify = useNotify();
  const offsetRef = useRef(0);
  const fetchGenRef = useRef(0);

  const [activeTab, setActiveTab] = useState('all');
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [eventTypes, setEventTypes] = useState([]);
  const [notifConfig, setNotifConfig] = useState({ enabled: false, notification_email: '', events: [], rate_limit_per_hour: 10 });
  const [configSaving, setConfigSaving] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState(null);
  const limit = 50;

  const fetchNotifications = useCallback(async (reset = false) => {
    const gen = ++fetchGenRef.current;
    if (reset) {
      setItems([]);
      setTotal(0);
      offsetRef.current = 0;
    }
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (activeTab === 'unread') params.set('unread_only', 'true');
      params.set('limit', String(limit));
      params.set('offset', String(offsetRef.current));
      const data = await api.get(`/notifications?${params.toString()}`);
      if (gen !== fetchGenRef.current) return;
      setItems(reset ? data.items : prev => [...prev, ...data.items]);
      setTotal(data.total);
      setUnread(data.unread);
      if (data.items.length > 0) {
        offsetRef.current += limit;
      }
    } catch (e) {
      if (gen === fetchGenRef.current) console.error(e);
    } finally {
      if (gen === fetchGenRef.current) setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    setSearchQuery('');
    setFilterCategory(null);
    fetchNotifications(true);
  }, [activeTab]);

  useEffect(() => {
    api.get('/settings/webhooks/events').then(d => setEventTypes(d.events || [])).catch(() => {});
    api.get('/notifications/config').then(d => setNotifConfig(d)).catch(() => {});
  }, []);

  const markRead = async (id) => {
    try {
      await api.patch(`/notifications/${id}/read`);
      setItems(prev => prev.map(n => n.id === id ? { ...n, read_at: new Date().toISOString() } : n));
      setUnread(prev => Math.max(0, prev - 1));
      refreshBadge();
    } catch (e) {
      console.error(e);
    }
  };

  const markAllRead = async () => {
    try {
      await api.post('/notifications/read-all');
      setItems(prev => prev.map(n => ({ ...n, read_at: n.read_at || new Date().toISOString() })));
      setUnread(0);
      refreshBadge();
    } catch (e) {
      console.error(e);
    }
  };

  const dismiss = async (id) => {
    try {
      await api.del(`/notifications/${id}`);
      const removed = items.find(n => n.id === id);
      setItems(prev => prev.filter(n => n.id !== id));
      setTotal(prev => prev - 1);
      if (removed && !removed.read_at) setUnread(prev => Math.max(0, prev - 1));
      refreshBadge();
    } catch (e) {
      console.error(e);
    }
  };

  const toggleEvent = (evt) => {
    setNotifConfig(prev => ({
      ...prev,
      events: prev.events.includes(evt) ? prev.events.filter(e => e !== evt) : [...prev.events, evt],
    }));
  };

  const saveConfig = async () => {
    setConfigSaving(true);
    try {
      const res = await api.put('/notifications/config', notifConfig);
      setNotifConfig(res);
      notify({ message: 'Notification preferences saved', type: 'success' });
    } catch (e) {
      console.error(e);
      notify({ message: 'Failed to save preferences', type: 'error' });
    } finally {
      setConfigSaving(false);
    }
  };

  const loadMore = () => {
    if (items.length < total) {
      fetchNotifications(false);
    }
  };

  const filteredItems = useMemo(() => {
    let result = items;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(n =>
        (n.title && n.title.toLowerCase().includes(q)) ||
        (n.message && n.message.toLowerCase().includes(q))
      );
    }
    if (filterCategory) {
      const catEvents = EVENT_CATEGORIES[filterCategory] || [];
      result = result.filter(n => catEvents.includes(n.event_type));
    }
    return result;
  }, [items, searchQuery, filterCategory]);

  const filterCategories = [
    { key: null, label: 'All' },
    { key: 'email', label: 'Email' },
    { key: 'lead', label: 'Leads' },
    { key: 'system', label: 'System' },
  ];

  const isFiltered = searchQuery.trim() || filterCategory;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="shrink-0 px-6 lg:px-8 pt-6 lg:pt-8 pb-0 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-transparent">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Notifications</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
              Stay updated on campaign activity, lead replies, and system events.
            </p>
          </div>
          {unread > 0 && (
            <Button size="sm" variant="outline" onClick={markAllRead}>
              <RiCheckDoubleLine className="mr-1" size={16} />
              Mark all as read
            </Button>
          )}
        </div>
        <nav className="mt-4 flex flex-wrap gap-x-1 gap-y-0 items-end" aria-label="Notification tabs">
          {[
            { id: 'all', label: `All (${total})` },
            { id: 'unread', label: `Unread (${unread})` },
            { id: 'preferences', label: 'Preferences' },
          ].map(t => (
            <button
              key={t.id}
              type="button"
              onClick={() => { setSearchQuery(''); setFilterCategory(null); setActiveTab(t.id); }}
              className={
                'px-3 py-2 text-sm font-medium leading-none transition-colors border-b-2 -mb-px ' +
                (activeTab === t.id
                  ? 'border-teal-500 text-teal-600 dark:text-teal-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600')
              }
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6 lg:px-8">
        {activeTab !== 'preferences' && (
          <>
            {/* Search and filter bar */}
            <div className="max-w-3xl mb-4 space-y-3">
              <div className="relative">
                <RiSearchLine className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  placeholder="Search notifications..."
                  className="w-full pl-9 pr-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 dark:text-gray-100 focus:ring-2 focus:ring-teal-500 focus:border-teal-500 outline-none placeholder:text-gray-400 dark:placeholder:text-gray-500"
                />
              </div>
              <div className="flex flex-wrap gap-1.5">
                {filterCategories.map(cat => (
                  <button
                    key={cat.key ?? 'all'}
                    type="button"
                    onClick={() => setFilterCategory(cat.key)}
                    className={`px-2.5 py-1 text-xs font-medium rounded-full border transition-colors ${
                      filterCategory === cat.key
                        ? 'bg-teal-500 text-white border-teal-500'
                        : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-300 dark:border-gray-600 hover:border-teal-300 dark:hover:border-teal-500'
                    }`}
                  >
                    {cat.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Loading state — first load */}
            {loading && items.length === 0 && (
              <div className="text-center py-20 text-gray-500 dark:text-gray-400">
                <div className="mx-auto mb-4 h-8 w-8 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm">Loading notifications...</p>
              </div>
            )}

            {/* Empty state */}
            {!loading && filteredItems.length === 0 && (
              <div className="text-center py-20 text-gray-500 dark:text-gray-400">
                <RiMailOpenLine size={48} className="mx-auto mb-4 opacity-50" />
                <p className="text-lg font-medium">
                  {isFiltered ? 'No matching notifications' : 'No notifications'}
                </p>
                <p className="text-sm mt-1">
                  {isFiltered
                    ? 'Try adjusting your search or filter.'
                    : activeTab === 'unread'
                      ? "You're all caught up!"
                      : 'Notifications will appear here when events occur.'}
                </p>
              </div>
            )}

            {/* Notification list */}
            {filteredItems.length > 0 && (
              <>
                <p className="text-xs text-gray-500 dark:text-gray-400 mb-3 max-w-3xl">
                  {isFiltered
                    ? `Showing ${filteredItems.length} of ${items.length} loaded`
                    : `Showing ${items.length} of ${total} notifications`}
                </p>
                <div className="space-y-2 max-w-3xl">
                  {filteredItems.map(n => (
                    <NotificationItem
                      key={n.id}
                      n={n}
                      onRead={markRead}
                      onDelete={dismiss}
                      navigate={navigate}
                    />
                  ))}
                </div>
              </>
            )}

            {/* Load more — only on All tab */}
            {activeTab !== 'unread' && items.length < total && !loading && (
              <div className="mt-6 text-center max-w-3xl">
                <Button size="sm" variant="outline" onClick={loadMore}>
                  Load more
                </Button>
              </div>
            )}

            {/* Loading more indicator */}
            {loading && items.length > 0 && (
              <div className="mt-4 text-center text-sm text-gray-500 dark:text-gray-400 max-w-3xl">
                <div className="inline-block h-4 w-4 border-2 border-teal-500 border-t-transparent rounded-full animate-spin mr-2 align-middle" />
                Loading more...
              </div>
            )}
          </>
        )}

        {activeTab === 'preferences' && (
          <div className="max-w-2xl space-y-8">
            <section>
              <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">Email Notifications</h2>
              <div className="space-y-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={notifConfig.enabled}
                    onChange={e => setNotifConfig(prev => ({ ...prev, enabled: e.target.checked }))}
                    className="rounded"
                  />
                  <span className="text-sm">Send me email notifications</span>
                </label>
                {notifConfig.enabled && (
                  <>
                    <Input
                      label="Notification email (optional)"
                      type="email"
                      value={notifConfig.notification_email}
                      onChange={e => setNotifConfig(prev => ({ ...prev, notification_email: e.target.value }))}
                      placeholder="Leave empty to use your account email"
                      size="sm"
                      className="max-w-md dark:bg-gray-800 dark:text-gray-100 dark:border-gray-600"
                    />
                    <Input
                      label="Rate limit per hour"
                      type="number"
                      min={1}
                      max={100}
                      value={notifConfig.rate_limit_per_hour}
                      onChange={e => setNotifConfig(prev => ({ ...prev, rate_limit_per_hour: parseInt(e.target.value) || 10 }))}
                      size="sm"
                      className="w-24 dark:bg-gray-800 dark:text-gray-100 dark:border-gray-600"
                    />
                  </>
                )}
              </div>
            </section>

            <section>
              <h2 className="text-lg font-semibold mb-3 border-b border-gray-200 dark:border-gray-700 pb-2">Event Types</h2>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                Choose which events generate notifications. In-app notifications are always created.
                Email is sent only when the email channel is enabled above.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {eventTypes.map(evt => (
                  <label key={evt} className="flex items-center gap-2 cursor-pointer py-1">
                    <input
                      type="checkbox"
                      checked={notifConfig.events.includes(evt)}
                      onChange={() => toggleEvent(evt)}
                      className="rounded"
                    />
                    <span className="text-sm text-gray-700 dark:text-gray-300">{EVENT_LABELS[evt] || evt}</span>
                  </label>
                ))}
                {eventTypes.length === 0 && (
                  <p className="text-sm text-gray-400">Loading event types...</p>
                )}
              </div>
              {notifConfig.events.length === 0 && (
                <p className="text-xs text-amber-600 mt-2">All events selected (no filter applied).</p>
              )}
            </section>

            <div className="pt-2">
              <Button onClick={saveConfig} disabled={configSaving}>
                {configSaving ? 'Saving...' : 'Save Preferences'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
