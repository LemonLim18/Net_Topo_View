import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { Send, Loader2, Bot, Sparkles, AlertCircle, FileCode, RotateCcw, ChevronRight, Paperclip, X, TriangleAlert } from 'lucide-react';
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

// ── VS Code-style markdown renderer ──────────────────────────────────────────
interface MarkdownProps { content: string; onOpenFile?: (path: string) => void; }

const VscodeMarkdown: React.FC<MarkdownProps> = ({ content, onOpenFile }) => (
  <ReactMarkdown
    components={{
      p:    ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed" style={{ fontSize: 13, color: '#cccccc' }}>{children}</p>,
      strong: ({ children }) => <strong style={{ fontWeight: 600, color: '#e2c08d' }}>{children}</strong>,
      em:   ({ children }) => <em style={{ fontStyle: 'italic', color: '#9cdcfe' }}>{children}</em>,
      code: ({ children, className }) => {
        if (className?.includes('language-')) return null; // handed by pre
        return <code style={{ fontFamily: 'monospace', fontSize: 12, background: '#0a0a0a', color: '#ce9178', padding: '1px 6px', borderRadius: 3, border: '1px solid #3c3c3c' }}>{children}</code>;
      },
      pre: ({ children }) => {
        const codeEl = children as React.ReactElement<any>;
        const rawText = typeof codeEl?.props?.children === 'string' ? codeEl.props.children : String(codeEl?.props?.children ?? '');
        const lang = (codeEl?.props?.className ?? '').replace('language-', '') || 'yaml';
        const isFilePath = rawText.trim().includes('/') && !rawText.includes('\n') && rawText.trim().length < 80;
        if (isFilePath && onOpenFile) {
          return (
            <button onClick={() => onOpenFile(rawText.trim())}
              style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 8px', margin: '4px 0', borderRadius: 4, background: '#1e3a5f', color: '#4fc3f7', fontSize: 12, fontFamily: 'monospace', border: '1px solid #264f78', cursor: 'pointer', width: '100%', textAlign: 'left' }}
            >
              <FileCode style={{ width: 12, height: 12, flexShrink: 0 }} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{rawText.trim()}</span>
              <ChevronRight style={{ width: 10, height: 10, marginLeft: 'auto', opacity: 0.6, flexShrink: 0 }} />
            </button>
          );
        }
        return (
          <div style={{ margin: '8px 0', borderRadius: 4, overflow: 'hidden', border: '1px solid #3c3c3c' }}>
            <div style={{ padding: '3px 10px', background: '#2d2d2d', borderBottom: '1px solid #3c3c3c', display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 10, color: '#858585', fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{lang}</span>
            </div>
            <pre style={{ background: '#0d0d0d', color: '#d4d4d4', fontFamily: 'monospace', fontSize: 11.5, lineHeight: 1.55, padding: '10px 12px', overflowX: 'auto', margin: 0, whiteSpace: 'pre' }}>{rawText}</pre>
          </div>
        );
      },
      ul: ({ children }) => <ul style={{ listStyle: 'none', paddingLeft: 0, margin: '6px 0' }}>{children}</ul>,
      ol: ({ children }) => <ol style={{ paddingLeft: 16, margin: '6px 0', color: '#cccccc', fontSize: 13 }}>{children}</ol>,
      li: ({ children }) => <li style={{ display: 'flex', gap: 6, color: '#cccccc', fontSize: 13, lineHeight: 1.5, marginBottom: 3 }}><span style={{ color: '#569cd6', flexShrink: 0 }}>›</span><span>{children}</span></li>,
      blockquote: ({ children }) => <blockquote style={{ margin: '6px 0', paddingLeft: 10, borderLeft: '2px solid #569cd6', color: '#9aa0aa', fontStyle: 'italic', fontSize: 12 }}>{children}</blockquote>,
      h1: ({ children }) => <h1 style={{ fontSize: 14, fontWeight: 700, color: '#e2e2e2', margin: '10px 0 4px' }}>{children}</h1>,
      h2: ({ children }) => <h2 style={{ fontSize: 13, fontWeight: 600, color: '#e2e2e2', margin: '8px 0 4px' }}>{children}</h2>,
      h3: ({ children }) => <h3 style={{ fontSize: 12, fontWeight: 600, color: '#9cdcfe', margin: '6px 0 2px' }}>{children}</h3>,
      a: ({ href, children }) => {
        const isRelFile = href && !href.startsWith('http') && (href.includes('/') || /\.(yml|yaml|json|j2|txt)$/.test(href));
        if (isRelFile && onOpenFile) {
          return <button onClick={() => onOpenFile(href!)} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, color: '#4fc3f7', fontSize: 12, fontFamily: 'monospace', textDecoration: 'underline', cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}><FileCode style={{ width: 11, height: 11 }} />{children}</button>;
        }
        return <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: '#4fc3f7', textDecoration: 'underline' }}>{children}</a>;
      },
      hr: () => <hr style={{ border: 'none', borderTop: '1px solid #3c3c3c', margin: '10px 0' }} />,
    }}
  >
    {content}
  </ReactMarkdown>
);

// ── Main chat panel ───────────────────────────────────────────────────────────
const AnsibleChatPanel: React.FC<AnsibleChatPanelProps> = ({
  onPlaybookReady, onOpenFile, activeJob,
}) => {
  const [messages, setMessages] = useState<AnsibleMessage[]>([{
    id:        'welcome',
    role:      'assistant',
    content:   '**Ansible AI** ready.\n\nDescribe what you want to configure in plain English. For example:\n\n> *Configure VLAN 100 named DATA on all access switches*\n\nI will generate an Ansible playbook, which you can then review, dry-run, and deploy.',
    timestamp: Date.now(),
  }]);
  const [input, setInput]               = useState('');
  const [isGenerating, setGenerating]   = useState(false);
  const [isDragOver, setIsDragOver]     = useState(false);
  const [attachedFiles, setAttachedFiles] = useState<{ name: string; content: string; lang: string; size: number; truncated: boolean }[]>([]);
  const bottomRef      = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);
  const fileInputRef   = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ── File helpers ────────────────────────────────────────────────────────
  const langFor = (name: string) =>
    /\.ya?ml$/i.test(name) ? 'yaml' :
    /\.json$/i.test(name) ? 'json' :
    /\.j2$/i.test(name)   ? 'jinja2' :
    /\.conf$/i.test(name) ? 'ini' :
    /\.py$/i.test(name)   ? 'python' :
    'text';

  const readTextFile = (file: File): Promise<string> =>
    new Promise((res, rej) => {
      const reader = new FileReader();
      reader.onload  = () => res(reader.result as string);
      reader.onerror = () => rej(new Error('Failed to read file'));
      reader.readAsText(file);
    });

  const MAX_FILE_BYTES = 8 * 1024;
  const fmtSize = (b: number) => b < 1024 ? `${b}B` : b < 1024 * 1024 ? `${(b / 1024).toFixed(1)}K` : `${(b / (1024 * 1024)).toFixed(1)}M`;

  const attachFiles = useCallback(async (files: FileList | File[]) => {
    const arr = Array.from(files);
    const results = await Promise.allSettled(arr.map(async (f) => {
      const raw = await readTextFile(f);
      const truncated = raw.length > MAX_FILE_BYTES;
      const content   = truncated ? raw.slice(0, MAX_FILE_BYTES) + '\n… [truncated to 8 KB]' : raw;
      return { name: f.name, content, lang: langFor(f.name), size: f.size, truncated };
    }));
    const valid = results
      .filter((r): r is PromiseFulfilledResult<{ name: string; content: string; lang: string; size: number; truncated: boolean }> => r.status === 'fulfilled')
      .map(r => r.value);
    setAttachedFiles(prev => [...prev, ...valid]);
  }, []);

  // Panel-level drag handlers with counter to avoid child-element flicker
  const handlePanelDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current += 1;
    if (dragCounterRef.current === 1) setIsDragOver(true);
  }, []);

  const handlePanelDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current === 0) setIsDragOver(false);
  }, []);

  const handlePanelDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current = 0;
    setIsDragOver(false);

    // OS filesystem files (native drag from desktop/explorer)
    if (e.dataTransfer.files.length) {
      attachFiles(e.dataTransfer.files);
      return;
    }

    // Files dragged from the Ansible file tree (path token set by AnsibleFileTree)
    const ansiblePath = e.dataTransfer.getData('application/ansible-path');
    if (ansiblePath) {
      try {
        const res = await ansibleApi.readFile(ansiblePath);
        const name = ansiblePath.split('/').pop() ?? ansiblePath;
        const raw  = res.content ?? '';
        const truncated = raw.length > MAX_FILE_BYTES;
        const content   = truncated ? raw.slice(0, MAX_FILE_BYTES) + '\n… [truncated to 8 KB]' : raw;
        setAttachedFiles(prev => [...prev, {
          name,
          content,
          lang: langFor(name),
          size: res.size ?? raw.length,
          truncated,
        }]);
      } catch (_) { /* ignore — file may not be readable */ }
    }
  }, [attachFiles]);

  const removeAttachment = (idx: number) =>
    setAttachedFiles(prev => prev.filter((_, i) => i !== idx));

  const addMsg = useCallback((msg: Omit<AnsibleMessage, 'id' | 'timestamp'>) => {
    setMessages(prev => [...prev, { ...msg, id: crypto.randomUUID(), timestamp: Date.now() }]);
  }, []);

  const handleSend = useCallback(async () => {
    const baseText = input.trim();
    if (!baseText && attachedFiles.length === 0) return;
    if (isGenerating) return;

    // Build the full message: prepend attached file contents as fenced blocks
    const fileContext = attachedFiles.map(f =>
      `**File: \`${f.name}\`**\n\`\`\`${f.lang}\n${f.content.trimEnd()}\n\`\`\``
    ).join('\n\n');
    const text = fileContext ? (fileContext + (baseText ? '\n\n' + baseText : '')) : baseText;

    setInput('');
    setAttachedFiles([]);
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
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const clearMessages = () => setMessages([{
    id: 'welcome', role: 'assistant', timestamp: Date.now(),
    content: '**Ansible AI** is ready. Describe your configuration intent.',
  }]);

  /* ── VS Code Copilot-style shell ──────────────────────────────────────── */
  return (
    <div
      className="flex flex-col h-full"
      style={{ background: '#1e1e1e', fontFamily: "'Menlo','Monaco','Consolas','Courier New',monospace", position: 'relative' }}
      onDragEnter={handlePanelDragEnter}
      onDragLeave={handlePanelDragLeave}
      onDragOver={e => e.preventDefault()}
      onDrop={handlePanelDrop}
    >
      {/* Full-panel drag-over overlay */}
      {isDragOver && (
        <div style={{
          position: 'absolute', inset: 0, zIndex: 50,
          background: 'rgba(14,99,156,0.12)',
          border: '2px dashed #0e639c',
          borderRadius: 4,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          pointerEvents: 'none',
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, color: '#4fc3f7' }}>
            <Paperclip style={{ width: 28, height: 28, opacity: 0.85 }} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Drop files to attach as context</span>
            <span style={{ fontSize: 11, color: '#7ab3d4' }}>.yml .yaml .json .j2 .conf .py .ini .log .csv .md</span>
          </div>
        </div>
      )}

      {/* Title bar — matches VS Code secondary sidebar */}
      <div className="flex items-center justify-between px-3 shrink-0"
        style={{ background: '#252526', borderBottom: '1px solid #3c3c3c', height: 35 }}>
        <div className="flex items-center gap-2">
          <Sparkles style={{ width: 13, height: 13, color: '#c586c0' }} />
          <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', color: '#bbbbbb', textTransform: 'uppercase' }}>ANSIBLE AI</span>
        </div>
        <div className="flex items-center gap-1">
          {activeJob && (
            <span style={{
              fontSize: 10, padding: '1px 7px', borderRadius: 10,
              background: activeJob.status === 'success' ? '#1b3a1f' : activeJob.status === 'failed' ? '#3a1010' : activeJob.status === 'running' ? '#3a2e00' : '#2d2d2d',
              color:      activeJob.status === 'success' ? '#69f0ae' : activeJob.status === 'failed' ? '#ff8a80' : activeJob.status === 'running' ? '#ffd740' : '#858585',
              border:     `1px solid ${activeJob.status === 'success' ? '#2e7d32' : activeJob.status === 'failed' ? '#c62828' : activeJob.status === 'running' ? '#f9a825' : '#3c3c3c'}`,
            }}>#{activeJob.jobId} · {activeJob.status}</span>
          )}
          <button onClick={clearMessages} title="Clear"
            style={{ padding: '3px 4px', borderRadius: 3, background: 'transparent', border: 'none', color: '#666', cursor: 'pointer' }}
            className="hover:bg-[#2d2d2d] transition-colors">
            <RotateCcw style={{ width: 13, height: 13 }} />
          </button>
        </div>
      </div>

      {/* Message list */}
      <div className="grow overflow-y-auto" style={{ padding: '6px 0' }}>
        {messages.map((msg, idx) => (
          <div key={msg.id}>
            {idx > 0 && <div style={{ height: 1, background: '#2a2a2a', margin: '2px 0' }} />}

            {msg.role === 'user' ? (
              /* User message */
              <div style={{ padding: '8px 14px 6px', background: '#252526' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
                  <div style={{ width: 16, height: 16, borderRadius: '50%', background: '#0e639c', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, color: '#fff', fontWeight: 700, flexShrink: 0 }}>U</div>
                  <span style={{ fontSize: 11, fontWeight: 600, color: '#cccccc' }}>You</span>
                  <span style={{ fontSize: 10, color: '#444' }}>{new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
                <div style={{ paddingLeft: 23, fontSize: 13, color: '#cccccc', lineHeight: 1.5, fontFamily: 'system-ui,-apple-system,sans-serif' }}>
                  {msg.content}
                </div>
              </div>
            ) : (
              /* Assistant message */
              <div style={{ padding: '8px 14px 6px', background: '#1e1e1e' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
                  <div style={{ width: 16, height: 16, borderRadius: 3, background: '#c586c0', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                    <Bot style={{ width: 10, height: 10, color: '#1e1e1e' }} />
                  </div>
                  <span style={{ fontSize: 11, fontWeight: 600, color: '#c586c0' }}>Ansible AI</span>
                </div>

                <div style={{ paddingLeft: 23, fontFamily: 'system-ui,-apple-system,sans-serif' }}>
                  {msg.isLoading ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#858585', fontSize: 13 }}>
                      <Loader2 style={{ width: 14, height: 14, color: '#569cd6' }} className="animate-spin" />
                      Generating playbook…
                    </div>
                  ) : msg.isError ? (
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 7, color: '#f48771', fontSize: 13 }}>
                      <AlertCircle style={{ width: 14, height: 14, flexShrink: 0, marginTop: 2 }} />
                      {msg.content}
                    </div>
                  ) : (
                    <VscodeMarkdown content={msg.content} onOpenFile={onOpenFile} />
                  )}

                  {/* Playbook file chip */}
                  {msg.playbookPath && (
                    <button onClick={() => onOpenFile(msg.playbookPath!)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, padding: '4px 8px', borderRadius: 4, background: '#1e3a5f', border: '1px solid #264f78', color: '#4fc3f7', fontSize: 11, fontFamily: 'monospace', cursor: 'pointer' }}
                      className="hover:bg-[#1e4a7f] transition-colors w-full text-left"
                    >
                      <FileCode style={{ width: 12, height: 12, flexShrink: 0 }} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{msg.playbookPath}</span>
                      <ChevronRight style={{ width: 10, height: 10, marginLeft: 'auto', opacity: 0.6, flexShrink: 0 }} />
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="shrink-0" style={{ borderTop: '1px solid #3c3c3c', background: '#1e1e1e', padding: '8px 10px' }}>

        {/* Hidden native file picker */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".yml,.yaml,.json,.j2,.conf,.txt,.py,.ini,.log,.csv,.md"
          style={{ display: 'none' }}
          onChange={e => {
            if (e.target.files?.length) attachFiles(e.target.files);
            e.target.value = '';
          }}
        />

        {/* Input box wrapper */}
        <div
          style={{
            borderRadius: 6,
            border: '1px solid #3c3c3c',
            background: '#2d2d2d',
            position: 'relative',
          }}
          className="focus-within:border-[#0e639c]! transition-colors"
        >

          {/* Attached file chips */}
          {attachedFiles.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '6px 8px 0' }}>
              {attachedFiles.map((f, i) => (
                <div
                  key={i}
                  role="listitem"
                  title={f.truncated ? `${f.name} — truncated to 8 KB` : `${f.name} (${fmtSize(f.size)})`}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 4,
                    background: '#1e3a5f', border: `1px solid ${f.truncated ? '#856404' : '#264f78'}`, borderRadius: 4,
                    padding: '2px 6px', fontSize: 11, fontFamily: 'monospace', color: '#4fc3f7',
                    maxWidth: 180,
                  }}
                >
                  <FileCode style={{ width: 11, height: 11, flexShrink: 0 }} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{f.name}</span>
                  <span style={{ fontSize: 10, color: '#7ab3d4', flexShrink: 0 }}>{fmtSize(f.size)}</span>
                  {f.truncated && (
                    <TriangleAlert style={{ width: 10, height: 10, color: '#fbbf24', flexShrink: 0 }} />
                  )}
                  <button
                    onClick={() => removeAttachment(i)}
                    aria-label={`Remove ${f.name}`}
                    style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: '#7ab3d4', display: 'flex', flexShrink: 0 }}
                  >
                    <X style={{ width: 10, height: 10 }} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Textarea + buttons row */}
          <div style={{ display: 'flex', gap: 6, alignItems: 'flex-end', padding: '6px 8px' }}>
            <textarea
              ref={textareaRef}
              style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', resize: 'none', fontSize: 13, fontFamily: 'system-ui,-apple-system,sans-serif', color: '#cccccc', minHeight: 52, maxHeight: 120, lineHeight: 1.5 }}
              placeholder={attachedFiles.length ? 'Add a message or send file as-is…' : 'Ask Ansible AI or drop a file…'}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isGenerating}
              rows={2}
            />
            {/* Attach File as Context button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              title="Attach File as Context"
              aria-label="Attach File as Context"
              style={{ padding: 6, borderRadius: 4, border: 'none', cursor: 'pointer', background: 'transparent', color: '#666', flexShrink: 0, alignSelf: 'flex-end', transition: 'color 0.15s' }}
              className="hover:text-[#4fc3f7]!"
            >
              <Paperclip style={{ width: 15, height: 15 }} />
            </button>
            {/* Send button */}
            <button
              onClick={handleSend}
              disabled={(!input.trim() && attachedFiles.length === 0) || isGenerating}
              title="Send (Enter)"
              style={{ padding: 6, borderRadius: 4, border: 'none', flexShrink: 0, alignSelf: 'flex-end', transition: 'background 0.15s',
                cursor: (input.trim() || attachedFiles.length > 0) && !isGenerating ? 'pointer' : 'not-allowed',
                background: (input.trim() || attachedFiles.length > 0) && !isGenerating ? '#0e639c' : '#333',
                color:      (input.trim() || attachedFiles.length > 0) && !isGenerating ? '#ffffff' : '#555',
              }}
            >
              {isGenerating
                ? <Loader2 style={{ width: 15, height: 15 }} className="animate-spin" />
                : <Send style={{ width: 15, height: 15 }} />
              }
            </button>
          </div>
        </div>

        <p style={{ fontSize: 10, color: '#444', textAlign: 'center', marginTop: 4 }}>Enter to send · Shift+Enter for newline · 📎 attach or drag from file tree</p>
      </div>
    </div>
  );
};

export default AnsibleChatPanel;
