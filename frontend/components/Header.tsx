import React from 'react';
import { Bell, Check, ListChecks, Sun } from 'lucide-react';
import { useNotification } from '../contexts/NotificationContext';
import { useTaskProgress } from '../contexts/TaskProgressContext';
import type { ViewId } from '../types';

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
    setView?: (view: ViewId) => void;
}

const Header: React.FC<HeaderProps> = ({ theme, setTheme, setView }) => {
  const { notifications, markAsRead, clearAll } = useNotification();
  const unreadCount = notifications.filter(n => !n.read).length;
    const { tasks, checkTask, removeTask, clearCheckedTasks } = useTaskProgress();
    const hasProcessingTask = tasks.some((t) => t.status === 'processing');
    const hasUncheckedDoneTask = tasks.some((t) => (t.status === 'done' || t.status === 'failed') && !t.checked);
    const hasCheckedTasks = tasks.some((t) => t.checked);

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
            <div tabIndex={0} className="mt-3 z-1 card card-compact dropdown-content w-80 bg-base-100 shadow-xl border border-base-200">
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
                            notifications.map(notif => {
                                const isClickable = !!notif.targetView && !!setView;
                                const handleCardClick = () => {
                                    markAsRead(notif.id);
                                    if (notif.targetView && setView) {
                                        setView(notif.targetView);
                                    }
                                    if (document.activeElement instanceof HTMLElement) {
                                        document.activeElement.blur();
                                    }
                                };
                                return (
                                    <div 
                                        key={notif.id} 
                                        onClick={handleCardClick}
                                        className={`p-3 rounded-lg border transition-all duration-200 
                                            ${notif.read 
                                                ? 'bg-base-100 border-base-200 opacity-60' 
                                                : 'bg-base-200 border-base-300 shadow-sm'}
                                            ${isClickable 
                                                ? 'cursor-pointer hover:bg-base-300 hover:border-base-400 active:scale-[0.98]' 
                                                : ''}`}
                                    >
                                        <div className="flex justify-between items-start gap-2">
                                            <div className={`font-semibold text-sm ${getIconColor(notif.type)}`}>{notif.title}</div>
                                            {!notif.read && (
                                                <button 
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        markAsRead(notif.id);
                                                    }} 
                                                    className="btn btn-ghost btn-xs btn-circle hover:bg-base-300" 
                                                    title="Mark as read"
                                                >
                                                    <Check className="w-3 h-3" />
                                                </button>
                                            )}
                                        </div>
                                        <p className="text-xs mt-1 leading-relaxed text-base-content/85">{notif.message}</p>
                                        <div className="text-[10px] opacity-40 mt-2 text-right">
                                            {new Date(notif.timestamp).toLocaleTimeString()}
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </div>
                </div>
            </div>
        </div>

        {/* Task List */}
        <div className="dropdown dropdown-end">
            <div tabIndex={0} role="button" className="btn btn-ghost btn-circle">
                <div className="indicator">
                    <ListChecks className="h-5 w-5" />
                    {hasProcessingTask ? (
                        <span className="absolute -right-1 flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-yellow-500"></span>
                        </span>
                    ) : hasUncheckedDoneTask ? (
                        <span className="absolute -right-1 flex h-2 w-2">
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-success"></span>
                        </span>
                    ) : null}
                </div>
            </div>
            <div tabIndex={0} className="mt-3 z-1 card card-compact dropdown-content w-96 bg-base-100 shadow-xl border border-base-200">
                <div className="card-body">
                    <div className="flex justify-between items-center mb-2">
                        <span className="font-bold text-lg">Background Actions</span>
                        <button
                            onClick={clearCheckedTasks}
                            className="btn btn-xs btn-ghost text-primary"
                            disabled={!hasCheckedTasks}
                        >
                            Clear checked
                        </button>
                    </div>
                    <div className="max-h-96 overflow-y-auto space-y-2">
                        {tasks.length === 0 ? (
                            <div className="text-center py-8 opacity-50 text-sm">No active background tasks</div>
                        ) : (
                            tasks.map((task) => {
                                const isDone = task.status === 'done' || task.status === 'failed';
                                return (
                                    <div
                                        key={task.id}
                                        className={`p-3 rounded-lg border flex items-start gap-3
                                          ${isDone
                                            ? 'bg-base-200/50 border-base-300'
                                            : 'bg-yellow-500/5 border-yellow-500/20'}`}
                                    >
                                        <div className="mt-0.5">
                                            {task.status === 'processing' ? (
                                                <span className="flex h-2 w-2 relative mt-1.5">
                                                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75"></span>
                                                    <span className="relative inline-flex rounded-full h-2 w-2 bg-yellow-500"></span>
                                                </span>
                                            ) : (
                                                <span className="relative inline-flex rounded-full h-2 w-2 bg-success mt-1.5"></span>
                                            )}
                                        </div>

                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="text-xs font-bold truncate">{task.message}</span>
                                                <span className={`badge badge-[5px] uppercase text-[10px] font-bold py-1 px-1.5
                                                  ${task.status === 'processing'
                                                    ? 'badge-warning text-warning-content'
                                                    : task.status === 'failed'
                                                      ? 'badge-error text-error-content'
                                                      : 'badge-success text-success-content'}`}
                                                >
                                                    {task.status}
                                                </span>
                                            </div>
                                            {task.subtitle && (
                                                <p className="text-[10px] opacity-60 mt-0.5 truncate">{task.subtitle}</p>
                                            )}
                                            {task.status === 'processing' && task.progressText && (
                                                <div className="mt-2 text-[10px] font-mono text-warning/90 bg-slate-950/40 px-2 py-1 rounded border border-warning/10 animate-pulse">
                                                    {task.progressText}
                                                </div>
                                            )}
                                            {isDone && (
                                                <p className={`text-[10px] mt-1 ${task.status === 'failed' ? 'text-error/90' : 'text-success/90'}`}>
                                                    {task.status === 'failed' ? 'Action failed.' : 'Action completed successfully.'}
                                                </p>
                                            )}
                                        </div>

                                        {isDone && (
                                            <button
                                                onClick={() => {
                                                    checkTask(task.id);
                                                    removeTask(task.id);
                                                }}
                                                className="btn btn-ghost btn-xs btn-circle hover:bg-success/20 text-success shrink-0"
                                                title="Dismiss Task"
                                            >
                                                <Check className="w-3 h-3" />
                                            </button>
                                        )}
                                    </div>
                                );
                            })
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