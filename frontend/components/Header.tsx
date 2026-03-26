import React from 'react';
import { Bell, Check, Sun } from 'lucide-react';
import { useNotification } from '../contexts/NotificationContext';

const daisyUIThemes = [
  "light", "dark", "cupcake", "emerald", "corporate", "synthwave",
  "retro", "cyberpunk", "valentine", "halloween", "garden", "forest", "aqua",
  "pastel", "fantasy", "wireframe", "black", "luxury", "dracula",
  "cmyk", "autumn", "business", "acid", "lemonade", "night", "coffee", "winter",
  "dim", "nord", "sunset",
];

interface HeaderProps {
    theme: string;
    setTheme: (theme: string) => void;
}

const Header: React.FC<HeaderProps> = ({ theme, setTheme }) => {
  const { notifications, markAsRead, clearAll } = useNotification();
  const unreadCount = notifications.filter(n => !n.read).length;

  const getIconColor = (type: string) => {
      switch(type) {
          case 'error': return 'text-error';
          case 'warning': return 'text-warning';
          case 'success': return 'text-success';
          default: return 'text-info';
      }
  };

  return (
    <header className="navbar bg-base-100 shadow-lg z-20 shrink-0">
      <div className="flex-1">
        <a className="btn btn-ghost text-xl normal-case">
          <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.684 13.342C8.886 12.938 9 12.482 9 12s-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.367 2.684 3 3 0 00-5.367-2.684z" />
          </svg>
          <span className="font-bold mr-2">CogniShield</span> <span className="text-xs opacity-50 font-stretch-75">LLM-Assisted Network Automation Controller</span>
        </a>
      </div>
      <div className="flex-none gap-4">
        {/* Notification Bell */}
        <div className="dropdown dropdown-end">
            <div tabIndex={0} role="button" className="btn btn-ghost btn-circle">
                <div className="indicator">
                    <Bell className="h-5 w-5" />
                    {unreadCount > 0 && <span className="badge badge-sm badge-error indicator-item">{unreadCount}</span>}
                </div>
            </div>
            <div tabIndex={0} className="mt-3 z-[1] card card-compact dropdown-content w-80 bg-base-100 shadow-xl border border-base-200">
                <div className="card-body">
                    <div className="flex justify-between items-center mb-2">
                        <span className="font-bold text-lg">Notifications</span>
                        <button onClick={clearAll} className="btn btn-xs btn-ghost text-error" disabled={notifications.length === 0}>
                            Clear all
                        </button>
                    </div>
                    <div className="max-h-96 overflow-y-auto space-y-2">
                        {notifications.length === 0 ? (
                            <div className="text-center py-8 opacity-50 text-sm">No notifications</div>
                        ) : (
                            notifications.map(notif => (
                                <div key={notif.id} className={`p-3 rounded-lg border ${notif.read ? 'bg-base-100 border-base-200 opacity-60' : 'bg-base-200 border-base-300'}`}>
                                    <div className="flex justify-between items-start gap-2">
                                        <div className={`font-semibold text-sm ${getIconColor(notif.type)}`}>{notif.title}</div>
                                        {!notif.read && (
                                            <button onClick={() => markAsRead(notif.id)} className="btn btn-ghost btn-xs btn-circle" title="Mark as read">
                                                <Check className="w-3 h-3" />
                                            </button>
                                        )}
                                    </div>
                                    <p className="text-xs mt-1 leading-relaxed">{notif.message}</p>
                                    <div className="text-[10px] opacity-40 mt-2 text-right">
                                        {new Date(notif.timestamp).toLocaleTimeString()}
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            </div>
        </div>
        
        <div className="dropdown dropdown-end">
            <div tabIndex={0} role="button" className="btn btn-ghost">
                <Sun className="h-5 w-5 stroke-1.5" />
                Theme
                <svg width="12px" height="12px" className="h-2 w-2 fill-current opacity-60 inline-block" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2048 2048"><path d="M1799 349l242 241-1017 1017L7 590l242-241 775 775 775-775z"></path></svg>
            </div>
            <ul tabIndex={0} className="dropdown-content z-1 p-2 shadow-2xl bg-base-300 rounded-box w-52 max-h-96 overflow-y-auto">
                {daisyUIThemes.filter(Boolean).map((item) => (
                    <li key={item}>
                        <input
                            type="radio"
                            name="theme-dropdown"
                            className="theme-controller btn btn-sm btn-block btn-ghost justify-start"
                      aria-label={item ? item.charAt(0).toUpperCase() + item.slice(1) : 'Theme option'}
                            value={item}
                            checked={theme === item}
                            onChange={(e) => setTheme(e.target.value)}
                        />
                    </li>
                ))}
            </ul>
        </div>
      </div>
    </header>
  );
};

export default Header;