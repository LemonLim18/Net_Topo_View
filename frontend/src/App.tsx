import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import Sidebar from '../components/Sidebar';
import Dashboard from '../views/Dashboard';
import Configuration from '../views/Configuration';
import AIAssistant from '../views/AIAssistant';
import GeminiChat from  "../views/AIChat";

import Login from '../views/Login';
import NetworkPerformance from '../views/NetworkPerformance';
import SecurityEvents from '../views/SecurityEvents';
import SecurityAnalysis from '../views/SecurityAnalysis';
import { NotificationProvider } from '../contexts/NotificationContext';
import NotificationToast from '../components/NotificationToast';

// type View = 'dashboard' | 'configuration' | 'ai_assistant' | 'gemini_chat';
import type { ViewId, User, AppDomain } from '../types';


const App: React.FC = () => {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'night');
  const [view, setView] = useState<ViewId>('dashboard');
  const [user, setUser] = useState<User | null>(null);

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

  // const renderView = () => {
  //   switch (view) {
  //     case 'dashboard':
  //       return <Dashboard />;
  //     case 'configuration':
  //       return <Configuration />;
  //     case 'ai_assistant':
  //       return <AIAssistant />;
  //     case 'gemini_chat':
  //       return <GeminiChat />;
  //     default:
  //       return <Dashboard />;
  //   }
  // };
  const renderView = () => {
    switch (view) {
      // Network Domain
      case 'dashboard':
        return <Dashboard />;
      case 'configuration':
        return <Configuration />;
      case 'net_perf':
        return <NetworkPerformance />;
      case 'net_chat':
        return <GeminiChat domain="network" />;
      
      // Security Domain
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
            <main className="flex-grow relative overflow-hidden bg-base-200">
            {renderView()}
            <NotificationToast />
            </main>
        </div>
        </div>
    </NotificationProvider>
  );
};

export default App;