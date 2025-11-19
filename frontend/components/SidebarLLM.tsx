import React from 'react';
import { Plus, MessageSquare, Github, ExternalLink, Trash2 } from 'lucide-react';
import  type { ChatSession } from '../typesLLM';

interface SidebarProps {
  isOpen: boolean;
  toggleSidebar: () => void;
  sessions: ChatSession[];
  currentSessionId: string | null;
  onNewChat: () => void;
  onSelectChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
}

export const SidebarLLM: React.FC<SidebarProps> = ({
  isOpen,
  sessions,
  currentSessionId,
  onNewChat,
  onSelectChat,
  onDeleteChat
}) => {
  return (
    <div className={`bg-base-300 h-full flex flex-col border-r border-base-content/10 transition-all duration-300 ease-in-out ${isOpen ? 'w-64 translate-x-0' : 'w-0 -translate-x-full overflow-hidden'} md:translate-x-0 md:static ${!isOpen && 'md:w-0 md:overflow-hidden'}`}>
      
      {/* New Chat Button */}
      <div className="p-3">
        <button 
          onClick={onNewChat}
          className="btn btn-primary rounded-xl text-primary-content w-full gap-2 font-normal whitespace-nowrap"
        >
          <Plus size={18} />
          New chat
        </button>
      </div>

      {/* History List */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-2 scrollbar-thin scrollbar-thumb-gray-700">
        <div className="text-xs font-semibold text-gray-500 mb-3 px-2">Recent</div>
        
        <div className="flex flex-col gap-1">
          {sessions.length === 0 && (
            <div className="text-center text-base-content/40 text-xs mt-3 whitespace-nowrap">
                No chat history
            </div>
          )}
          
          {sessions.map(session => (
            <div 
              key={session.id}
              className={`group relative flex items-center gap-3 rounded-lg px-3 py-3 text-sm transition-colors cursor-pointer ${
                currentSessionId === session.id 
                  ? 'bg-neutral text-neutral-content' 
                  : 'text-gray-800 hover:text-gray-300 hover:bg-gray-900'
              }`}
              onClick={() => onSelectChat(session.id)}
            >
              <MessageSquare size={16} className="shrink-0" />
              <span className="truncate flex-1">{session.title}</span>
              
              {/* Delete Action (visible on hover or active) */}
              {currentSessionId === session.id && (
                <button 
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteChat(session.id);
                  }}
                  className="absolute right-2 text-gray-400 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  title="Delete chat"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};