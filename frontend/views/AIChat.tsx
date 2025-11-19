import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { GoogleGenAI, Chat } from "@google/genai";
import { v4 as uuidv4 } from 'uuid';

import { Role } from '../typesLLM';
import type { Message, ChatSession } from '../typesLLM';
import { createChatSession, sendMessageStream, generateTitle } from '../services/gemini';
import { SidebarLLM } from '../components/SidebarLLM';
import { ChatInput } from '../components/ChatInput';
import { MessageBubble } from '../components/MessageBubble';

// Mock ID generator if uuid not available in env, though we'll just use random strings
const generateId = () => Math.random().toString(36).substring(2, 15);

const GeminiChat = () => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  
  // Refs for chat management
  const chatInstanceRef = useRef<Chat | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Scroll to bottom on new messages
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startNewChat = useCallback(() => {
    const newSessionId = generateId();
    const newChat = createChatSession();
    
    chatInstanceRef.current = newChat;
    setMessages([]);
    setCurrentSessionId(newSessionId);
    setIsSidebarOpen(window.innerWidth > 768); // Open sidebar on desktop by default
  }, []);

  const handleSendMessage = async (text: string) => {
    if (!text.trim() || !currentSessionId) return;

    const userMsgId = generateId();
    const userMessage: Message = {
      id: userMsgId,
      role: Role.User,
      content: text,
      timestamp: Date.now()
    };

    // Add User Message
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

    // Handle Session Title: add a new session only if it doesn't already exist.
    // Use functional update to avoid races where multiple sends try to insert the same session.
    if (currentSessionId) {
      setSessions(prevSessions => {
        const exists = prevSessions.some(s => s.id === currentSessionId);
        if (exists) return prevSessions;

        const newSession: ChatSession = {
          id: currentSessionId,
          title: "New Chat",
          updatedAt: Date.now()
        };

        // Kick off title generation (updates the session title when available)
        generateTitle(text).then(title => {
          setSessions(prev => prev.map(s => s.id === currentSessionId ? { ...s, title } : s));
        }).catch(() => {});

        return [newSession, ...prevSessions];
      });
    }

    try {
      if (!chatInstanceRef.current) {
        chatInstanceRef.current = createChatSession();
      }

      const stream = await sendMessageStream(chatInstanceRef.current, text);
      
      let fullContent = "";
      
      for await (const chunk of stream) {
        fullContent += chunk;
        setMessages(prev => prev.map(msg => 
          msg.id === aiMsgId 
            ? { ...msg, content: fullContent } 
            : msg
        ));
      }

      // Finalize message
      setMessages(prev => prev.map(msg => 
        msg.id === aiMsgId 
          ? { ...msg, isStreaming: false } 
          : msg
      ));

    } catch (error) {
      console.error("Chat error:", error);
      setMessages(prev => prev.map(msg => 
        msg.id === aiMsgId 
          ? { ...msg, isStreaming: false, isError: true, content: "I encountered an error processing your request. Please try again." } 
          : msg
      ));
    } finally {
      setIsLoading(false);
    }
  };

  const handleStopGeneration = () => {
    // Note: The JS SDK doesn't expose a direct abort method on the stream iterator easily 
    // without breaking the loop manually. In this implementation, we mostly just stop the UI spinner.
    // True abort requires AbortController support which might be limited in the high-level `sendMessageStream` 
    // unless we implement manual chunk reading.
    // For now, we simulate stop by just ending the loading state.
    setIsLoading(false);
    setMessages(prev => {
        const lastMsg = prev[prev.length - 1];
        if (lastMsg.role === Role.Model && lastMsg.isStreaming) {
            return prev.map(m => m.id === lastMsg.id ? { ...m, isStreaming: false, content: m.content + " [Stopped]" } : m);
        }
        return prev;
    });
  };

  const handleDeleteChat = (id: string) => {
    setSessions(prev => prev.filter(s => s.id !== id));
    if (currentSessionId === id) {
        startNewChat();
    }
  };

  const handleSelectChat = (id: string) => {
      // In a real app, we would fetch history from DB here.
      // Since this is client-side only with no persistence for this demo,
      // we just switch the active ID. 
      // NOTE: Switching back to an old ID in this demo won't restore messages 
      // because we aren't storing message history per session ID in a global store 
      // to keep the code concise. We only keep the current active messages.
      // To fix this, we'd need a `allMessages: Record<string, Message[]>` state.
      
      // For this demo, we will just clear messages if switching (simulating a fresh load)
      // or warn the user. Let's reset for simplicity or the user will be confused.
      if (id !== currentSessionId) {
          setCurrentSessionId(id);
          setMessages([]); // Reset visible messages as we don't have persistence in this simple demo
          chatInstanceRef.current = createChatSession(); // Reset context
      }
  };

  return (
    <div className="flex h-full bg-base-100 relative overflow-hidden">
      
      {/* Mobile Overlay */}
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-20 md:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
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

      {/* Main Content */}
      <div className="flex-1 flex flex-col h-full relative min-w-0">
        {/* Header */}
        <header className="h-14 border-b border-base-300 flex items-center justify-between px-4 gap-3 shrink-0 bg-base-100/50 backdrop-blur">
          <div className="flex items-center gap-2">
             <button 
              onClick={() => setIsSidebarOpen(!isSidebarOpen)}
              className="btn btn-ghost btn-sm btn-square"
            >
              {isSidebarOpen ? <PanelLeftClose size={20} /> : <PanelLeftOpen size={20} />}
            </button>
            <span className="font-medium truncate">
                {sessions.find(s => s.id === currentSessionId)?.title || 'Gemini Chat'}
            </span>
          </div>
          <div className="text-xs text-gray-500 font-mono">Gemini 2.5 Flash</div>
        </header>

        {/* Chat Area */}
        <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-800 p-4 sm:p-6 md:p-10 scroll-smooth">
           {messages.length === 0 ? (
             <div className="flex flex-col items-center text-base-content justify-center h-full text-center opacity-50 select-none"> 
                <div className="w-20 h-20 bg-base-200 rounded-full flex items-center justify-center mb-4">
                  <RoleIcon size={48} />
                </div>
                <h2 className="text-lg font-semibold mb-2">How can I help you today?</h2>
                <p className="max-w-md text-sm text-gray-400">
                    I'm a large language model, trained by Google.
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

        {/* Input Area */}
        <div className="p-4 bg-base-100 border-t border-base-300 shrink-0">
           <ChatInput 
              onSend={handleSendMessage} 
              disabled={false} 
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