import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { Input } from '../components/ui/Input';
import { Button } from '../components/ui/Button';

export default function AddCampaign() {
  const [inboxes, setInboxes] = useState([]);
  const [form, setForm] = useState({
    name: '',
    inbox_ids: [],
    sending_days: [0,1,2,3,4],
    sending_hours_start: '09:00',
    sending_hours_end: '17:00',
    stop_on_reply: true,
    // Tracking (off by default)
    track_opens: false,
    track_clicks: false,
    // Unsubscribe
    add_unsubscribe_header: true,
    // sending format
    send_first_as_text: false,
    send_all_as_text: false,
  });
  const [message, setMessage] = useState(null);
  const navigate = useNavigate();

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
      navigate(`/campaigns/${data.id}`);
    } catch (err) {
      setMessage({ type: 'error', text: err.message });
    }
  }

  return (
    <div className="max-w-xl">
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
            <Input
            label="Sending window start"
            name="sending_hours_start"
            value={form.sending_hours_start}
            onChange={handleChange}
          />
          </div>
          <div>
            <Input
            label="Sending window end"
            name="sending_hours_end"
            value={form.sending_hours_end}
            onChange={handleChange}
          />
          </div>
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
        <div className="flex gap-2">
          <Button type="submit" variant="default">Create campaign</Button>
        </div>
      </form>
    </div>
  );
}
