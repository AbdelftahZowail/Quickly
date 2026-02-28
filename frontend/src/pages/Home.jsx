import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

export default function Home() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    api.get('/status').then(setStatus).catch(() => {});
  }, []);

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold">Quickly</h1>
      <p className="mt-4 text-gray-600">Simple email campaigns: leads, sequences, and a smart queue. No login.</p>
      <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
        <Link to="/campaigns" className="card p-4 bg-white rounded shadow hover:bg-gray-50">
          <strong>Campaigns</strong>
          <p className="mt-2 text-sm text-gray-500">Create campaigns with email sequences, assign inboxes, set windows and limits.</p>
        </Link>
        <Link to="/inboxes" className="card p-4 bg-white rounded shadow hover:bg-gray-50">
          <strong>Inboxes</strong>
          <p className="mt-2 text-sm text-gray-500">Configure sending addresses (Resend, SMTP, Gmail OAuth).</p>
        </Link>
        <Link to="/calendar" className="card p-4 bg-white rounded shadow hover:bg-gray-50">
          <strong>Calendar</strong>
          <p className="mt-2 text-sm text-gray-500">View all sent and scheduled emails across campaigns.</p>
        </Link>
        <Link to="/mailbox" className="card p-4 bg-white rounded shadow hover:bg-gray-50">
          <strong>Unibox</strong>
          <p className="mt-2 text-sm text-gray-500">Review synced Gmail threads and send replies from one place.</p>
        </Link>
      </div>
      {status && (
        <div className="mt-8 p-4 bg-gray-100 rounded">
          <h2 className="font-semibold">System Status</h2>
          <p>Scheduler running: {status.scheduler_running ? '✔' : '✘'}</p>
          <p>Next send job: {status.next_send_job_run || 'n/a'}</p>
          <p>Test mode: {status.test_mode ? 'enabled' : 'disabled'}</p>
        </div>
      )}
    </div>
  );
}
