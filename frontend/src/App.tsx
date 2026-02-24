import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import Sidebar from '../components/Sidebar';
import Dashboard from '../views/Dashboard';
import Configuration from '../views/Configuration';
import GeminiChat from  "../views/AIChat";
import Deployments from '../views/Deployments';
import NetBoxDevices from '../views/NetBoxDevices';
import AIAgentPanel from '../components/AIAgentPanel';
import KnowledgeBase from '../views/KnowledgeBase';

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
  const [view, setView] = useState<ViewId>('dashboard');
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
      case 'dashboard':
        return <Dashboard />;
      case 'configuration':
        return <Configuration />;
      case 'netbox_devices':
        return <NetBoxDevices />;
      case 'deployments':
        return <Deployments />;
      case 'net_perf':
        return <NetworkPerformance />;
      case 'net_chat':
        return <GeminiChat domain="network" />;
      case 'knowledge_base':
        return <KnowledgeBase />;

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
        return <Dashboard />;
    }
  };

  if (!user) {
      return (
        <div data-theme={theme} className="w-screen h-screen font-sans">
             <Login onLogin={setUser} />
        </div>
      );
  }

  const handlePlaybookGenerated = (playbook: any) => {
    console.log('Playbook generated:', playbook);
    // In production, this would update a playbook editor/viewer in main area
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
              
              {/* AI Agent Panel (Docked Right) */}
              <AIAgentPanel 
                isOpen={aiPanelOpen}
                onToggle={() => setAiPanelOpen(!aiPanelOpen)}
                domain={getCurrentDomain()}
                onPlaybookGenerated={handlePlaybookGenerated}
              />
              
              {/* Toggle Button when panel is closed */}
              {!aiPanelOpen && (
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