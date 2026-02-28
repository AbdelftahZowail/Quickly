import Sidebar from './ui/Sidebar';
import TestModeBanner from './TestModeBanner';
import { NotificationProvider } from '../context/NotificationContext';
import { LoadingProvider } from '../context/LoadingContext';
import { DarkModeProvider } from '../context/DarkModeContext';
import { ConfirmProvider } from '../context/ConfirmContext';

export default function Layout({ children }) {
  return (
    <NotificationProvider>
      <LoadingProvider>
        <DarkModeProvider>
          <ConfirmProvider>
            <div className="flex min-h-screen">
              <Sidebar />
              <div className="flex-1 ml-44 bg-gray-50 text-gray-900">
                <TestModeBanner />
                {children}
              </div>
            </div>
          </ConfirmProvider>
        </DarkModeProvider>
      </LoadingProvider>
    </NotificationProvider>
  );
}
