import React, { useState, useEffect, useRef } from 'react';
import Header from '../components/Header';
import { authApi } from './services/api';
import Sidebar from '../components/Sidebar';
import OverviewDashboard from '../views/OverviewDashboard';
import Dashboard from '../views/Dashboard';
import GeminiChat from  "../views/AIChat";
import NetBoxDevices from '../views/NetBoxDevices';
import KnowledgeBase from '../views/KnowledgeBase';
import AnsibleStudioView from '../views/AnsibleStudioView';
import TemplateStudioView from '../views/TemplateStudioView';
import ConfigBackupView from '../views/ConfigBackupView';
import DriftView from '../views/DriftView';
import IncidentDashboard from '../views/IncidentDashboard';
import OnboardingView from '../views/OnboardingView';
import IntentHistoryView from '../views/IntentHistoryView';
import IntentParserView from '../views/IntentParserView';
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
import { Bot } from 'lucide-react';

import type { ViewId, User } from '../types';

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
    'intent_parser',
    'sdn_dashboard',
    'playbook_comparison',
    'approval_queue',
    'template_studio',
    'config_backup',
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
  const [overlayOpen, setOverlayOpen] = useState(false);
  
  // Custom Draggable Button State
  const [buttonPos, setButtonPos] = useState({ x: window.innerWidth - 80, y: window.innerHeight - 80 });
  const [isDraggingButton, setIsDraggingButton] = useState(false);
  const [hasMoved, setHasMoved] = useState(false);
  const buttonDragStart = useRef({ x: 0, y: 0 });

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
    localStorage.setItem('sidebarCollapsed', String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  // --- Button Dragging Logic ---
  const handleButtonMouseDown = (e: React.MouseEvent) => {
    setIsDraggingButton(true);
    setHasMoved(false);
    buttonDragStart.current = {
      x: e.clientX - buttonPos.x,
      y: e.clientY - buttonPos.y
    };
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDraggingButton) return;
      
      const newX = e.clientX - buttonDragStart.current.x;
      const newY = e.clientY - buttonDragStart.current.y;
      
      // If moved more than 5px, mark as moved
      if (Math.abs(newX - buttonPos.x) > 5 || Math.abs(newY - buttonPos.y) > 5) {
        setHasMoved(true);
      }

      setButtonPos({
        x: Math.max(0, Math.min(window.innerWidth - 60, newX)),
        y: Math.max(0, Math.min(window.innerHeight - 60, newY))
      });
    };
    const handleMouseUp = () => setIsDraggingButton(false);

    if (isDraggingButton) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDraggingButton]);

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
      case 'intent_parser':
        return <IntentParserView />;

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

      case 'config_backup':
        return <ConfigBackupView />;

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
              
              {/* Enriched Draggable Toggle Button */}
              {!overlayOpen && (
                <>
                  <style>{`
                    @keyframes agent-breath {
                      0%, 100% { transform: scale(1); box-shadow: 0 0 20px rgba(var(--p), 0.2); }
                      50% { transform: scale(1.05); box-shadow: 0 0 30px rgba(var(--p), 0.4); }
                    }
                    @keyframes agent-hover {
                      0% { transform: rotate(0deg) scale(1.1); }
                      25% { transform: rotate(5deg) scale(1.15); }
                      75% { transform: rotate(-5deg) scale(1.15); }
                      100% { transform: rotate(0deg) scale(1.1); }
                    }
                    .agent-breathing {
                      animation: agent-breath 3s ease-in-out infinite;
                    }
                    .agent-button:hover {
                      animation: agent-hover 0.5s ease-in-out infinite;
                    }
                  `}</style>
                  <div 
                    style={{ 
                      position: 'fixed', 
                      left: `${buttonPos.x}px`, 
                      top: `${buttonPos.y}px`,
                      zIndex: 50,
                      cursor: isDraggingButton ? 'grabbing' : 'grab'
                    }}
                    onMouseDown={handleButtonMouseDown}
                  >
                    <button 
                      onClick={() => !hasMoved && setOverlayOpen(true)}
                      className="btn btn-primary btn-circle shadow-xl border-2 border-primary/20 agent-button agent-breathing transition-all active:scale-95"
                      title="Open Inventory AI & Network Ops Center"
                    >
                      <Bot className="w-6 h-6" />
                    </button>
                  </div>
                </>
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