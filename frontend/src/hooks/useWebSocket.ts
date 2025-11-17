import { useEffect, useRef, useState, useCallback } from 'react';
import type { TopologyData } from '../../types';
// For temporary local development you can seed topology from the sample
// data in `frontend/constants.ts` instead of connecting to the real
// WebSocket. To enable, set `VITE_USE_LOCAL_TOPOLOGY=true` in a Vite
// `.env` file or rely on `import.meta.env.MODE === 'development'.
//
// Example `.env` entry (project root):
// VITE_USE_LOCAL_TOPOLOGY=true
//
// When enabled the hook will return the mock `topology` immediately
// and skip creating a real WebSocket connection. This makes UI
// development faster and deterministic.
import { mockTopologyData } from '../../constants';

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

interface WebSocketMessage {
  type: string;
  [key: string]: any;
}

interface UseWebSocketOptions {
  url: string;
  onTopologyUpdate?: (topology: TopologyData) => void;
  onError?: (error: Event) => void;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
}

export const useWebSocket = ({
  url,
  onTopologyUpdate,
  onError,
  reconnectInterval = 3000,
  maxReconnectAttempts = 10,
}: UseWebSocketOptions) => {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const [topology, setTopology] = useState<TopologyData | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const pingIntervalRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isManualCloseRef = useRef(false);

  // Toggle to use local sample topology for development.
  // If `VITE_USE_LOCAL_TOPOLOGY` is set to 'true' in the environment,
  // the hook will seed `topology` from local `topologyData` and avoid
  // creating a WebSocket connection. You can also enable this by
  // running Vite in `development` mode — change the condition below
  // if you prefer a different behavior.
  const USE_LOCAL_TOPOLOGY = (import.meta.env?.VITE_USE_LOCAL_TOPOLOGY === 'true');
  // Debug info: will be removed after troubleshooting
  // Shows the resolved boolean and the raw env values injected by Vite
  // (check browser console when running `npm run dev`).
  // eslint-disable-next-line no-console
  console.debug('[useWebSocket] USE_LOCAL_TOPOLOGY=', USE_LOCAL_TOPOLOGY, 'raw=', import.meta.env?.VITE_USE_LOCAL_TOPOLOGY, 'MODE=', import.meta.env?.MODE);

  const connect = useCallback(() => {
    // eslint-disable-next-line no-console
    console.debug('[useWebSocket] connect() url=', url);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    isManualCloseRef.current = false;
    setStatus('connecting');

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('WebSocket connected');
        setStatus('connected');
        reconnectAttemptsRef.current = 0;

        // Start ping interval (send ping every 30 seconds)
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
        }
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);
      };

      ws.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data);
          
          switch (message.type) {
            case 'initial_topology':
            case 'topology_response':
              if (message.topology) {
                setTopology(message.topology);
                onTopologyUpdate?.(message.topology);
              }
              break;
            
            case 'topology_update':
              if (message.full_topology) {
                setTopology(message.full_topology);
                onTopologyUpdate?.(message.full_topology);
                console.log('Topology updated:', {
                  timestamp: message.timestamp,
                  changes: message.changes?.length || 0,
                });
              }
              break;
            
            case 'pong':
              // Keep-alive response
              break;
            
            case 'ack':
              // Acknowledgment
              break;
            
            default:
              console.warn('Unknown message type:', message.type);
          }
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
        }
      };

      ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        setStatus('error');
        onError?.(error);
      };

      ws.onclose = (event) => {
        console.log('WebSocket closed:', event.code, event.reason);
        setStatus('disconnected');
        
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }

        // Attempt to reconnect if not manually closed
        if (!isManualCloseRef.current && reconnectAttemptsRef.current < maxReconnectAttempts) {
          reconnectAttemptsRef.current += 1;
          console.log(`Reconnecting... (attempt ${reconnectAttemptsRef.current}/${maxReconnectAttempts})`);
          
          reconnectTimeoutRef.current = setTimeout(() => {
            connect();
          }, reconnectInterval);
        } else if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
          console.error('Max reconnection attempts reached');
          setStatus('error');
        }
      };
    } catch (error) {
      console.error('Failed to create WebSocket:', error);
      setStatus('error');
      onError?.(error as Event);
    }
  }, [url, onTopologyUpdate, onError, reconnectInterval, maxReconnectAttempts]);

  const disconnect = useCallback(() => {
    isManualCloseRef.current = true;
    
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    
    setStatus('disconnected');
  }, []);

  const requestTopology = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'request_topology' }));
    }
  }, []);

  useEffect(() => {
    if (USE_LOCAL_TOPOLOGY) {
      // Seed topology from local constants for quick UI/dev work.
      // eslint-disable-next-line no-console
      console.debug('[useWebSocket] using local topologyData (no WebSocket)');
      setTopology(mockTopologyData as TopologyData);
      onTopologyUpdate?.(mockTopologyData as TopologyData);
      setStatus('connected');
      // No WebSocket connection created when using the local topology.
      return () => {
        /* no-op cleanup for local topology */
      };
    }

    // eslint-disable-next-line no-console
    console.debug('[useWebSocket] USE_LOCAL_TOPOLOGY is false; attempting WebSocket connect');
    connect();
    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  return {
    status,
    topology,
    connect,
    disconnect,
    requestTopology,
  };
};

