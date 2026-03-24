import { Routes, Route, Navigate } from 'react-router-dom';
import { useEffect } from 'react';
import Layout from './components/Layout';
import Campaigns from './pages/Campaigns';
import AddCampaign from './pages/AddCampaign';
import CampaignDetail from './pages/CampaignDetail';
import Inboxes from './pages/Inboxes';
import Schedule from './pages/Schedule';
import Settings from './pages/Settings';
import Analytics from './pages/Analytics';
import Unibox from './pages/Unibox';
import DeliverabilityTips from './pages/DeliverabilityTips';
import SystemHealth from './pages/SystemHealth';
import Leads from './pages/Leads';
import LeadDetail from './pages/LeadDetail';
import Login from './pages/Login';
import { api } from './api';
import { AuthProvider, useAuth } from './context/AuthContext';
import SplashScreen from './components/SplashScreen';
import { DarkModeProvider } from './context/DarkModeContext';
import { SystemHealthProvider } from './context/SystemHealthContext';

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) {
    return <SplashScreen />;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function AppRoutes() {
  const { user, loading } = useAuth();

  // Register this browser's IP as a known IP (auto-expires after 1 week)
  useEffect(() => {
    if (user) {
      api.post('/settings/known-ips/heartbeat', {}).catch(() => {});
    }
  }, [user]);

  if (loading) {
    return <SplashScreen />;
  }

  return (
    <Routes>
      <Route path="/login" element={
        user ? <Navigate to="/" replace /> : <Login />
      } />
      <Route path="/*" element={
        <ProtectedRoute>
          <Layout>
            <Routes>
              <Route path="/" element={<Analytics />} />
              <Route path="/campaigns" element={<Campaigns />} />
              <Route path="/campaigns/add" element={<AddCampaign />} />
              <Route path="/campaigns/:id" element={<CampaignDetail />} />
              <Route path="/leads" element={<Leads />} />
              <Route path="/leads/:id" element={<LeadDetail />} />
              <Route path="/inboxes" element={<Inboxes />} />
              <Route path="/unibox" element={<Unibox />} />
              <Route path="/schedule" element={<Schedule />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/deliverability-tips" element={<DeliverabilityTips />} />
              <Route path="/system-health" element={<SystemHealth />} />
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          </Layout>
        </ProtectedRoute>
      } />
    </Routes>
  );
}

export default function App() {
  return (
    <DarkModeProvider>
      <AuthProvider>
        <SystemHealthProvider>
          <AppRoutes />
        </SystemHealthProvider>
      </AuthProvider>
    </DarkModeProvider>
  );
}
