import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, apiCache } from '../api';
import { Input } from '../components/ui/Input';
import { Button } from '../components/ui/Button';

export default function AddCampaign() {
  const [inboxes, setInboxes] = useState(() => apiCache.get('/inboxes') || []);
  const [form, setForm] = useState({
    name: '',
    inbox_ids: [],
    sending_days: [0,1,2,3,4],
    sending_hours_start: '09:00',
    sending_hours_end: '17:00',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    stop_on_reply: true,
    // Tracking (off by default)
    track_opens: false,
    track_clicks: false,
    // Unsubscribe
    add_unsubscribe_header: true,
    // sending format
    send_first_as_text: false,
    send_all_as_text: false,
    match_lead_provider: true,
  });
  const [message, setMessage] = useState(null);
  const [tzSearch, setTzSearch] = useState('');
  const navigate = useNavigate();

  const tzList = useMemo(() => {
    return Intl.supportedValuesOf('timeZone').map(tz => {
      let offsetLabel = '';
      try {
        const parts = new Intl.DateTimeFormat('en', { timeZone: tz, timeZoneName: 'shortOffset' }).formatToParts(new Date());
        const off = parts.find(p => p.type === 'timeZoneName');
        if (off) offsetLabel = ` (${off.value})`;
      } catch (_) {}
      return { value: tz, label: `${tz.replace(/_/g, ' ')}${offsetLabel}` };
    });
  }, []);

  const filteredTz = tzSearch
    ? tzList.filter(t => t.label.toLowerCase().includes(tzSearch.toLowerCase()))
    : tzList;

  useEffect(() => {
    api.get('/inboxes').then(setInboxes).catch(() => {
      setMessage({ type: 'error', text: 'Could not load inboxes. Add inboxes first.' });
    });
  }, []);

  function handleCheckboxChange(e) {
    const { name, value, checked } = e.target;
    if (name === 'inbox_id') {
      const id = parseInt(value, 10);
      setForm(f => {
        const ids = new Set(f.inbox_ids);
        if (checked) ids.add(id); else ids.delete(id);
        return { ...f, inbox_ids: Array.from(ids) };
      });
    } else if (name === 'day') {
      const day = parseInt(value, 10);
      setForm(f => {
        const days = new Set(f.sending_days);
        if (checked) days.add(day); else days.delete(day);
        return { ...f, sending_days: Array.from(days).sort() };
      });
    }
  }

  function handleChange(e) {
    const { name, value, type, checked } = e.target;
    setForm(f => ({ ...f, [name]: type === 'checkbox' ? checked : value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (form.inbox_ids.length === 0) {
      setMessage({ type: 'error', text: 'Select at least one inbox.' });
      return;
    }
    try {
      const data = await api.post('/campaigns', form);
      setMessage({ type: 'success', text: `Campaign created. ` });
      navigate(`/campaigns/${data.id}#analytics`);
    } catch (err) {
      setMessage({ type: 'error', text: err.message });
    }
  }

  return (
    <div className="p-8 max-w-xl">
      <h1 className="text-2xl font-bold mb-4">Create campaign</h1>
      {message && (
        <div className={message.type === 'error' ? 'text-red-600' : 'text-green-600'}>
          {message.text}
        </div>
      )}
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <Input
            label="Campaign name *"
            name="name"
            value={form.name}
            onChange={handleChange}
            required
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700">
            Sending inboxes * (select at least one)
          </label>
          <div className="mt-1 space-y-1 max-h-52 overflow-y-auto p-2 border border-gray-300 rounded">
            {inboxes.map(i => (
              <label key={i.id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  name="inbox_id"
                  value={i.id}
                  checked={form.inbox_ids.includes(i.id)}
                  onChange={handleCheckboxChange}
                />
                <span className="text-sm">
                  {i.email}{i.display_name ? ` (${i.display_name})` : ''} — max {i.max_emails_per_day}/day
                </span>
              </label>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700">Sending days</label>
          <div className="mt-1 flex flex-wrap gap-2">
            {[0,1,2,3,4,5,6].map(d => (
              <label key={d} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  name="day"
                  value={d}
                  checked={form.sending_days.includes(d)}
                  onChange={handleCheckboxChange}
                />
                <span className="text-sm">
                  {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]}
                </span>
              </label>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Sending window start</label>
            <input
              type="time"
              name="sending_hours_start"
              value={form.sending_hours_start}
              onChange={handleChange}
              className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Sending window end</label>
            <input
              type="time"
              name="sending_hours_end"
              value={form.sending_hours_end}
              onChange={handleChange}
              className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
            />
          </div>
        </div>
        {/* Timezone */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Timezone</label>
          <div className="relative">
            <input
              type="text"
              placeholder="Search timezone…"
              value={tzSearch || form.timezone}
              onFocus={e => { setTzSearch(''); e.target.select(); }}
              onChange={e => { setTzSearch(e.target.value); }}
              onBlur={() => setTimeout(() => setTzSearch(''), 200)}
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
            />
            {tzSearch !== '' && (
              <ul className="absolute z-50 mt-1 w-full max-h-52 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg">
                {filteredTz.slice(0, 100).map(t => (
                  <li
                    key={t.value}
                    className={`px-3 py-2 text-sm cursor-pointer hover:bg-teal-50 ${
                      form.timezone === t.value ? 'bg-teal-100 font-medium' : ''
                    }`}
                    onMouseDown={() => { setForm(f => ({ ...f, timezone: t.value })); setTzSearch(''); }}
                  >
                    {t.label}
                  </li>
                ))}
                {filteredTz.length === 0 && <li className="px-3 py-2 text-sm text-gray-400">No match</li>}
              </ul>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-1">Sending hours above are interpreted in this timezone</p>
        </div>
        <div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              name="stop_on_reply"
              checked={form.stop_on_reply}
              onChange={handleChange}
            />
            <span className="text-sm">Stop sending sequence when lead replies</span>
          </label>
        </div>

        {/* Tracking */}
        <div className="border-t pt-3">
          <p className="text-sm font-semibold text-gray-700 mb-1">Tracking</p>
          <div className="space-y-1 pl-1">
            <label className="flex items-center gap-2">
              <input type="checkbox" name="track_opens" checked={form.track_opens} onChange={handleChange} />
              <span className="text-sm">Track email opens</span>
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" name="track_clicks" checked={form.track_clicks} onChange={handleChange} />
              <span className="text-sm">Track link clicks</span>
            </label>
          </div>
        </div>

        {/* Unsubscribe */}
        <div className="border-t pt-3">
          <p className="text-sm font-semibold text-gray-700 mb-1">Unsubscribe</p>
          <div className="space-y-1 pl-1">
            <label className="flex items-center gap-2">
              <input type="checkbox" name="add_unsubscribe_header" checked={form.add_unsubscribe_header} onChange={handleChange} />
              <span className="text-sm">Add List-Unsubscribe header (recommended)</span>
            </label>
          </div>
        </div>

        {/* Sending format */}
        <div className="border-t pt-3">
          <p className="text-sm font-semibold text-gray-700 mb-1">Sending format</p>
          <div className="space-y-1 pl-1">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                name="send_first_as_text"
                checked={form.send_first_as_text}
                disabled={form.send_all_as_text}
                onChange={handleChange}
              />
              <span className="text-sm">Send first email as plain text (improves deliverability)</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                name="send_all_as_text"
                checked={form.send_all_as_text}
                onChange={e => setForm(f => ({
                  ...f,
                  send_all_as_text: e.target.checked,
                  send_first_as_text: e.target.checked ? false : f.send_first_as_text,
                }))}
              />
              <span className="text-sm">Send all emails as plain text</span>
            </label>
          </div>
        </div>

        {/* Provider matching */}
        <div className="border-t pt-3">
          <p className="text-sm font-semibold text-gray-700 mb-1">Provider matching</p>
          <div className="space-y-1 pl-1">
            <label className="flex items-center gap-2">
              <input type="checkbox" name="match_lead_provider" checked={form.match_lead_provider} onChange={handleChange} />
              <span className="text-sm">Match lead provider — send from Google inboxes to Google leads, Office 365 to Office 365 (falls back to any inbox if none match)</span>
            </label>
          </div>
        </div>

        <div className="flex gap-2">
          <Button type="submit" variant="default">Create campaign</Button>
        </div>
      </form>
    </div>
  );
}
