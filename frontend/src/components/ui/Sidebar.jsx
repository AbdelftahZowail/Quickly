import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import logo from '../../assets/quickly_logo.svg';

const links = [
  { to: '/analytics', label: 'Analytics' },
  { to: '/campaigns', label: 'Campaigns' },
  { to: '/inboxes', label: 'Inboxes' },
  { to: '/mailbox', label: 'Unibox' },
  { to: '/calendar', label: 'Calendar' },
  { to: '/settings', label: 'Settings' },
];

export default function Sidebar() {
  const location = useLocation();

  return (
    <nav className="fixed top-0 left-0 h-full w-44 bg-gray-800 text-gray-300 flex flex-col p-4">
      <NavLink to="/" className="mb-8 flex items-center gap-2 no-underline hover:no-underline">
        <img src={logo} alt="Quickly logo" className="h-8 w-8" />
        <span className="text-primary font-extrabold text-xl">Quickly</span>
      </NavLink>
      <div className="flex flex-col gap-2">
        {links.map(l => (
          <NavLink
            key={l.to}
            to={l.to}
            end
            className={({ isActive }) => {
              const active =
                isActive ||
                (l.to === '/analytics' && location.pathname === '/');
              return `block py-2 rounded px-2 transition-colors !no-underline !hover:no-underline ${
                active
                  ? 'text-primary font-semibold bg-gray-700'
                  : 'hover:bg-gray-700/50'
              }`;
            }}
          >
            {l.label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
