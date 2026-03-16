import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import { DiffEditor } from '@monaco-editor/react';
import { Send, Loader2, Bot, Sparkles, AlertCircle, FileCode, RotateCcw, ChevronRight, Paperclip, X, TriangleAlert, ChevronDown, Check, Undo2, RefreshCw } from 'lucide-react';
import { ansibleApi, llmApi, ragApi } from '../../src/services/api';
import { Toast } from '../../src/utils/swal';
import type { LlmRuntimeState } from '../../src/services/api';
import type { AnsibleGeneratedArtifactFile, AnsibleMessage, AnsibleJob, AnsibleProposedEdit } from '../../typesLLM';

const ANSIBLE_MODEL_STORAGE_KEY = 'ansibleStudio.selectedModel';
const OLLAMA_BASE_URL_STORAGE_KEY = 'llmRuntimeSelector.ollamaBaseUrl';
const DEFAULT_OLLAMA_BASE_URL = 'http://192.168.1.252:11434';

function prettifyModelLabel(providerName: string, modelName: string): string {
  if (providerName === 'gemini') {
    if (modelName === 'gemini-2.5-flash') return 'Gemini 2.5 Flash';
    if (modelName === 'gemini-2.5-pro') return 'Gemini 2.5 Pro';
    return modelName;
  }
  return providerName === 'ollama' ? `${modelName} | Ollama` : modelName;
}

interface AnsibleChatPanelProps {
  onPlaybookReady: (payload: {
    jobId: number;
    content: string;
    name: string;
    entrypointPath?: string | null;
    files?: AnsibleGeneratedArtifactFile[];
  }) => void;
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
  // Items after "on" that look like device names only (exclude interface names, parentheticals)
  const onMatch = text.match(/\bon\s+([^\n.]+)/i);
  const hostStr = onMatch ? onMatch[1] : '';
  const isLikelyHostname = (h: string) => {
    const t = h.replace(/;$/,'').trim();
    if (t.length < 3 || !/[a-z]/i.test(t)) return false;
    if (/^\(|\)$/.test(t) || /^(?:Gi|Te|Fa|Eth|Ethernet|GE)\s*\d+\/\d+/i.test(t) || /^\d+\/\d+/.test(t)) return false;
    return /^[a-z][a-z0-9_\-]*$/i.test(t);
  };
  const extraHosts = hostStr.split(/[,\s]+/).map(h => h.replace(/;$/,'').trim()).filter(isLikelyHostname);
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
interface AnsibleModelSelection {
  provider: string;
  model: string;
  ollama_base_url?: string | null;
}

const AnsibleChatPanel: React.FC<AnsibleChatPanelProps> = ({
  onPlaybookReady, onOpenFile, activeJob,
}) => {
  const [messages, setMessages] = useState<AnsibleMessage[]>([{
    id:        'welcome',
    role:      'assistant',
    content:   '**Ansible AI** ready.\n\nDescribe what you want to configure in plain English. For example:\n\n> *Configure VLAN 100 named DATA on all access switches*\n\nOr use **`/template`** to generate from a Config Engine template:\n\n> `/template cisco_ios vlan {"vlan_id":100,"vlan_name":"DATA"}`\n\nI will generate an Ansible playbook, which you can then review, dry-run, and deploy.',
    timestamp: Date.now(),
  }]);
  const [input, setInput]               = useState('');
  const [isGenerating, setGenerating]   = useState(false);
  const [isDragOver, setIsDragOver]     = useState(false);
  const [attachedFiles, setAttachedFiles] = useState<{ name: string; content: string; lang: string; size: number; truncated: boolean }[]>([]);
  const [runtime, setRuntime]           = useState<LlmRuntimeState | null>(null);
  const [ansibleModel, setAnsibleModel] = useState<AnsibleModelSelection>({ provider: 'gemini', model: 'gemini-2.5-flash', ollama_base_url: null });
  const [modelSearch, setModelSearch]   = useState('');
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [isRebuildingRag, setIsRebuildingRag] = useState(false);
  const bottomRef      = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);
  const fileInputRef   = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  /** Hydrated file paths for messages that only had playbookPath (e.g. from before multi-file links). */
  const [hydratedFilePaths, setHydratedFilePaths] = useState<Record<string, string[]>>({});

  // Load LLM runtime (Gemini + Ollama models) and restore Ansible model selection
  useEffect(() => {
    const ollamaBaseUrl = localStorage.getItem(OLLAMA_BASE_URL_STORAGE_KEY) || DEFAULT_OLLAMA_BASE_URL;
    llmApi.getRuntime(ollamaBaseUrl).then((r) => {
      setRuntime(r);
      try {
        const raw = localStorage.getItem(ANSIBLE_MODEL_STORAGE_KEY);
        if (raw) {
          const parsed = JSON.parse(raw) as AnsibleModelSelection;
          if (parsed?.provider && parsed?.model) {
            const ollamaDesc = r.providers.find((p) => p.name === 'ollama');
            setAnsibleModel({
              provider: parsed.provider,
              model: parsed.model,
              ollama_base_url: parsed.provider === 'ollama' ? (parsed.ollama_base_url ?? ollamaDesc?.base_url ?? null) : null,
            });
            return;
          }
        }
      } catch (_) { /* ignore */ }
      setAnsibleModel({
        provider: r.active_provider || 'gemini',
        model: r.active_model || 'gemini-2.5-flash',
        ollama_base_url: r.active_base_url ?? null,
      });
    }).catch(() => { /* keep defaults */ });
  }, []);

  const combinedModelOptions = useMemo(() => {
    if (!runtime?.providers) return [];
    return runtime.providers.flatMap((provider) =>
      provider.models.map((modelName) => ({
        providerName: provider.name,
        providerLabel: provider.label,
        modelName,
        displayLabel: prettifyModelLabel(provider.name, modelName),
      })),
    );
  }, [runtime?.providers]);

  const filteredModelOptions = useMemo(() => {
    if (!modelSearch.trim()) return combinedModelOptions;
    const q = modelSearch.trim().toLowerCase();
    return combinedModelOptions.filter(
      (o) =>
        o.displayLabel.toLowerCase().includes(q) ||
        o.modelName.toLowerCase().includes(q) ||
        o.providerName.toLowerCase().includes(q),
    );
  }, [combinedModelOptions, modelSearch]);

  const currentModelLabel = useMemo(() => {
    return prettifyModelLabel(ansibleModel.provider, ansibleModel.model);
  }, [ansibleModel.provider, ansibleModel.model]);

  const persistAnsibleModel = useCallback((sel: AnsibleModelSelection) => {
    setAnsibleModel(sel);
    try {
      localStorage.setItem(ANSIBLE_MODEL_STORAGE_KEY, JSON.stringify(sel));
    } catch (_) { /* ignore */ }
  }, []);

  const handleSelectModel = useCallback((providerName: string, modelName: string) => {
    const ollamaDesc = runtime?.providers.find((p) => p.name === 'ollama');
    persistAnsibleModel({
      provider: providerName,
      model: modelName,
      ollama_base_url: providerName === 'ollama' ? (ollamaDesc?.base_url ?? null) : null,
    });
    setModelDropdownOpen(false);
    setModelSearch('');
  }, [runtime?.providers, persistAnsibleModel]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!modelDropdownOpen) return;
    const onOutside = (e: MouseEvent) => {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) setModelDropdownOpen(false);
    };
    document.addEventListener('mousedown', onOutside);
    return () => document.removeEventListener('mousedown', onOutside);
  }, [modelDropdownOpen]);

  // Hydrate file list from job for messages that show "X generated files" but only had playbookPath (e.g. old responses)
  useEffect(() => {
    const toHydrate = messages.filter(
      (m) => m.role === 'assistant' && m.jobId && !m.isLoading && !m.isError &&
        (!m.generatedPaths?.length || m.generatedPaths.length <= 1) &&
        hydratedFilePaths[m.id] === undefined
    );
    toHydrate.forEach((m) => {
      const msgId = m.id;
      ansibleApi.getResult(m.jobId!).then((r) => {
        const paths = (r.files ?? []).map((f) => f.path).filter(Boolean);
        if (paths.length > 0) {
          setHydratedFilePaths((prev) => ({ ...prev, [msgId]: paths }));
        }
      }).catch(() => { /* ignore */ });
    });
  }, [messages, hydratedFilePaths]);

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
      // ── /template slash-command: generate from Config Engine template ────
      const templateMatch = baseText.match(/^\/template\s+(\S+)\s+(\S+)(?:\s+(.+))?$/i);

      if (templateMatch) {
        const [, tplVendor, tplFeature, tplParamsRaw] = templateMatch;
        let tplParams: Record<string, any> = {};
        if (tplParamsRaw) {
          try { tplParams = JSON.parse(tplParamsRaw); }
          catch { tplParams = {}; }
        }

        const result = await ansibleApi.createFromTemplate({
          vendor: tplVendor,
          feature: tplFeature,
          params: tplParams,
          target_hosts: ['all'],
        });

        const playbookName = result.entrypoint_path || `playbook_${result.job_id}.yml`;
        setMessages(prev => prev.map(m =>
          m.id === loadingId ? {
            ...m,
            isLoading: false,
            content:
              `✅ **Template playbook generated** → \`${playbookName}\`\n\n` +
              `- **Job ID:** ${result.job_id}\n` +
              `- **Vendor:** \`${tplVendor}\`  **Feature:** \`${tplFeature}\`\n` +
              `- **Source:** Config Engine template\n\n` +
              `Recommend running **Check** to see the dry-run diff before deploying.`,
            jobId: result.job_id,
            playbookPath: playbookName,
            generatedPaths: result.files?.map((file) => file.path) || [playbookName],
          } : m
        ));

        onPlaybookReady({
          jobId: result.job_id,
          content: result.playbook,
          name: playbookName.split('/').pop() ?? playbookName,
          entrypointPath: result.entrypoint_path,
          files: result.files,
        });
      } else {
        // ── Standard LLM generation ─────────────────────────────────────────
        const { vendor, feature, target_hosts, params } = parseIntent(text);

        const result = await ansibleApi.createAndPlan({
          intent_description: text,
          target_hosts,
          vendor,
          feature,
          params,
          provider: ansibleModel.provider,
          model: ansibleModel.model,
          ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
        });

        const playbookName = result.entrypoint_path || `playbook_${result.job_id}.yml`;
        const resolvedHosts = 'target_hosts' in result && Array.isArray((result as { target_hosts?: string[] }).target_hosts) ? (result as { target_hosts: string[] }).target_hosts : target_hosts;
        const displayTargets = resolvedHosts.join(', ');

        setMessages(prev => prev.map(m =>
          m.id === loadingId ? {
            ...m,
            isLoading: false,
            content:
              `✅ **${result.artifact_type === 'bundle' ? 'Bundle generated' : 'Playbook generated'}** → \`${playbookName}\`\n\n` +
              `- **Job ID:** ${result.job_id}\n` +
              `- **Vendor:** \`${vendor}\`  **Feature:** \`${feature}\`\n` +
              `- **Targets:** ${displayTargets}\n` +
              (result.artifact_type === 'bundle' ? `- **Files:** ${(result.files?.length ?? 0)} generated files\n\n` : `\n`) +
              `Recommend running **Check** to see the dry-run diff before deploying.`,
            jobId: result.job_id,
            playbookPath: playbookName,
            generatedPaths: result.files?.map((file) => file.path) || [playbookName],
          } : m
        ));

        onPlaybookReady({
          jobId: result.job_id,
          content: result.playbook,
          name: playbookName.split('/').pop() ?? playbookName,
          entrypointPath: result.entrypoint_path,
          files: result.files,
        });
      }
    } catch (err: any) {
      let errorMsg = err.message || 'Unknown error';
      if (typeof err.message === 'object') {
        try {
          errorMsg = err.message.message || JSON.stringify(err.message, null, 1);
        } catch {
          errorMsg = String(err.message);
        }
      }
      setMessages(prev => prev.map(m =>
        m.id === loadingId ? {
          ...m, isLoading: false, isError: true,
          content: `❌ **Failed to generate playbook**\n\n${errorMsg}`,
        } : m
      ));
    } finally {
      setGenerating(false);
    }
  }, [input, isGenerating, addMsg, onPlaybookReady, attachedFiles, ansibleModel]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const clearMessages = () => setMessages([{
    id: 'welcome', role: 'assistant', timestamp: Date.now(),
    content: '**Ansible AI** is ready. Describe your configuration intent.',
  }]);

  const handleRebuildRag = useCallback(async () => {
    setIsRebuildingRag(true);
    try {
      const result = await ragApi.rebuild();
      const errors = result?.errors ?? [];
      if (errors.length > 0) {
        Toast.warning('RAG Rebuild finished with errors', `${result?.total_chunks ?? 0} chunks; ${errors.length} error(s). Check Knowledge Base for details.`);
      } else {
        Toast.success('RAG Rebuild started', `Re-ingestion runs in the background. ${result?.total_chunks ?? 0} chunks will be updated.`);
      }
    } catch (err: any) {
      Toast.error('RAG Rebuild failed', err?.message ?? 'Could not start rebuild.');
    } finally {
      setIsRebuildingRag(false);
    }
  }, []);

  /* ── Proposed edits block (diff + Keep/Undo per file, Keep All/Undo All) ── */
  function ProposedEditsBlock({
    edits,
    onKeepFile,
    onUndoFile,
    onKeepAll,
    onUndoAll,
    onOpenFile,
  }: {
    edits: AnsibleProposedEdit[];
    onKeepFile: (path: string, suggested: string) => void;
    onUndoFile: (path: string) => void;
    onKeepAll: () => void;
    onUndoAll: () => void;
    onOpenFile: (path: string) => void;
  }) {
    const language = (path: string) =>
      /\.(ya?ml|json)$/i.test(path) ? 'yaml' : /\.(md|markdown)$/i.test(path) ? 'markdown' : 'plaintext';
    return (
      <div className="mt-4 rounded-lg border border-base-300 bg-base-300 overflow-hidden">
        <div className="px-3 py-2 border-b border-base-300 flex items-center justify-between flex-wrap gap-2">
          <span className="text-xs font-semibold text-base-content/90">Proposed edits</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-xs btn-success gap-1"
              onClick={onKeepAll}
            >
              <Check className="h-3 w-3" /> Keep All
            </button>
            <button
              type="button"
              className="btn btn-xs btn-ghost gap-1"
              onClick={onUndoAll}
            >
              <Undo2 className="h-3 w-3" /> Undo All
            </button>
          </div>
        </div>
        <div className="divide-y divide-base-300">
          {edits.map((edit) => (
            <div key={edit.path} className="bg-base-200">
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-base-300">
                <button
                  type="button"
                  className="text-left text-xs font-mono text-primary hover:underline truncate flex-1"
                  onClick={() => onOpenFile(edit.path)}
                >
                  {edit.path}
                </button>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    className="btn btn-xs btn-success gap-0.5"
                    onClick={() => onKeepFile(edit.path, edit.suggested)}
                  >
                    <Check className="h-3 w-3" /> Keep
                  </button>
                  <button
                    type="button"
                    className="btn btn-xs btn-ghost gap-0.5"
                    onClick={() => onUndoFile(edit.path)}
                  >
                    <Undo2 className="h-3 w-3" /> Undo
                  </button>
                </div>
              </div>
              <div className="h-48 min-h-[120px]">
                <DiffEditor
                  original={edit.original}
                  modified={edit.suggested}
                  language={language(edit.path)}
                  theme="vs-dark"
                  options={{
                    readOnly: true,
                    renderSideBySide: true,
                    enableSplitViewResizing: true,
                    fontSize: 12,
                    minimap: { enabled: false },
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  /* ── Panel aligned with app theme (dark) ───────────────────────────────── */
  return (
    <div
      className="flex flex-col h-full bg-base-200 text-base-content relative select-text font-sans"
      onDragEnter={handlePanelDragEnter}
      onDragLeave={handlePanelDragLeave}
      onDragOver={e => e.preventDefault()}
      onDrop={handlePanelDrop}
    >
      {/* Full-panel drag-over overlay */}
      {isDragOver && (
        <div className="absolute inset-0 z-50 flex items-center justify-center pointer-events-none rounded border-2 border-dashed border-primary/60 bg-primary/10">
          <div className="flex flex-col items-center gap-2 text-primary">
            <Paperclip className="w-7 h-7 opacity-85" />
            <span className="text-sm font-semibold">Drop files to attach as context</span>
            <span className="text-xs opacity-80">.yml .yaml .json .j2 .conf .py .ini .log .csv .md</span>
          </div>
        </div>
      )}

      {/* Title bar */}
      <div className="flex items-center justify-between px-3 shrink-0 h-9 bg-base-300 border-b border-base-300">
        <div className="flex items-center gap-2">
          <Sparkles className="w-3.5 h-3.5 text-primary shrink-0" />
          <span className="text-[11px] font-semibold tracking-wider uppercase text-base-content/90">Ansible AI</span>
          {/* VSCode-style model selector */}
          <div ref={modelDropdownRef} style={{ position: 'relative' }}>
            <button
              type="button"
              onClick={() => setModelDropdownOpen((o) => !o)}
              style={{
                display: 'flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 4,
                background: modelDropdownOpen ? '#3c3c3c' : 'transparent', border: '1px solid transparent',
                color: '#9cdcfe', fontSize: 11, fontFamily: 'inherit', cursor: 'pointer',
              }}
              className="hover:bg-[#2d2d2d]"
            >
              <span style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{currentModelLabel}</span>
              <ChevronDown style={{ width: 12, height: 12, opacity: modelDropdownOpen ? 1 : 0.7, transform: modelDropdownOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }} />
            </button>
            {modelDropdownOpen && (
              <div
                style={{
                  position: 'absolute', top: '100%', left: 0, marginTop: 2, zIndex: 100,
                  minWidth: 220, maxWidth: 320, maxHeight: 280,
                  background: '#252526', border: '1px solid #3c3c3c', borderRadius: 4, boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
                  display: 'flex', flexDirection: 'column', overflow: 'hidden',
                }}
              >
                <div style={{ padding: 6, borderBottom: '1px solid #3c3c3c' }}>
                  <input
                    type="text"
                    placeholder="Search models"
                    value={modelSearch}
                    onChange={(e) => setModelSearch(e.target.value)}
                    autoFocus
                    style={{
                      width: '100%', padding: '4px 8px', fontSize: 12, background: '#3c3c3c', border: '1px solid #555',
                      borderRadius: 4, color: '#cccccc', outline: 'none',
                    }}
                  />
                </div>
                <div style={{ overflowY: 'auto', flex: 1, padding: 4 }}>
                  {filteredModelOptions.length === 0 ? (
                    <div style={{ padding: 8, fontSize: 11, color: '#858585' }}>No models found</div>
                  ) : (
                    filteredModelOptions.map((opt) => {
                      const isSelected = ansibleModel.provider === opt.providerName && ansibleModel.model === opt.modelName;
                      return (
                        <button
                          key={`${opt.providerName}::${opt.modelName}`}
                          type="button"
                          onClick={() => handleSelectModel(opt.providerName, opt.modelName)}
                          style={{
                            width: '100%', textAlign: 'left', padding: '5px 8px', borderRadius: 4, border: 'none',
                            background: isSelected ? '#0e639c' : 'transparent', color: isSelected ? '#fff' : '#cccccc',
                            fontSize: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
                          }}
                          className="hover:bg-[#2d2d2d]"
                        >
                          {isSelected && <span style={{ color: '#4fc3f7' }}>✓</span>}
                          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{opt.displayLabel}</span>
                          <span style={{ fontSize: 10, color: '#858585', flexShrink: 0 }}>{opt.providerName === 'ollama' ? 'Local' : 'Cloud'}</span>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </div>
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
          <button
            onClick={handleRebuildRag}
            disabled={isRebuildingRag}
            title="Rebuild RAG (re-ingest reference docs so the LLM uses the latest Ansible reference)"
            style={{ padding: '3px 4px', borderRadius: 3, background: 'transparent', border: 'none', color: '#666', cursor: isRebuildingRag ? 'wait' : 'pointer' }}
            className="hover:bg-[#2d2d2d] transition-colors disabled:opacity-50"
          >
            <RefreshCw style={{ width: 13, height: 13 }} className={isRebuildingRag ? 'animate-spin' : ''} />
          </button>
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
            {idx > 0 && <div className="h-px bg-base-300 my-0.5" />}

            {msg.role === 'user' ? (
              /* User message */
              <div className="px-3 py-2 bg-base-300">
                <div className="flex items-center gap-2 mb-1">
                  <div className="w-4 h-4 rounded-full bg-primary flex items-center justify-center text-[9px] text-primary-content font-bold shrink-0">U</div>
                  <span className="text-[11px] font-semibold text-base-content">You</span>
                  <span className="text-[10px] text-base-content/50">{new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
                <div className="pl-6 text-sm text-base-content/90 leading-relaxed font-sans select-text">
                  {msg.content}
                </div>
              </div>
            ) : (
              /* Assistant message */
              <div className="px-3 py-2 bg-base-200">
                <div className="flex items-center gap-2 mb-1.5">
                  <div className="w-4 h-4 rounded-sm bg-primary flex items-center justify-center shrink-0">
                    <Bot className="w-2.5 h-2.5 text-primary-content" />
                  </div>
                  <span className="text-[11px] font-semibold text-primary">Ansible AI</span>
                </div>

                <div className="pl-6 font-sans select-text">
                  {msg.isLoading ? (
                    <div className="flex items-center gap-2 text-base-content/60 text-sm">
                      <Loader2 className="w-3.5 h-3.5 text-primary animate-spin shrink-0" />
                      Generating playbook…
                    </div>
                  ) : msg.isError ? (
                    <div className="flex items-start gap-2 text-error text-sm">
                      <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      {msg.content}
                    </div>
                  ) : (
                    <VscodeMarkdown content={msg.content} onOpenFile={onOpenFile} />
                  )}

                  {/* Proposed edits: diff view with Keep / Undo per file and Keep All / Undo All */}
                  {msg.proposedEdits && msg.proposedEdits.length > 0 && (
                    <ProposedEditsBlock
                      edits={msg.proposedEdits}
                      onKeepFile={(path, suggested) => {
                        ansibleApi.writeFile(path, suggested).then(() => {
                          setMessages((prev) =>
                            prev.map((m) =>
                              m.id === msg.id
                                ? { ...m, proposedEdits: m.proposedEdits!.filter((e) => e.path !== path) }
                                : m
                            )
                          );
                        });
                      }}
                      onUndoFile={(path) =>
                        setMessages((prev) =>
                          prev.map((m) =>
                            m.id === msg.id
                              ? { ...m, proposedEdits: m.proposedEdits!.filter((e) => e.path !== path) }
                              : m
                          )
                        )
                      }
                      onKeepAll={() => {
                        Promise.all(
                          msg.proposedEdits!.map((e) => ansibleApi.writeFile(e.path, e.suggested))
                        ).then(() =>
                          setMessages((prev) =>
                            prev.map((m) => (m.id === msg.id ? { ...m, proposedEdits: [] } : m))
                          )
                        );
                      }}
                      onUndoAll={() =>
                        setMessages((prev) =>
                          prev.map((m) => (m.id === msg.id ? { ...m, proposedEdits: [] } : m))
                        )
                      }
                      onOpenFile={onOpenFile}
                    />
                  )}

                  {/* Generated files: from message, hydrated from job for old messages, or single playbook chip */}
                  {((msg.generatedPaths?.length ? msg.generatedPaths : null) ??
                   (msg.jobId && hydratedFilePaths[msg.id]?.length ? hydratedFilePaths[msg.id] : null) ??
                   (msg.playbookPath ? [msg.playbookPath] : [])
                  ).map((filePath) => (
                    <button
                      key={filePath}
                      onClick={() => onOpenFile(filePath)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, padding: '4px 8px', borderRadius: 4, background: '#1e3a5f', border: '1px solid #264f78', color: '#4fc3f7', fontSize: 11, fontFamily: 'monospace', cursor: 'pointer' }}
                      className="hover:bg-[#1e4a7f] transition-colors w-full text-left"
                    >
                      <FileCode style={{ width: 12, height: 12, flexShrink: 0 }} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{filePath}</span>
                      <ChevronRight style={{ width: 10, height: 10, marginLeft: 'auto', opacity: 0.6, flexShrink: 0 }} />
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-base-300 bg-base-200 p-2">

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
        <div className="rounded-md border border-base-300 bg-base-300 relative focus-within:border-primary/70 transition-colors">

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
          <div className="flex gap-2 items-end p-2">
            <textarea
              ref={textareaRef}
              placeholder={attachedFiles.length ? 'Add a message or send file as-is…' : 'Ask Ansible AI or drop a file…'}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isGenerating}
              rows={2}
              className="flex-1 bg-transparent border-none outline-none resize-none text-sm text-base-content placeholder-base-content/50 min-h-[52px] max-h-[120px] leading-relaxed"
            />
            {/* Attach File as Context button */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              title="Attach File as Context"
              aria-label="Attach File as Context"
              className="p-1.5 rounded border-none cursor-pointer bg-transparent text-base-content/50 shrink-0 self-end transition-colors hover:text-primary"
            >
              <Paperclip className="w-4 h-4" />
            </button>
            {/* Send button */}
            <button
              type="button"
              onClick={handleSend}
              disabled={(!input.trim() && attachedFiles.length === 0) || isGenerating}
              title="Send (Enter)"
              className={`p-1.5 rounded border-none shrink-0 self-end transition-colors btn btn-sm ${
                (input.trim() || attachedFiles.length > 0) && !isGenerating ? 'btn-primary' : 'btn-ghost opacity-50 cursor-not-allowed'
              }`}
            >
              {isGenerating
                ? <Loader2 className="w-4 h-4 animate-spin" />
                : <Send className="w-4 h-4" />
              }
            </button>
          </div>
        </div>

        <p className="text-[10px] text-base-content/40 text-center mt-1">Enter to send · Shift+Enter for newline · 📎 attach or drag from file tree</p>
      </div>
    </div>
  );
};

export default AnsibleChatPanel;
