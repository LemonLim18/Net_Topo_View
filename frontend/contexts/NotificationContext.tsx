import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { AppNotification, NotificationType, ViewId } from '../types';
import { ENABLE_DRIFT_NOTIFICATIONS, API_CONFIG } from '../src/config';

/* ── Telemetry WS listener (incident push events) ───────────── */
// Use configured backend URL when available so frontend works across networks.
const _wsBase = (API_CONFIG?.API_URL || `http://${window.location.hostname}:8000`).replace(/^http/, 'ws').replace(/\/$/, '');
const TELEMETRY_WS = `${_wsBase}/api/telemetry`;

interface NotificationContextType {
    notifications: AppNotification[];
    addNotification: (title: string, message: string, type: NotificationType, targetView?: ViewId) => void;
    markAsRead: (id: string) => void;
    clearAll: () => void;
    removeNotification: (id: string) => void;
    openIncidentCount: number;
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined);

export const NotificationProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [notifications, setNotifications] = useState<AppNotification[]>([]);
    const [openIncidentCount, setOpenIncidentCount] = useState(0);
    const wsRef = useRef<WebSocket | null>(null);
    const recentNotificationRef = useRef<Map<string, number>>(new Map());

    const addNotification = useCallback((title: string, message: string, type: NotificationType, targetView?: ViewId) => {
        const key = `${type}|${title}|${message}`;
        const now = Date.now();
        const DUPLICATE_WINDOW_MS = 15000;
        const lastAt = recentNotificationRef.current.get(key);

        // Drop repeated telemetry toasts that arrive in short bursts.
        if (typeof lastAt === 'number' && now - lastAt < DUPLICATE_WINDOW_MS) {
            return;
        }

        recentNotificationRef.current.set(key, now);

        // Lightweight cleanup to keep the in-memory dedupe table bounded.
        if (recentNotificationRef.current.size > 200) {
            for (const [k, ts] of recentNotificationRef.current.entries()) {
                if (now - ts > DUPLICATE_WINDOW_MS * 4) {
                    recentNotificationRef.current.delete(k);
                }
            }
        }

        const newNotif: AppNotification = {
            id: Math.random().toString(36).substring(2, 9),
            title,
            message,
            type,
            timestamp: Date.now(),
            read: false,
            targetView,
        };
        setNotifications(prev => [newNotif, ...prev]);

        // Browser notification for critical severity
        if (type === 'error' && Notification.permission === 'granted') {
            new Notification(title, { body: message, icon: '/favicon.ico' });
        }
    }, []);

    /* ── Telemetry WebSocket bridge ───────────────────────────── */
    useEffect(() => {
        let retryDelay = 2000;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let alive = true;
        let intentionallyClosing = false;

        // Request browser notification permission once
        if (Notification.permission === 'default') {
            Notification.requestPermission();
        }

        function connect() {
            if (!alive) return;
            const ws = new WebSocket(TELEMETRY_WS);
            wsRef.current = ws;

            ws.onopen = () => { retryDelay = 2000; };
            ws.onerror = () => {
                // Suppress noisy browser error logs during expected teardown/reconnect races.
                if (intentionallyClosing || !alive) return;
            };


            ws.onmessage = (ev) => {
                try {
                    const msg = JSON.parse(ev.data);

                    // Track open incident count from periodic snapshots
                    if (msg.open_incidents !== undefined) {
                        setOpenIncidentCount(msg.open_incidents);
                    }

                    // Push events from orchestrator
                    if (msg.event === 'incident_created') {
                        const sev = msg.data?.severity ?? 'medium';
                        const notifType: NotificationType = sev === 'critical' ? 'error' : sev === 'high' ? 'warning' : 'info';
                        addNotification(
                            'New Incident Detected',
                            `${(msg.data?.anomaly_type ?? 'anomaly').replace(/_/g, ' ')} on ${(msg.data?.affected_devices ?? []).join(', ') || 'unknown device'}`,
                            notifType,
                            'overview',
                        );
                    } else if (msg.event === 'incident_resolved') {
                        addNotification('Incident Resolved', msg.data?.id ?? 'incident', 'success', 'overview');
                    } else if (msg.event === 'incident_updated' && msg.data?.status === 'approved') {
                        addNotification('Incident Approved', `Playbook ready for deployment: ${msg.data?.id ?? ''}`, 'info', 'approval_queue');
                    } else if (msg.event === 'security_event') {
                        const sev = String(msg.data?.severity ?? 'info').toLowerCase();
                        if (sev === 'low' || sev === 'info') return; // Filter out low severity spam
                        const notifType: NotificationType = sev === 'critical' ? 'error' : sev === 'high' ? 'warning' : 'info';
                        addNotification(
                            'Syslog Event',
                            `${msg.data?.event_type ?? 'event'} from ${msg.data?.source_ip ?? 'unknown source'}${msg.data?.description ? `: ${msg.data.description}` : ''}`,
                            notifType,
                            'net_perf',
                        );
                    } else if (msg.event === 'device_syslog_event') {
                        const sev = String(msg.data?.severity ?? 'info').toLowerCase();
                        if (sev === 'low' || sev === 'info') return; // Filter out low severity spam
                        const notifType: NotificationType = sev === 'critical' ? 'error' : sev === 'high' ? 'warning' : 'info';
                        const source = msg.data?.source_host ?? msg.data?.source_ip ?? 'unknown source';
                        const mnemonic = msg.data?.mnemonic ?? msg.data?.facility ?? 'syslog';
                        addNotification(
                            'Device Syslog',
                            `${mnemonic} from ${source}${msg.data?.message ? `: ${msg.data.message}` : ''}`,
                            notifType,
                            'net_perf',
                        );
                    }

                    // Phase 13 — Drift detection push events
                    if (ENABLE_DRIFT_NOTIFICATIONS && msg.event === 'drift_check_complete') {
                        const dCount = msg.data?.drift_detected ?? 0;
                        if (dCount > 0) {
                            addNotification(
                                'Drift Detected',
                                `${dCount} device(s) with configuration drift (${msg.data?.devices_checked ?? 0} checked)`,
                                dCount > 2 ? 'error' : 'warning',
                                'compliance_hub',
                            );
                        }
                    }
                } catch { /* ignore non-JSON frames */ }
            };

            ws.onclose = () => {
                wsRef.current = null;
                if (alive) {
                    timer = setTimeout(connect, retryDelay);
                    retryDelay = Math.min(retryDelay * 1.5, 30000);
                }
            };
        }

        connect();
        return () => {
            alive = false;
            intentionallyClosing = true;
            if (timer) clearTimeout(timer);
            if (wsRef.current && wsRef.current.readyState < WebSocket.CLOSING) {
                wsRef.current.close(1000, 'Notification provider unmount');
            }
            wsRef.current = null;
        };
    }, [addNotification]);

    const markAsRead = useCallback((id: string) => {
        setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
    }, []);

    const removeNotification = useCallback((id: string) => {
        setNotifications(prev => prev.filter(n => n.id !== id));
    }, []);

    const clearAll = useCallback(() => {
        setNotifications([]);
    }, []);

    return (
        <NotificationContext.Provider value={{ notifications, addNotification, markAsRead, clearAll, removeNotification, openIncidentCount }}>
            {children}
        </NotificationContext.Provider>
    );
};

export const useNotification = () => {
    const context = useContext(NotificationContext);
    if (!context) {
        throw new Error('useNotification must be used within a NotificationProvider');
    }
    return context;
};
