import { useEffect, useState } from 'react';
import { api } from '../api';

export default function TestModeBanner() {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    api.get('/settings/test-mode').then(data => {
      setEnabled(data.enabled);
    }).catch(() => {});
  }, []);

  if (!enabled) return null;
  return (
    <div className="bg-red-100 text-red-800 p-2 text-center">
      Test mode is enabled. Emails will not be sent to real recipients.
    </div>
  );
}
