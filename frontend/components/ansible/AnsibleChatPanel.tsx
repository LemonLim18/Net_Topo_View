import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { Send, Loader2, Bot, User2, Sparkles, AlertCircle } from 'lucide-react';
import { ansibleApi } from '../../src/services/api';
import type { AnsibleMessage, AnsibleJob } from '../../typesLLM';

interface AnsibleChatPanelProps {
  onPlaybookReady: (jobId: number, playbook: string, name: string) => void;
  onOpenFile:      (path: string) => void;
  activeJob:       AnsibleJob | null;
}

// ── Lightweight intent parser ─────────────────────────────────────────────────
// Extracts vendor / feature / hosts from plain English without an extra LLM call.
function parseIntent(text: string): {
  vendor: string;
  feature: string;
  target_hosts: string[];
  params: Record<string, any>;
} {
  const lower = text.toLowerCase();

  // Vendor
  const vendor =
    lower.includes('junos') || lower.includes('juniper') ? 'juniper_junos' :
    lower.includes('nxos') || lower.includes('nexus')    ? 'cisco_nxos'    :
    lower.includes('huawei') || lower.includes('vrp')    ? 'huawei_vrp'    :
    'cisco_ios';

  // Feature
  const feature =
    lower.includes('vlan')          ? 'vlan'      :
    lower.includes('ospf')          ? 'ospf'      :
    lower.includes('bgp')           ? 'bgp'       :
    lower.includes('interface')     ? 'interface' :
    lower.includes('acl') || lower.includes('access-list') ? 'acl' :
    lower.includes('route')         ? 'routing'   :
    'general';

  // Hosts: words that look like hostnames (contain - or sw, rt, rtr, core, access)
  const hostUnicodePattern = /\b([a-z][a-z0-9\-]{2,}(?:sw|rt|rtr|core|access|edge|leaf|spine|router|switch)[a-z0-9\-]*)\b/gi;
  const matches = [...text.matchAll(hostUnicodePattern)].map(m => m[1]);
  // Also handle comma-separated or space-separated items after "on"
  const onMatch = text.match(/\bon\s+([^\n.]+)/i);
  const hostStr = onMatch ? onMatch[1] : '';
  const extraHosts = hostStr.split(/[,\s]+/).filter(h => h.length > 3 && /[a-z]/i.test(h));
  const target_hosts = [...new Set([...matches, ...extraHosts])].slice(0, 10);

  // VLAN ID
  const params: Record<string, any> = {};
  const vlanMatch = text.match(/\bvlan\s+(\d+)/i);
  if (vlanMatch) params['vlan_id'] = parseInt(vlanMatch[1]);
  const nameMatch = text.match(/name[d]?\s+["']?(\w[\w-]+)["']?/i);
  if (nameMatch) params['vlan_name'] = nameMatch[1];

  return { vendor, feature, target_hosts: target_hosts.length ? target_hosts : ['all'], params };
}

const AnsibleChatPanel: React.FC<AnsibleChatPanelProps> = ({
  onPlaybookReady, onOpenFile, activeJob,
}) => {
  const [messages, setMessages] = useState<AnsibleMessage[]>([{
    id:        'welcome',
    role:      'assistant',
    content:   '**Ansible AI** ready.\n\nDescribe what you want to configure in plain English. For example:\n\n> *Configure VLAN 100 named DATA on all access switches*\n\nI will generate an Ansible playbook, which you can then review, dry-run, and deploy.',
    timestamp: Date.now(),
  }]);
  const [input, setInput]           = useState('');
  const [isGenerating, setGenerating] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const addMsg = useCallback((msg: Omit<AnsibleMessage, 'id' | 'timestamp'>) => {
    setMessages(prev => [...prev, { ...msg, id: crypto.randomUUID(), timestamp: Date.now() }]);
  }, []);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isGenerating) return;

    setInput('');
    addMsg({ role: 'user', content: text });

    // Loading bubble
    const loadingId = crypto.randomUUID();
    setMessages(prev => [...prev, {
      id: loadingId, role: 'assistant', content: '', isLoading: true, timestamp: Date.now(),
    }]);
    setGenerating(true);

    try {
      const { vendor, feature, target_hosts, params } = parseIntent(text);

      const result = await ansibleApi.createAndPlan({
        intent_description: text,
        target_hosts,
        vendor,
        feature,
        params,
      });

      const playbookName = `playbook_${result.job_id}.yml`;

      // Replace loading bubble with success message
      setMessages(prev => prev.map(m =>
        m.id === loadingId ? {
          ...m,
          isLoading: false,
          content:
            `✅ **Playbook generated** → \`${playbookName}\`\n\n` +
            `- **Job ID:** ${result.job_id}\n` +
            `- **Vendor:** \`${vendor}\`  **Feature:** \`${feature}\`\n` +
            `- **Targets:** ${target_hosts.join(', ')}\n\n` +
            `Recommend running **Check** to see the dry-run diff before deploying.`,
          jobId: result.job_id,
          playbookPath: playbookName,
        } : m
      ));

      onPlaybookReady(result.job_id, result.playbook, playbookName);
    } catch (err: any) {
      setMessages(prev => prev.map(m =>
        m.id === loadingId ? {
          ...m, isLoading: false, isError: true,
          content: `❌ Failed to generate playbook: ${err.message}`,
        } : m
      ));
    } finally {
      setGenerating(false);
    }
  }, [input, isGenerating, addMsg, onPlaybookReady]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full bg-base-100 border-l border-base-300">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-base-200 flex-shrink-0">
        <Sparkles className="h-4 w-4 text-primary" />
        <span className="text-sm font-bold">Ansible AI</span>
        {activeJob && (
          <span className={`badge badge-xs ml-auto ${
            activeJob.status === 'success' ? 'badge-success' :
            activeJob.status === 'failed'  ? 'badge-error'  :
            activeJob.status === 'running' ? 'badge-warning' :
            'badge-ghost'
          }`}>
            Job {activeJob.jobId} · {activeJob.status}
          </span>
        )}
      </div>

      {/* Message list */}
      <div className="flex-grow overflow-y-auto px-3 py-3 space-y-3">
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex gap-2 ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}
          >
            <div className={`flex-shrink-0 rounded-full p-1.5 h-7 w-7 flex items-center justify-center ${
              msg.role === 'user' ? 'bg-primary text-primary-content' : 'bg-base-300'
            }`}>
              {msg.role === 'user' ? <User2 className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
            </div>
            <div className={`max-w-[80%] rounded-xl px-3 py-2 text-sm ${
              msg.role === 'user'
                ? 'bg-primary text-primary-content rounded-br-none'
                : msg.isError
                  ? 'bg-error/10 text-error border border-error/30 rounded-bl-none'
                  : 'bg-base-200 rounded-bl-none'
            }`}>
              {msg.isLoading ? (
                <div className="flex items-center gap-2 opacity-60">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>Generating playbook…</span>
                </div>
              ) : msg.isError ? (
                <div className="flex items-start gap-1.5">
                  <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
                  <span>{msg.content}</span>
                </div>
              ) : (
                <ReactMarkdown
                  components={{
                    p:    ({children}) => <p className="mb-1 last:mb-0">{children}</p>,
                    code: ({children}) => <code className="bg-base-300 px-1 rounded text-xs font-mono">{children}</code>,
                    ul:   ({children}) => <ul className="list-disc pl-4 my-1 space-y-0.5">{children}</ul>,
                    strong: ({children}) => <strong className="font-bold">{children}</strong>,
                  }}
                >
                  {msg.content}
                </ReactMarkdown>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="p-3 border-t border-base-200 flex-shrink-0">
        <div className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            className="textarea textarea-bordered flex-grow text-sm resize-none min-h-[60px] max-h-[120px]"
            placeholder="Describe what you want to configure…"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isGenerating}
            rows={2}
          />
          <button
            className="btn btn-primary btn-sm h-auto py-3"
            onClick={handleSend}
            disabled={!input.trim() || isGenerating}
            title="Send (Enter)"
          >
            {isGenerating
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Send className="h-4 w-4" />
            }
          </button>
        </div>
        <p className="text-xs opacity-40 mt-1 text-center">Enter to send · Shift+Enter for newline</p>
      </div>
    </div>
  );
};

export default AnsibleChatPanel;
