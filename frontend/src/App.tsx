import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import Sidebar from '../components/Sidebar';
import Dashboard from '../views/Dashboard';
import Configuration from '../views/Configuration';
import AIAssistant from '../views/AIAssistant';
import GeminiChat from  "../views/AIChat";

type View = 'dashboard' | 'configuration' | 'ai_assistant' | 'gemini_chat';

const App: React.FC = () => {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'night');
  const [view, setView] = useState<View>(() => {
    try {
      const v = localStorage.getItem('currentView') as View | null;
      return v || 'dashboard';
    } catch (e) {
      return 'dashboard';
    }
  });

  useEffect(() => {
    localStorage.setItem('theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const renderView = () => {
    switch (view) {
      case 'dashboard':
        return <Dashboard />;
      case 'configuration':
        return <Configuration />;
      case 'ai_assistant':
        return <AIAssistant />;
      case 'gemini_chat':
        return <GeminiChat />;
      default:
        return <Dashboard />;
    }
  };

  useEffect(() => {
    try {
      localStorage.setItem('currentView', view);
    } catch (e) {
      // ignore storage errors
    }
  }, [view]);

  return (
    <div data-theme={theme} className="w-screen h-screen flex flex-col font-sans">
      <Header theme={theme} setTheme={setTheme} />
      <div className="flex grow overflow-hidden">
        <Sidebar currentView={view} setView={setView} />
        <main className="grow relative overflow-hidden bg-base-200">
          {renderView()}
        </main>
      </div>
    </div>
  );
};

export default App;