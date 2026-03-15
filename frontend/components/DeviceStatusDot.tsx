import React from 'react';

interface DeviceStatusDotProps {
  status?: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  /** Optional tooltip; defaults to status label. */
  title?: string;
}

const DeviceStatusDot: React.FC<DeviceStatusDotProps> = ({ 
  status = 'unknown', 
  size = 'md',
  className = '',
  title: titleProp,
}) => {
  const isOnline = status === 'active' || status === 'registered' || status === 'provisioning';
  const title = titleProp ?? (status.charAt(0).toUpperCase() + status.slice(1));
  
  const sizeClasses = {
    sm: 'w-2 h-2',
    md: 'w-3 h-3',
    lg: 'w-4 h-4'
  };

  return (
    <>
      <style>{`
        @keyframes status-breath {
          0%, 100% { transform: scale(1); opacity: 0.8; box-shadow: 0 0 0 0px rgba(34, 197, 94, 0.4); }
          50% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 8px 2px rgba(34, 197, 94, 0.6); }
        }
        .animate-status-breath {
          animation: status-breath 3s ease-in-out infinite;
        }
      `}</style>
      <div className={`relative flex items-center justify-center ${className}`}>
        <div 
          className={`
            rounded-full 
            ${sizeClasses[size]} 
            ${isOnline ? 'bg-success animate-status-breath' : 'bg-error'}
            transition-all duration-500
          `}
          title={title}
        />
      </div>
    </>
  );
};

export default DeviceStatusDot;
