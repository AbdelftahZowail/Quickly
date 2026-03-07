import { useState, useEffect } from 'react';
import { api } from '../api';
import { useNotify } from '../context/NotificationContext';
import { Button } from './ui/Button';
import { Card } from './ui/Card';

const MAX_TEST_EMAILS = 10;

const STATUS_ICON = {
  valid:   { icon: '✓', label: 'Send',          cls: 'text-green-600 font-semibold' },
  invalid: { icon: '✗', label: 'Skip',          cls: 'text-red-500 font-semibold' },
  risky:   { icon: '⚠', label: 'Skip (risky)',  cls: 'text-yellow-600 font-semibold' },
  unknown: { icon: '?', label: 'Allow through', cls: 'text-gray-500' },
};

export default function EmailVerificationSettings() {
  const notify = useNotify();

  // ── core settings ──────────────────────────────────────────────────────────
  const [expanded,     setExpanded]     = useState(false);
  const [enabled,      setEnabled]      = useState(false);
  const [provider,     setProvider]     = useState('mailtester_ninja');
  const [apiKey,       setApiKey]       = useState('');
  const [apiKeyMasked, setApiKeyMasked] = useState('');
  const [providers,    setProviders]    = useState([]);
  const [saving,       setSaving]       = useState(false);
  const [testing,      setTesting]      = useState(false);
  const [testResult,   setTestResult]   = useState(null);

  // ── custom provider ────────────────────────────────────────────────────────
  const [customUrl,          setCustomUrl]          = useState('');
  const [customField,        setCustomField]        = useState('');
  const [customValidValues,  setCustomValidValues]  = useState('');
  const [customInvalidValues,setCustomInvalidValues]= useState('');
  const [customMethod,       setCustomMethod]       = useState('GET');

  // ── probe ──────────────────────────────────────────────────────────────────
  const [probeEmail,  setProbeEmail]  = useState('');
  const [probing,     setProbing]     = useState(false);
  const [probeResult, setProbeResult] = useState(null);  // { email, response }

  // ── run-test ───────────────────────────────────────────────────────────────
  const [extraEmails,       setExtraEmails]       = useState('');
  const [customTesting,     setCustomTesting]     = useState(false);
  const [customTestResults, setCustomTestResults] = useState(null);

  // ── load saved settings ────────────────────────────────────────────────────
  useEffect(() => {
    api.get('/settings/email-verification').then(data => {
      setEnabled(data.enabled || false);
      setProvider(data.provider || 'mailtester_ninja');
      setApiKeyMasked(data.api_key_masked || '');
      setProviders(data.providers || []);
      setCustomUrl(data.custom_url || '');
      setCustomField(data.custom_field_path || '');
      setCustomValidValues((data.custom_valid_values || []).join(', '));
      setCustomInvalidValues((data.custom_invalid_values || []).join(', '));
      setCustomMethod(data.custom_method || 'GET');
    }).catch(() => {});
  }, []);

  // ── helpers ────────────────────────────────────────────────────────────────
  const parseValues = str => str.split(',').map(s => s.trim()).filter(Boolean);

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        enabled,
        provider,
        custom_url: customUrl,
        custom_field_path: customField,
        custom_valid_values: parseValues(customValidValues),
        custom_invalid_values: parseValues(customInvalidValues),
        custom_method: customMethod,
      };
      if (apiKey) payload.api_key = apiKey;
      const res = await api.post('/settings/email-verification', payload);
      setApiKey('');
      setApiKeyMasked(res.api_key_masked || '');
      setCustomTestResults(null);
      notify({ type: 'success', message: 'Email verification settings saved' });
    } catch (e) {
      notify({ type: 'error', message: e.message });
    } finally {
      setSaving(false);
    }
  };

  const testApiKey = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.post('/settings/email-verification/test');
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const probeUrl = async () => {
    if (!customUrl || !customUrl.includes('{email}')) {
      return notify({ type: 'error', message: 'URL must contain {email}' });
    }
    setProbing(true);
    setProbeResult(null);
    const emailToProbe = probeEmail.trim() || 'test@gmail.com';
    try {
      const res = await api.post('/settings/email-verification/test-custom', {
        url_template: customUrl,
        field_path: '',
        valid_values: [],
        invalid_values: [],
        method: customMethod,
        test_emails: [emailToProbe],
      });
      const first = res.results?.[0];
      setProbeResult(first ? { email: first.email, response: first.raw_response } : null);
    } catch (e) {
      notify({ type: 'error', message: e.message });
    } finally {
      setProbing(false);
    }
  };

  const runTest = async () => {
    if (!customUrl || !customUrl.includes('{email}')) {
      return notify({ type: 'error', message: 'URL must contain {email}' });
    }
    setCustomTesting(true);
    setCustomTestResults(null);
    const extra = extraEmails
      .split(/[\n,]+/)
      .map(s => s.trim())
      .filter(Boolean)
      .slice(0, MAX_TEST_EMAILS);
    try {
      const res = await api.post('/settings/email-verification/test-custom', {
        url_template: customUrl,
        field_path: customField,
        valid_values: parseValues(customValidValues),
        invalid_values: parseValues(customInvalidValues),
        method: customMethod,
        test_emails: extra, // empty = backend auto-picks from inboxes/leads/synthetics
      });
      setCustomTestResults(res.results || []);
      notify({ type: 'success', message: `Tested ${res.results?.length ?? 0} emails` });
    } catch (e) {
      notify({ type: 'error', message: e.message });
    } finally {
      setCustomTesting(false);
    }
  };

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <Card className="mb-4">

      {/* ── header row ── */}
      <div
        className="flex items-center justify-between cursor-pointer select-none"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-center gap-3 min-w-0">
          <span className={`text-gray-400 transition-transform text-xs ${expanded ? 'rotate-90' : ''}`}>▶</span>
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 truncate">Email Verification</h3>
          {enabled
            ? <span className="text-[10px] bg-green-100 text-green-700 border border-green-200 rounded-full px-2 py-0.5 font-medium shrink-0">Enabled</span>
            : <span className="text-[10px] bg-gray-100 text-gray-500 border rounded-full px-2 py-0.5 font-medium shrink-0">Disabled</span>
          }
        </div>
        <label className="flex items-center gap-1.5 cursor-pointer shrink-0 ml-4" onClick={e => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={enabled}
            className="rounded"
            onChange={e => setEnabled(e.target.checked)}
          />
          <span className="text-xs font-medium text-gray-600 whitespace-nowrap">
            Enable
          </span>
        </label>
      </div>

      {/* ── expandable body ── */}
      <div className={`grid transition-[grid-template-rows] duration-200 ease-in-out ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
        <div className="min-h-0 overflow-hidden">
          <div className="mt-4 space-y-5 border-t pt-4">

            <p className="text-sm text-gray-500">
              Automatically check each lead's email address before sending. Addresses that look fake or
              undeliverable are skipped so you don't waste sending quota.
            </p>

            {/* ── Provider selector ── */}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Verification service
              </label>
              <select
                className="border rounded-lg px-3 py-2 text-sm w-full max-w-xs dark:bg-gray-800 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-teal-300"
                value={provider}
                onChange={e => {
                  setProvider(e.target.value);
                  setCustomTestResults(null);
                  setProbeResult(null);
                }}
              >
                {(providers.length > 0 ? providers : ['mailtester_ninja']).map(p => (
                  <option key={p} value={p}>
                    {p === 'custom'           ? 'My own API (custom)'
                      : p === 'mailtester_ninja' ? 'Mailtester Ninja'
                      : p.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            </div>

            {/* ── API key (built-in providers) ── */}
            {provider !== 'custom' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                <input
                  type="password"
                  className="border rounded-lg px-3 py-2 text-sm w-full max-w-md dark:bg-gray-800 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-teal-300"
                  placeholder={apiKeyMasked || 'Enter API key'}
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                />
                {apiKeyMasked && !apiKey && (
                  <p className="text-xs text-gray-400 mt-1">Current key: {apiKeyMasked}</p>
                )}
              </div>
            )}

            {/* ══════════════════════════════════════════════
                Custom provider wizard
                ══════════════════════════════════════════════ */}
            {provider === 'custom' && (
              <div className="space-y-6 p-4 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-dashed border-gray-300 dark:border-gray-600">

                {/* ── Step 1: URL ── */}
                <div>
                  <StepLabel n={1} title="Enter your API's web address" />
                  <p className="text-xs text-gray-500 mb-2">
                    Include <code className="bg-gray-200 dark:bg-gray-700 px-1 rounded font-mono">{'{email}'}</code> where
                    the email should go. The system will replace it automatically for each address checked.
                  </p>
                  <div className="flex gap-2 flex-wrap">
                    <input
                      type="text"
                      className="flex-1 min-w-0 border rounded-lg px-3 py-2 text-sm dark:bg-gray-800 dark:border-gray-600 font-mono focus:outline-none focus:ring-2 focus:ring-teal-300"
                      placeholder="https://api.example.com/verify?email={email}"
                      value={customUrl}
                      onChange={e => {
                        setCustomUrl(e.target.value);
                        setProbeResult(null);
                        setCustomTestResults(null);
                      }}
                    />
                    <select
                      className="border rounded-lg px-3 py-2 text-sm dark:bg-gray-800 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-teal-300"
                      value={customMethod}
                      onChange={e => setCustomMethod(e.target.value)}
                    >
                      <option value="GET">GET</option>
                      <option value="POST">POST</option>
                    </select>
                  </div>
                </div>

                {/* ── Step 2: Probe ── */}
                <div>
                  <StepLabel n={2} title="Preview the response" />
                  <p className="text-xs text-gray-500 mb-2">
                    Send one request to see what your API actually returns. Use any email address you like.
                  </p>
                  <div className="flex gap-2 flex-wrap items-center">
                    <input
                      type="email"
                      className="border rounded-lg px-3 py-2 text-sm dark:bg-gray-800 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-teal-300 w-56"
                      placeholder="test@gmail.com"
                      value={probeEmail}
                      onChange={e => setProbeEmail(e.target.value)}
                    />
                    <Button size="sm" variant="outline" onClick={probeUrl} disabled={probing || !customUrl}>
                      {probing ? 'Loading…' : 'Preview response'}
                    </Button>
                  </div>

                  {probeResult && (
                    <div className="mt-3 rounded-lg bg-gray-100 dark:bg-gray-900 border text-xs font-mono p-3 overflow-x-auto max-h-48">
                      <p className="text-gray-500 mb-1 font-sans font-medium not-italic">
                        Response for <strong>{probeResult.email}</strong>:
                      </p>
                      <pre className="whitespace-pre-wrap break-all text-gray-700 dark:text-gray-300">
                        {JSON.stringify(probeResult.response, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>

                {/* ── Step 3: Field ── */}
                <div>
                  <StepLabel n={3} title="Which field shows the result?" />
                  <p className="text-xs text-gray-500 mb-2">
                    Look at the response above and type the name of the field that tells you if the email is good
                    or bad. If it's inside a nested object, use a dot — e.g.{' '}
                    <code className="bg-gray-200 dark:bg-gray-700 px-0.5 rounded font-mono">data.status</code>.
                  </p>
                  <input
                    type="text"
                    className="border rounded-lg px-3 py-2 text-sm w-full max-w-sm dark:bg-gray-800 dark:border-gray-600 font-mono focus:outline-none focus:ring-2 focus:ring-teal-300"
                    placeholder="status"
                    value={customField}
                    onChange={e => setCustomField(e.target.value)}
                  />
                </div>

                {/* ── Step 4: Good / Bad values ── */}
                <div>
                  <StepLabel n={4} title="What do the values mean?" />
                  <p className="text-xs text-gray-500 mb-3">
                    Type the exact values your API uses and separate them with a comma.
                    Anything not listed is treated as unknown — the email is still sent.
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="flex items-center gap-1.5 text-sm font-medium text-green-700 dark:text-green-400 mb-1">
                        <span>✓</span> Good — email is valid, send it
                      </label>
                      <input
                        type="text"
                        className="border rounded-lg px-3 py-2 text-sm w-full dark:bg-gray-800 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-green-300"
                        placeholder="valid, ok, deliverable"
                        value={customValidValues}
                        onChange={e => setCustomValidValues(e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="flex items-center gap-1.5 text-sm font-medium text-red-600 dark:text-red-400 mb-1">
                        <span>✗</span> Bad — skip this email
                      </label>
                      <input
                        type="text"
                        className="border rounded-lg px-3 py-2 text-sm w-full dark:bg-gray-800 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-red-300"
                        placeholder="invalid, blocked, risky"
                        value={customInvalidValues}
                        onChange={e => setCustomInvalidValues(e.target.value)}
                      />
                    </div>
                  </div>
                </div>

                {/* ── Step 5: Run test ── */}
                <div>
                  <StepLabel n={5} title="Run a test before saving" />
                  <p className="text-xs text-gray-500 mb-2">
                    We'll automatically test a sample of your inboxes and leads. You can also add your own
                    addresses below (up to {MAX_TEST_EMAILS}), one per line or separated by commas.
                  </p>
                  <textarea
                    className="border rounded-lg px-3 py-2 text-sm w-full dark:bg-gray-800 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-teal-300 resize-none font-mono"
                    rows={3}
                    placeholder={`Optional — add up to ${MAX_TEST_EMAILS} emails\nexample@gmail.com\ntest@domain.com`}
                    value={extraEmails}
                    onChange={e => setExtraEmails(e.target.value)}
                  />
                  {(() => {
                    const n = extraEmails.split(/[\n,]+/).map(s => s.trim()).filter(Boolean).length;
                    return n > MAX_TEST_EMAILS
                      ? <p className="text-xs text-amber-600 mt-1">Only the first {MAX_TEST_EMAILS} will be tested.</p>
                      : null;
                  })()}
                  <div className="mt-2">
                    <Button size="sm" variant="outline" onClick={runTest} disabled={customTesting || !customUrl}>
                      {customTesting ? 'Testing…' : 'Run test'}
                    </Button>
                  </div>
                </div>

                {/* ── Test results ── */}
                {customTestResults !== null && (
                  <div>
                    <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      Results — check the "Decision" column matches what you'd expect:
                    </p>
                    <div className="overflow-x-auto rounded-lg border">
                      <table className="w-full text-xs">
                        <thead className="bg-gray-50 dark:bg-gray-800">
                          <tr>
                            <th className="px-3 py-2 text-left font-medium text-gray-500">Email address</th>
                            <th className="px-3 py-2 text-left font-medium text-gray-500">API returned</th>
                            <th className="px-3 py-2 text-left font-medium text-gray-500">Decision</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y">
                          {customTestResults.map((r, i) => {
                            const s = STATUS_ICON[r.status] || { icon: '', label: r.status, cls: 'text-gray-500' };
                            return (
                              <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800/40">
                                <td className="px-3 py-2 font-mono">{r.email}</td>
                                <td className="px-3 py-2 font-mono text-gray-600 dark:text-gray-400">
                                  {r.raw_field_value != null
                                    ? String(r.raw_field_value)
                                    : <span className="text-gray-400 italic">—</span>}
                                </td>
                                <td className={`px-3 py-2 ${s.cls}`}>
                                  {s.icon} {s.label}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    {customTestResults.some(r => r.status === 'unknown') && (
                      <p className="text-xs text-amber-600 mt-2">
                        ⚠ Some emails came back as unknown — the value your API returned isn't in your Good or Bad
                        list. Add it above, or leave it to allow those emails through.
                      </p>
                    )}
                    <p className="text-xs text-gray-400 mt-1">
                      Happy with the results? Click Save below.
                    </p>
                  </div>
                )}

              </div>
            )}
            {/* ── end custom provider wizard ── */}

            {/* ── Save / Test connection ── */}
            <div className="flex items-center gap-3 flex-wrap">
              <Button size="sm" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
              {provider !== 'custom' && (
                <Button size="sm" variant="outline" onClick={testApiKey} disabled={testing}>
                  {testing ? 'Testing…' : 'Test connection'}
                </Button>
              )}
            </div>

            {testResult && (
              <div className={`text-sm p-3 rounded-lg ${testResult.ok
                ? 'bg-green-50 text-green-700 border border-green-200'
                : 'bg-red-50 text-red-700 border border-red-200'}`}
              >
                {testResult.ok
                  ? <>Connection successful — status: <strong>{testResult.status || 'ok'}</strong>{testResult.message ? ` (${testResult.message})` : ''}</>
                  : <>Test failed: {testResult.error}</>
                }
              </div>
            )}

          </div>
        </div>
      </div>
    </Card>
  );
}

/* ── small helper ── */
function StepLabel({ n, title }) {
  return (
    <div className="flex items-center gap-2 mb-1">
      <span className="flex-shrink-0 w-5 h-5 rounded-full bg-teal-500 text-white text-[10px] font-bold flex items-center justify-center">
        {n}
      </span>
      <p className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</p>
    </div>
  );
}
