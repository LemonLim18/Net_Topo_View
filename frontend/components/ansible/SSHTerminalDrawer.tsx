import React, { useCallback, useRef } from 'react';
import { Plus, Terminal as TerminalIcon } from 'lucide-react';
import SSHTerminalPanel, { type SSHTab }   from '../SSHTerminalPanel';
import SSHConnectModal                       from '../SSHConnectModal';
import { sshApi }                            from '../../src/services/api';
import { Toast }                             from '../../src/utils/swal';
import type { SSHConnectionState }           from '../SSHTerminal';

export type { SSHTab };
export type { SSHConnectionState };

interface SSHTerminalDrawerProps {
  tabs:            SSHTab[];
  activeTabId:     string | null;
  tabStates:       Record<string, SSHConnectionState>;
  height:          number;
  minimized:       boolean;
  pendingHostname: string;
  pendingHost:     string;
  modalOpen:       boolean;

  onSelectTab:       (sid: string) => void;
  onCloseTab:        (sid: string) => void;
  onConnectionChange:(sid: string, state: SSHConnectionState) => void;
  onToggleMinimize:  () => void;
  onOpenModal:       (hostname: string, host: string) => void;
  onCloseModal:      () => void;
  onTabAdded:        (tab: SSHTab) => void;
  onHeightChange:    (h: number) => void;
}

const SSHTerminalDrawer: React.FC<SSHTerminalDrawerProps> = ({
  tabs, activeTabId, tabStates, height, minimized,
  pendingHostname, pendingHost, modalOpen,
  onSelectTab, onCloseTab, onConnectionChange,
  onToggleMinimize, onOpenModal, onCloseModal, onTabAdded, onHeightChange,
}) => {
  /** y-position when drag started */
  const dragStartY   = useRef<number>(0);
  const dragStartH   = useRef<number>(0);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    dragStartY.current = e.clientY;
    dragStartH.current = height;
    const onMove = (ev: MouseEvent) => {
      const delta = dragStartY.current - ev.clientY;
      onHeightChange(Math.max(80, Math.min(600, dragStartH.current + delta)));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [height, onHeightChange]);

  const handleConnect = useCallback(async (
    hostname: string, _host: string, username: string, password: string, port: number, saveToVault: boolean,
  ) => {
    try {
      const { session_id, host } = await sshApi.connectWithCreds(
        hostname, username, password, port, saveToVault,
      );
      const wsUrl = sshApi.getWsUrl(session_id);
      onTabAdded({ sessionId: session_id, hostname, host, wsUrl });
      onCloseModal();
      if (minimized) onToggleMinimize();
      Toast.success('SSH Connected', `Connected to ${hostname}`);
    } catch (err: any) {
      const msg = err.message || 'Failed to connect';
      const isGateway = msg.includes('502') || msg.toLowerCase().includes('bad gateway');
      Toast.error(
        isGateway ? 'SSH Gateway Error' : 'SSH Connection Failed',
        isGateway
          ? 'The SSH backend is unreachable (502 Bad Gateway). Ensure the server is running.'
          : msg,
      );
      throw err;
    }
  }, [onTabAdded, onCloseModal, minimized, onToggleMinimize]);


  return (
    <>
      {/* Floating "Open terminal" button when there are no tabs or drawer is closed */}
      {tabs.length === 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-t border-base-300 bg-base-200 flex-shrink-0">
          <TerminalIcon className="h-4 w-4 opacity-40" />
          <span className="text-xs opacity-40 flex-grow">No terminal sessions</span>
          <button
            className="btn btn-xs btn-ghost gap-1"
            onClick={() => onOpenModal('', '')}
            title="Open new SSH terminal"
          >
            <Plus className="h-3.5 w-3.5" /> New terminal
          </button>
        </div>
      )}

      {tabs.length > 0 && (
        <div className="relative flex-shrink-0" style={{ height: minimized ? 36 : height }}>
          <SSHTerminalPanel
            tabs={tabs}
            activeTabId={activeTabId}
            onSelectTab={onSelectTab}
            onCloseTab={onCloseTab}
            height={height}
            minimized={minimized}
            onToggleMinimize={onToggleMinimize}
            onMouseDown={handleMouseDown}
            tabStates={tabStates}
            onConnectionChange={onConnectionChange}
          />
        </div>
      )}

      {/* Connect modal */}
      <SSHConnectModal
        hostname={pendingHostname}
        host={pendingHost}
        isOpen={modalOpen}
        onConnect={handleConnect}
        onClose={onCloseModal}
      />
    </>
  );
};

export default SSHTerminalDrawer;
