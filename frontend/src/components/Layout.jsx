import React from 'react';
import Sidebar from './ui/Sidebar';
import TestModeBanner from './TestModeBanner';
import Onboarding from './Onboarding';
import { NotificationProvider } from '../context/NotificationContext';
import { LoadingProvider } from '../context/LoadingContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import { UniboxNotificationsProvider } from '../context/UniboxNotificationsContext';
import { AppModeProvider, useAppMode } from '../context/AppModeContext';
import { OnboardingProvider } from '../context/OnboardingContext';

function LayoutInner({ children, sidebarCollapsed, setSidebarCollapsed }) {
  const { isProduction } = useAppMode();
  const contentMargin = sidebarCollapsed ? 'ml-16' : 'ml-44';

  return (
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
        {!isProduction && <TestModeBanner />}
        {children}
      </div>
    </div>
  );
}

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

  return (
    <NotificationProvider>
      <LoadingProvider>
        <ConfirmProvider>
          <UniboxNotificationsProvider>
            <AppModeProvider>
              <OnboardingProvider>
                <Onboarding />
                <LayoutInner
                  sidebarCollapsed={sidebarCollapsed}
                  setSidebarCollapsed={setSidebarCollapsed}
                >
                  {children}
                </LayoutInner>
              </OnboardingProvider>
            </AppModeProvider>
          </UniboxNotificationsProvider>
        </ConfirmProvider>
      </LoadingProvider>
    </NotificationProvider>
  );
}
