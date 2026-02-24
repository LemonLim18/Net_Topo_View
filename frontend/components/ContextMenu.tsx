import React, { useEffect, useRef } from 'react';
import { Terminal, Info, X } from 'lucide-react';

export interface ContextMenuAction {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}

interface ContextMenuProps {
  x: number;
  y: number;
  actions: ContextMenuAction[];
  onClose: () => void;
}

const ContextMenu: React.FC<ContextMenuProps> = ({ x, y, actions, onClose }) => {
  const menuRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose]);

  // Adjust position so menu doesn't overflow viewport
  const adjustedX = Math.min(x, window.innerWidth - 200);
  const adjustedY = Math.min(y, window.innerHeight - (actions.length * 40 + 16));

  return (
    <ul
      ref={menuRef}
      className="menu menu-sm bg-base-200 border border-base-300 rounded-box shadow-xl w-52 z-[100] absolute"
      style={{ left: adjustedX, top: adjustedY }}
    >
      {actions.map((action, i) => (
        <li key={i}>
          <button
            className="flex items-center gap-2 text-sm"
            onClick={() => {
              action.onClick();
              onClose();
            }}
            disabled={action.disabled}
          >
            {action.icon}
            {action.label}
          </button>
        </li>
      ))}
    </ul>
  );
};

export default ContextMenu;
