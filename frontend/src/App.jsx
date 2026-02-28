import { Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Campaigns from './pages/Campaigns';
import AddCampaign from './pages/AddCampaign';
import CampaignDetail from './pages/CampaignDetail';
import Inboxes from './pages/Inboxes';
import Calendar from './pages/Calendar';
import Settings from './pages/Settings';
import Analytics from './pages/Analytics';
import Unibox from './pages/Unibox';

export default function App() {
  return (
    <Layout>
      <Routes>
        {/* analytics becomes the landing page under root */}
        <Route path="/" element={<Analytics />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/campaigns/add" element={<AddCampaign />} />
        <Route path="/campaigns/:id" element={<CampaignDetail />} />
        <Route path="/inboxes" element={<Inboxes />} />
        <Route path="/mailbox" element={<Unibox />} />
        <Route path="/calendar" element={<Calendar />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  );
}
