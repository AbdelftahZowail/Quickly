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
import { api } from './api';

export default function App() {
  // Register this browser's IP as a known IP (auto-expires after 1 week)
  useEffect(() => {
    api.post('/settings/known-ips/heartbeat', {}).catch(() => {});
  }, []);
  return (
    <Layout>
      <Routes>
        {/* analytics becomes the landing page under root */}
        <Route path="/" element={<Analytics />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/campaigns/add" element={<AddCampaign />} />
        <Route path="/campaigns/:id" element={<CampaignDetail />} />
        <Route path="/inboxes" element={<Inboxes />} />
        <Route path="/unibox" element={<Unibox />} />
        <Route path="/schedule" element={<Schedule />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/deliverability-tips" element={<DeliverabilityTips />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  );
}
