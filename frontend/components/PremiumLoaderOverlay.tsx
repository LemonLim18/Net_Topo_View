import React, { useEffect, useState, useRef } from 'react';
import { Sparkles, Terminal, Database, Server, RefreshCw, Layers } from 'lucide-react';
import { useTaskProgress } from '../contexts/TaskProgressContext';

interface PremiumLoaderOverlayProps {
  type: 'ai' | 'check' | 'sync' | 'capture' | 'refresh' | 'default';
  message: string;
  subtitle?: string;
}

export const PremiumLoaderOverlay: React.FC<PremiumLoaderOverlayProps> = ({
  type,
  message,
  subtitle,
}) => {
  const { addTask, updateTask, removeTask } = useTaskProgress();
  const [taskId] = useState(() => `${type}-${Math.random().toString(36).substr(2, 9)}`);
  const [isMinimized, setIsMinimized] = useState(false);
  const isMinimizedRef = useRef(false);

  const [tipIndex, setTipIndex] = useState(0);

  const tips: Record<string, string[]> = {
    ai: [
      'AI is synthesizing configuration logic…',
      'Analyzing playbook dependencies…',
      'Structuring task variables and host definitions…',
      'Applying validation rules to intent parameters…',
    ],
    check: [
      'Establishing SSH connection to endpoints…',
      'Executing Ansible dry-run (check mode)…',
      'Analyzing potential configuration diffs…',
      'Retrieving task status and execution warnings…',
    ],
    sync: [
      'Connecting to NetBox REST API…',
      'Fetching device inventory tables…',
      'Reconciling local cached database with NetBox…',
      'Updating device status and interfaces…',
    ],
    capture: [
      'Establishing SSH credentials handshake…',
      'Executing command "show running-config"…',
      'Streaming configuration buffer…',
      'Storing snapshot baseline in DB…',
    ],
    refresh: [
      'Scanning network segments…',
      'Polling active topology endpoints…',
      'Parsing LLDP/CDP neighbor tables…',
      'Updating real-time layout graph…',
    ],
    default: [
      'Executing background transaction…',
      'Please wait while we process this task…',
      'Synchronizing operations…',
    ],
  };

  const activeTips = tips[type] || tips.default;

  const handleMinimize = () => {
    isMinimizedRef.current = true;
    setIsMinimized(true);
    updateTask(taskId, { minimized: true });
  };

  useEffect(() => {
    addTask({
      id: taskId,
      type,
      message,
      subtitle,
      status: 'processing',
      minimized: false,
      progressText: activeTips[0],
    });

    return () => {
      if (isMinimizedRef.current) {
        updateTask(taskId, { status: 'done' });
      } else {
        removeTask(taskId);
      }
    };
  }, [taskId, type, message, subtitle]);

  useEffect(() => {
    const timer = setInterval(() => {
      setTipIndex((prev) => {
        const next = (prev + 1) % activeTips.length;
        updateTask(taskId, { progressText: activeTips[next] });
        return next;
      });
    }, 3500);
    return () => clearInterval(timer);
  }, [activeTips, taskId]);

  const getIcon = () => {
    switch (type) {
      case 'ai':
        return <Sparkles className="w-8 h-8 text-cyan-400" />;
      case 'check':
        return <Terminal className="w-8 h-8 text-amber-400" />;
      case 'sync':
        return <Database className="w-8 h-8 text-emerald-400" />;
      case 'capture':
        return <Server className="w-8 h-8 text-indigo-400" />;
      case 'refresh':
        return <RefreshCw className="w-8 h-8 text-purple-400" />;
      default:
        return <Layers className="w-8 h-8 text-primary" />;
    }
  };

  if (isMinimized) return null;

  return (
    <div 
      className="fixed inset-0 flex items-center justify-center z-[9999] pointer-events-none"
      role="alert"
      aria-busy="true"
      aria-label={message}
    >
      {/* Styles for custom keyframes and animations */}
      <style>{`
        @keyframes spin-clockwise {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
        @keyframes spin-counterclockwise {
          0% { transform: rotate(360deg); }
          100% { transform: rotate(0deg); }
        }
        @keyframes pulse-glow {
          0%, 100% { opacity: 0.25; transform: scale(1); }
          50% { opacity: 0.55; transform: scale(1.15); }
        }
        .animate-spin-clockwise {
          animation: spin-clockwise 3s linear infinite;
        }
        .animate-spin-counterclockwise {
          animation: spin-counterclockwise 2s linear infinite;
        }
        .animate-pulse-glow {
          animation: pulse-glow 4s ease-in-out infinite;
        }
        .animate-spin-slow {
          animation: spin-clockwise 10s linear infinite;
        }
      `}</style>

      {/* Dimmed backdrop */}
      <div className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm pointer-events-auto" />

      {/* Center Modal Card */}
      <div className="relative flex flex-col items-center max-w-sm w-full p-6 text-center z-10 bg-slate-900/95 border border-slate-800 rounded-2xl shadow-2xl pointer-events-auto">
        {/* Minimize Button */}
        <button 
          onClick={handleMinimize}
          className="absolute top-3 right-3 btn btn-ghost btn-xs btn-circle text-slate-400 hover:text-white hover:bg-white/10"
          title="Minimize to NOC Overlay"
        >
          <span className="text-base font-bold leading-none select-none">−</span>
        </button>

        {/* Radial ambient glow backdrop */}
        <div className="absolute w-[200px] h-[200px] bg-gradient-to-tr from-violet-600/25 via-cyan-500/5 to-indigo-500/20 rounded-full blur-[50px] animate-pulse-glow pointer-events-none" />

        {/* Ring container */}
        <div className="relative w-24 h-24 flex items-center justify-center mb-6 select-none">
          {/* Outer ring */}
          <div className="absolute inset-0 rounded-full border-4 border-t-cyan-500/80 border-r-violet-500/20 border-b-indigo-500/80 border-l-transparent animate-spin-clockwise" />
          
          {/* Inner ring */}
          <div className="absolute w-18 h-18 rounded-full border-4 border-t-violet-500/80 border-r-transparent border-b-cyan-500/80 border-l-fuchsia-500/20 animate-spin-counterclockwise" />

          {/* Central Icon */}
          <div className={`relative p-3.5 rounded-full bg-slate-900/95 border border-slate-800 shadow-2xl flex items-center justify-center ${type === 'refresh' ? 'animate-spin-slow' : ''}`}>
            {getIcon()}
          </div>
        </div>

        {/* Text Details */}
        <div className="space-y-1 select-text">
          <h2 className="text-base font-bold tracking-tight text-white bg-clip-text text-transparent bg-gradient-to-r from-white via-slate-100 to-slate-300">
            {message}
          </h2>
          {subtitle && (
            <p className="text-[11px] text-slate-400 max-w-xs mx-auto">
              {subtitle}
            </p>
          )}
        </div>

        {/* Rotating Progress Detail */}
        <div className="mt-6 h-10 flex flex-col justify-center select-none w-full">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-950/90 border border-slate-800 shadow-inner max-w-xs mx-auto">
            <span className="flex h-1.5 w-1.5 relative shrink-0">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-cyan-500"></span>
            </span>
            <span className="text-[10px] font-medium text-slate-300 truncate">
              {activeTips[tipIndex]}
            </span>
          </div>
        </div>

        {/* Minimal Progress indicator */}
        <div className="flex gap-1.5 mt-3 justify-center select-none">
          {activeTips.map((_, i) => (
            <span
              key={i}
              className={`w-1 h-1 rounded-full transition-all duration-300 ${
                i === tipIndex ? 'bg-cyan-400 w-2.5' : 'bg-slate-700'
              }`}
            />
          ))}
        </div>
      </div>
    </div>
  );
};

export default PremiumLoaderOverlay;
