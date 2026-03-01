import { useState } from 'react';
import { NavLink } from 'react-router-dom';
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
} from 'react-icons/ri';

// add icon component to each link. Home gets RiHomeLine.
const links = [
  { to: '/', label: 'Home', icon: <RiHomeLine size={20} /> },
  { to: '/campaigns', label: 'Campaigns', icon: <RiSendPlaneLine size={20} /> },
  { to: '/analytics', label: 'Analytics', icon: <RiLineChartLine size={20} /> },
  { to: '/inboxes', label: 'Inboxes', icon: <RiMailLine size={20} /> },
  { to: '/mailbox', label: 'Unibox', icon: <RiInboxLine size={20} /> },
  { to: '/calendar', label: 'Calendar', icon: <RiCalendarScheduleLine size={20} /> },
  { to: '/settings', label: 'Settings', icon: <RiSettingsLine size={20} /> },
];

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);

  // width classes change depending on collapsed state
  const containerClass = `bg-gray-900 text-gray-400 flex-shrink-0 h-full fixed top-0 left-0 p-4 overflow-y-auto transition-width duration-200 ${
    collapsed ? 'w-16' : 'w-36'
  }`;

  return (
    <nav className={containerClass}>
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
            className={({ isActive }) =>
              `flex items-center mb-3 font-medium whitespace-nowrap ${
                isActive
                  ? 'text-teal-400 font-semibold'
                  : 'hover:text-teal-300'
              }`
            }
          >
            <span className="flex-shrink-0">{l.icon}</span>
            {!collapsed && <span className="ml-2">{l.label}</span>}
          </NavLink>
        ))}
      </div>

      {/* toggle collapse control at bottom */}
      <div className="absolute bottom-4 left-0 w-full flex justify-center">
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
