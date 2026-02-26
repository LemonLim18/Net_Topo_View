import React, { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';

/** Current connection state of an SSH terminal tab */
export type SSHConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error';

interface SSHTerminalProps {
  /** WebSocket URL for this SSH session */
  wsUrl: string;
  /** Whether this terminal tab is currently visible/active */
  isActive: boolean;
  /** Callback when the SSH session disconnects */
  onDisconnect?: () => void;
  /** Callback when connection error occurs */
  onError?: (message: string) => void;
  /** Callback when connection state changes */
  onConnectionChange?: (state: SSHConnectionState) => void;
}

const SSHTerminal: React.FC<SSHTerminalProps> = ({
  wsUrl,
  isActive,
  onDisconnect,
  onError,
  onConnectionChange,
}) => {
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const initializedRef = useRef(false);

  // Initialize terminal + WebSocket once
  useEffect(() => {
    if (!terminalRef.current || initializedRef.current) return;
    initializedRef.current = true;

    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: 'block',
      fontSize: 14,
      fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", Menlo, Monaco, monospace',
      theme: {
        background: '#1d232a',
        foreground: '#a6adbb',
        cursor: '#a6adbb',
        selectionBackground: '#3d4451',
        black: '#1d232a',
        red: '#f87272',
        green: '#36d399',
        yellow: '#fbbd23',
        blue: '#3abff8',
        magenta: '#d926a9',
        cyan: '#22d3ee',
        white: '#a6adbb',
      },
      allowProposedApi: true,
    });

    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(webLinksAddon);
    term.open(terminalRef.current);

    // Initial fit
    try {
      fitAddon.fit();
    } catch {
      // Container may not be visible yet
    }

    xtermRef.current = term;
    fitAddonRef.current = fitAddon;

    // Connect WebSocket — append JWT token for backend auth
    term.writeln('\x1b[36mConnecting to SSH session...\x1b[0m');
    onConnectionChange?.('connecting');
    const token = localStorage.getItem('auth_token');
    const fullUrl = token ? `${wsUrl}?token=${encodeURIComponent(token)}` : wsUrl;
    const ws = new WebSocket(fullUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      term.writeln('\x1b[32mConnected!\x1b[0m\r\n');
      onConnectionChange?.('connected');
      // Send initial resize
      const { cols, rows } = term;
      ws.send(JSON.stringify({ type: 'resize', cols, rows }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'output') {
          term.write(msg.data);
        } else if (msg.type === 'disconnect') {
          term.writeln('\r\n\x1b[31mSSH session disconnected.\x1b[0m');
          onConnectionChange?.('disconnected');
          onDisconnect?.();
        } else if (msg.type === 'error') {
          term.writeln(`\r\n\x1b[31mError: ${msg.data}\x1b[0m`);
          onConnectionChange?.('error');
          onError?.(msg.data);
        }
      } catch {
        // Raw text fallback
        term.write(event.data);
      }
    };

    ws.onerror = () => {
      term.writeln('\r\n\x1b[31mWebSocket error occurred.\x1b[0m');
      onConnectionChange?.('error');
      onError?.('WebSocket connection error');
    };

    ws.onclose = (event) => {
      if (event.code === 4001) {
        term.writeln('\r\n\x1b[31mAuthentication failed. Please log in again.\x1b[0m');
        onConnectionChange?.('error');
      } else if (event.code === 4003) {
        term.writeln('\r\n\x1b[31mAccess denied: not your session.\x1b[0m');
        onConnectionChange?.('error');
      } else {
        term.writeln('\r\n\x1b[33mConnection closed.\x1b[0m');
        onConnectionChange?.('disconnected');
      }
    };

    // Forward keystrokes to SSH
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });

    // Forward resize events
    term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    return () => {
      ws.close();
      term.dispose();
      initializedRef.current = false;
    };
  }, [wsUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fit when tab becomes active or container resizes
  useEffect(() => {
    if (!isActive || !fitAddonRef.current) return;

    const doFit = () => {
      try {
        fitAddonRef.current?.fit();
      } catch {
        // ignore
      }
    };

    // Small delay to allow container to render
    const timer = setTimeout(doFit, 100);

    const observer = new ResizeObserver(() => doFit());
    if (terminalRef.current) {
      observer.observe(terminalRef.current);
    }

    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [isActive]);

  // Focus terminal when tab becomes active
  useEffect(() => {
    if (isActive && xtermRef.current) {
      xtermRef.current.focus();
    }
  }, [isActive]);

  return (
    <div
      ref={terminalRef}
      className="w-full h-full"
      style={{ padding: '4px', backgroundColor: '#1d232a' }}
    />
  );
};

export default SSHTerminal;
