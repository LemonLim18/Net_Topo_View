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
  Edit3
} from 'lucide-react';
import type { AppDomain } from '../types';

// ==================== TYPES ====================

type AgentMode = 'idle' | 'planning' | 'awaiting_confirmation' | 'generating' | 'playbook_ready';

interface ContextSummary {
  topology: string;
  currentState: string;
  affectedDevices: string[];
}

interface ExpectedOutcomes {
  positive: string[];
  warnings: string[];
}

interface PlanningResponse {
  contextSummary: ContextSummary;
  proposedActions: string[];
  expectedOutcomes: ExpectedOutcomes;
  riskLevel: 'low' | 'medium' | 'high';
}

interface AnsiblePlaybook {
  name: string;
  filename: string;
  content: string;
}

interface Message {
  id: string;
  role: 'user' | 'agent';
  content: string;
  timestamp: number;
  planning?: PlanningResponse;
  playbook?: AnsiblePlaybook;
  mode?: AgentMode;
}

interface AIAgentPanelProps {
  isOpen: boolean;
  onToggle: () => void;
  domain: AppDomain;
  onPlaybookGenerated?: (playbook: AnsiblePlaybook) => void;
}

// ==================== MOCK DATA ====================

const MOCK_PLANNING_RESPONSE: PlanningResponse = {
  contextSummary: {
    topology: "R1 ↔ R2 connected via Ethernet0/2 (192.168.2.0/24)",
    currentState: "No OSPF configured on either device",
    affectedDevices: ["R1 (10.0.0.1)", "R2 (10.0.0.2)"]
  },
  proposedActions: [
    "Enable OSPF process 1 on R1",
    "Enable OSPF process 1 on R2", 
    "Advertise Loopback and interconnect networks in Area 0",
    "Verify neighbor adjacency"
  ],
  expectedOutcomes: {
    positive: [
      "R1 and R2 will form OSPF adjacency (FULL state)",
      "Routes 10.0.0.1/32 and 10.0.0.2/32 will be exchanged"
    ],
    warnings: [
      "Brief traffic interruption (~2-5 sec) during SPF calculation"
    ]
  },
  riskLevel: 'medium'
};

const MOCK_PLAYBOOK: AnsiblePlaybook = {
  name: "Configure OSPF on Backbone Routers",
  filename: "ospf_backbone_config.yml",
  content: `---
- name: Configure OSPF on Backbone Routers
  hosts: backbone_routers
  gather_facts: no
  
  vars:
    ospf_process_id: 1
    ospf_area: 0
    
  tasks:
    - name: Enable OSPF Process on R1
      ios_config:
        lines:
          - router ospf {{ ospf_process_id }}
          - router-id 10.0.0.1
          - network 10.0.0.0 0.0.0.255 area {{ ospf_area }}
          - network 192.168.2.0 0.0.0.255 area {{ ospf_area }}
          - passive-interface default
          - no passive-interface Ethernet0/2
      when: inventory_hostname == "R1"
      
    - name: Enable OSPF Process on R2
      ios_config:
        lines:
          - router ospf {{ ospf_process_id }}
          - router-id 10.0.0.2
          - network 10.0.0.0 0.0.0.255 area {{ ospf_area }}
          - network 192.168.2.0 0.0.0.255 area {{ ospf_area }}
          - passive-interface default
          - no passive-interface Ethernet0/2
      when: inventory_hostname == "R2"
      
    - name: Verify OSPF Neighbor Adjacency
      ios_command:
        commands:
          - show ip ospf neighbor
      register: ospf_neighbors
      
    - name: Display OSPF Status
      debug:
        var: ospf_neighbors.stdout_lines`
};

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

const PlanningResponseCard: React.FC<{ 
  planning: PlanningResponse; 
  onProceed: () => void;
  onRevise: () => void;
  onCancel: () => void;
}> = ({ planning, onProceed, onRevise, onCancel }) => {
  return (
    <div className="space-y-4">
      {/* Context Summary */}
      <div className="bg-base-200 rounded-lg p-4">
        <h4 className="font-semibold text-sm flex items-center gap-2 mb-3">
          <Network className="w-4 h-4 text-primary" /> Context Summary
        </h4>
        <div className="space-y-2 text-sm">
          <div className="flex gap-2">
            <span className="text-base-content/60 min-w-[80px]">Topology:</span>
            <span>{planning.contextSummary.topology}</span>
          </div>
          <div className="flex gap-2">
            <span className="text-base-content/60 min-w-[80px]">State:</span>
            <span>{planning.contextSummary.currentState}</span>
          </div>
          <div className="flex gap-2">
            <span className="text-base-content/60 min-w-[80px]">Devices:</span>
            <span>{planning.contextSummary.affectedDevices.join(', ')}</span>
          </div>
        </div>
      </div>

      {/* Proposed Actions */}
      <div className="bg-base-200 rounded-lg p-4">
        <h4 className="font-semibold text-sm flex items-center gap-2 mb-3">
          <FileCode className="w-4 h-4 text-secondary" /> Proposed Actions
        </h4>
        <ol className="list-decimal list-inside space-y-1 text-sm">
          {planning.proposedActions.map((action, i) => (
            <li key={i}>{action}</li>
          ))}
        </ol>
      </div>

      {/* Expected Outcomes */}
      <div className="bg-base-200 rounded-lg p-4">
        <h4 className="font-semibold text-sm flex items-center gap-2 mb-3">
          <Zap className="w-4 h-4 text-warning" /> Expected Outcomes
        </h4>
        <div className="space-y-2 text-sm">
          {planning.expectedOutcomes.positive.map((outcome, i) => (
            <div key={i} className="flex items-start gap-2">
              <CheckCircle className="w-4 h-4 text-success shrink-0 mt-0.5" />
              <span>{outcome}</span>
            </div>
          ))}
          {planning.expectedOutcomes.warnings.map((warning, i) => (
            <div key={i} className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
              <span>{warning}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Risk Level */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-base-content/60">Risk Assessment:</span>
        <RiskBadge level={planning.riskLevel} />
      </div>

      {/* Action Buttons */}
      <div className="border-t border-base-300 pt-4">
        <p className="text-sm text-center mb-3 text-base-content/70">
          Shall I generate the Ansible playbook for this configuration?
        </p>
        <div className="flex gap-2 justify-center flex-wrap">
          <button onClick={onProceed} className="btn btn-primary btn-sm gap-1">
            <Play className="w-3 h-3" /> Proceed to Generate
          </button>
          <button onClick={onRevise} className="btn btn-ghost btn-sm gap-1">
            <Edit3 className="w-3 h-3" /> Revise Plan
          </button>
          <button onClick={onCancel} className="btn btn-ghost btn-sm gap-1 text-error">
            <X className="w-3 h-3" /> Cancel
          </button>
        </div>
      </div>
    </div>
  );
};

const PlaybookResponseCard: React.FC<{ 
  playbook: AnsiblePlaybook;
  onDryRun: () => void;
  onApply: () => void;
  onCopy: () => void;
}> = ({ playbook, onDryRun, onApply, onCopy }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(playbook.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    onCopy();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <CheckCircle className="w-5 h-5 text-success" />
        <span className="font-semibold">Playbook Generated</span>
      </div>
      
      {/* Playbook Preview */}
      <div className="bg-base-300 rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 bg-base-200 border-b border-base-300">
          <span className="text-xs font-mono flex items-center gap-2">
            <FileCode className="w-3 h-3" /> {playbook.filename}
          </span>
          <button onClick={handleCopy} className="btn btn-ghost btn-xs">
            {copied ? <CheckCircle className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
          </button>
        </div>
        <pre className="p-3 text-xs overflow-x-auto max-h-64 overflow-y-auto">
          <code>{playbook.content}</code>
        </pre>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-2 justify-center flex-wrap">
        <button onClick={onDryRun} className="btn btn-outline btn-sm gap-1">
          <Play className="w-3 h-3" /> 🔬 Dry-Run First
        </button>
        <button onClick={onApply} className="btn btn-success btn-sm gap-1">
          <CheckCircle className="w-3 h-3" /> Apply to Devices
        </button>
      </div>
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
  const [currentPlanning, setCurrentPlanning] = useState<PlanningResponse | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const generateId = () => Math.random().toString(36).substring(2, 9);

  const handleSend = () => {
    if (!input.trim()) return;

    // Add user message
    const userMsg: Message = {
      id: generateId(),
      role: 'user',
      content: input,
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setAgentMode('planning');

    // Simulate AI planning response after delay
    setTimeout(() => {
      const planningMsg: Message = {
        id: generateId(),
        role: 'agent',
        content: "I've analyzed the current network state and prepared a plan:",
        timestamp: Date.now(),
        planning: MOCK_PLANNING_RESPONSE,
        mode: 'planning'
      };
      setMessages(prev => [...prev, planningMsg]);
      setCurrentPlanning(MOCK_PLANNING_RESPONSE);
      setAgentMode('awaiting_confirmation');
    }, 1500);
  };

  const handleProceed = () => {
    setAgentMode('generating');
    
    // Add transition message
    const generatingMsg: Message = {
      id: generateId(),
      role: 'agent',
      content: "Generating Ansible playbook...",
      timestamp: Date.now(),
      mode: 'generating'
    };
    setMessages(prev => [...prev, generatingMsg]);

    // Simulate playbook generation
    setTimeout(() => {
      const playbookMsg: Message = {
        id: generateId(),
        role: 'agent',
        content: "",
        timestamp: Date.now(),
        playbook: MOCK_PLAYBOOK,
        mode: 'playbook_ready'
      };
      setMessages(prev => [...prev.slice(0, -1), playbookMsg]);
      setAgentMode('playbook_ready');
      onPlaybookGenerated?.(MOCK_PLAYBOOK);
    }, 2000);
  };

  const handleRevise = () => {
    const reviseMsg: Message = {
      id: generateId(),
      role: 'agent',
      content: "What changes would you like to make to the plan?",
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, reviseMsg]);
    setAgentMode('idle');
  };

  const handleCancel = () => {
    const cancelMsg: Message = {
      id: generateId(),
      role: 'agent',
      content: "Plan cancelled. Let me know if you need anything else.",
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, cancelMsg]);
    setAgentMode('idle');
    setCurrentPlanning(null);
  };

  const handleDryRun = () => {
    const dryRunMsg: Message = {
      id: generateId(),
      role: 'agent',
      content: "🔬 Dry-run completed successfully! No errors detected. The playbook is safe to apply.",
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, dryRunMsg]);
  };

  const handleApply = () => {
    const applyMsg: Message = {
      id: generateId(),
      role: 'agent',
      content: "✅ Playbook applied successfully to R1 and R2! OSPF adjacency is now FULL.",
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, applyMsg]);
    setAgentMode('idle');
  };

  const handleCopy = () => {
    // Already handled in PlaybookResponseCard
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
              {msg.content && <p className="text-sm">{msg.content}</p>}
              
              {msg.mode === 'generating' && (
                <div className="flex items-center gap-2 text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Generating...</span>
                </div>
              )}

              {msg.planning && (
                <div className="mt-3">
                  <PlanningResponseCard 
                    planning={msg.planning}
                    onProceed={handleProceed}
                    onRevise={handleRevise}
                    onCancel={handleCancel}
                  />
                </div>
              )}

              {msg.playbook && (
                <PlaybookResponseCard 
                  playbook={msg.playbook}
                  onDryRun={handleDryRun}
                  onApply={handleApply}
                  onCopy={handleCopy}
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
            disabled={agentMode === 'generating'}
          />
          <button 
            onClick={handleSend} 
            className="btn btn-primary btn-sm btn-square"
            disabled={!input.trim() || agentMode === 'generating'}
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};

export default AIAgentPanel;
