import React, { useState, useRef, useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import logo from '../../assets/quickly_logo.svg';
import {
  RiSendPlaneLine,
  RiLineChartLine,
  RiMailLine,
  RiInboxLine,
  RiCalendarScheduleLine,
  RiSettingsLine,
  RiSidebarFoldLine,
  RiInformationLine,
  RiHeartPulseLine,
  RiContactsLine,
  RiNotification3Line,
} from 'react-icons/ri';
import { useUniboxNotifications } from '../../context/UniboxNotificationsContext';
import { useNotifications } from '../../context/NotificationsContext';
import { useOnboarding } from '../../context/OnboardingContext';
import { useSystemHealth } from '../../context/SystemHealthContext';

// links including icons
const links = [
  { to: '/analytics', label: 'Analytics', icon: <RiLineChartLine size={20} /> },
  { to: '/campaigns', label: 'Campaigns', icon: <RiSendPlaneLine size={20} /> },
  { to: '/leads', label: 'Leads', icon: <RiContactsLine size={20} /> },
  { to: '/inboxes', label: 'Inboxes', icon: <RiMailLine size={20} /> },
  { to: '/unibox', label: 'Unibox', icon: <RiInboxLine size={20} /> },
  { to: '/schedule', label: 'Schedule', icon: <RiCalendarScheduleLine size={20} /> },
  { to: '/notifications', label: 'Notifications', icon: <RiNotification3Line size={20} /> },
  { to: '/settings#general', label: 'Settings', icon: <RiSettingsLine size={20} /> },
];

const APP_VERSION = '0.1.0';

function getOS() {
  const ua = navigator.userAgent;
  if (/Windows NT 10|Windows NT 11/.test(ua)) return 'Windows 10/11';
  if (/Windows NT/.test(ua)) return 'Windows';
  if (/Mac OS X/.test(ua)) return 'macOS';
  if (/Android/.test(ua)) return 'Android';
  if (/iPhone|iPad/.test(ua)) return 'iOS';
  if (/Linux/.test(ua)) return 'Linux';
  return 'Unknown';
}

function getBrowser() {
  const ua = navigator.userAgent;
  if (/Edg\//.test(ua)) return 'Edge';
  if (/OPR\/|Opera/.test(ua)) return 'Opera';
  if (/Chrome\//.test(ua)) return 'Chrome';
  if (/Safari\//.test(ua)) return 'Safari';
  if (/Firefox\//.test(ua)) return 'Firefox';
  return 'Unknown';
}

function buildBugUrl() {
  const body = [
    '## 🐛 Bug Report',
    '',
    '**Describe the bug**',
    'A clear description of what the bug is.',
    '',
    '**Steps to reproduce**',
    '1. ',
    '2. ',
    '3. ',
    '',
    '**Expected behavior**',
    '',
    '**Actual behavior**',
    '',
    '**Screenshots**',
    '',
    '**Environment**',
    `- OS: ${getOS()}`,
    `- Browser: ${getBrowser()}`,
    `- Quickly version: ${APP_VERSION}`,
  ].join('\n');
  return `https://github.com/AbdelftahZowail/Quickly/issues/new?labels=bug&title=%5BBug%5D%20&body=${encodeURIComponent(body)}`;
}

function buildFeatureUrl() {
  const body = [
    '## 💡 Feature Request',
    '',
    '**Is your feature request related to a problem?**',
    '',
    '**Describe the solution you\'d like**',
    '',
    '**Describe alternatives you\'ve considered**',
    '',
    '**Additional context**',
  ].join('\n');
  return `https://github.com/AbdelftahZowail/Quickly/issues/new?labels=enhancement&title=%5BFeature%5D%20&body=${encodeURIComponent(body)}`;
}

export default function Sidebar({ collapsed, onToggle }) {
  const location = useLocation();
  const { count: unreadCount } = useUniboxNotifications();
  const { count: notifUnreadCount } = useNotifications();
  const { startOnboarding } = useOnboarding();
  const { overallStatus } = useSystemHealth();
  const [helpOpen, setHelpOpen] = useState(false);
  const helpRef = useRef(null);

  const healthDotColor = {
    error:   'bg-red-500',
    warning: 'bg-yellow-400',
    ok:      'bg-green-500',
    unknown: 'bg-gray-400',
  }[overallStatus] || 'bg-gray-400';

  useEffect(() => {
    function handleClickOutside(e) {
      if (helpRef.current && !helpRef.current.contains(e.target)) {
        setHelpOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);
  const widthClass = collapsed ? 'w-16' : 'w-44';
  const justifyLogo = collapsed ? 'justify-center' : '';

  return (
    <nav
      className={`fixed top-0 left-0 z-40 h-full bg-gray-800 text-gray-300 flex flex-col p-3 transition-width duration-200 ${
        widthClass
      }`}
    >
      <NavLink
        to="/"
        className={`mb-8 flex items-center gap-2 no-underline hover:no-underline ${
          justifyLogo
        }`}
        title="Home"
      >
        <img src={logo} alt="Quickly logo" className="h-8 w-8" />
        {!collapsed && (
          <span className="text-primary font-extrabold text-xl">Quickly</span>
        )}
      </NavLink>
      <div className="flex flex-col gap-2">
        {links.map(l => (
          <div key={l.to} className="relative group">
            <NavLink
              to={l.to}
              end
              className={({ isActive }) => {
                const active =
                  isActive ||
                  (l.to === '/analytics' && location.pathname === '/') ||
                  (l.to.startsWith('/settings') && location.pathname === '/settings');
                return `flex items-center py-2 rounded px-2 transition-colors transition-transform transform-gpu active:scale-95 hover:scale-102 duration-150 whitespace-nowrap !no-underline !hover:no-underline ${
                  active
                    ? 'text-primary font-semibold bg-gray-700'
                    : 'hover:bg-gray-700/50'
                }`;
              }}
            >
              <span className="flex-shrink-0 relative inline-flex">
                {l.icon}
                {l.to === '/unibox' && unreadCount > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white leading-none">
                    {unreadCount > 99 ? '99+' : unreadCount}
                  </span>
                )}
                {l.to === '/notifications' && notifUnreadCount > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white leading-none">
                    {notifUnreadCount > 99 ? '99+' : notifUnreadCount}
                  </span>
                )}
              </span>
              {!collapsed && <span className="ml-2">{l.label}</span>}
            </NavLink>
            {collapsed && (
              <span className="absolute left-full top-1/2 transform -translate-y-1/2 ml-2 opacity-0 group-hover:opacity-100 transition-opacity bg-gray-700 text-white text-xs rounded px-2 py-1 whitespace-nowrap z-10 pointer-events-none shadow-md">
                {l.label}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Help item + collapse at bottom */}
      <div className="mt-auto flex flex-col gap-2">

        {/* System Health */}
        <div className="relative group">
          <NavLink
            to="/system-health"
            className={({ isActive }) =>
              `flex items-center py-2 rounded px-2 transition-colors transition-transform transform-gpu active:scale-95 hover:scale-102 duration-150 whitespace-nowrap !no-underline !hover:no-underline ${
                isActive ? 'text-primary font-semibold bg-gray-700' : 'hover:bg-gray-700/50'
              }`
            }
          >
            <span className="flex-shrink-0 relative inline-flex">
              <RiHeartPulseLine size={20} />
              <span
                className={`absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-gray-800 ${healthDotColor}`}
              />
            </span>
            {!collapsed && <span className="ml-2">System Health</span>}
          </NavLink>
          {collapsed && (
            <span className="absolute left-full top-1/2 transform -translate-y-1/2 ml-2 opacity-0 group-hover:opacity-100 transition-opacity bg-gray-700 text-white text-xs rounded px-2 py-1 whitespace-nowrap z-10 pointer-events-none shadow-md">
              System Health
            </span>
          )}
        </div>

        {/* Help — styled like nav links, popover opens to the right */}
        <div className="relative group" ref={helpRef}>
          <button
            onClick={() => { setHelpOpen(prev => !prev); }}
            className={`w-full flex items-center py-2 rounded px-2 transition-colors transition-transform transform-gpu active:scale-95 hover:scale-102 duration-150 whitespace-nowrap ${
              helpOpen ? 'text-primary font-semibold bg-gray-700' : 'hover:bg-gray-700/50 text-gray-300'
            }`}
          >
            <span className="flex-shrink-0"><RiInformationLine size={20} /></span>
            {!collapsed && <span className="ml-2">Help</span>}
          </button>

          {/* Collapsed tooltip */}
          {collapsed && (
            <span className="absolute left-full top-1/2 transform -translate-y-1/2 ml-2 opacity-0 group-hover:opacity-100 transition-opacity bg-gray-700 text-white text-xs rounded px-2 py-1 whitespace-nowrap z-10 pointer-events-none shadow-md">
              Help
            </span>
          )}

          {helpOpen && (
            <div className="absolute bottom-0 left-full ml-2 w-52 bg-gray-900 border border-gray-700 rounded-lg shadow-xl z-[60] overflow-hidden">

              {/* Deliverability Tips — navigates to full page */}
              <button
                onClick={() => { setHelpOpen(false); startOnboarding(); }}
                className="w-full text-left block px-3 py-2 text-sm text-gray-300 hover:text-primary hover:bg-gray-700"
              >
                App Tour
              </button>

              <div className="border-t border-gray-700" />

              <NavLink
                to="/deliverability-tips"
                onClick={() => setHelpOpen(false)}
                className="block px-3 py-2 text-sm text-gray-300 hover:text-primary hover:bg-gray-700 !no-underline"
              >
                Deliverability Tips
              </NavLink>

              <div className="border-t border-gray-700" />

              {/* Report a Bug */}
              <a
                href={buildBugUrl()}
                target="_blank"
                rel="noopener noreferrer"
                className="block px-3 py-2 text-sm text-gray-300 hover:text-primary hover:bg-gray-700"
              >
                Report a Bug
              </a>

              <div className="border-t border-gray-700" />

              {/* Suggest a Feature */}
              <a
                href={buildFeatureUrl()}
                target="_blank"
                rel="noopener noreferrer"
                className="block px-3 py-2 text-sm text-gray-300 hover:text-primary hover:bg-gray-700"
              >
                Suggest a Feature
              </a>
            </div>
          )}
        </div>

        {/* Collapse toggle */}
        <div className="flex justify-end mr-1">
        <button
          onClick={onToggle}
          onMouseDown={e => e.preventDefault()}
          className="text-gray-400 hover:text-primary focus:outline-none focus-visible:outline-none focus:ring-0 bg-transparent focus:bg-transparent active:bg-transparent transition-transform duration-200 active:scale-90"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <RiSidebarFoldLine
            size={24}
            className={`transition-transform duration-300 ${
              collapsed ? 'rotate-180' : 'rotate-0'
            }`}
          />
        </button>
        </div>
      </div>
    </nav>
  );
}
