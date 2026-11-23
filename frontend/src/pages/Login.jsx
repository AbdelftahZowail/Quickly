import { useEffect, useState } from 'react';
import { useAuth, setAccessToken } from '../context/AuthContext';
import { FileUploadArea } from '../components/ui/FileUploadArea';

const BACKUP_MIN_PASSWORD_LEN = 8;

const INPUT_CLASS = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm';

function parseDetailMessage(text) {
  try {
    const j = JSON.parse(text);
    const d = j.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d))
      return d.map(x => (typeof x === 'string' ? x : x.msg || JSON.stringify(x))).join(' ');
  } catch {
    /* ignore */
  }
  return text;
}

async function postJson(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text || res.statusText };
  }
  return { res, data, text };
}

function SetupCodeField({ id, value, onChange, disabled, hint = false }) {
  return (
    <div className="space-y-1 text-left">
      <label htmlFor={id} className="block text-sm font-medium text-gray-700">
        Setup code
      </label>
      <input
        id={id}
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
        placeholder="XXXX-XXXX"
        className={`${INPUT_CLASS} font-mono`}
      />
      {hint && (
        <p className="text-xs text-gray-500">
          Find it in your server logs:{' '}
          <code className="text-[11px]">docker compose logs app | grep "Setup code"</code>
        </p>
      )}
    </div>
  );
}

const OAUTH_BUTTON_CLASS =
  'w-full flex items-center justify-center gap-3 py-2.5 px-4 border border-gray-300 rounded-lg shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed';

/**
 * Google + Microsoft sign-in buttons. Always rendered so the option never
 * disappears; a provider without credentials is disabled and says why on hover.
 */
function OAuthButtons({ isFirstUser, query, disabled, google, microsoft }) {
  const googleLabel = isFirstUser
    ? 'Create admin account with Google'
    : 'Sign in with Google';
  const microsoftLabel = isFirstUser
    ? 'Create admin account with Microsoft'
    : 'Sign in with Microsoft';

  return (
    <>
      <div className="flex items-center gap-3" aria-hidden="true">
        <div className="h-px flex-1 bg-gray-200" />
        <span className="text-xs uppercase tracking-wide text-gray-500">or continue with</span>
        <div className="h-px flex-1 bg-gray-200" />
      </div>

      <div className="space-y-4">
        <button
          type="button"
          onClick={() => window.location.assign(`/oauth/app/google/authorize${query}`)}
          disabled={disabled || !google}
          title={!google ? 'Google sign-in is not configured on this server' : undefined}
          className={OAUTH_BUTTON_CLASS}
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          {googleLabel}
        </button>

        <button
          type="button"
          onClick={() => window.location.assign(`/oauth/app/microsoft/authorize${query}`)}
          disabled={disabled || !microsoft}
          title={!microsoft ? 'Microsoft sign-in is not configured on this server' : undefined}
          className={OAUTH_BUTTON_CLASS}
        >
          <svg className="w-5 h-5" viewBox="0 0 21 21">
            <rect fill="#F25022" x="1" y="1" width="9" height="9"/>
            <rect fill="#7FBA00" x="11" y="1" width="9" height="9"/>
            <rect fill="#00A4EF" x="1" y="11" width="9" height="9"/>
            <rect fill="#FFB900" x="11" y="11" width="9" height="9"/>
          </svg>
          {microsoftLabel}
        </button>
      </div>
    </>
  );
}

export default function Login() {
  const { setupComplete, setupCodeRequired, authProviders, checkSetup } = useAuth();
  const [restoreExpanded, setRestoreExpanded] = useState(false);
  const [restoreFile, setRestoreFile] = useState(null);
  const [restoreFileKey, setRestoreFileKey] = useState(0);
  const [restorePassword, setRestorePassword] = useState('');
  const [restoreMeta, setRestoreMeta] = useState(null);
  const [restoreMetaBusy, setRestoreMetaBusy] = useState(false);
  const [restorePreview, setRestorePreview] = useState(null);
  const [restorePreviewBusy, setRestorePreviewBusy] = useState(false);
  const [restoreExecuteBusy, setRestoreExecuteBusy] = useState(false);
  const [restoreMsg, setRestoreMsg] = useState(null);

  const [setupCode, setSetupCode] = useState('');
  // First run: the code is checked before any other form is shown, so nobody
  // can see (or poke at) the signup/restore screens without it.
  const [setupUnlocked, setSetupUnlocked] = useState(false);
  const [unlockBusy, setUnlockBusy] = useState(false);
  const [unlockError, setUnlockError] = useState('');

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState('');

  const [regEmail, setRegEmail] = useState('');
  const [regUsername, setRegUsername] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regConfirm, setRegConfirm] = useState('');
  const [registerBusy, setRegisterBusy] = useState(false);
  const [registerError, setRegisterError] = useState('');

  const isFirstUser = setupComplete === false;
  const setupCodeValue = setupCode.trim();
  const hasSetupCode = setupCodeValue.length > 0;
  const needsSetupCode = isFirstUser && setupCodeRequired !== false;
  const needsSetupUnlock = needsSetupCode && !setupUnlocked;
  // The restore wizard is only reachable after the code is unlocked, so it
  // never asks for it again. Sending it along is still required by the API.
  const canRestore = !needsSetupCode || setupUnlocked;
  const oauthDisabled = needsSetupCode && !hasSetupCode;

  const showGoogle = !!authProviders?.google;
  const showMicrosoft = !!authProviders?.microsoft;


  const oauthQuery = needsSetupCode && hasSetupCode
    ? `?setup_code=${encodeURIComponent(setupCodeValue)}`
    : '';

  useEffect(() => {
    if (!isFirstUser || !restoreExpanded || !restoreFile || !canRestore) {
      setRestoreMetaBusy(false);
      return undefined;
    }
    let cancelled = false;
    setRestoreMetaBusy(true);
    setRestoreMeta(null);
    setRestorePreview(null);
    setRestoreMsg(null);
    (async () => {
      try {
        const form = new FormData();
        form.append('file', restoreFile);
        form.append('setup_code', setupCodeValue);
        const res = await fetch('/api/auth/restore-setup/metadata', { method: 'POST', body: form });
        const text = await res.text();
        let data;
        try {
          data = JSON.parse(text);
        } catch {
          data = { detail: text || res.statusText };
        }
        if (!res.ok) {
          throw new Error(parseDetailMessage(text) || res.statusText);
        }
        if (!cancelled) {
          setRestoreMeta(data);
        }
      } catch (e) {
        if (!cancelled) {
          setRestoreMsg({ type: 'err', text: e.message || 'Could not read backup file' });
        }
      } finally {
        if (!cancelled) {
          setRestoreMetaBusy(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // setupCodeValue is only needed when a file is selected; canRestore gates it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFirstUser, restoreExpanded, restoreFile, canRestore]);

  const clearRestoreWizard = () => {
    setRestoreFile(null);
    setRestoreFileKey(k => k + 1);
    setRestorePassword('');
    setRestoreMeta(null);
    setRestorePreview(null);
    setRestoreMsg(null);
  };

  const handleUnlock = async (e) => {
    e.preventDefault();
    if (unlockBusy) return;
    setUnlockError('');
    if (!hasSetupCode) {
      setUnlockError('Enter the setup code from your server logs.');
      return;
    }
    setUnlockBusy(true);
    try {
      const { res, text } = await postJson('/api/auth/setup-code/verify', {
        code: setupCodeValue,
      });
      if (!res.ok) {
        setUnlockError(
          res.status === 429
            ? 'Too many attempts. Wait a few minutes and try again.'
            : 'That setup code is not valid. Check the server logs.'
        );
        return;
      }
      setSetupUnlocked(true);
    } catch {
      setUnlockError('Could not verify the code. Check your connection and try again.');
    } finally {
      setUnlockBusy(false);
    }
  };

  const handleSignIn = async (e) => {
    e.preventDefault();
    if (loginBusy) return;
    setLoginError('');
    const identifier = username.trim();
    if (!identifier || !password) {
      setLoginError('Enter your email or username and password.');
      return;
    }
    setLoginBusy(true);
    try {
      const { res, data } = await postJson('/api/auth/login', {
        username: identifier,
        password,
      });
      if (!res.ok || !data?.access_token) {
        setLoginError('Invalid credentials.');
        return;
      }
      setAccessToken(data.access_token);
      await checkSetup();
      window.location.assign('/');
    } catch {
      setLoginError('Could not sign in. Check your connection and try again.');
    } finally {
      setLoginBusy(false);
    }
  };

  const handleCreateAdmin = async (e) => {
    e.preventDefault();
    if (registerBusy) return;
    setRegisterError('');
    if (!regEmail.trim() || !regUsername.trim() || !regPassword) {
      setRegisterError('Enter an email, username, and password.');
      return;
    }
    if (regPassword.length < 8) {
      setRegisterError('Password must be at least 8 characters.');
      return;
    }
    if (regPassword !== regConfirm) {
      setRegisterError('Passwords do not match.');
      return;
    }
    setRegisterBusy(true);
    try {
      const { res, text } = await postJson('/api/auth/register', {
        username: regUsername.trim(),
        email: regEmail.trim(),
        password: regPassword,
        setup_code: setupCodeValue,
      });
      if (!res.ok) {
        setRegisterError(parseDetailMessage(text) || 'Could not create the admin account.');
        return;
      }
      const login = await postJson('/api/auth/login', {
        username: regUsername.trim(),
        password: regPassword,
      });
      if (!login.res.ok || !login.data?.access_token) {
        setRegisterError('Account created, but sign-in failed. Try signing in.');
        return;
      }
      setAccessToken(login.data.access_token);
      await checkSetup();
      window.location.assign('/');
    } catch {
      setRegisterError('Could not create the admin account. Try again.');
    } finally {
      setRegisterBusy(false);
    }
  };

  const heading = needsSetupUnlock
    ? 'Enter your setup code'
    : isFirstUser
      ? 'Create your admin account'
      : 'Sign in to your account';

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full space-y-8 p-8">
        <div>
          <h1 className="text-center text-3xl font-bold text-gray-900">
            Quickly
          </h1>
          <h2 className="mt-2 text-center text-lg text-gray-600">
            {heading}
          </h2>
        </div>

        {needsSetupUnlock ? (
          <form className="space-y-4" onSubmit={handleUnlock} noValidate>
            <SetupCodeField
              id="setup-unlock-code"
              value={setupCode}
              onChange={setSetupCode}
              disabled={unlockBusy}
              hint
            />
            {unlockError && (
              <p className="text-sm text-red-600" role="alert">{unlockError}</p>
            )}
            <button
              type="submit"
              disabled={unlockBusy || !hasSetupCode}
              className="w-full py-2.5 px-4 rounded-lg text-sm font-medium text-white bg-gray-900 hover:bg-gray-800 disabled:opacity-50"
            >
              {unlockBusy ? 'Checking…' : 'Continue'}
            </button>
          </form>
        ) : isFirstUser ? (
          <div className="space-y-4">
            <form className="space-y-3" onSubmit={handleCreateAdmin} noValidate>
              <div className="space-y-1 text-left">
                <label htmlFor="register-email" className="block text-sm font-medium text-gray-700">
                  Email
                </label>
                <input
                  id="register-email"
                  type="email"
                  value={regEmail}
                  onChange={e => setRegEmail(e.target.value)}
                  className={INPUT_CLASS}
                  autoComplete="email"
                  disabled={registerBusy}
                />
              </div>
              <div className="space-y-1 text-left">
                <label htmlFor="register-username" className="block text-sm font-medium text-gray-700">
                  Username
                </label>
                <input
                  id="register-username"
                  type="text"
                  value={regUsername}
                  onChange={e => setRegUsername(e.target.value)}
                  className={INPUT_CLASS}
                  autoComplete="username"
                  disabled={registerBusy}
                />
              </div>
              <div className="space-y-1 text-left">
                <label htmlFor="register-password" className="block text-sm font-medium text-gray-700">
                  Password
                </label>
                <input
                  id="register-password"
                  type="password"
                  value={regPassword}
                  onChange={e => setRegPassword(e.target.value)}
                  className={INPUT_CLASS}
                  autoComplete="new-password"
                  disabled={registerBusy}
                />
              </div>
              <div className="space-y-1 text-left">
                <label htmlFor="register-confirm" className="block text-sm font-medium text-gray-700">
                  Confirm password
                </label>
                <input
                  id="register-confirm"
                  type="password"
                  value={regConfirm}
                  onChange={e => setRegConfirm(e.target.value)}
                  className={INPUT_CLASS}
                  autoComplete="new-password"
                  disabled={registerBusy}
                />
              </div>
              {registerError && <p className="text-sm text-red-600" role="alert">{registerError}</p>}
              <button
                type="submit"
                disabled={registerBusy}
                className="w-full py-2.5 px-4 rounded-lg text-sm font-medium text-white bg-gray-900 hover:bg-gray-800 disabled:opacity-50"
              >
                {registerBusy ? 'Creating account…' : 'Create admin account'}
              </button>
            </form>

            <p className="text-center text-sm text-gray-500">
              The first account created becomes the admin.
            </p>

            <OAuthButtons
              isFirstUser={isFirstUser}
              query={oauthQuery}
              disabled={oauthDisabled}
              google={showGoogle}
              microsoft={showMicrosoft}
            />

            <div className="mt-10 pt-8 border-t border-gray-200 space-y-3">
              {!restoreExpanded ? (
                <div className="text-center">
                  <button
                    type="button"
                    onClick={() => setRestoreExpanded(true)}
                    className="text-sm font-medium text-blue-700 hover:text-blue-800 underline underline-offset-2"
                  >
                    Want to restore from a backup?
                  </button>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-gray-800">Restore from backup</p>
                    <button
                      type="button"
                      onClick={() => {
                        setRestoreExpanded(false);
                        clearRestoreWizard();
                      }}
                      className="text-xs text-gray-500 hover:text-gray-700 underline"
                    >
                      Hide
                    </button>
                  </div>
                  <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                    Use a Quickly backup file (<code className="text-[11px]">.qbk</code>). If the backup is encrypted, you need the password — losing it
                    means the data in that file is unrecoverable. The optional hint is stored in plain text in the file.
                  </p>

                  <div className="flex items-stretch gap-2">
                    <FileUploadArea
                      key={restoreFileKey}
                      size="full"
                      className="text-xs flex-1 min-w-0"
                      accept=".qbk,application/octet-stream"
                      disabled={!canRestore || restoreMetaBusy || restorePreviewBusy || restoreExecuteBusy}
                      onChange={e => {
                        setRestoreMsg(null);
                        setRestorePreview(null);
                        setRestoreMeta(null);
                        setRestoreFile(e.target.files?.[0] || null);
                      }}
                    >
                      {restoreFile ? (
                        <span className="truncate text-gray-800">{restoreFile.name}</span>
                      ) : (
                        <span className="text-gray-500">Choose backup file (.qbk)</span>
                      )}
                    </FileUploadArea>
                    {restoreFile ? (
                      <button
                        type="button"
                        title="Remove file"
                        onClick={clearRestoreWizard}
                        className="shrink-0 px-3 rounded-lg border border-gray-300 text-sm font-medium text-gray-600 bg-white hover:bg-gray-50"
                        disabled={restoreMetaBusy || restorePreviewBusy || restoreExecuteBusy}
                      >
                        ×
                      </button>
                    ) : null}
                  </div>

                  {restoreMetaBusy && (
                    <p className="text-center text-xs text-gray-500">Reading backup…</p>
                  )}

                  {restoreMeta && !restoreMetaBusy && (
                    <div className="rounded-lg border border-gray-200 bg-white p-3 text-xs space-y-2 text-left">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <p className="font-semibold text-gray-800">This backup</p>
                          <ul className="text-gray-600 space-y-0.5">
                            <li>When: {restoreMeta.backup_preview?.backed_up_at ? new Date(restoreMeta.backup_preview.backed_up_at).toLocaleString() : '—'}</li>
                            <li>Leads: {restoreMeta.backup_preview?.lead_count ?? '—'}</li>
                            <li>Inboxes: {restoreMeta.backup_preview?.inbox_count ?? '—'}</li>
                            <li>Campaigns: {restoreMeta.backup_preview?.campaign_count ?? '—'}</li>
                            <li>Users: {restoreMeta.backup_preview?.user_count ?? '—'}</li>
                            <li>Admins: {(restoreMeta.backup_preview?.admin_emails || []).join(', ') || '—'}</li>
                            <li>Encrypted: {restoreMeta.encrypted ? 'yes' : 'no'}</li>
                          </ul>
                        </div>
                        <div>
                          <p className="font-semibold text-gray-800">Current database (will be replaced)</p>
                          <ul className="text-gray-600 space-y-0.5">
                            <li>Leads: {restoreMeta.current_database?.lead_count ?? '—'}</li>
                            <li>Inboxes: {restoreMeta.current_database?.inbox_count ?? '—'}</li>
                            <li>Campaigns: {restoreMeta.current_database?.campaign_count ?? '—'}</li>
                            <li>Users: {restoreMeta.current_database?.user_count ?? '—'}</li>
                            <li>Admins: {(restoreMeta.current_database?.admin_emails || []).join(', ') || '—'}</li>
                          </ul>
                        </div>
                      </div>
                      {restoreMeta.password_hint ? (
                        <p className="text-gray-500">
                          Hint: <span className="font-mono">{restoreMeta.password_hint}</span>
                        </p>
                      ) : null}
                      <p className="text-amber-800 border-t border-amber-100 pt-2 mt-2">
                        Confirm only if this is the correct backup.
                      </p>
                    </div>
                  )}

                  {restoreMeta && !restorePreview && !restoreMeta.encrypted && (
                    <button
                      type="button"
                      disabled={!canRestore || restorePreviewBusy || restoreMetaBusy}
                      className="w-full py-2 px-4 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                      onClick={async () => {
                        if (!restoreFile) return;
                        setRestorePreviewBusy(true);
                        setRestoreMsg(null);
                        setRestorePreview(null);
                        try {
                          const form = new FormData();
                          form.append('file', restoreFile);
                          form.append('setup_code', setupCodeValue);
                          const res = await fetch('/api/auth/restore-setup/preview', { method: 'POST', body: form });
                          const text = await res.text();
                          let data;
                          try {
                            data = JSON.parse(text);
                          } catch {
                            data = { detail: text || res.statusText };
                          }
                          if (!res.ok) {
                            throw new Error(parseDetailMessage(text) || res.statusText);
                          }
                          setRestorePreview(data);
                        } catch (e) {
                          setRestoreMsg({ type: 'err', text: e.message || 'Could not verify backup' });
                        } finally {
                          setRestorePreviewBusy(false);
                        }
                      }}
                    >
                      {restorePreviewBusy ? 'Checking…' : 'Verify backup'}
                    </button>
                  )}

                  {restoreMeta && restoreMeta.encrypted && !restorePreview && (
                    <>
                      <input
                        type="password"
                        placeholder={`Backup password (at least ${BACKUP_MIN_PASSWORD_LEN} characters)`}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                        value={restorePassword}
                        onChange={e => setRestorePassword(e.target.value)}
                        disabled={!canRestore || restorePreviewBusy || restoreExecuteBusy}
                        autoComplete="off"
                      />
                      <button
                        type="button"
                        disabled={
                          !canRestore ||
                          restorePreviewBusy ||
                          !restoreFile ||
                          restorePassword.length < BACKUP_MIN_PASSWORD_LEN
                        }
                        className="w-full py-2 px-4 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                        onClick={async () => {
                          if (!restoreFile) return;
                          setRestorePreviewBusy(true);
                          setRestoreMsg(null);
                          setRestorePreview(null);
                          try {
                            const form = new FormData();
                            form.append('file', restoreFile);
                            form.append('password', restorePassword);
                            form.append('setup_code', setupCodeValue);
                            const res = await fetch('/api/auth/restore-setup/preview', { method: 'POST', body: form });
                            const text = await res.text();
                            let data;
                            try {
                              data = JSON.parse(text);
                            } catch {
                              data = { detail: text || res.statusText };
                            }
                            if (!res.ok) {
                              throw new Error(parseDetailMessage(text) || res.statusText);
                            }
                            setRestorePreview(data);
                          } catch (e) {
                            setRestoreMsg({ type: 'err', text: e.message || 'Wrong password or invalid backup' });
                          } finally {
                            setRestorePreviewBusy(false);
                          }
                        }}
                      >
                        {restorePreviewBusy ? 'Checking…' : 'Verify password'}
                      </button>
                    </>
                  )}

                  {restorePreview && (
                    <div className="rounded-lg border border-gray-200 bg-white p-3 text-xs space-y-2 text-left">
                      <p className="font-semibold text-gray-800">Verified — full details</p>
                      <ul className="text-gray-600 space-y-0.5">
                        <li>When: {restorePreview.backup?.backed_up_at ? new Date(restorePreview.backup.backed_up_at).toLocaleString() : '—'}</li>
                        <li>Leads: {restorePreview.backup?.lead_count ?? '—'}</li>
                        <li>Inboxes: {restorePreview.backup?.inbox_count ?? '—'}</li>
                        <li>Campaigns: {restorePreview.backup?.campaign_count ?? '—'}</li>
                        <li>Users: {restorePreview.backup?.user_count ?? '—'}</li>
                        <li>Admins: {(restorePreview.backup?.admin_emails || []).join(', ') || '—'}</li>
                      </ul>
                      <button
                        type="button"
                        disabled={!canRestore || restoreExecuteBusy}
                        className="w-full py-2 px-4 rounded-lg text-sm font-medium text-white bg-gray-900 hover:bg-gray-800 disabled:opacity-50"
                        onClick={async () => {
                          if (
                            !window.confirm(
                              'Replace the database with this backup? This cannot be undone.',
                            )
                          ) {
                            return;
                          }
                          setRestoreExecuteBusy(true);
                          setRestoreMsg(null);
                          try {
                            const res = await fetch('/api/auth/restore-setup/execute', {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json' },
                              body: JSON.stringify({
                                restore_token: restorePreview.restore_token,
                                setup_code: setupCodeValue,
                              }),
                            });
                            const text = await res.text();
                            let data;
                            try {
                              data = JSON.parse(text);
                            } catch {
                              data = { detail: text || res.statusText };
                            }
                            if (!res.ok) {
                              throw new Error(parseDetailMessage(text) || res.statusText);
                            }
                            await checkSetup();
                            setRestoreMsg({ type: 'ok', text: data.detail || 'Restore complete. Reloading…' });
                            setRestorePreview(null);
                            if (res.headers.get('X-Quickly-Reload') === '1') {
                              setTimeout(() => window.location.reload(), 300);
                            }
                          } catch (e) {
                            setRestoreMsg({ type: 'err', text: e.message || 'Restore failed' });
                          } finally {
                            setRestoreExecuteBusy(false);
                          }
                        }}
                      >
                        {restoreExecuteBusy ? 'Restoring…' : 'Confirm and restore'}
                      </button>
                    </div>
                  )}

                  {restoreMsg && (
                    <p
                      className={`text-center text-xs ${restoreMsg.type === 'ok' ? 'text-green-700' : 'text-red-600'}`}
                    >
                      {restoreMsg.text}
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="mt-8 space-y-4">
            <form className="space-y-3" onSubmit={handleSignIn} noValidate>
              <div className="space-y-1 text-left">
                <label htmlFor="login-username" className="block text-sm font-medium text-gray-700">
                  Email or username
                </label>
                <input
                  id="login-username"
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  className={INPUT_CLASS}
                  autoComplete="username"
                  disabled={loginBusy}
                />
              </div>
              <div className="space-y-1 text-left">
                <label htmlFor="login-password" className="block text-sm font-medium text-gray-700">
                  Password
                </label>
                <input
                  id="login-password"
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  className={INPUT_CLASS}
                  autoComplete="current-password"
                  disabled={loginBusy}
                />
              </div>
              {loginError && <p className="text-sm text-red-600" role="alert">{loginError}</p>}
              <button
                type="submit"
                disabled={loginBusy}
                className="w-full py-2.5 px-4 rounded-lg text-sm font-medium text-white bg-gray-900 hover:bg-gray-800 disabled:opacity-50"
              >
                {loginBusy ? 'Signing in…' : 'Sign in'}
              </button>
            </form>

            <OAuthButtons
              isFirstUser={isFirstUser}
              query=""
              disabled={oauthDisabled}
              google={showGoogle}
              microsoft={showMicrosoft}
            />
          </div>
        )}
      </div>
    </div>
  );
}
