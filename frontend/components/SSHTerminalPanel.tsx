import React, { useState, useCallback } from 'react';
import { X, Minus, Maximize2, Terminal as TerminalIcon } from 'lucide-react';
import SSHTerminal, { type SSHConnectionState } from './SSHTerminal';
import { sshApi } from '../src/services/api';
import { Toast } from '../src/utils/swal';

export interface SSHTab {
  sessionId: string;
  hostname: string;
  host: string;
  wsUrl: string;
}

interface SSHTerminalPanelProps {
  tabs: SSHTab[];
  activeTabId: string | null;
  onSelectTab: (sessionId: string) => void;
  onCloseTab: (sessionId: string) => void;
  /** Height of the panel in pixels */
  height: number;
  /** Whether the panel is minimized */
  minimized: boolean;
  onToggleMinimize: () => void;
  onMouseDown: (e: React.MouseEvent) => void;
  /** Per-tab connection states (keyed by sessionId) */
  tabStates: Record<string, SSHConnectionState>;
  /** Fired when a tab's connection state changes */
  onConnectionChange: (sessionId: string, state: SSHConnectionState) => void;
}

/** Tailwind classes for the per-tab connection-state dot */
const STATE_DOT: Record<SSHConnectionState, string> = {
  connecting: 'bg-warning animate-pulse',
  connected:  'bg-success',
  disconnected: 'bg-base-content/40',
  error: 'bg-error',
};

const SSHTerminalPanel: React.FC<SSHTerminalPanelProps> = ({
  tabs,
  activeTabId,
  onSelectTab,
  onCloseTab,
  height,
  minimized,
  onToggleMinimize,
  onMouseDown,
  tabStates,
  onConnectionChange,
}) => {
  const handleCloseTab = useCallback(async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    try {
      await sshApi.closeSession(sessionId);
    } catch {
      // Session may already be closed
    }
    onCloseTab(sessionId);
  }, [onCloseTab]);

  if (tabs.length === 0) return null;

  return (
    <div
      className="absolute bottom-0 left-0 right-0 flex flex-col bg-base-100 border-t border-base-300 shadow-[0_-4px_16px_rgba(0,0,0,0.3)] z-30"
      style={{ height: minimized ? '36px' : `${height}px` }}
    >
      {/* Resize handle */}
      {!minimized && (
        <div
          className="w-full h-2 shrink-0 cursor-row-resize flex items-center justify-center hover:bg-base-300/50"
          onMouseDown={onMouseDown}
          role="separator"
          aria-label="Resize terminal panel"
        >
          <div className="w-12 h-1 bg-base-300/70 rounded-full" />
        </div>
      )}

      {/* Tab bar */}
      <div className="flex items-center bg-base-200/80 px-2 shrink-0 h-8 gap-1">
        <TerminalIcon size={14} className="text-primary mr-1 shrink-0" />
        <div className="flex items-center gap-0.5 overflow-x-auto flex-1 scrollbar-none">
          {tabs.map((tab) => (
            <button
              key={tab.sessionId}
              className={`btn btn-xs gap-1 rounded-b-none min-w-0 max-w-40 ${
                activeTabId === tab.sessionId
                  ? 'btn-primary'
                  : 'btn-ghost text-base-content/70'
              }`}
              onClick={() => onSelectTab(tab.sessionId)}
              title={`${tab.hostname} (${tab.host})`}
            >
              {/* Connection-state dot */}
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  STATE_DOT[tabStates[tab.sessionId] ?? 'connecting']
                }`}
              />
              <span className="truncate text-xs">{tab.hostname}</span>
              <span
                className="hover:bg-error/20 rounded-sm p-0.5 shrink-0"
                onClick={(e) => handleCloseTab(e, tab.sessionId)}
              >
                <X size={10} />
              </span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 shrink-0 ml-auto">
          <button
            className="btn btn-ghost btn-xs btn-square"
            onClick={onToggleMinimize}
            title={minimized ? 'Maximize' : 'Minimize'}
          >
            {minimized ? <Maximize2 size={12} /> : <Minus size={12} />}
          </button>
        </div>
      </div>

      {/* Terminal content area */}
      {!minimized && (
        <div className="flex-1 overflow-hidden relative">
          {tabs.map((tab) => (
            <div
              key={tab.sessionId}
              className="absolute inset-0"
              style={{ display: activeTabId === tab.sessionId ? 'block' : 'none' }}
            >
              <SSHTerminal
                wsUrl={tab.wsUrl}
                isActive={activeTabId === tab.sessionId}
                onConnectionChange={(state) => onConnectionChange(tab.sessionId, state)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default SSHTerminalPanel;
