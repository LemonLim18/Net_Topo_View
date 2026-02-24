import React, { useState, useRef, useEffect } from 'react';
import { 
  Bot, 
  Send, 
  PanelRightClose, 
  PanelRightOpen,
  CheckCircle,
  AlertTriangle,
  Play,
  X,
  FileCode,
  Loader2,
  Copy,
  Network,
  Shield,
  Zap,
  Edit3,
  RotateCcw,
  ClipboardList
} from 'lucide-react';
import { llmApi, proposalApi } from '../src/services/api';
import { Toast } from '../src/utils/swal';
import type { AppDomain } from '../types';

// ==================== TYPES ====================

type AgentMode = 'idle' | 'planning' | 'awaiting_confirmation' | 'generating' | 'deploying' | 'deployed' | 'playbook_ready';

interface ConfigProposal {
  id: string;
  status: string;
  user_prompt: string;
  target_device: string;
  target_device_id: number;
  vendor: string;
  feature: string;
  params: Record<string, any>;
  generated_config: string;
  rollback_config: string;
  diff_text: string;
  risk_level: 'low' | 'medium' | 'high';
  ai_explanation: string;
  job_id?: number;
  request_id?: number;
  created_at?: string;
  updated_at?: string;
}

interface Message {
  id: string;
  role: 'user' | 'agent';
  content: string;
  timestamp: number;
  proposal?: ConfigProposal;
  deployResult?: { status: string; message: string; job_id?: number };
  mode?: AgentMode;
}

interface AIAgentPanelProps {
  isOpen: boolean;
  onToggle: () => void;
  domain: AppDomain;
  onPlaybookGenerated?: (playbook: any) => void;
}

// ==================== SUB-COMPONENTS ====================

const RiskBadge: React.FC<{ level: 'low' | 'medium' | 'high' }> = ({ level }) => {
  const config = {
    low: { color: 'badge-success', icon: '🟢', label: 'Low Risk' },
    medium: { color: 'badge-warning', icon: '🟡', label: 'Medium Risk' },
    high: { color: 'badge-error', icon: '🔴', label: 'High Risk' }
  };
  const { color, icon, label } = config[level];
  return <span className={`badge ${color} gap-1`}>{icon} {label}</span>;
};

// ---- ProposalCard: Shows config, rollback, diff, and action buttons ----
const ProposalCard: React.FC<{ 
  proposal: ConfigProposal; 
  onAccept: () => void;
  onRevise: () => void;
  onReject: () => void;
  isDeploying: boolean;
}> = ({ proposal, onAccept, onRevise, onReject, isDeploying }) => {
  const [showRollback, setShowRollback] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-primary" />
          <span className="font-semibold text-sm">Configuration Proposal</span>
        </div>
        <RiskBadge level={proposal.risk_level} />
      </div>

      {/* Target Info */}
      <div className="bg-base-200 rounded-lg p-3 text-sm space-y-1">
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
        <div className="bg-base-200 rounded-lg p-3">
          <h4 className="font-semibold text-xs flex items-center gap-1 mb-2">
            <Zap className="w-3 h-3 text-warning" /> AI Explanation
          </h4>
          <p className="text-xs leading-relaxed whitespace-pre-wrap">{proposal.ai_explanation}</p>
        </div>
      )}

      {/* Proposed Configuration */}
      <div className="bg-base-300 rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 bg-base-200 border-b border-base-300">
          <span className="text-xs font-semibold flex items-center gap-1">
            <FileCode className="w-3 h-3 text-secondary" /> Proposed Config
          </span>
          <button 
            onClick={() => navigator.clipboard.writeText(proposal.generated_config)} 
            className="btn btn-ghost btn-xs"
          >
            <Copy className="w-3 h-3" />
          </button>
        </div>
        <pre className="p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto font-mono">
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
            <pre className="p-3 text-xs overflow-x-auto max-h-32 overflow-y-auto font-mono">
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
            <pre className="p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto font-mono">
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
                <><CheckCircle className="w-3 h-3" /> Accept & Deploy</>
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

// ---- DeploymentStatusCard: Shown after successful deployment ----
const DeploymentStatusCard: React.FC<{
  result: { status: string; message: string; job_id?: number };
  proposal: ConfigProposal;
  onRollback: () => void;
  isRollingBack: boolean;
}> = ({ result, proposal, onRollback, isRollingBack }) => {
  const isSuccess = result.status === 'deployed';
  
  return (
    <div className="space-y-3">
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

      <div className="bg-base-200 rounded-lg p-3 text-sm">
        <p>{result.message}</p>
        {result.job_id && (
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

// ==================== MAIN COMPONENT ====================

const AIAgentPanel: React.FC<AIAgentPanelProps> = ({ 
  isOpen, 
  onToggle, 
  domain,
  onPlaybookGenerated 
}) => {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [agentMode, setAgentMode] = useState<AgentMode>('idle');
  const [currentProposal, setCurrentProposal] = useState<ConfigProposal | null>(null);
  const [isDeploying, setIsDeploying] = useState(false);
  const [isRollingBack, setIsRollingBack] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const generateId = () => Math.random().toString(36).substring(2, 9);

  // ---- Send message to AI agent ----
  const handleSend = async () => {
    if (!input.trim()) return;
    const userInput = input;

    // Add user message
    const userMsg: Message = {
      id: generateId(),
      role: 'user',
      content: userInput,
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setAgentMode('planning');

    // Add thinking indicator
    const thinkingId = generateId();
    setMessages(prev => [...prev, {
      id: thinkingId,
      role: 'agent',
      content: 'Analyzing your request...',
      timestamp: Date.now(),
      mode: 'generating'
    }]);

    try {
      const result = await llmApi.chat(userInput);

      // Remove thinking indicator
      setMessages(prev => prev.filter(m => m.id !== thinkingId));

      if (result.type === 'proposal' && result.proposal) {
        // AI generated a config proposal
        const proposal = result.proposal as ConfigProposal;
        setCurrentProposal(proposal);
        
        const proposalMsg: Message = {
          id: generateId(),
          role: 'agent',
          content: result.content,
          timestamp: Date.now(),
          proposal: proposal,
          mode: 'awaiting_confirmation'
        };
        setMessages(prev => [...prev, proposalMsg]);
        setAgentMode('awaiting_confirmation');
      } else {
        // Normal text response
        const agentMsg: Message = {
          id: generateId(),
          role: 'agent',
          content: result.content,
          timestamp: Date.now()
        };
        setMessages(prev => [...prev, agentMsg]);
        setAgentMode('idle');
      }
    } catch (error: any) {
      // Remove thinking indicator and show error
      setMessages(prev => prev.filter(m => m.id !== thinkingId));
      const errorMsg: Message = {
        id: generateId(),
        role: 'agent',
        content: `❌ Error: ${error.message || 'Failed to reach the AI agent.'}`,
        timestamp: Date.now()
      };
      setMessages(prev => [...prev, errorMsg]);
      setAgentMode('idle');
      Toast.error('Agent Error', error.message || 'Failed to reach the AI agent.');
    }
  };

  // ---- Accept & Deploy ----
  const handleAccept = async () => {
    if (!currentProposal) return;
    setIsDeploying(true);

    try {
      const result = await proposalApi.approve(currentProposal.id);
      
      const deployMsg: Message = {
        id: generateId(),
        role: 'agent',
        content: '',
        timestamp: Date.now(),
        deployResult: { 
          status: result.status, 
          message: result.message,
          job_id: result.job_id 
        },
        proposal: result.proposal as ConfigProposal,
        mode: 'deployed'
      };
      setMessages(prev => [...prev, deployMsg]);
      setCurrentProposal(result.proposal as ConfigProposal);
      setAgentMode('deployed');
    } catch (error: any) {
      const errorMsg: Message = {
        id: generateId(),
        role: 'agent',
        content: `❌ Deployment failed: ${error.message}`,
        timestamp: Date.now()
      };
      setMessages(prev => [...prev, errorMsg]);
      Toast.error('Deployment Failed', error.message);
    } finally {
      setIsDeploying(false);
    }
  };

  // ---- Revise plan ----
  const handleRevise = () => {
    const reviseMsg: Message = {
      id: generateId(),
      role: 'agent',
      content: "What changes would you like to make to the plan? I'll regenerate the configuration.",
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, reviseMsg]);
    setAgentMode('idle');
  };

  // ---- Reject proposal ----
  const handleReject = async () => {
    if (!currentProposal) return;

    try {
      await proposalApi.reject(currentProposal.id);
    } catch (e) {
      Toast.warning('Could not notify server');
    }

    const cancelMsg: Message = {
      id: generateId(),
      role: 'agent',
      content: "Proposal rejected. Let me know if you need anything else.",
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, cancelMsg]);
    setAgentMode('idle');
    setCurrentProposal(null);
  };

  // ---- Rollback deployed config ----
  const handleRollback = async () => {
    if (!currentProposal) return;
    setIsRollingBack(true);

    try {
      const result = await proposalApi.rollback(currentProposal.id);

      const rollbackMsg: Message = {
        id: generateId(),
        role: 'agent',
        content: `↩️ ${result.message}`,
        timestamp: Date.now()
      };
      setMessages(prev => [...prev, rollbackMsg]);
      setCurrentProposal(result.proposal as ConfigProposal);
      setAgentMode('idle');
    } catch (error: any) {
      const errorMsg: Message = {
        id: generateId(),
        role: 'agent',
        content: `❌ Rollback failed: ${error.message}`,
        timestamp: Date.now()
      };
      setMessages(prev => [...prev, errorMsg]);
      Toast.error('Rollback Failed', error.message);
    } finally {
      setIsRollingBack(false);
    }
  };

  const domainIcon = domain === 'network' ? Network : Shield;
  const DomainIcon = domainIcon;

  return (
    <div className={`h-full flex flex-col bg-base-100 border-l border-base-300 transition-all duration-300 ${isOpen ? 'w-96' : 'w-0 overflow-hidden'}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-base-300 bg-base-200/50">
        <div className="flex items-center gap-2">
          <Bot className="w-5 h-5 text-primary" />
          <span className="font-semibold text-sm">AI Agent</span>
          <span className={`badge badge-xs ${domain === 'network' ? 'badge-primary' : 'badge-secondary'}`}>
            {domain === 'network' ? 'Network' : 'Security'}
          </span>
        </div>
        <button onClick={onToggle} className="btn btn-ghost btn-sm btn-square">
          <PanelRightClose className="w-4 h-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center py-12 text-base-content/50">
            <DomainIcon className="w-12 h-12 mx-auto mb-3 opacity-30" />
            <p className="text-sm">
              I'm your {domain === 'network' ? 'Network' : 'Security'} Operations Agent.
            </p>
            <p className="text-xs mt-1">
              Tell me what you'd like to configure.
            </p>
          </div>
        )}

        {messages.map(msg => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[95%] rounded-lg p-3 ${
              msg.role === 'user' 
                ? 'bg-primary text-primary-content ml-4' 
                : 'bg-base-200 mr-4'
            }`}>
              {msg.content && <p className="text-sm whitespace-pre-wrap">{msg.content}</p>}
              
              {msg.mode === 'generating' && (
                <div className="flex items-center gap-2 text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Analyzing...</span>
                </div>
              )}

              {msg.proposal && !msg.deployResult && (
                <div className="mt-3">
                  <ProposalCard 
                    proposal={msg.proposal}
                    onAccept={handleAccept}
                    onRevise={handleRevise}
                    onReject={handleReject}
                    isDeploying={isDeploying}
                  />
                </div>
              )}

              {msg.deployResult && msg.proposal && (
                <DeploymentStatusCard 
                  result={msg.deployResult}
                  proposal={msg.proposal}
                  onRollback={handleRollback}
                  isRollingBack={isRollingBack}
                />
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-3 border-t border-base-300">
        <div className="flex gap-2">
          <input
            type="text"
            className="input input-bordered input-sm flex-1"
            placeholder={`Ask the ${domain} agent...`}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSend()}
            disabled={agentMode === 'generating' || agentMode === 'deploying'}
          />
          <button 
            onClick={handleSend} 
            className="btn btn-primary btn-sm btn-square"
            disabled={!input.trim() || agentMode === 'generating' || agentMode === 'deploying'}
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};

export default AIAgentPanel;
