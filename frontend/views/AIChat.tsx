import React, { useState, useEffect, useRef, useCallback } from 'react';
import { 
  PanelLeftClose, 
  PanelLeftOpen,
  CheckCircle,
  AlertTriangle,
  X,
  FileCode,
  Loader2,
  Copy,
  Check,
  Edit3,
  RotateCcw,
  ClipboardList,
  Zap,
  Bot
} from 'lucide-react';

import { Role } from '../typesLLM';
import type { Message, ChatSession, ConfigProposal, DeployResult } from '../typesLLM';
import { SidebarLLM } from '../components/SidebarLLM';
import { ChatInput } from '../components/ChatInput';
import { MessageBubble } from '../components/MessageBubble';
import { llmApi, proposalApi } from '../src/services/api';

import type { AppDomain } from '../types';

// Mock ID generator
const generateId = () => Math.random().toString(36).substring(2, 15);

// ==================== M19 SUB-COMPONENTS ====================

const RiskBadge: React.FC<{ level: 'low' | 'medium' | 'high' }> = ({ level }) => {
  const config = {
    low:    { color: 'badge-success', icon: '🟢', label: 'Low Risk' },
    medium: { color: 'badge-warning', icon: '🟡', label: 'Medium Risk' },
    high:   { color: 'badge-error',   icon: '🔴', label: 'High Risk' },
  };
  const { color, icon, label } = config[level];
  return <span className={`badge ${color} gap-1`}>{icon} {label}</span>;
};

/** ProposalCard — renders config, rollback, diff, and Accept/Revise/Reject buttons */
const ProposalCard: React.FC<{
  proposal: ConfigProposal;
  onAccept: () => void;
  onRevise: () => void;
  onReject: () => void;
  isDeploying: boolean;
}> = ({ proposal, onAccept, onRevise, onReject, isDeploying }) => {
  const [showRollback, setShowRollback] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-3 mt-3 border border-base-300 rounded-xl p-4 bg-base-200/50">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-primary" />
          <span className="font-semibold text-sm">Configuration Proposal</span>
        </div>
        <RiskBadge level={proposal.risk_level} />
      </div>

      {/* Target Info */}
      <div className="bg-base-300 rounded-lg p-3 text-sm space-y-1">
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-[60px]">Target:</span>
          <span className="font-mono">{proposal.target_device} ({proposal.vendor})</span>
        </div>
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-[60px]">Feature:</span>
          <span>{proposal.feature}</span>
        </div>
      </div>

      {/* AI Explanation */}
      {proposal.ai_explanation && (
        <div className="bg-base-300 rounded-lg p-3">
          <h4 className="font-semibold text-xs flex items-center gap-1 mb-2">
            <Zap className="w-3 h-3 text-warning" /> AI Explanation
          </h4>
          <p className="text-xs leading-relaxed whitespace-pre-wrap max-h-32 overflow-y-auto">
            {proposal.ai_explanation}
          </p>
        </div>
      )}

      {/* Proposed Configuration */}
      <div className="bg-base-300 rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 bg-base-200 border-b border-base-300">
          <span className="text-xs font-semibold flex items-center gap-1">
            <FileCode className="w-3 h-3 text-secondary" /> Proposed Config
          </span>
          <button
            onClick={() => handleCopy(proposal.generated_config)}
            className="btn btn-ghost btn-xs"
          >
            {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
          </button>
        </div>
        <pre className="p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto font-mono bg-neutral-800 text-green-400">
          <code>{proposal.generated_config}</code>
        </pre>
      </div>

      {/* Rollback Commands (Collapsible) */}
      {proposal.rollback_config && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowRollback(!showRollback)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <RotateCcw className="w-3 h-3 text-error" /> Rollback Commands
            </span>
            <span className="text-xs">{showRollback ? '▲' : '▼'}</span>
          </button>
          {showRollback && (
            <pre className="p-3 text-xs overflow-x-auto max-h-32 overflow-y-auto font-mono bg-neutral-800 text-yellow-400">
              <code>{proposal.rollback_config}</code>
            </pre>
          )}
        </div>
      )}

      {/* Config Diff (Collapsible) */}
      {proposal.diff_text && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowDiff(!showDiff)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <ClipboardList className="w-3 h-3 text-info" /> Configuration Diff
            </span>
            <span className="text-xs">{showDiff ? '▲' : '▼'}</span>
          </button>
          {showDiff && (
            <pre className="p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto font-mono bg-neutral-800 text-sky-300">
              <code>{proposal.diff_text}</code>
            </pre>
          )}
        </div>
      )}

      {/* Action Buttons */}
      {proposal.status === 'pending' && (
        <div className="border-t border-base-300 pt-3">
          <p className="text-xs text-center mb-2 text-base-content/70">
            Deploy this configuration to {proposal.target_device}?
          </p>
          <div className="flex gap-2 justify-center flex-wrap">
            <button
              onClick={onAccept}
              className="btn btn-success btn-sm gap-1"
              disabled={isDeploying}
            >
              {isDeploying ? (
                <><Loader2 className="w-3 h-3 animate-spin" /> Deploying...</>
              ) : (
                <><CheckCircle className="w-3 h-3" /> Accept &amp; Deploy</>
              )}
            </button>
            <button onClick={onRevise} className="btn btn-ghost btn-sm gap-1" disabled={isDeploying}>
              <Edit3 className="w-3 h-3" /> Revise
            </button>
            <button onClick={onReject} className="btn btn-ghost btn-sm gap-1 text-error" disabled={isDeploying}>
              <X className="w-3 h-3" /> Reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

/** DeploymentStatusCard — shown after approval or failure */
const DeploymentStatusCard: React.FC<{
  result: DeployResult;
  proposal: ConfigProposal;
  onRollback: () => void;
  isRollingBack: boolean;
}> = ({ result, proposal, onRollback, isRollingBack }) => {
  const isSuccess = result.status === 'deployed';

  return (
    <div className="space-y-3 mt-3 border border-base-300 rounded-xl p-4 bg-base-200/50">
      <div className="flex items-center gap-2">
        {isSuccess ? (
          <CheckCircle className="w-5 h-5 text-success" />
        ) : (
          <AlertTriangle className="w-5 h-5 text-error" />
        )}
        <span className="font-semibold text-sm">
          {isSuccess ? 'Deployment Successful' : 'Deployment Failed'}
        </span>
      </div>

      <div className="bg-base-300 rounded-lg p-3 text-sm">
        <p>{result.message}</p>
        {result.job_id != null && result.job_id > 0 && (
          <p className="text-xs text-base-content/60 mt-1">Job ID: #{result.job_id}</p>
        )}
      </div>

      {isSuccess && proposal.rollback_config && (
        <div className="flex gap-2 justify-center">
          <button
            onClick={onRollback}
            className="btn btn-warning btn-sm gap-1"
            disabled={isRollingBack}
          >
            {isRollingBack ? (
              <><Loader2 className="w-3 h-3 animate-spin" /> Rolling back...</>
            ) : (
              <><RotateCcw className="w-3 h-3" /> Rollback</>
            )}
          </button>
        </div>
      )}
    </div>
  );
};

interface AIChatProps {
    domain: AppDomain;
}

const GeminiChat: React.FC<AIChatProps> = ({ domain }) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);

  // M19 — agentic workflow state
  const [currentProposal, setCurrentProposal] = useState<ConfigProposal | null>(null);
  const [isDeploying, setIsDeploying] = useState(false);
  const [isRollingBack, setIsRollingBack] = useState(false);
  
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

      if (response.type === 'proposal' && response.proposal) {
        // M19: AI generated a config proposal → show ProposalCard
        const proposal = response.proposal as ConfigProposal;
        setCurrentProposal(proposal);

        setMessages(prev => prev.map(msg =>
          msg.id === aiMsgId
            ? {
                ...msg,
                isStreaming: false,
                content: response.content,
                proposal: proposal,
              }
            : msg
        ));
      } else {
        // Normal text response
        setMessages(prev => prev.map(msg =>
          msg.id === aiMsgId
            ? { ...msg, isStreaming: false, content: response.content }
            : msg
        ));
      }

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

  // ------------------------------------------------------------------
  // M19: Proposal lifecycle handlers
  // ------------------------------------------------------------------

  const handleAcceptProposal = async () => {
    if (!currentProposal) return;
    setIsDeploying(true);

    try {
      const result = await proposalApi.approve(currentProposal.id);

      const deployMsg: Message = {
        id: generateId(),
        role: Role.Model,
        content: '',
        timestamp: Date.now(),
        deployResult: {
          status: result.status,
          message: result.message,
          job_id: result.job_id,
        },
        proposal: result.proposal as ConfigProposal,
      };
      setMessages(prev => [...prev, deployMsg]);
      setCurrentProposal(result.proposal as ConfigProposal);
    } catch (error: any) {
      const errorMsg: Message = {
        id: generateId(),
        role: Role.Model,
        content: `❌ Deployment failed: ${error.message}`,
        isError: true,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setIsDeploying(false);
    }
  };

  const handleReviseProposal = () => {
    const reviseMsg: Message = {
      id: generateId(),
      role: Role.Model,
      content: "What changes would you like to make? I'll regenerate the configuration.",
      timestamp: Date.now(),
    };
    setMessages(prev => [...prev, reviseMsg]);
    setCurrentProposal(null);
  };

  const handleRejectProposal = async () => {
    if (!currentProposal) return;

    try {
      await proposalApi.reject(currentProposal.id);
    } catch {
      // Proceed even if API call fails
    }

    const rejectMsg: Message = {
      id: generateId(),
      role: Role.Model,
      content: 'Proposal rejected. Let me know if you need anything else.',
      timestamp: Date.now(),
    };
    setMessages(prev => [...prev, rejectMsg]);
    setCurrentProposal(null);
  };

  const handleRollbackProposal = async () => {
    if (!currentProposal) return;
    setIsRollingBack(true);

    try {
      const result = await proposalApi.rollback(currentProposal.id);

      const rollbackMsg: Message = {
        id: generateId(),
        role: Role.Model,
        content: `↩️ ${result.message}`,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, rollbackMsg]);
      setCurrentProposal(result.proposal as ConfigProposal);
    } catch (error: any) {
      const errorMsg: Message = {
        id: generateId(),
        role: Role.Model,
        content: `❌ Rollback failed: ${error.message}`,
        isError: true,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setIsRollingBack(false);
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
                 <div key={msg.id}>
                   {/* Deployment result card (no text bubble needed) */}
                   {msg.deployResult && msg.proposal ? (
                     <div className="chat chat-start mb-4">
                       <div className="chat-bubble chat-bubble-neutral max-w-[85%] sm:max-w-[75%]">
                         <DeploymentStatusCard
                           result={msg.deployResult}
                           proposal={msg.proposal}
                           onRollback={handleRollbackProposal}
                           isRollingBack={isRollingBack}
                         />
                       </div>
                     </div>
                   ) : (
                     <>
                       {/* Normal text bubble */}
                       <MessageBubble message={msg} />
                       {/* M19: Proposal card attached below the AI message */}
                       {msg.proposal && !msg.deployResult && (
                         <div className="chat chat-start mb-4 -mt-2">
                           <div className="chat-image avatar hidden sm:block">
                             <div className="w-10" />
                           </div>
                           <div className="chat-bubble chat-bubble-neutral max-w-[85%] sm:max-w-[75%] p-0 overflow-visible bg-transparent shadow-none">
                             <ProposalCard
                               proposal={msg.proposal}
                               onAccept={handleAcceptProposal}
                               onRevise={handleReviseProposal}
                               onReject={handleRejectProposal}
                               isDeploying={isDeploying}
                             />
                           </div>
                         </div>
                       )}
                     </>
                   )}
                 </div>
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