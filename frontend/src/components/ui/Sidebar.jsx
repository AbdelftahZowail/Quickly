import React from 'react';
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
} from 'react-icons/ri';

// links including icons
const links = [
  { to: '/analytics', label: 'Analytics', icon: <RiLineChartLine size={20} /> },
  { to: '/campaigns', label: 'Campaigns', icon: <RiSendPlaneLine size={20} /> },
  { to: '/inboxes', label: 'Inboxes', icon: <RiMailLine size={20} /> },
  { to: '/unibox', label: 'Unibox', icon: <RiInboxLine size={20} /> },
  { to: '/scheduler', label: 'Scheduler', icon: <RiCalendarScheduleLine size={20} /> },
  { to: '/settings', label: 'Settings', icon: <RiSettingsLine size={20} /> },
];

export default function Sidebar({ collapsed, onToggle }) {
  const location = useLocation();
  const widthClass = collapsed ? 'w-16' : 'w-44';
  const justifyLogo = collapsed ? 'justify-center' : '';

  return (
    <nav
      className={`fixed top-0 left-0 h-full bg-gray-800 text-gray-300 flex flex-col p-3 transition-width duration-200 ${
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
                  (l.to === '/analytics' && location.pathname === '/');
                return `flex items-center py-2 rounded px-2 transition-colors transition-transform transform-gpu active:scale-95 hover:scale-102 duration-150 whitespace-nowrap !no-underline !hover:no-underline ${
                  active
                    ? 'text-primary font-semibold bg-gray-700'
                    : 'hover:bg-gray-700/50'
                }`;
              }}
            >
              <span className="flex-shrink-0">{l.icon}</span>
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

      {/* collapse button moved to right */}
      <div className="mt-auto flex justify-end">
        <button
          onClick={onToggle}
          onMouseDown={e => e.preventDefault()}
          className="text-gray-400 hover:text-primary focus:outline-none focus-visible:outline-none focus:ring-0 bg-transparent focus:bg-transparent active:bg-transparent mr-1 transition-transform duration-200 active:scale-90"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {/* single icon flipped based on state */}
          <RiSidebarFoldLine
            size={24}
            className={`transition-transform duration-300 ${
              collapsed ? 'rotate-180' : 'rotate-0'
            }`}
          />
        </button>
      </div>
    </nav>
  );
}
