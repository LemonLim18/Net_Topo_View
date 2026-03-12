import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import { authApi } from './services/api';
import Sidebar from '../components/Sidebar';
import OverviewDashboard from '../views/OverviewDashboard';
import Dashboard from '../views/Dashboard';
import GeminiChat from  "../views/AIChat";
import NetBoxDevices from '../views/NetBoxDevices';
import AIAgentPanel from '../components/AIAgentPanel';
import KnowledgeBase from '../views/KnowledgeBase';
import AnsibleStudioView from '../views/AnsibleStudioView';
import TemplateStudioView from '../views/TemplateStudioView';
import DriftView from '../views/DriftView';
import IncidentDashboard from '../views/IncidentDashboard';
import OnboardingView from '../views/OnboardingView';
import IntentHistoryView from '../views/IntentHistoryView';
import SDNDashboardView from '../views/SDNDashboardView';
import PlaybookComparisonView from '../views/PlaybookComparisonView';
import ApprovalQueueView from '../views/ApprovalQueueView';
import FloatingInventoryOverlay from '../components/FloatingInventoryOverlay';

import Login from '../views/Login';
import NetworkPerformance from '../views/NetworkPerformance';
import SecurityEvents from '../views/SecurityEvents';
import SecurityAnalysis from '../views/SecurityAnalysis';
import SecurityDashboard from '../views/SecurityDashboard';
import { NotificationProvider } from '../contexts/NotificationContext';
import { DeviceProvider } from '../contexts/DeviceContext';
import { Bot, Database } from 'lucide-react';

import type { ViewId, User, AppDomain } from '../types';

const CURRENT_VIEW_KEY = 'currentView';
const SELECTED_DEVICE_KEY = 'selectedDeviceId';

const isViewId = (value: string | null): value is ViewId => {
  if (!value) return false;
  return [
    'overview',
    'dashboard',
    'topology',
    'netbox_devices',
    'drift',
    'net_perf',
    'net_chat',
    'knowledge_base',
    'ansible_studio',
    'device_detail',
    'sec_dashboard',
    'sec_events',
    'sec_analysis',
    'sec_chat',
    'incidents',
    'onboarding',
    'intent_history',
    'sdn_dashboard',
    'playbook_comparison',
    'approval_queue',
    'template_studio',
  ].includes(value);
};


const App: React.FC = () => {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'night');
  const [view, setView] = useState<ViewId>(() => {
    const saved = localStorage.getItem(CURRENT_VIEW_KEY);
    return isViewId(saved) ? saved : 'overview';
  });
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(() => {
    const saved = localStorage.getItem(SELECTED_DEVICE_KEY);
    if (!saved) return null;
    const parsed = Number(saved);
    return Number.isFinite(parsed) ? parsed : null;
  });
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    return localStorage.getItem('sidebarCollapsed') === 'true';
  });
  const [aiPanelOpen, setAiPanelOpen] = useState(() => {
    const saved = localStorage.getItem('aiPanelOpen');
    return saved !== null ? JSON.parse(saved) : true;
  });
  const [overlayOpen, setOverlayOpen] = useState(false);

  // ── Hydrate user from stored JWT on mount ──
  useEffect(() => {
    const token = localStorage.getItem('auth_token');
    if (!token) {
      setAuthChecked(true);
      return;
    }
    authApi.me()
      .then(me => {
        setUser({
          id: me.id,
          username: me.username,
          role: me.role as User['role'],
          lastLogin: me.lastLogin || new Date().toISOString(),
        });
      })
      .catch(() => {
        localStorage.removeItem('auth_token');
      })
      .finally(() => setAuthChecked(true));
  }, []);

  useEffect(() => {
    localStorage.setItem('theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    try {
      localStorage.setItem(CURRENT_VIEW_KEY, view);
    } catch (e) {
      // ignore storage errors
    }
  }, [view]);

  useEffect(() => {
    try {
      if (selectedDeviceId === null) {
        localStorage.removeItem(SELECTED_DEVICE_KEY);
      } else {
        localStorage.setItem(SELECTED_DEVICE_KEY, String(selectedDeviceId));
      }
    } catch (e) {
      // ignore storage errors
    }
  }, [selectedDeviceId]);

  useEffect(() => {
    localStorage.setItem('aiPanelOpen', JSON.stringify(aiPanelOpen));
  }, [aiPanelOpen]);

  useEffect(() => {
    localStorage.setItem('sidebarCollapsed', String(sidebarCollapsed));
  }, [sidebarCollapsed]);

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
      case 'netbox_devices':
        return <NetBoxDevices />;
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

      // Incident Response
      case 'incidents':
        return <IncidentDashboard />;

      // M0 — Device Onboarding
      case 'onboarding':
        return <OnboardingView setView={setView} />;

      // Phase 1 — Intent Lifecycle Management
      case 'intent_history':
        return <IntentHistoryView setView={setView} />;

      // Phase 3 — SDN Controller Dashboard
      case 'sdn_dashboard':
        return <SDNDashboardView setView={setView} />;

      // Phase 12 — Playbook Candidate Comparison (UC14)
      case 'playbook_comparison':
        return <PlaybookComparisonView setView={setView} />;

      // Phase 16 — Approval Queue Dashboard
      case 'approval_queue':
        return <ApprovalQueueView setView={setView} />;

      // Template Studio — Jinja2 template editor
      case 'template_studio':
        return <TemplateStudioView />;

      default:
        return <OverviewDashboard setView={setView} setSelectedDeviceId={setSelectedDeviceId} />;
    }
  };

  if (!authChecked) {
    return (
      <div data-theme={theme} className="w-screen h-screen font-sans flex items-center justify-center">
        <span className="loading loading-spinner loading-lg text-primary"></span>
      </div>
    );
  }

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
      <DeviceProvider>
        <div data-theme={theme} className="w-screen h-screen flex flex-col font-sans">
        <Header theme={theme} setTheme={setTheme} />
        <div className="flex grow overflow-hidden">
            <Sidebar 
                currentView={view} 
                setView={setView} 
                user={user} 
                onLogout={() => { localStorage.removeItem('auth_token'); setUser(null); }}
                collapsed={sidebarCollapsed}
                onToggleCollapse={() => setSidebarCollapsed(c => !c)}
            />
            <main className="grow relative overflow-hidden bg-base-200 flex">
              {/* Main Content Area */}
              <div className="grow overflow-hidden relative">
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

              {/* Floating Overlay Button */}
              {!overlayOpen && (
                <button 
                  onClick={() => setOverlayOpen(true)}
                  className="absolute left-4 bottom-4 btn btn-accent btn-circle shadow-lg z-20"
                  title="Inventory & Ollama Playground"
                >
                  <Database className="w-5 h-5 text-accent-content" />
                </button>
              )}

              {/* The Floating Overlay */}
              {overlayOpen && (
                <FloatingInventoryOverlay onClose={() => setOverlayOpen(false)} />
              )}
            </main>
        </div>
        </div>
      </DeviceProvider>
    </NotificationProvider>
  );
};

export default App;