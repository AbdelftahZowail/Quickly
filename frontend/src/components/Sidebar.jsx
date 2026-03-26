import { useState, useRef, useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import logo from '../assets/quickly_logo.svg';
import {
  RiSendPlaneLine,
  RiLineChartLine,
  RiMailLine,
  RiInboxLine,
  RiCalendarScheduleLine,
  RiSettingsLine,
  RiSidebarFoldLine,
  RiSidebarUnfoldLine,
  RiHomeLine,
  RiInformationLine,
  RiArrowRightSLine,
  RiArrowDownSLine,
  RiHeartPulseLine,
} from 'react-icons/ri';
import { useSystemHealth } from '../context/SystemHealthContext';

const links = [
  { to: '/', label: 'Home', icon: <RiHomeLine size={20} /> },
  { to: '/campaigns', label: 'Campaigns', icon: <RiSendPlaneLine size={20} /> },
  { to: '/analytics', label: 'Analytics', icon: <RiLineChartLine size={20} /> },
  { to: '/inboxes', label: 'Inboxes', icon: <RiMailLine size={20} /> },
  { to: '/unibox', label: 'Unibox', icon: <RiInboxLine size={20} /> },
  { to: '/schedule', label: 'Schedule', icon: <RiCalendarScheduleLine size={20} /> },
  { to: '/settings#general', label: 'Settings', icon: <RiSettingsLine size={20} /> },
];

export default function Sidebar() {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [tipsOpen, setTipsOpen] = useState(false);
  const helpRef = useRef(null);
  const { overallStatus } = useSystemHealth();

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
        setTipsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // width classes change depending on collapsed state
  const containerClass = `bg-gray-900 text-gray-400 flex-shrink-0 h-full fixed top-0 left-0 z-40 flex flex-col transition-width duration-200 ${
    collapsed ? 'w-16' : 'w-36'
  }`;

  return (
    <nav className={containerClass}>
      {/* Scrollable content area */}
      <div className="flex-1 overflow-y-auto p-4">
        <div
          className={`mb-6 flex items-center space-x-2 ${
            collapsed ? 'justify-center' : ''
          }`}
        >
          <img src={logo} alt="Quickly logo" className="h-8 w-8" />
          {!collapsed && (
            <span className="text-teal-400 font-bold text-lg">Quickly</span>
          )}
        </div>

        <div className="flex flex-col">
          {links.map(l => (
            <NavLink
              key={l.to}
              to={l.to}
              end
              title={l.label} // tooltip for hover
              className={({ isActive }) => {
                const onSettings = l.to.startsWith('/settings') && location.pathname === '/settings';
                return `flex items-center mb-3 font-medium whitespace-nowrap ${
                  isActive || onSettings
                    ? 'text-teal-400 font-semibold'
                    : 'hover:text-teal-300'
                }`;
              }}
            >
              <span className="flex-shrink-0">{l.icon}</span>
              {!collapsed && <span className="ml-2">{l.label}</span>}
            </NavLink>
          ))}
        </div>
      </div>

      {/* help + toggle collapse control at bottom */}
      <div className="p-4 flex flex-col items-center gap-3">

        {/* System Health indicator */}
        <NavLink
          to="/system-health"
          title="System Health"
          className={({ isActive }) =>
            `flex items-center gap-2 text-gray-400 hover:text-teal-300 transition-colors ${
              isActive ? 'text-teal-400' : ''
            }`
          }
        >
          <div className="relative flex-shrink-0">
            <RiHeartPulseLine size={22} />
            <span
              className={`absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-gray-900 ${healthDotColor}`}
            />
          </div>
          {!collapsed && (
            <span className="text-xs font-medium whitespace-nowrap">System Health</span>
          )}
        </NavLink>

        {/* Help popover */}
        <div className="relative" ref={helpRef}>
          <button
            onClick={() => { setHelpOpen(prev => !prev); setTipsOpen(false); }}
            className="text-gray-400 hover:text-teal-300 focus:outline-none"
            title="Help"
          >
            <RiInformationLine size={24} />
          </button>

          {helpOpen && (
            <div className="absolute bottom-full mb-2 left-2 w-52 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-[60] overflow-hidden">

              {/* Deliverability Tips */}
              <button
                onClick={() => setTipsOpen(prev => !prev)}
                className="w-full text-left px-3 py-2 text-sm text-gray-300 hover:text-teal-300 hover:bg-gray-700 flex items-center justify-between"
              >
                <span>Deliverability Tips</span>
                {tipsOpen ? <RiArrowDownSLine size={16} /> : <RiArrowRightSLine size={16} />}
              </button>

              {tipsOpen && (
                <ul className="text-xs text-gray-400 px-3 pb-2 space-y-1 border-t border-gray-700">
                  <li className="pt-2">• Warm up new inboxes gradually</li>
                  <li>• Set up SPF, DKIM &amp; DMARC records</li>
                  <li>• Keep bounce rate below 2%</li>
                  <li>• Personalize subject lines</li>
                  <li>• Avoid spam-trigger words</li>
                  <li>• Prefer plain-text or minimal HTML</li>
                  <li>• Maintain a clean, verified lead list</li>
                </ul>
              )}

              <div className="border-t border-gray-700" />

              {/* Report bug / Suggest feature */}
              <a
                href="https://github.com/azowail/quickly/issues/new?labels=bug&title=%5BBug%5D%20&body=%23%23%20%F0%9F%90%9B%20Bug%20Report%0A%0A**Describe%20the%20bug**%0AA%20clear%20description%20of%20what%20the%20bug%20is.%0A%0A**Steps%20to%20reproduce**%0A1.%20%0A2.%20%0A3.%20%0A%0A**Expected%20behavior**%0A%0A**Actual%20behavior**%0A%0A**Screenshots**%0A%0A**Environment**%0A-%20OS%3A%20%0A-%20Browser%3A%20%0A-%20Quickly%20version%3A%20"
                target="_blank"
                rel="noopener noreferrer"
                className="block px-3 py-2 text-sm text-gray-300 hover:text-teal-300 hover:bg-gray-700"
              >
                Report a Bug / Suggest Feature
              </a>
            </div>
          )}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed(prev => !prev)}
          className="text-gray-400 hover:text-teal-300 focus:outline-none"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <RiSidebarUnfoldLine size={24} />
          ) : (
            <RiSidebarFoldLine size={24} />
          )}
        </button>
      </div>
    </nav>
  );
}
