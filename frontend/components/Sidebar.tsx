import React from 'react';
import { LayoutDashboard, FileCog, BotMessageSquare } from 'lucide-react';

type View = 'dashboard' | 'configuration' | 'ai_assistant';

interface SidebarProps {
  currentView: View;
  setView: (view: View) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ currentView, setView }) => {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'configuration', label: 'Configuration', icon: FileCog },
    { id: 'ai_assistant', label: 'AI Assistant', icon: BotMessageSquare },
  ];

  return (
    <aside className="w-64 bg-base-100 flex-shrink-0 shadow-lg z-10 p-4">
      <ul className="menu p-0">
        {navItems.map(item => (
          <li key={item.id}>
            <a
              onClick={() => setView(item.id as View)}
              className={currentView === item.id ? 'active' : ''}
            >
              <item.icon className="h-5 w-5" />
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
};

export default Sidebar;
