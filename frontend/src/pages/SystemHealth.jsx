import { useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useSystemHealth } from '../context/SystemHealthContext';
import {
  RiRefreshLine,
  RiCheckboxCircleLine,
  RiErrorWarningLine,
  RiCloseCircleLine,
  RiQuestionLine,
  RiNotificationOffLine,
  RiNotificationLine,
  RiGoogleLine,
  RiWindowsLine,
  RiInboxLine,
  RiRobot2Line,
  RiSettings3Line,
  RiArrowRightSLine,
  RiShieldCheckLine,
  RiTimeLine,
  RiCheckLine,
  RiGlobalLine,
} from 'react-icons/ri';

/* ─── helpers ───────────────────────────────────────────────────────────── */

function statusColor(status) {
  switch (status) {
    case 'error':   return { dot: 'bg-red-500',    text: 'text-red-500',    border: 'border-red-200',    bg: 'bg-red-50',    badge: 'bg-red-100 text-red-700' };
    case 'warning': return { dot: 'bg-yellow-400', text: 'text-yellow-600', border: 'border-yellow-200', bg: 'bg-yellow-50', badge: 'bg-yellow-100 text-yellow-700' };
    case 'ok':      return { dot: 'bg-green-500',  text: 'text-green-600',  border: 'border-green-200',  bg: 'bg-green-50',  badge: 'bg-green-100 text-green-700' };
    default:        return { dot: 'bg-gray-400',   text: 'text-gray-500',   border: 'border-gray-200',   bg: 'bg-gray-50',   badge: 'bg-gray-100 text-gray-600' };
  }
}

function statusLabel(status) {
  switch (status) {
    case 'error':   return 'Error';
    case 'warning': return 'Warning';
    case 'ok':      return 'Healthy';
    default:        return 'Unknown';
  }
}

function StatusIcon({ status, size = 18 }) {
  const cls = statusColor(status).text;
  switch (status) {
    case 'error':   return <RiCloseCircleLine    size={size} className={cls} />;
    case 'warning': return <RiErrorWarningLine   size={size} className={cls} />;
    case 'ok':      return <RiCheckboxCircleLine size={size} className={cls} />;
    default:        return <RiQuestionLine       size={size} className={cls} />;
  }
}

function IssueLevelIcon({ level }) {
  if (level === 'error')   return <RiCloseCircleLine   size={14} className="text-red-500    flex-shrink-0 mt-0.5" />;
  if (level === 'warning') return <RiErrorWarningLine  size={14} className="text-yellow-500 flex-shrink-0 mt-0.5" />;
  return                          <RiCheckLine          size={14} className="text-green-500  flex-shrink-0 mt-0.5" />;
}

function CategoryIcon({ icon, size = 20 }) {
  const cls = 'flex-shrink-0';
  switch (icon) {
    case 'google':       return <RiGoogleLine     size={size} className={cls} />;
    case 'microsoft':   return <RiWindowsLine    size={size} className={cls} />;
    case 'inbox':       return <RiInboxLine      size={size} className={cls} />;
    case 'sync':        return <RiRefreshLine    size={size} className={cls} />;
    case 'ai':          return <RiRobot2Line     size={size} className={cls} />;
    case 'settings':    return <RiSettings3Line  size={size} className={cls} />;
    case 'verify':      return <RiShieldCheckLine size={size} className={cls} />;
    case 'domain':      return <RiGlobalLine     size={size} className={cls} />;
    default:            return <RiShieldCheckLine size={size} className={cls} />;
  }
}

function RelativeTime({ date }) {
  if (!date) return null;
  const delta = Math.round((Date.now() - date) / 1000);
  let label;
  if (delta < 10)  label = 'just now';
  else if (delta < 60)  label = `${delta}s ago`;
  else if (delta < 3600) label = `${Math.floor(delta / 60)}m ago`;
  else label = `${Math.floor(delta / 3600)}h ago`;
  return <span className="text-gray-400 text-xs">{label}</span>;
}

/* ─── Overall summary header ────────────────────────────────────────────── */

function OverallHeader({ status, loading, lastChecked, onRefresh, issueCount }) {
  const col = statusColor(status);

  const heroMessages = {
    error:   { headline: 'Action Required',       sub: 'One or more services need attention.' },
    warning: { headline: 'Warnings Detected',     sub: 'Some things could be improved.' },
    ok:      { headline: 'Everything looks good', sub: 'All monitored services are healthy.' },
    unknown: { headline: 'Status unknown',        sub: 'Could not fetch health data.' },
  };
  const msg = heroMessages[status] || heroMessages.unknown;

  return (
    <div className={`rounded-xl border ${col.border} ${col.bg} p-6 flex items-center justify-between gap-4`}>
      <div className="flex items-center gap-4">
        {/* animated dot */}
        <div className="relative flex-shrink-0">
          <span className={`block w-5 h-5 rounded-full ${col.dot}`} />
          {(status === 'error' || status === 'warning') && (
            <span className={`absolute inset-0 rounded-full ${col.dot} animate-ping opacity-50`} />
          )}
        </div>
        <div>
          <h1 className={`text-xl font-semibold ${col.text}`}>{msg.headline}</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {status === 'ok'
              ? msg.sub
              : `${issueCount} issue${issueCount !== 1 ? 's' : ''} found — ${msg.sub}`}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3 flex-shrink-0">
        {lastChecked && (
          <div className="hidden sm:flex items-center gap-1.5 text-gray-400 text-xs">
            <RiTimeLine size={13} />
            <span>Checked <RelativeTime date={lastChecked} /></span>
          </div>
        )}
        <button
          onClick={onRefresh}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-300 bg-white text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          <RiRefreshLine size={15} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Checking…' : 'Refresh'}
        </button>
      </div>
    </div>
  );
}

/* ─── Individual check card ─────────────────────────────────────────────── */

function CheckCard({ check, muted, onToggleMute }) {
  const isMuted = muted.has(check.id);
  const displayStatus = isMuted ? 'ok' : check.status;
  const col = statusColor(displayStatus);
  const isHealthy = check.status === 'ok';

  return (
    <div
      className={`rounded-xl border bg-white shadow-sm flex flex-col overflow-hidden transition-opacity ${
        isMuted ? 'opacity-60' : ''
      } ${col.border}`}
    >
      {/* Card header */}
      <div className={`flex items-center justify-between px-4 py-3 ${col.bg} border-b ${col.border}`}>
        <div className="flex items-center gap-2.5">
          <div className={col.text}>
            <CategoryIcon icon={check.icon} size={18} />
          </div>
          <span className="font-semibold text-gray-800 text-sm">{check.label}</span>
          {isMuted && (
            <span className="text-xs text-gray-400 italic">(muted)</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${col.badge}`}>
            {isMuted ? 'Muted' : statusLabel(check.status)}
          </span>
          <button
            onClick={() => onToggleMute(check.id)}
            title={isMuted ? 'Unmute this check' : 'Mute this check (won\'t affect overall health)'}
            className="text-gray-400 hover:text-gray-600 transition-colors p-0.5 rounded"
          >
            {isMuted
              ? <RiNotificationLine     size={16} />
              : <RiNotificationOffLine  size={16} />}
          </button>
        </div>
      </div>

      {/* Card body */}
      <div className="px-4 py-3 flex-1 flex flex-col gap-3">
        {/* Summary line */}
        <p className="text-sm text-gray-500">{check.detail}</p>

        {/* Issues list */}
        {check.issues.length > 0 && !isMuted ? (
          <ul className="space-y-2">
            {check.issues.map((issue, i) => (
              <li key={i} className="flex flex-col gap-1">
                <div className="flex items-start gap-1.5">
                  <IssueLevelIcon level={issue.level} />
                  <span className="text-sm text-gray-700 leading-snug">{issue.text}</span>
                </div>
                {issue.fix && (
                  <div className="ml-5 flex items-start gap-1.5 flex-wrap">
                    <span className="text-xs text-gray-400">→</span>
                    <span className="text-xs text-gray-500 leading-snug">{issue.fix}</span>
                    {issue.action && (
                      <Link
                        to={issue.action.to}
                        className="text-xs text-teal-600 hover:text-teal-700 font-medium inline-flex items-center gap-0.5 flex-shrink-0"
                      >
                        {issue.action.label}
                        <RiArrowRightSLine size={12} />
                      </Link>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : isHealthy || isMuted ? (
          <div className="flex items-center gap-1.5 text-green-600 text-xs font-medium">
            <RiCheckboxCircleLine size={14} />
            <span>{isMuted ? 'Warnings suppressed for this category' : 'No issues detected'}</span>
          </div>
        ) : null}

        {/* Extra meta for specific checks */}
        <CheckMeta check={check} />
      </div>
    </div>
  );
}

/* ─── Per-check extra metadata ──────────────────────────────────────────── */

function TokenStatusBadge({ status }) {
  if (status === 'valid')         return <span className="text-xs bg-green-100  text-green-700  px-1.5 py-0.5 rounded-full font-medium">Valid</span>;
  if (status === 'expiring_soon') return <span className="text-xs bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded-full font-medium">Expiring</span>;
  return                                  <span className="text-xs bg-red-100    text-red-700    px-1.5 py-0.5 rounded-full font-medium">Expired</span>;
}

function CheckMeta({ check }) {
  if (check.id === 'google_oauth' && check.meta.accounts?.length > 0) {
    return (
      <div className="mt-1 space-y-1.5 border-t border-gray-100 pt-2">
        <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">Connected accounts</p>
        {check.meta.accounts.map(acc => (
          <div key={acc.id} className="flex items-center justify-between gap-2 text-sm">
            <span className="text-gray-700 truncate">{acc.google_email}</span>
            <TokenStatusBadge status={acc.token_status} />
          </div>
        ))}
      </div>
    );
  }

  if (check.id === 'microsoft_oauth' && check.meta.accounts?.length > 0) {
    return (
      <div className="mt-1 space-y-1.5 border-t border-gray-100 pt-2">
        <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">Connected accounts</p>
        {check.meta.accounts.map(acc => (
          <div key={acc.id} className="flex items-center justify-between gap-2 text-sm">
            <span className="text-gray-700 truncate">{acc.microsoft_email}</span>
            <TokenStatusBadge status={acc.token_status} />
          </div>
        ))}
      </div>
    );
  }

  if (check.id === 'inbox_status' && check.meta.inboxList?.length > 0) {
    return (
      <div className="mt-1 space-y-1.5 border-t border-gray-100 pt-2">
        <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">Inboxes</p>
        {check.meta.inboxList.map(inbox => (
          <div key={inbox.id} className="flex items-center justify-between gap-2 text-sm">
            <span className="text-gray-700 truncate">{inbox.display_name || inbox.email}</span>
            {inbox.paused
              ? <span className="text-xs bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded-full font-medium">Paused</span>
              : <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full font-medium">Active</span>
            }
          </div>
        ))}
      </div>
    );
  }

  if (check.id === 'unibox_sync') {
    return (
      <div className="mt-1 border-t border-gray-100 pt-2 space-y-1.5">
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">Push notifications</span>
          {check.meta.pushEnabled
            ? <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full font-medium">Enabled</span>
            : <span className="text-xs bg-gray-100  text-gray-600  px-1.5 py-0.5 rounded-full font-medium">Polling</span>
          }
        </div>
        {check.meta.syncInProgress && (
          <div className="flex items-center gap-1.5 text-xs text-blue-600">
            <RiRefreshLine size={13} className="animate-spin" />
            <span>Sync in progress for {check.meta.inflightIds.length} inbox{check.meta.inflightIds.length !== 1 ? 'es' : ''}</span>
          </div>
        )}
      </div>
    );
  }

  if (check.id === 'ai_features' && check.meta.allFeatures?.length > 0) {
    return (
      <div className="mt-1 space-y-1.5 border-t border-gray-100 pt-2">
        <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">Features</p>
        {check.meta.allFeatures.map(f => (
          <div key={f.id} className="flex flex-col gap-0.5">
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className={`truncate ${f.enabled ? 'text-gray-700' : 'text-gray-400'}`}>{f.label}</span>
              <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium flex-shrink-0 ${
                !f.enabled
                  ? 'bg-gray-100 text-gray-500'
                  : !f.api_key_set
                    ? 'bg-yellow-100 text-yellow-700'
                    : !f.connection_tested
                      ? 'bg-orange-100 text-orange-700'
                      : f.last_error
                        ? 'bg-red-100 text-red-700'
                        : 'bg-green-100 text-green-700'
              }`}>
                {!f.enabled ? 'Disabled' : !f.api_key_set ? 'Missing key' : !f.connection_tested ? 'Not tested' : f.last_error ? 'Error' : 'Healthy'}
              </span>
            </div>
            {f.enabled && f.last_error && (
              <p className="text-xs text-red-400 truncate ml-1">
                {f.last_error_at ? new Date(f.last_error_at).toLocaleString() + ': ' : ''}{f.last_error}
              </p>
            )}
          </div>
        ))}
      </div>
    );
  }

  if (check.id === 'email_verification') {
    const ev = check.meta.emailVerification;
    if (!ev) return null;
    return (
      <div className="mt-1 border-t border-gray-100 pt-2 space-y-1.5">
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">Status</span>
          {!ev.enabled
            ? <span className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded-full font-medium">Disabled</span>
            : ev.connection_tested
              ? <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full font-medium">Tested ✓</span>
              : <span className="text-xs bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded-full font-medium">Not tested</span>
          }
        </div>
        {ev.enabled && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-500">Provider</span>
            <span className="text-gray-700 text-xs font-medium">{ev.provider}</span>
          </div>
        )}
        {ev.last_error && (
          <p className="text-xs text-red-400 truncate">
            {ev.last_error_at ? new Date(ev.last_error_at).toLocaleString() + ': ' : ''}{ev.last_error}
          </p>
        )}
      </div>
    );
  }

  if (check.id === 'active_settings') {
    return (
      <div className="mt-1 border-t border-gray-100 pt-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">Test Mode</span>
          {check.meta.testMode
            ? <span className="text-xs bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded-full font-medium">Active</span>
            : <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full font-medium">Off</span>
          }
        </div>
      </div>
    );
  }

  if (check.id === 'tracking_domains' && check.meta.inboxesWithDomains?.length > 0) {
    return (
      <div className="mt-1 space-y-2 border-t border-gray-100 pt-2">
        <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">Domains</p>
        {check.meta.inboxesWithDomains.map(inbox => (
          <div key={inbox.id} className="flex flex-col gap-0.5">
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className="text-gray-700 truncate">{inbox.display_name || inbox.email}</span>
              {inbox.tracking_domain_status === 'ok'
                ? <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full font-medium">Connected</span>
                : <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full font-medium">Not connected</span>
              }
            </div>
            <p className="text-xs text-gray-400 ml-1">{inbox.tracking_domain}</p>
          </div>
        ))}
      </div>
    );
  }

  return null;
}

/* ─── Main page ─────────────────────────────────────────────────────────── */

export default function SystemHealth() {
  const { checks, loading, lastChecked, fetchError, refresh, muted, toggleMute, overallStatus } = useSystemHealth();

  const issueCount = checks.reduce((n, c) => {
    if (muted.has(c.id)) return n;
    return n + c.issues.length;
  }, 0);

  const mutedCount = checks.filter(c => muted.has(c.id)).length;

  const STATUS_RANK = { error: 3, warning: 2, ok: 1, unknown: 0 };
  const sortedChecks = [...checks].sort((a, b) => {
    const aIsMuted = muted.has(a.id);
    const bIsMuted = muted.has(b.id);
    if (aIsMuted !== bIsMuted) return aIsMuted ? 1 : -1;
    return (STATUS_RANK[b.status] ?? 0) - (STATUS_RANK[a.status] ?? 0);
  });

  const unmuteAll = useCallback(() => {
    checks.forEach(c => { if (muted.has(c.id)) toggleMute(c.id); });
  }, [checks, muted, toggleMute]);

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Page title */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">System Health</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Monitor connection status, OAuth tokens, and feature configuration.
          </p>
        </div>
        {mutedCount > 0 && (
          <button
            onClick={unmuteAll}
            className="text-xs text-teal-600 hover:text-teal-700 border border-teal-200 rounded-lg px-3 py-1.5 transition-colors"
          >
            Unmute all ({mutedCount})
          </button>
        )}
      </div>

      {/* Error fetching */}
      {fetchError && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
          Failed to fetch health data: {fetchError}
        </div>
      )}

      {/* Overall summary */}
      <OverallHeader
        status={overallStatus}
        loading={loading}
        lastChecked={lastChecked}
        onRefresh={refresh}
        issueCount={issueCount}
      />

      {/* Check cards grid */}
      {checks.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {sortedChecks.map(check => (
            <CheckCard
              key={check.id}
              check={check}
              muted={muted}
              onToggleMute={toggleMute}
            />
          ))}
        </div>
      ) : loading ? (
        <div className="text-center py-16 text-gray-400">
          <RiRefreshLine size={32} className="animate-spin mx-auto mb-3" />
          <p>Loading health data…</p>
        </div>
      ) : null}

      {/* Footer note */}
      <p className="text-xs text-center text-gray-400 pb-4">
        Health data refreshes automatically every 5 minutes and when you click Refresh. 
        Muted checks are stored locally and do not affect other users.
      </p>
    </div>
  );
}
