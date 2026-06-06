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
        <div className="fixed top-20 right-4 z-50 flex flex-col gap-3 w-96 pointer-events-none">
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
        success: <CheckCircle className="w-6 h-6" />,
        warning: <AlertTriangle className="w-6 h-6" />,
        error: <AlertOctagon className="w-6 h-6" />,
        info: <Info className="w-6 h-6" />
    };

    return (
        <div className={`alert ${styles[toast.type as keyof typeof styles]} shadow-2xl flex items-start p-5 text-sm animate-in slide-in-from-right pointer-events-auto relative overflow-hidden border border-white/10 backdrop-blur-md`}>
            <div className="shrink-0 mt-0.5 mr-3">
                {icons[toast.type as keyof typeof icons]}
            </div>
            <div className="flex-1 min-w-0">
                <h3 className="font-bold text-base leading-tight">{toast.title}</h3>
                <p className="text-xs opacity-90 wrap-break-word mt-1.5 leading-relaxed">{toast.message}</p>
            </div>
            <button className="btn btn-ghost btn-xs btn-square -mr-3 -mt-3 text-current/70 hover:text-current hover:bg-black/10" onClick={onClose}>
                <X className="w-4 h-4" />
            </button>
            <div 
                className="absolute bottom-0 left-0 h-1 bg-current opacity-30 transition-all duration-100 ease-linear"
                style={{ 
                    width: '100%',
                    animation: `shrink ${TOAST_DURATION}ms linear forwards`
                }}
            />
            <style>{`
                @keyframes shrink {
                    from { width: 100%; }
                    to { width: 0%; }
                }
            `}</style>
        </div>
    );
};

export default NotificationToast;
