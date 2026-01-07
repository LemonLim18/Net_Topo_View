import React, { createContext, useContext, useState, useCallback } from 'react';
import type { ReactNode } from 'react';
import type { AppNotification, NotificationType } from '../types';

interface NotificationContextType {
    notifications: AppNotification[];
    addNotification: (title: string, message: string, type: NotificationType) => void;
    markAsRead: (id: string) => void;
    clearAll: () => void;
    removeNotification: (id: string) => void;
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined);

export const NotificationProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [notifications, setNotifications] = useState<AppNotification[]>([]);

    const addNotification = useCallback((title: string, message: string, type: NotificationType) => {
        const newNotif: AppNotification = {
            id: Math.random().toString(36).substring(2, 9),
            title,
            message,
            type,
            timestamp: Date.now(),
            read: false,
        };
        setNotifications(prev => [newNotif, ...prev]);
    }, []);

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
        <NotificationContext.Provider value={{ notifications, addNotification, markAsRead, clearAll, removeNotification }}>
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
