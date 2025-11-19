import React from 'react';
import { LayoutDashboard, FileCog, BotMessageSquare } from 'lucide-react';

type View = 'dashboard' | 'configuration' | 'ai_assistant' | 'gemini_chat';

interface SidebarProps {
  currentView: View;
  setView: (view: View) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ currentView, setView }) => {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'configuration', label: 'Configuration', icon: FileCog },
    { id: 'ai_assistant', label: 'AI Assistant', icon: BotMessageSquare },
    { id: 'gemini_chat', label: 'Gemini Chat', icon: BotMessageSquare },
  ];

  return (
   <aside className="w-64 bg-base-100 shrink-0 shadow-lg z-10 p-4">
      <ul className="menu w-full p-0 gap-1">
        {navItems.map(item => (
          <li
            key={item.id}
            className={`rounded-lg ${currentView === item.id ? 'active bg-primary text-primary-content' : ''}`}
          >
            <a
              role="button"
              tabIndex={0}
              aria-current={currentView === item.id ? 'page' : undefined}
              onClick={() => setView(item.id as View)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setView(item.id as View); }}
              className="rounded-lg gap-2.5 py-2 flex items-center"
            >
              <item.icon className="h-5 w-5 mr-2" />
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
};

export default Sidebar;
