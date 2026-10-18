import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  RiAlertLine,
  RiArrowUpLine,
  RiCloseLine,
  RiMegaphoneLine,
  RiShieldLine,
} from 'react-icons/ri';
import { api } from '../api';

// localStorage keys (stable, shared across sessions/tabs).
const DISMISSED_ANNOUNCEMENTS_KEY = 'dismissedAnnouncementIds';
const DISMISSED_VERSION_KEY = 'dismissedVersion';
const MAX_DISMISSED_ANNOUNCEMENTS = 50;
const BODY_TRUNCATE = 800;

// security > warning > info; anything unknown ranks last.
const LEVEL_RANK = { security: 0, warning: 1, info: 2 };

// One floating card (bottom-right), mirroring the opencode-webui notice card.
const LEVEL_STYLE = {
  security: { border: 'border-red-300', title: 'text-red-700', icon: 'text-red-600', label: 'Security notice', Icon: RiShieldLine },
  warning: { border: 'border-amber-300', title: 'text-amber-700', icon: 'text-amber-500', label: 'Notice', Icon: RiAlertLine },
  info: { border: 'border-gray-200', title: 'text-gray-900', icon: 'text-blue-500', label: 'Announcement', Icon: RiMegaphoneLine },
};

function readDismissedAnnouncements() {
  try {
    const raw = localStorage.getItem(DISMISSED_ANNOUNCEMENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function readDismissedVersion() {
  try {
    return localStorage.getItem(DISMISSED_VERSION_KEY) || null;
  } catch {
    return null;
  }
}

function truncate(text, max) {
  if (!text) return '';
  const value = String(text);
  return value.length > max ? `${value.slice(0, max).trimEnd()}…` : value;
}

/** Highest-priority un-dismissed announcement: security > warning > info, then newest. */
function pickAnnouncement(announcements, dismissedIds) {
  const visible = (announcements || []).filter(
    (item) => item && !dismissedIds.includes(item.id),
  );
  if (visible.length === 0) return null;
  visible.sort((a, b) => {
    const rankA = LEVEL_RANK[a.level] ?? 3;
    const rankB = LEVEL_RANK[b.level] ?? 3;
    if (rankA !== rankB) return rankA - rankB;
    const timeA = Date.parse(a.published_at || '') || 0;
    const timeB = Date.parse(b.published_at || '') || 0;
    return timeB - timeA;
  });
  return visible[0];
}

/** The release entry matching the latest version name (falls back to its tag). */
function findRelease(versions, name) {
  if (!Array.isArray(versions)) return null;
  return versions.find((v) => v && (v.name === name || v.tag === `v${name}`)) || null;
}

export default function AppNotices() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [dismissedIds, setDismissedIds] = useState(readDismissedAnnouncements);
  const [dismissedVersion, setDismissedVersion] = useState(readDismissedVersion);

  useEffect(() => {
    let cancelled = false;
    // Load once; fail silently so the card never breaks a page.
    api
      .get('/notices')
      .then((res) => {
        if (!cancelled && res && !res.unavailable) setData(res);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data) return null;

  const announcement = pickAnnouncement(data.announcements, dismissedIds);
  const showUpdate =
    Boolean(data.update_available) &&
    Boolean(data.latest_version) &&
    data.latest_version !== dismissedVersion;

  if (!announcement && !showUpdate) return null;

  const dismissAnnouncement = () => {
    const next = [...dismissedIds, announcement.id].slice(-MAX_DISMISSED_ANNOUNCEMENTS);
    setDismissedIds(next);
    try {
      localStorage.setItem(DISMISSED_ANNOUNCEMENTS_KEY, JSON.stringify(next));
    } catch {}
  };

  const dismissVersion = () => {
    setDismissedVersion(data.latest_version);
    try {
      localStorage.setItem(DISMISSED_VERSION_KEY, data.latest_version);
    } catch {}
  };

  // One card at a time: an announcement outranks the update line (mirrors the webui).
  const style = LEVEL_STYLE[announcement?.level] || LEVEL_STYLE.info;
  const release = showUpdate ? findRelease(data.versions, data.latest_version) : null;
  const updateSecurity = Boolean(release && release.security);
  const showAnnouncement = Boolean(announcement);
  const border = showAnnouncement
    ? style.border
    : updateSecurity
      ? 'border-red-300'
      : 'border-gray-200';

  return (
    <div
      className={`fixed left-3 right-3 bottom-[calc(5.5rem+env(safe-area-inset-bottom))] z-50 flex w-auto max-h-[min(62vh,30rem)] flex-col gap-2 rounded-xl border bg-white p-3 shadow-2xl dark:bg-gray-800 sm:left-auto sm:right-4 sm:bottom-4 sm:w-[min(24rem,calc(100vw-2rem))] sm:max-h-[min(72vh,34rem)] ${border}`}
      role="status"
      aria-live={announcement?.level === 'security' ? 'assertive' : 'polite'}
      data-notice-card
    >
      <div className="flex min-h-0 items-start gap-2">
        {showAnnouncement ? (
          <style.Icon className={`mt-0.5 shrink-0 ${style.icon}`} size={16} />
        ) : (
          <RiArrowUpLine className={`mt-0.5 shrink-0 ${updateSecurity ? 'text-red-600' : 'text-blue-500'}`} size={16} />
        )}
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {showAnnouncement ? (
            <>
              <div className={`text-xs font-medium ${style.title}`}>{style.label}</div>
              <div className="mt-0.5 text-xs font-medium text-gray-900 dark:text-gray-100">{announcement.title}</div>
              {announcement.body && (
                <div className="mt-0.5 whitespace-pre-line text-xs leading-relaxed text-gray-600 dark:text-gray-300">
                  {announcement.body}
                </div>
              )}
            </>
          ) : (
            <>
              <div className={`text-xs font-medium ${updateSecurity ? 'text-red-700' : 'text-gray-900 dark:text-gray-100'}`}>
                {updateSecurity ? 'Security update' : 'Quickly update'}
              </div>
              <div className="mt-0.5 text-xs leading-relaxed text-gray-600 dark:text-gray-300">
                A newer version (v{data.latest_version}) is available.
              </div>
              {data.current_version && (
                <div className="mt-0.5 font-mono text-[10px] text-gray-400">
                  current {data.current_version}
                </div>
              )}
              {release && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-[10px] text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                    What&apos;s new
                  </summary>
                  <div className="mt-1 max-h-40 overflow-auto whitespace-pre-line text-[10px] leading-relaxed text-gray-600 dark:text-gray-300">
                    {release.title && <div className="mb-1 font-medium text-gray-900 dark:text-gray-100">{release.title}</div>}
                    {release.body && <div>{truncate(release.body, BODY_TRUNCATE)}</div>}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {/* The permanent "find it later" home — never changes dismissal state. */}
          <button
            type="button"
            data-notice-details
            onClick={() => navigate('/settings?section=about')}
            className="text-[11px] text-gray-400 underline-offset-2 transition-colors hover:text-gray-700 hover:underline dark:hover:text-gray-200"
          >
            Details
          </button>
          <button
            type="button"
            onClick={showAnnouncement ? dismissAnnouncement : dismissVersion}
            title="Dismiss"
            aria-label="Dismiss notice"
            className="flex size-5 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-100"
          >
            <RiCloseLine size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
