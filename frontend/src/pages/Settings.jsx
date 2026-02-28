import { useEffect, useState } from 'react';
import { api } from '../api';
import { useDarkMode } from '../context/DarkModeContext';
import { useConfirm } from '../context/ConfirmContext';
import { useNotify } from '../context/NotificationContext';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';


export default function Settings() {
  const notify = useNotify();
  const { darkMode, toggleDarkMode } = useDarkMode();
  const confirm = useConfirm();
  const [strategy, setStrategy] = useState('priority');
  const [testMode, setTestMode] = useState(false);


  const loadStrategy = async () => {
    try {
      const [data, tm] = await Promise.all([
        api.get('/settings/scheduling-strategy'),
        api.get('/settings/test-mode'),
      ]);
      setStrategy(data.scheduling_strategy || 'priority');
      setTestMode(tm.test_mode || false);
    } catch {};
  };



  useEffect(() => {
    loadStrategy();
  }, []);





  const submitStrategy = async val => {
    if (val !== strategy) {
      try {
        const { has_leads } = await api.get('/campaigns/has-leads');
        if (has_leads) {
          const ok = await confirm(
            'Changing the scheduling strategy will recalculate all campaigns. ' +
            'This may take a few seconds when leads are enrolled. Continue?'
          );
          if (!ok) return;
        }
      } catch {}
    }

    try {
      await api.post('/settings/scheduling-strategy', { scheduling_strategy: val });
      setStrategy(val);
      notify({type:'success',message:'Strategy saved (recalculation running in background)'});
    } catch(e){notify({type:'error',message:e.message});}
  };

  const submitTestMode = async val => {
    try {
      await api.post('/settings/test-mode', { test_mode: val });
      setTestMode(val);
      notify({ type: 'success', message: 'Test mode saved' });
    } catch (e) {
      notify({ type: 'error', message: e.message });
    }
  };






  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-4">Settings</h1>
      <div className="flex items-center gap-2 mb-6">
        <Button size="sm" variant="outline" onClick={toggleDarkMode}>
          {darkMode ? 'Light Mode' : 'Dark Mode'}
        </Button>
        <span className="text-sm text-gray-500">(UI preference only)</span>
      </div>
      {/* test mode */}
      <Card className="mt-6">
        <h2 className="text-lg font-semibold mb-2">Test Mode</h2>
        <p className="text-xs text-gray-500 mb-2">
          When enabled, emails are redirected or simulated; disable to send
          real messages.
        </p>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={testMode}
            onChange={e => submitTestMode(e.target.checked)}
          />
          <span className="text-sm">Enabled</span>
        </label>
      </Card>

      {/* scheduling strategy */}
      <Card className="mt-6">
        <h2 className="text-lg font-semibold mb-2">Scheduling Strategy</h2>
        <p className="text-xs text-gray-500 mb-2">Controls how ⚡ Recalculate All Campaigns distributes emails across campaigns.</p>
        <div className="space-y-3">
          <label className="flex gap-2 items-start cursor-pointer">
            <input type="radio" name="strategy" value="priority" checked={strategy==='priority'} onChange={()=>submitStrategy('priority')} />
            <span>
              <strong>Priority by campaign</strong><br/>
              <span className="text-xs text-gray-500">
                Campaigns are processed in ascending priority order — #1 gets inbox capacity first. <a href="/campaigns" className="text-teal-500 underline">Go to Campaigns</a> to reorder.
              </span>
            </span>
          </label>
          <label className="flex gap-2 items-start cursor-pointer">
            <input type="radio" name="strategy" value="round_robin" checked={strategy==='round_robin'} onChange={()=>submitStrategy('round_robin')} />
            <span>
              <strong>Round-robin distribution</strong><br/>
              <span className="text-xs text-gray-500">
                Inbox capacity is divided evenly across active campaigns; leads are scheduled in batches.
              </span>
            </span>
          </label>
        </div>
      </Card>
    </div>
  );
}
