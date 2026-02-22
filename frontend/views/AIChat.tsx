import React, { useState, useEffect, useRef, useCallback } from 'react';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';

import { Role } from '../typesLLM';
import type { Message, ChatSession } from '../typesLLM';
import { SidebarLLM } from '../components/SidebarLLM';
import { ChatInput } from '../components/ChatInput';
import { MessageBubble } from '../components/MessageBubble';
import { llmApi } from '../src/services/api';

import type { AppDomain } from '../types';

// Mock ID generator
const generateId = () => Math.random().toString(36).substring(2, 15);

interface AIChatProps {
    domain: AppDomain;
}

const GeminiChat: React.FC<AIChatProps> = ({ domain }) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Initialize first chat on load if none exists
  useEffect(() => {
    if (!currentSessionId) {
        startNewChat();
    }
  }, []);

  const startNewChat = useCallback(() => {
    const newSessionId = generateId();
    // Clear backend history when starting new chat (if that's desired behavior)
    // llmApi.clearHistory(); 
    
    setMessages([{
        id: generateId(),
        role: Role.Model,
        content: `I am your ${domain === 'network' ? 'Network' : 'Security'} Operations Assistant. How can I help you today?`,
        timestamp: Date.now()
    }]);
    setCurrentSessionId(newSessionId);
    setIsSidebarOpen(window.innerWidth > 768);
  }, [domain]);

  // Reset chat when domain changes
  useEffect(() => {
     startNewChat();
  }, [domain, startNewChat]);

  const handleSendMessage = async (text: string) => {
    if (!text.trim() || !currentSessionId) return;

    const userMsgId = generateId();
    const userMessage: Message = {
      id: userMsgId,
      role: Role.User,
      content: text,
      timestamp: Date.now()
    };

    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    // Create placeholder for AI Message
    const aiMsgId = generateId();
    const aiPlaceholder: Message = {
      id: aiMsgId,
      role: Role.Model,
      content: "",
      isStreaming: true,
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, aiPlaceholder]);

    // Handle Session Title (Client side only for now)
    if (currentSessionId) {
      setSessions(prevSessions => {
        const exists = prevSessions.some(s => s.id === currentSessionId);
        if (exists) return prevSessions;
        return [{
          id: currentSessionId,
          title: text.substring(0, 30) + (text.length > 30 ? "..." : ""),
          updatedAt: Date.now()
        }, ...prevSessions];
      });
    }

    try {
      // Call Backend API
      const response = await llmApi.chat(text);
      
      setMessages(prev => prev.map(msg => 
        msg.id === aiMsgId 
          ? { ...msg, isStreaming: false, content: response.content } 
          : msg
      ));

    } catch (error: any) {
      console.error("Chat error:", error);
      setMessages(prev => prev.map(msg => 
        msg.id === aiMsgId 
          ? { ...msg, isStreaming: false, isError: true, content: `Error: ${error.message || "Failed to get response"}` } 
          : msg
      ));
    } finally {
      setIsLoading(false);
    }
  };

  const handleStopGeneration = () => {
    setIsLoading(false);
  };

  const handleDeleteChat = (id: string) => {
    setSessions(prev => prev.filter(s => s.id !== id));
    if (currentSessionId === id) {
        startNewChat();
    }
    // Ideally call backend to clear specific session history if supported
    llmApi.clearHistory();
  };

  const handleSelectChat = (id: string) => {
      if (id !== currentSessionId) {
          setCurrentSessionId(id);
          setMessages([]); 
          llmApi.clearHistory(); // Clear backend context for "new" session feel
          // detailed history fetching would go here
      }
  };

  return (
    <div className="flex h-full bg-base-100 relative overflow-hidden">
      
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-20 md:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div className={`fixed inset-y-0 left-0 z-30 transform transition-transform duration-300 ease-in-out md:relative md:translate-x-0 ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'} flex`}>
        <SidebarLLM 
          isOpen={isSidebarOpen}
          toggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)}
          sessions={sessions}
          currentSessionId={currentSessionId}
          onNewChat={startNewChat}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
        />
      </div>

      <div className="flex-1 flex flex-col h-full relative min-w-0">
        <header className="h-14 border-b border-base-300 flex items-center justify-between px-4 gap-3 shrink-0 bg-base-100/50 backdrop-blur">
          <div className="flex items-center gap-2">
             <button 
              onClick={() => setIsSidebarOpen(!isSidebarOpen)}
              className="btn btn-ghost btn-sm btn-square"
            >
              {isSidebarOpen ? <PanelLeftClose size={20} /> : <PanelLeftOpen size={20} />}
            </button>
            <span className="font-medium truncate">
                {sessions.find(s => s.id === currentSessionId)?.title || 'NetTopo Assistant'}
            </span>
          </div>
          <div className="text-xs text-gray-500 font-mono">Backend Agent (M5)</div>
        </header>

        <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-800 p-4 sm:p-6 md:p-10 scroll-smooth">
           {messages.length === 0 ? (
             <div className="flex flex-col items-center text-base-content justify-center h-full text-center opacity-50 select-none"> 
                <div className="w-20 h-20 bg-base-200 rounded-full flex items-center justify-center mb-4">
                  <RoleIcon size={48} />
                </div>
                <h2 className="text-lg font-semibold mb-2">How can I help you today?</h2>
                <p className="max-w-md text-sm text-gray-400">
                    I have access to your network topology and tools.
                </p>
             </div>
           ) : (
             <div className="max-w-3xl mx-auto pb-4">
               {messages.map(msg => (
                 <MessageBubble key={msg.id} message={msg} />
               ))}
               <div ref={messagesEndRef} className="h-4" />
             </div>
           )}
        </div>

        <div className="p-4 bg-base-100 border-t border-base-300 shrink-0">
           <ChatInput 
              onSend={handleSendMessage} 
              disabled={isLoading} 
              isGenerating={isLoading}
              onStop={handleStopGeneration}
           />
        </div>

      </div>
    </div>
  );
};

const RoleIcon = ({ size }: { size: number }) => {
  return <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="gray" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2v-2a2 2 0 0 1-2-2 2 2 0 0 1 2-2Z"/><path d="m9 22 3-8 3 8"/><path d="M8 22h8"/><path d="M12 10v2c0 1.5-2 2-2 3.5V16"/><path d="M12 22h.01"/></svg>
}

export default GeminiChat;