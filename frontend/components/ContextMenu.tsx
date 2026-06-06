import React, { useEffect, useRef } from 'react';

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

  // Slightly right + above the click point, while avoiding viewport overflow.
  const desiredX = x + 10;
  const desiredY = y - 8;
  const adjustedX = Math.max(8, Math.min(desiredX, window.innerWidth - 220));
  const adjustedY = Math.max(8, Math.min(desiredY, window.innerHeight - (actions.length * 40 + 20)));

  return (
    <ul
      ref={menuRef}
      className="menu menu-sm bg-base-200 border border-base-300 rounded-box shadow-xl w-52 z-9999 fixed"
      style={{ left: adjustedX, top: adjustedY }}
    >
      {actions.map((action, i) => (
        <li key={i}>
          <button
            className={`flex items-center gap-2 text-sm w-full px-3 py-2 rounded transition-colors text-left ${
              action.disabled
                ? 'opacity-40 cursor-not-allowed text-base-content/50'
                : 'hover:bg-base-300 active:bg-base-300'
            }`}
            onClick={() => {
              if (action.disabled) return;
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
