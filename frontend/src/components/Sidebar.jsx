import { NavLink } from 'react-router-dom';
import logo from '../assets/quickly_logo.svg';

const links = [
  { to: '/', label: 'Home' },
  { to: '/campaigns', label: 'Campaigns' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/inboxes', label: 'Inboxes' },
  { to: '/calendar', label: 'Calendar' },
  { to: '/settings', label: 'Settings' },
];

export default function Sidebar() {

  return (
    <nav className="bg-gray-900 text-gray-400 w-36 flex-shrink-0 h-full fixed top-0 left-0 p-4 overflow-y-auto">
      <div className="mb-6 flex items-center space-x-2">
        <img src={logo} alt="Quickly logo" className="h-8 w-8" />
        <span className="text-teal-400 font-bold text-lg">Quickly</span>
      </div>
      {links.map(l => (
        <NavLink
          key={l.to}
          to={l.to}
          end
          className={({ isActive }) =>
            `block mb-3 font-medium ${isActive ? 'text-teal-400 font-semibold' : 'hover:text-teal-300'}`
          }
        >
          {l.label}
        </NavLink>
      ))}

    </nav>
  );
}
