import React from 'react';
import Sidebar from './ui/Sidebar';
import TestModeBanner from './TestModeBanner';
import { NotificationProvider } from '../context/NotificationContext';
import { LoadingProvider } from '../context/LoadingContext';
import { DarkModeProvider } from '../context/DarkModeContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import { UniboxNotificationsProvider } from '../context/UniboxNotificationsContext';

export default function Layout({ children }) {
  // initialize collapsed state from localStorage (knows before first paint)
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(() => {
    try {
      const stored = localStorage.getItem('sidebarCollapsed');
      return stored === 'true';
    } catch {
      return false;
    }
  });

  // update margin for main content when sidebar changes
  const contentMargin = sidebarCollapsed ? 'ml-16' : 'ml-44';

  return (
    <NotificationProvider>
      <LoadingProvider>
        <DarkModeProvider>
          <ConfirmProvider>
            <UniboxNotificationsProvider>
            <div className="flex min-h-screen">
              <Sidebar
                collapsed={sidebarCollapsed}
                onToggle={() => {
                  setSidebarCollapsed(c => {
                    const next = !c;
                    try {
                      localStorage.setItem('sidebarCollapsed', next ? 'true' : 'false');
                    } catch {}
                    return next;
                  });
                }}
              />
              <div className={`${contentMargin} flex-1 bg-gray-50 text-gray-900`}
              >
                <TestModeBanner />
                {children}
              </div>
            </div>
            </UniboxNotificationsProvider>
          </ConfirmProvider>
        </DarkModeProvider>
      </LoadingProvider>
    </NotificationProvider>
  );
}
