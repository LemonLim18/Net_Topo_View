import { useEffect, useRef, useState, useCallback } from 'react';
import { API_CONFIG } from '../config';

/* ─── Types ─────────────────────────────────────────────────────── */

export interface IncidentBrief {
  id: string;
  anomaly_type: string;
  severity: string;
  affected_devices: string[];
  status: string;
  root_cause_summary: string;
  created_at: string;
}

export interface TelemetryMessage {
  type: string;
  tick?: number;
  timestamp?: string;
  devices?: any[];
  recent_security_events?: any[];
  open_incidents?: number;
  // Incident push events
  incident?: IncidentBrief;
}

/* ─── Hook ──────────────────────────────────────────────────────── */

export function useTelemetry() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const backoff = useRef(1000);

  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<TelemetryMessage | null>(null);
  const [openIncidents, setOpenIncidents] = useState(0);
  const [recentIncidents, setRecentIncidents] = useState<IncidentBrief[]>([]);

  // Listeners registered by consumers (e.g. NotificationContext)
  const listenersRef = useRef<Set<(msg: TelemetryMessage) => void>>(new Set());

  const subscribe = useCallback((fn: (msg: TelemetryMessage) => void) => {
    listenersRef.current.add(fn);
    return () => { listenersRef.current.delete(fn); };
  }, []);

  useEffect(() => {
    let disposed = false;

    function connect() {
      if (disposed) return;
      const wsBase = API_CONFIG.API_URL.replace(/^http/, 'ws');
      const ws = new WebSocket(`${wsBase}/api/telemetry`);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        backoff.current = 1000;
        console.debug('[telemetry] connected');
      };

      ws.onmessage = (e) => {
        try {
          const data: TelemetryMessage = JSON.parse(e.data);
          setLastMessage(data);

          // Track open incidents from periodic snapshots
          if (data.type === 'metrics' && data.open_incidents !== undefined) {
            setOpenIncidents(data.open_incidents);
          }

          // Track incident push events
          if (
            data.incident &&
            (data.type === 'incident_created' || data.type === 'incident_updated' || data.type === 'incident_resolved')
          ) {
            setRecentIncidents((prev) => {
              const filtered = prev.filter((i) => i.id !== data.incident!.id);
              return [data.incident!, ...filtered].slice(0, 10);
            });
            if (data.type === 'incident_created') {
              setOpenIncidents((prev) => prev + 1);
            } else if (data.type === 'incident_resolved') {
              setOpenIncidents((prev) => Math.max(0, prev - 1));
            }
          }

          // Notify all subscribers
          listenersRef.current.forEach((fn) => {
            try { fn(data); } catch { /* ignore */ }
          });
        } catch {
          /* ignore parse errors */
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        console.debug('[telemetry] disconnected, reconnecting in', backoff.current, 'ms');
        if (!disposed) {
          reconnectTimer.current = setTimeout(() => {
            backoff.current = Math.min(backoff.current * 2, 30000);
            connect();
          }, backoff.current);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      disposed = true;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, []);

  return { isConnected, lastMessage, openIncidents, recentIncidents, subscribe };
}
