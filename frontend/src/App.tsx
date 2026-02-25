import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import Sidebar from '../components/Sidebar';
import OverviewDashboard from '../views/OverviewDashboard';
import Dashboard from '../views/Dashboard';
import Configuration from '../views/Configuration';
import GeminiChat from  "../views/AIChat";
import Deployments from '../views/Deployments';
import NetBoxDevices from '../views/NetBoxDevices';
import AIAgentPanel from '../components/AIAgentPanel';
import KnowledgeBase from '../views/KnowledgeBase';
import AnsibleStudioView from '../views/AnsibleStudioView';
import DriftView from '../views/DriftView';

import Login from '../views/Login';
import NetworkPerformance from '../views/NetworkPerformance';
import SecurityEvents from '../views/SecurityEvents';
import SecurityAnalysis from '../views/SecurityAnalysis';
import SecurityDashboard from '../views/SecurityDashboard';
import { NotificationProvider } from '../contexts/NotificationContext';
import { Bot } from 'lucide-react';

import type { ViewId, User, AppDomain } from '../types';


const App: React.FC = () => {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'night');
  const [view, setView] = useState<ViewId>('overview');
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [aiPanelOpen, setAiPanelOpen] = useState(() => {
    const saved = localStorage.getItem('aiPanelOpen');
    return saved !== null ? JSON.parse(saved) : true;
  });

  useEffect(() => {
    localStorage.setItem('theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    try {
      localStorage.setItem('currentView', view);
    } catch (e) {
      // ignore storage errors
    }
  }, [view]);

  useEffect(() => {
    localStorage.setItem('aiPanelOpen', JSON.stringify(aiPanelOpen));
  }, [aiPanelOpen]);

  // Determine domain based on current view
  const getCurrentDomain = (): AppDomain => {
    if (view.startsWith('sec_')) return 'security';
    return 'network';
  };

  const renderView = () => {
    switch (view) {
      // Network Domain
      case 'overview':
        return <OverviewDashboard setView={setView} setSelectedDeviceId={setSelectedDeviceId} />;
      case 'dashboard':
      case 'topology':
        return <Dashboard />;
      case 'configuration':
        return <Configuration />;
      case 'netbox_devices':
        return <NetBoxDevices />;
      case 'deployments':
        return <Deployments />;
      case 'drift':
        return <DriftView />;
      case 'device_detail':
        return <div className="p-6"><h2 className="text-xl font-bold">Device 360 — #{selectedDeviceId ?? '?'}</h2><p className="mt-2 opacity-60">Device detail view — Phase 2.</p></div>;
      case 'net_perf':
        return <NetworkPerformance />;
      case 'net_chat':
        return <GeminiChat domain="network" />;
      case 'knowledge_base':
        return <KnowledgeBase />;
      case 'ansible_studio':
        return <AnsibleStudioView user={user} />;

      // Security Domain
      case 'sec_dashboard':
        return <SecurityDashboard />;
      case 'sec_events':
        return <SecurityEvents />;
      case 'sec_chat':
        return <GeminiChat domain="security" />;
      case 'sec_analysis':
         return <SecurityAnalysis />;

      default:
        return <OverviewDashboard setView={setView} setSelectedDeviceId={setSelectedDeviceId} />;
    }
  };

  if (!user) {
      return (
        <div data-theme={theme} className="w-screen h-screen font-sans">
             <Login onLogin={setUser} />
        </div>
      );
  }

  const handlePlaybookGenerated = (_playbook: any) => {
    setView('ansible_studio');
  };

  return (
    <NotificationProvider>
        <div data-theme={theme} className="w-screen h-screen flex flex-col font-sans">
        <Header theme={theme} setTheme={setTheme} />
        <div className="flex flex-grow overflow-hidden">
            <Sidebar 
                currentView={view} 
                setView={setView} 
                user={user} 
                onLogout={() => setUser(null)}
            />
            <main className="flex-grow relative overflow-hidden bg-base-200 flex">
              {/* Main Content Area */}
              <div className="flex-grow overflow-hidden relative">
                {renderView()}
              </div>
              
              {/* AI Agent Panel (Docked Right) — hidden on Ansible Studio which has its own chat */}
              {view !== 'ansible_studio' && (
                <AIAgentPanel 
                  isOpen={aiPanelOpen}
                  onToggle={() => setAiPanelOpen(!aiPanelOpen)}
                  domain={getCurrentDomain()}
                  onPlaybookGenerated={handlePlaybookGenerated}
                />
              )}
              
              {/* Toggle Button when panel is closed */}
              {view !== 'ansible_studio' && !aiPanelOpen && (
                <button 
                  onClick={() => setAiPanelOpen(true)}
                  className="absolute right-4 bottom-4 btn btn-primary btn-circle shadow-lg z-20"
                  title="Open AI Agent (Ctrl+Shift+I)"
                >
                  <Bot className="w-5 h-5" />
                </button>
              )}
            </main>
        </div>
        </div>
    </NotificationProvider>
  );
};

export default App;