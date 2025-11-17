import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import Sidebar from '../components/Sidebar';
import Dashboard from '../views/Dashboard';
import Configuration from '../views/Configuration';
import AIAssistant from '../views/AIAssistant';

type View = 'dashboard' | 'configuration' | 'ai_assistant';

const App: React.FC = () => {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'night');
  const [view, setView] = useState<View>('dashboard');

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
      default:
        return <Dashboard />;
    }
  };

  return (
    <div data-theme={theme} className="w-screen h-screen flex flex-col font-sans">
      <Header theme={theme} setTheme={setTheme} />
      <div className="flex flex-grow overflow-hidden">
        <Sidebar currentView={view} setView={setView} />
        <main className="flex-grow relative overflow-hidden bg-base-200">
          {renderView()}
        </main>
      </div>
    </div>
  );
};

export default App;