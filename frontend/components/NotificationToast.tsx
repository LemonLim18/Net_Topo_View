import React, { useEffect } from 'react';
import { useNotification } from '../contexts/NotificationContext';
import { X, CheckCircle, AlertTriangle, AlertOctagon, Info } from 'lucide-react';

const TOAST_DURATION = 5000;

const NotificationToast: React.FC = () => {
    const { notifications, removeNotification } = useNotification();

    // Only show unread, non-info notifications as popups, or recent ones (last 5 seconds)
    // For simplicity, we filter recent unread warnings/errors
    const toasts = notifications.filter(n => 
        !n.read && 
        (n.type === 'warning' || n.type === 'error' || n.type === 'success') &&
        Date.now() - n.timestamp < TOAST_DURATION
    );

    return (
        <div className="fixed top-20 right-4 z-50 flex flex-col gap-2 w-80 pointer-events-none">
            {toasts.map(toast => (
                <ToastItem key={toast.id} toast={toast} onClose={() => removeNotification(toast.id)} />
            ))}
        </div>
    );
};

const ToastItem: React.FC<{ toast: any, onClose: () => void }> = ({ toast, onClose }) => {
    useEffect(() => {
        const timer = setTimeout(onClose, TOAST_DURATION);
        return () => clearTimeout(timer);
    }, [onClose]);

    const styles = {
        success: 'alert-success text-success-content',
        warning: 'alert-warning text-warning-content',
        error: 'alert-error text-error-content',
        info: 'alert-info text-info-content'
    };

    const icons = {
        success: <CheckCircle className="w-5 h-5" />,
        warning: <AlertTriangle className="w-5 h-5" />,
        error: <AlertOctagon className="w-5 h-5" />,
        info: <Info className="w-5 h-5" />
    };

    return (
        <div className={`alert ${styles[toast.type as keyof typeof styles]} shadow-lg flex items-start p-3 text-sm animate-in slide-in-from-right pointer-events-auto`}>
            <div className="flex-shrink-0 mt-0.5">
                {icons[toast.type as keyof typeof icons]}
            </div>
            <div className="flex-1 min-w-0">
                <h3 className="font-bold leading-tight">{toast.title}</h3>
                <div className="text-xs opacity-90 break-words mt-1">{toast.message}</div>
            </div>
            <button className="btn btn-ghost btn-xs btn-square -mr-2 -mt-2" onClick={onClose}>
                <X className="w-4 h-4" />
            </button>
        </div>
    );
};

export default NotificationToast;
