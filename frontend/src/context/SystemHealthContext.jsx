import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api';
import { useAuth } from './AuthContext';

const SystemHealthContext = createContext(null);

const MUTE_KEY = 'quickly_health_muted_v1';
const AUTO_REFRESH_MS = 5 * 60 * 1000;

function loadMuted() {
  try {
    return new Set(JSON.parse(localStorage.getItem(MUTE_KEY) || '[]'));
  } catch {
    return new Set();
  }
}

function saveMuted(set) {
  localStorage.setItem(MUTE_KEY, JSON.stringify([...set]));
}

function computeO365TokenStatus(tokenExpiry) {
  if (!tokenExpiry) return 'expired';
  const expiry = new Date(tokenExpiry);
  const now = new Date();
  const msLeft = expiry - now;
  if (msLeft <= 0) return 'expired';
  if (msLeft < 24 * 60 * 60 * 1000) return 'expiring_soon';
  return 'valid';
}

async function fetchAllHealthData() {
  return api.get('/system-health');
}
function buildChecks(d) {
  if (!d) return [];
  const {
    google_oauth,
    microsoft_oauth,
    inboxes: inboxList = [],
    unibox_sync,
    ai_features: rawAi = [],
    email_verification: evData = null,
    flags,
    beacon_reconciliation: beaconReconciliation = null,
  } = d;
  const checks = [];

  /* ── Google OAuth ─────────────────────────────────────────────── */
  const googleAccounts = google_oauth?.accounts || [];
  const hasGmailInboxes = inboxList.some(i => i.provider === 'gmail');
  let googleStatus = 'ok';
  const googleIssues = [];

  if (!google_oauth) {
    googleStatus = 'unknown';
  } else {
    if (!google_oauth.configured && hasGmailInboxes) {
      googleStatus = 'error';
      googleIssues.push({
        level: 'error',
        text: 'Google OAuth credentials are not configured, but Gmail inboxes exist.',
        fix: 'Open Settings and add your Google OAuth Client ID / Secret.',
        action: { label: 'Open Settings', to: '/settings#general' },
      });
    }
    googleAccounts.forEach(acc => {
      if (acc.token_status === 'expired') {
        googleStatus = 'error';
        googleIssues.push({
          level: 'error',
          text: `Token expired for ${acc.google_email} (${acc.inbox_display_name || ''})`,
          fix: 'Reconnect this account from the Inboxes page.',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
      }
      if (acc.login_status === 'invalid') {
        googleStatus = 'error';
        googleIssues.push({
          level: 'error',
          text: `Login is no longer valid for ${acc.google_email}`,
          fix: 'Reconnect this account from the Inboxes page.',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
      }
      if (acc.missing_scopes?.length > 0) {
        googleStatus = 'error';
        googleIssues.push({
          level: 'error',
          text: `Missing required send scope for ${acc.google_email}: ${acc.missing_scopes.map(s => s.name).join(', ')}`,
          fix: 'Disconnect and reconnect the account, granting the required Gmail send permission.',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
      }
    });
  }

  checks.push({
    id: 'google_oauth',
    label: 'Google OAuth',
    icon: 'google',
    status: googleStatus,
    issues: googleIssues,
    meta: {
      configured: google_oauth?.configured,
      accountCount: googleAccounts.length,
      accounts: googleAccounts,
    },
    detail: googleAccounts.length > 0
      ? `${googleAccounts.length} account${googleAccounts.length !== 1 ? 's' : ''} connected`
      : 'No accounts connected',
  });

  /* ── Microsoft OAuth ──────────────────────────────────────────── */
  const o365Accounts = microsoft_oauth?.accounts || [];
  const hasO365Inboxes = inboxList.some(i => i.provider === 'office365');
  let msStatus = 'ok';
  const msIssues = [];

  if (!microsoft_oauth) {
    msStatus = 'unknown';
  } else {
    if (!microsoft_oauth.configured && hasO365Inboxes) {
      msStatus = 'error';
      msIssues.push({
        level: 'error',
        text: 'Microsoft OAuth credentials are not configured, but Office 365 inboxes exist.',
        fix: 'Open Settings and add your Azure AD App credentials.',
        action: { label: 'Open Settings', to: '/settings#general' },
      });
    }
    o365Accounts.forEach(acc => {
      if (acc.token_status === 'expired') {
        msStatus = 'error';
        msIssues.push({
          level: 'error',
          text: `Token expired for ${acc.microsoft_email}`,
          fix: 'Reconnect this Office 365 account from the Inboxes page.',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
      }
      if (acc.login_status === 'invalid') {
        msStatus = 'error';
        msIssues.push({
          level: 'error',
          text: `Login is no longer valid for ${acc.microsoft_email}`,
          fix: 'Reconnect this Office 365 account from the Inboxes page.',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
      }
    });
  }

  checks.push({
    id: 'microsoft_oauth',
    label: 'Microsoft OAuth',
    icon: 'microsoft',
    status: msStatus,
    issues: msIssues,
    meta: {
      configured: microsoft_oauth?.configured,
      accountCount: o365Accounts.length,
      accounts: o365Accounts,
    },
    detail: o365Accounts.length > 0
      ? `${o365Accounts.length} account${o365Accounts.length !== 1 ? 's' : ''} connected`
      : 'No accounts connected',
  });

  /* ── Inbox Status ─────────────────────────────────────────────── */
  let inboxStatLvl = 'ok';
  const inboxIssues = [];

  inboxList.forEach(inbox => {
    if (inbox.paused) {
      if (inboxStatLvl === 'ok') inboxStatLvl = 'warning';
      inboxIssues.push({
        level: 'warning',
        text: `"${inbox.display_name || inbox.email}" is paused`,
        fix: 'Unpause this inbox from the Inboxes page when you are ready to resume sending.',
        action: { label: 'Open Inboxes', to: '/inboxes' },
      });
    }
  });

  checks.push({
    id: 'inbox_status',
    label: 'Inbox Status',
    icon: 'inbox',
    status: inboxStatLvl,
    issues: inboxIssues,
    meta: { inboxList },
    detail: inboxList.length === 0
      ? 'No inboxes configured'
      : `${inboxList.length} inbox${inboxList.length !== 1 ? 'es' : ''} — ${inboxList.filter(i => !i.paused).length} active`,
  });

  /* ── Custom Tracking Domains ──────────────────────────────────────── */
  const inboxesWithDomains = inboxList.filter(i => i.tracking_domain);
  if (inboxesWithDomains.length > 0) {
    let domainStatLvl = 'ok';
    const domainIssues = [];
    inboxesWithDomains.forEach(inbox => {
      if (inbox.tracking_domain_status !== 'ok') {
        domainStatLvl = 'error';
        const name = inbox.display_name || inbox.email;
        domainIssues.push({
          level: 'error',
          text: `Domain "${inbox.tracking_domain}" for "${name}" is not responding over HTTPS.`,
          fix: 'Make sure the CNAME record points to this server and that HTTPS (TLS) is working for the domain.',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
      }
    });
    checks.push({
      id: 'tracking_domains',
      label: 'Custom Tracking Domains',
      icon: 'domain',
      status: domainStatLvl,
      issues: domainIssues,
      meta: { inboxesWithDomains },
      detail: inboxesWithDomains.length === 1
        ? `1 domain — ${inboxesWithDomains[0].tracking_domain}`
        : `${inboxesWithDomains.length} domains configured`,
    });
  }

  /* ── Beacon tracking hosts ───────────────────────────────────────── */
  const inboxesWithBeacon = inboxList.filter(i => i.beacon_connected && i.beacon_base_url);
  if (inboxesWithBeacon.length > 0) {
    let beaconStatLvl = 'ok';
    const beaconIssues = [];
    inboxesWithBeacon.forEach(inbox => {
      const name = inbox.display_name || inbox.email;
      if (inbox.beacon_status !== 'ok') {
        beaconStatLvl = 'error';
        beaconIssues.push({
          level: 'error',
          text: `Beacon at "${inbox.beacon_base_url}" for "${name}" failed the health check (not reachable or not connected to this inbox).`,
          fix: 'Confirm the Beacon service is running, the URL is correct, and Beacon is still connected (try reconnect from Inboxes if needed).',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
        return;
      }
      if (inbox.beacon_registration_ok === false) {
        if (beaconStatLvl === 'ok') beaconStatLvl = 'warning';
        beaconIssues.push({
          level: 'warning',
          text: `Beacon registration count still mismatched for "${name}" (Quickly expects ${inbox.beacon_registration_expected}, Beacon has ${inbox.beacon_registration_actual}).`,
          fix: 'The server attempted a full resync during this health check. If this persists, check connectivity to Beacon.',
          action: { label: 'Open Inboxes', to: '/inboxes' },
        });
      }
    });
    checks.push({
      id: 'beacon_tracking',
      label: 'Beacon tracking',
      icon: 'domain',
      status: beaconStatLvl,
      issues: beaconIssues,
      meta: { inboxesWithBeacon, beaconReconciliation },
      detail: inboxesWithBeacon.length === 1
        ? `1 host — ${inboxesWithBeacon[0].beacon_base_url}`
        : `${inboxesWithBeacon.length} Beacon hosts`,
    });
  }

  /* ── Unibox Sync ──────────────────────────────────────────────── */
  let uniboxStatLvl = 'ok';
  const uniboxIssues = [];

  if (unibox_sync && !unibox_sync.push_enabled) {
    uniboxStatLvl = 'warning';
    uniboxIssues.push({
      level: 'warning',
      text: 'Gmail push notifications (Pub/Sub) are not configured. Email detection uses polling instead.',
      fix: 'Set up a Google Cloud Pub/Sub topic in Settings → Setup (Gmail sync) for instant real-time email events.',
      action: { label: 'Open Settings', to: '/settings#setup' },
    });
  }
  if (unibox_sync?.initial_list_sync_in_progress) {
    if (uniboxStatLvl === 'ok') uniboxStatLvl = 'warning';
    uniboxIssues.push({
      level: 'warning',
      text: 'Initial inbox sync is currently in progress.',
      fix: null,
    });
  }

  checks.push({
    id: 'unibox_sync',
    label: 'Unibox & Email Sync',
    icon: 'sync',
    status: uniboxStatLvl,
    issues: uniboxIssues,
    meta: {
      pushEnabled: Boolean(unibox_sync?.push_enabled),
      syncInProgress: Boolean(unibox_sync?.initial_list_sync_in_progress),
      inflightIds: unibox_sync?.inflight_inbox_ids || [],
    },
    detail: unibox_sync?.push_enabled ? 'Push notifications active' : 'Polling mode',
  });

  /* ── AI Features ──────────────────────────────────────────────── */
  const enabledAiFeatures = rawAi.filter(f => f.enabled);
  let aiStatLvl = 'ok';
  const aiIssues = [];

  enabledAiFeatures.forEach(f => {
    if (!f.api_key_set) {
      if (aiStatLvl === 'ok') aiStatLvl = 'warning';
      aiIssues.push({
        level: 'warning',
        text: `"${f.label}" is enabled but no API key is configured.`,
        fix: 'Open Settings → Features → AI features to add an API key.',
        action: { label: 'Open Settings', to: '/settings#features' },
      });
    } else if (!f.connection_tested) {
      if (aiStatLvl === 'ok') aiStatLvl = 'warning';
      aiIssues.push({
        level: 'warning',
        text: `"${f.label}" is enabled but connection has not been verified.`,
        fix: 'Open Settings → Features → AI features and click "Test Connection".',
        action: { label: 'Open Settings', to: '/settings#features' },
      });
    } else if (f.last_error) {
      if (aiStatLvl !== 'error') aiStatLvl = 'error';
      aiIssues.push({
        level: 'error',
        text: `"${f.label}" failed during use: ${f.last_error}`,
        fix: 'Check your API key and quota. Re-test connection in Settings → Features → AI features.',
        action: { label: 'Open Settings', to: '/settings#features' },
      });
    }
  });

  checks.push({
    id: 'ai_features',
    label: 'AI Features',
    icon: 'ai',
    status: aiStatLvl,
    issues: aiIssues,
    meta: { enabledFeatures: enabledAiFeatures, allFeatures: rawAi },
    detail: enabledAiFeatures.length === 0
      ? 'No AI features enabled'
      : `${enabledAiFeatures.length} feature${enabledAiFeatures.length !== 1 ? 's' : ''} enabled`,
  });

  /* ── Email Verification ────────────────────────────────────────── */
  let evStatLvl = 'ok';
  const evIssues = [];

  if (evData) {
    if (evData.enabled && !evData.connection_tested) {
      evStatLvl = 'warning';
      evIssues.push({
        level: 'warning',
        text: 'Email verification is enabled but the connection has not been tested.',
        fix: 'Open Settings → Features → Email verification and run "Test Connection".',
        action: { label: 'Open Settings', to: '/settings#features' },
      });
    } else if (evData.enabled && evData.last_error) {
      evStatLvl = 'error';
      evIssues.push({
        level: 'error',
        text: `Email verification failed during use: ${evData.last_error}`,
        fix: 'Check your API key and provider configuration in Settings → Features → Email verification.',
        action: { label: 'Open Settings', to: '/settings#features' },
      });
    }
  }

  checks.push({
    id: 'email_verification',
    label: 'Email Verification',
    icon: 'verify',
    status: evStatLvl,
    issues: evIssues,
    meta: { emailVerification: evData },
    detail: !evData || !evData.enabled
      ? 'Not enabled'
      : evData.connection_tested
        ? `Active — ${evData.provider}`
        : 'Enabled but not tested',
  });

  /* ── Active Settings / Flags ──────────────────────────────────── */
  let flagsStatLvl = 'ok';
  const flagsIssues = [];

  if (flags?.test_mode) {
    flagsStatLvl = 'warning';
    flagsIssues.push({
      level: 'warning',
      text: 'Test Mode is ON — emails will not be sent to real recipients.',
      fix: 'Disable Test mode in Settings → Dev when you are ready to send live emails.',
      action: { label: 'Open Settings', to: '/settings#dev' },
    });
  }

  checks.push({
    id: 'active_settings',
    label: 'Active Settings',
    icon: 'settings',
    status: flagsStatLvl,
    issues: flagsIssues,
    meta: { testMode: flags?.test_mode },
    detail: flags?.test_mode ? 'Test mode is active' : 'Normal operation',
  });

  return checks;
}

const STATUS_RANK = { error: 3, warning: 2, ok: 1, unknown: 0 };

function computeOverall(checks, muted) {
  return checks.reduce((worst, check) => {
    if (muted.has(check.id)) return worst;
    const rank = STATUS_RANK[check.status] ?? 0;
    if (rank > (STATUS_RANK[worst] ?? 0)) return check.status;
    return worst;
  }, 'ok');
}

export function SystemHealthProvider({ children }) {
  const [rawData, setRawData] = useState(null);
  const [checks, setChecks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [lastChecked, setLastChecked] = useState(null);
  const [fetchError, setFetchError] = useState(null);
  const [muted, setMutedState] = useState(loadMuted);
  const { user } = useAuth();
  const refreshTimerRef = useRef(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const data = await fetchAllHealthData();
      setRawData(data);
      setChecks(buildChecks(data));
      setLastChecked(new Date());
    } catch (e) {
      setFetchError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    setLastChecked(null);
  }, [refresh, user]);

  useEffect(() => {
    if (!user) {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      return;
    }

    refresh();
    refreshTimerRef.current = setInterval(() => {
      refresh();
    }, AUTO_REFRESH_MS);

    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, [refresh, user]);

  const toggleMute = useCallback((checkId) => {
    setMutedState(prev => {
      const next = new Set(prev);
      if (next.has(checkId)) next.delete(checkId);
      else next.add(checkId);
      saveMuted(next);
      return next;
    });
  }, []);

  const overallStatus = computeOverall(checks, muted);

  return (
    <SystemHealthContext.Provider
      value={{ checks, loading, lastChecked, fetchError, refresh, muted, toggleMute, overallStatus, rawData }}
    >
      {children}
    </SystemHealthContext.Provider>
  );
}

export function useSystemHealth() {
  const ctx = useContext(SystemHealthContext);
  if (!ctx) throw new Error('useSystemHealth must be used inside SystemHealthProvider');
  return ctx;
}
