import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import { DiffEditor } from '@monaco-editor/react';
import { Send, Loader2, Bot, Sparkles, AlertCircle, FileCode, RotateCcw, ChevronRight, Paperclip, X, TriangleAlert, ChevronDown, Check, Undo2, RefreshCw, Square } from 'lucide-react';
import { ansibleApi, llmApi, netboxApi, ragApi } from '../../src/services/api';
import { Toast } from '../../src/utils/swal';
import {
  buildPlannedInventoryPreselectChips,
  preselectPlannedInventoryInterfacesByDevice,
} from '../../src/utils/interfaceInventoryPreselect';
import { parseNativeVlanNumberFromPrompt, userPromptSuggestsNativeVlanIntent } from '../../src/utils/vlanPromptHints';
import type { LlmRuntimeState } from '../../src/services/api';
import type { AnsibleGeneratedArtifactFile, AnsibleMessage, AnsibleJob, AnsibleProposedEdit } from '../../typesLLM';
import { PlaybookStudioOverlay, type IntentSchemaInspectorPayload } from './PlaybookStudioOverlay';
import {
  DEFAULT_LLM_PROVIDER,
  DEFAULT_OLLAMA_BASE_URL,
  DEFAULT_OLLAMA_MODEL,
} from '../../src/config';

const ANSIBLE_MODEL_STORAGE_KEY = 'ansibleStudio.selectedModel';
const OLLAMA_BASE_URL_STORAGE_KEY = 'llmRuntimeSelector.ollamaBaseUrl';

function prettifyModelLabel(providerName: string, modelName: string): string {
  if (providerName === 'gemini') {
    if (modelName === 'gemini-2.5-flash') return 'Gemini 2.5 Flash';
    if (modelName === 'gemini-2.5-pro') return 'Gemini 2.5 Pro';
    return modelName;
  }
  if (providerName === 'ollama') {
    if (modelName === 'qwen2.5-coder:3b') return 'Qwen 2.5 Coder 3B | Ollama (local)';
    return `${modelName} | Ollama (local)`;
  }
  return modelName;
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

  // Interface-like tokens (Gi0/1, Te1/1, Fa0/1, Ethernet0/1, etc.) — prompts often omit the word "interface"
  const hasIfaceToken = /\b(?:gi|gig|gigabit|te|ten|fa|fast|eth|ethernet|ge)[- ]?\d+\/\d+/i.test(text)
    || /\b(?:gi|gig|te|fa|eth)\d+\/\d+\/\d+/i.test(text);

  const looksLikeIfaceShutdown =
    /\b(?:shut\s*down|shutdown|admin[- ]?down|disable|turn\s+off)\b/i.test(lower)
    && (hasIfaceToken || lower.includes('interface') || lower.includes('interfaces') || lower.includes('port'));

  const looksLikeIfaceConfigure =
    (lower.includes('trunk') || lower.includes('access vlan') || /\b(?:l3|routed)\b/i.test(lower))
    && (hasIfaceToken || lower.includes('interface') || lower.includes('interfaces'));

  // Feature
  const feature =
    lower.includes('vlan')          ? 'vlan'      :
    lower.includes('ospf')          ? 'ospf'      :
    lower.includes('bgp')           ? 'bgp'       :
    looksLikeIfaceShutdown || looksLikeIfaceConfigure || lower.includes('interface') ? 'interface' :
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
  // Words after "on …" that look hostname-shaped but are not (e.g. "on devices")
  // Role / grammar tokens from phrases like "on access switch 1, 2, and 3" — not hostnames.
  // "access" is critical: backend expands token `access` to every access-layer device in inventory.
  const hostnameDenylist = new Set(
    [
      'devices',
      'device',
      'switches',
      'switch',
      'routers',
      'router',
      'hosts',
      'host',
      'network',
      'inventory',
      'access',
      'core',
      'distribution',
      'dist',
      'edge',
      'leaf',
      'spine',
      'and',
      'or',
    ],
  );
  const extraHosts = hostStr
    .split(/[,\s]+/)
    .map(h => h.replace(/;$/,'').trim())
    .filter((h) => isLikelyHostname(h) && !hostnameDenylist.has(h.toLowerCase()));
  const target_hosts = [...new Set([...matches, ...extraHosts])].slice(0, 10);

  // VLAN ID
  const params: Record<string, any> = {};
  const vlanMatch = text.match(/\bvlan\s+(\d+)/i);
  if (vlanMatch) params['vlan_id'] = parseInt(vlanMatch[1], 10);
  const nameMatch =
    text.match(/\bnamed\s+["']?([^\s,;]+)/i) ||
    text.match(/\bcalled\s+["']?([^\s,;]+)/i) ||
    text.match(/name[d]?\s*[=:]\s*["']?([^\s,;]+)/i);
  if (nameMatch) {
    const raw = nameMatch[1].replace(/[.,;'"]+$/g, '');
    if (raw && !/^\d+$/.test(raw)) params['vlan_name'] = raw;
  }

  // Empty means "unspecified" — callers pass undefined to the API instead of ['all'],
  // which previously forced full-inventory resolution and broke follow-up previews.
  return { vendor, feature, target_hosts: target_hosts.length ? target_hosts : [], params };
}

/** Hosts from lightweight parse, or undefined when nothing real was inferred (never force ['all']). */
function safeParsedHostsForPreview(text: string): string[] | undefined {
  const { target_hosts } = parseIntent(text);
  if (!target_hosts.length) return undefined;
  const filtered = target_hosts.filter((h) => h && h !== 'all');
  return filtered.length ? filtered : undefined;
}

function promptRequestsAllInterfaces(text: string): boolean {
  const tl = String(text || '').toLowerCase();
  return (
    /admin[- ]?down all interfaces/i.test(tl) ||
    /admin[- ]?down all the interfaces/i.test(tl) ||
    /shutdown all interfaces/i.test(tl) ||
    /shut down all interfaces/i.test(tl) ||
    /disable all interfaces/i.test(tl) ||
    /turn off all interfaces/i.test(tl) ||
    /bring down all interfaces/i.test(tl) ||
    /all interfaces/i.test(tl)
  ) && !/turn on|enable|bring up|administratively up|admin[- ]?up/i.test(tl);
}

function userPromptSuggestsTrunkPortIntent(text: string): boolean {
  const t = String(text || '').trim().toLowerCase();
  if (!t) return false;
  if (/\b(?:no|not|without)\s+trunk\b/.test(t)) return false;
  if (/\btrunk\b/.test(t)) return true;
  if (/\ballow(?:ed)?\s+vlan/.test(t)) return true;
  if (/\btagged\b/.test(t)) return true;
  if (/\bon\s+(?:the\s+)?trunk\b/.test(t)) return true;
  return false;
}

function userPromptSuggestsAccessPortIntent(text: string): boolean {
  const t = String(text || '').trim().toLowerCase();
  if (!t) return false;
  if (/\baccess\s+port\b/.test(t)) return true;
  if (/\bas\s+access\b/.test(t)) return true;
  if (/\baccess\s+vlan\b/.test(t)) return true;
  return false;
}

function hasAuthoritativeInterfaceSelection(params: Record<string, unknown>): boolean {
  if (!params || typeof params !== 'object') return false;
  const hasList = (v: unknown) => Array.isArray(v) && v.length > 0;
  if (
    hasList(params.interfaces_to_shutdown) ||
    hasList(params.interfaces) ||
    hasList(params.interfaces_config) ||
    hasList(params.apply_to) ||
    hasList(params.interface_apply) ||
    hasList(params.passive_interfaces) ||
    typeof params.next_hop === 'string'
  ) {
    return true;
  }
  const sec = params.secondary_intents;
  if (Array.isArray(sec)) {
    return sec.some((s) => {
      if (!s || typeof s !== 'object') return false;
      const p = (s as { parameters?: Record<string, unknown> }).parameters || {};
      return (
        hasList(p.interfaces_to_shutdown) ||
        hasList(p.interfaces) ||
        hasList(p.interfaces_config) ||
        hasList(p.apply_to) ||
        hasList(p.interface_apply) ||
        hasList(p.passive_interfaces) ||
        typeof p.next_hop === 'string'
      );
    });
  }
  return false;
}

/** Backend preview param wizard field (primary or secondary_intents slice). */
export type PreviewParamField = {
  field: string;
  question: string;
  secondary_intent_index?: number;
  label?: string;
  /** When set with ``options``, render checkboxes instead of free-text (e.g. trunk VLAN IDs from planned context). */
  control?: 'multi_select_integers';
  options?: number[];
};

type PlannedDeviceInterfaces = { hostname: string; interfaces: { name: string }[] };

function mergePlannedDeviceInterfaces(
  prev: PlannedDeviceInterfaces[],
  next: PlannedDeviceInterfaces[],
): PlannedDeviceInterfaces[] {
  const byHost = new Map<string, Set<string>>();
  for (const row of prev) {
    const host = String(row.hostname || '').trim();
    if (!host) continue;
    byHost.set(
      host,
      new Set((row.interfaces || []).map((i) => String(i.name || '').trim()).filter(Boolean)),
    );
  }
  for (const row of next) {
    const host = String(row.hostname || '').trim();
    if (!host) continue;
    const set = byHost.get(host) || new Set<string>();
    for (const iface of row.interfaces || []) {
      const name = String(iface.name || '').trim();
      if (name) set.add(name);
    }
    byHost.set(host, set);
  }
  return Array.from(byHost.entries()).map(([hostname, set]) => ({
    hostname,
    interfaces: Array.from(set).map((name) => ({ name })),
  }));
}

/** Value map key: disambiguate duplicate field names across primary vs secondary intents. */
function previewFieldValueKey(f: PreviewParamField): string {
  if (f.secondary_intent_index !== undefined && f.secondary_intent_index !== null) {
    return `${f.field}::__sec__${f.secondary_intent_index}`;
  }
  return f.field;
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
  const [ansibleModel, setAnsibleModel] = useState<AnsibleModelSelection>(() =>
    DEFAULT_LLM_PROVIDER === 'gemini'
      ? { provider: 'gemini', model: 'gemini-2.5-flash', ollama_base_url: null }
      : { provider: 'ollama', model: DEFAULT_OLLAMA_MODEL, ollama_base_url: DEFAULT_OLLAMA_BASE_URL },
  );
  const [modelSearch, setModelSearch]   = useState('');
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [isRebuildingRag, setIsRebuildingRag] = useState(false);
  const bottomRef      = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);
  const fileInputRef   = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const loadingPhaseTimeoutRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  /** Hydrated file paths for messages that only had playbookPath (e.g. from before multi-file links). */
  const [hydratedFilePaths, setHydratedFilePaths] = useState<Record<string, string[]>>({});
  const [studioOpen, setStudioOpen] = useState(false);
  const [studioIntent, setStudioIntent] = useState('');
  const [disambiguation, setDisambiguation] = useState<{
    messageId: string;
    question: string;
    options: { id: string; label: string; clarification?: string; matches?: string[] }[];
    originalText: string;
  } | null>(null);
  const [disambiguationChoice, setDisambiguationChoice] = useState<string>('access');
  const [disambiguationCustom, setDisambiguationCustom] = useState<string>('');
  const [interfaceDisambiguation, setInterfaceDisambiguation] = useState<{
    messageId: string;
    question: string;
    devices: { hostname: string; interfaces: { name: string }[] }[];
    intentText: string;
    /** Backend intent schema action (e.g. shutdown_interface vs configure_interface) for param key on submit. */
    schemaAction?: string;
    validationNotice?: string;
    invalidEntities?: { hostname: string; value: string; kind: string; message: string; suggestions?: string[] }[];
    paramsNeedContextFix?: boolean;
  } | null>(null);
  /** Per-hostname interface picks (inventory names can repeat across devices). */
  const [selectedInterfacesByDevice, setSelectedInterfacesByDevice] = useState<Record<string, Set<string>>>({});
  const [paramsDisambiguation, setParamsDisambiguation] = useState<{
    messageId: string;
    question: string;
    fields: PreviewParamField[];
    intentText: string;
  } | null>(null);
  const [paramsDisambiguationValues, setParamsDisambiguationValues] = useState<
    Record<string, string | number | string[] | number[]>
  >({});
  const [paramsDisambiguationStep, setParamsDisambiguationStep] = useState(0);
  /** Planned-inventory device picker (hints like SW1, wrong names, or missing targets) — before interface/params. */
  const [deviceDisambiguation, setDeviceDisambiguation] = useState<{
    messageId: string;
    question: string;
    candidateDevices: string[];
    intentText: string;
    kind?: string;
  } | null>(null);
  const [selectedDevices, setSelectedDevices] = useState<Set<string>>(new Set());
  /** After device picker / explicit selection — always pass these on later preview follow-ups (params wizard, interfaces). */
  const [previewLockedTargetHosts, setPreviewLockedTargetHosts] = useState<string[]>([]);
  /** Merged params (vlan_id/name, target_hosts, interfaces, …) so each follow-up preview does not drop prior answers. */
  const [previewAccumulatedParams, setPreviewAccumulatedParams] = useState<Record<string, unknown>>({});
  /** Latest local planned context used for interface option pickers in params wizard. */
  const [plannedPickerDevices, setPlannedPickerDevices] = useState<PlannedDeviceInterfaces[]>([]);
  /** Device options for params wizard (target_devices) from planned context only. */
  const [plannedPickerDeviceNames, setPlannedPickerDeviceNames] = useState<string[]>([]);
  /** Dev-only guard so we don't spam repeated free-text fallback warnings. */
  const freeTextGuardWarnedRef = useRef<Set<string>>(new Set());
  /** Guard auto-submit for all-interfaces prompts per disambiguation message. */
  const autoSubmittedInterfaceDisambiguationRef = useRef<Set<string>>(new Set());
  const previewAccumulatedParamsRef = useRef<Record<string, unknown>>({});
  const [studioInitialPreview, setStudioInitialPreview] = useState<{
    categories: string[];
    references: { doc_name: string; feature: string; vendor: string; path: string }[];
    reasoning: string[];
    policy_approved: boolean;
    target_hosts: string[];
    reasoning_trace?: { steps: { title: string; detail: string }[] };
    parsed_intent: Record<string, unknown>;
    intent_schema_inspector?: IntentSchemaInspectorPayload;
  } | null>(null);

  useEffect(() => {
    previewAccumulatedParamsRef.current = previewAccumulatedParams;
  }, [previewAccumulatedParams]);

  // Auto-fill params wizard interface fields when user explicitly asks for "all interfaces".
  // This avoids forcing another manual interface selection step.
  useEffect(() => {
    if (!paramsDisambiguation || paramsDisambiguation.fields.length === 0) return;
    const f = paramsDisambiguation.fields[paramsDisambiguationStep];
    if (!f) return;
    const fk = String(f.field || '').toLowerCase();
    const isIfaceField = fk === 'interfaces' || fk === 'interfaces_to_shutdown' || fk.includes('interface');
    if (!isIfaceField) return;
    if (!promptRequestsAllInterfaces(paramsDisambiguation.intentText)) return;

    const targetDevicesVal =
      Object.entries(paramsDisambiguationValues).find(([k]) => k.startsWith('target_devices'))?.[1] ?? null;
    const selectedTargets: string[] = Array.isArray(targetDevicesVal)
      ? targetDevicesVal.map((x) => String(x).trim()).filter(Boolean)
      : typeof targetDevicesVal === 'string'
        ? targetDevicesVal.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
        : previewLockedTargetHosts.length > 0
          ? previewLockedTargetHosts
          : Array.from(selectedDevices);

    const scoped = selectedTargets.length > 0
      ? plannedPickerDevices.filter((d) => selectedTargets.includes(d.hostname))
      : plannedPickerDevices;
    const devices = scoped.length > 0 ? scoped : plannedPickerDevices;
    if (devices.length === 0) return;

    const allQualified = devices.flatMap((d) =>
      (d.interfaces || [])
        .map((i) => String(i.name || '').trim())
        .filter((name) => Boolean(name) && !/management|mgmt/i.test(name))
        .map((name) => `${d.hostname}:${name}`),
    );
    if (allQualified.length === 0) return;

    const vk = previewFieldValueKey(f);
    const cur = (paramsDisambiguationValues[vk] as string[] | undefined) ?? [];
    const curKey = [...cur].sort().join('||');
    const nextKey = [...allQualified].sort().join('||');
    if (curKey === nextKey) return;

    setParamsDisambiguationValues((prev) => ({ ...prev, [vk]: allQualified }));
  }, [
    paramsDisambiguation,
    paramsDisambiguationStep,
    paramsDisambiguationValues,
    previewLockedTargetHosts,
    selectedDevices,
    plannedPickerDevices,
  ]);

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
      const prov = r.active_provider || DEFAULT_LLM_PROVIDER;
      const fallbackModel = prov === 'gemini' ? 'gemini-2.5-flash' : DEFAULT_OLLAMA_MODEL;
      setAnsibleModel({
        provider: prov,
        model: r.active_model || fallbackModel,
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

  /**
   * After previewIntent: device picker → switch-role menu → params wizard → interface picker → studio.
   * Params before interfaces so missing VLAN fields aren’t skipped when inventory also returns an interface picker.
   * Returns true if a branch handled the response (skip generic “Thinking” snippet).
   */
  const applyPreviewFollowUps = useCallback(
    (res: any, intentText: string): boolean => {
      const secMerge = res?.parsed_intent?.secondary_intents;
      if (Array.isArray(secMerge) && secMerge.length) {
        setPreviewAccumulatedParams((prev) => ({ ...prev, secondary_intents: secMerge }));
      }
      const devicesDis = res?.devices_disambiguation as
        | {
            question?: string;
            candidate_devices?: string[];
            ask_user_selection?: boolean;
            disambiguation_kind?: string;
          }
        | undefined;
      const pickerDevicesFromIface =
        ((res?.interfaces_disambiguation?.devices as PlannedDeviceInterfaces[] | undefined) ||
        (res?.interfaces_for_selection?.devices as PlannedDeviceInterfaces[] | undefined) ||
        []);
      if (pickerDevicesFromIface.length > 0) {
        setPlannedPickerDevices((prev) => mergePlannedDeviceInterfaces(prev, pickerDevicesFromIface));
      }
      const pickerDeviceNames = Array.from(
        new Set([
          ...pickerDevicesFromIface.map((d) => String(d.hostname || '').trim()).filter(Boolean),
          ...((devicesDis?.candidate_devices || []).map((d) => String(d).trim()).filter(Boolean)),
        ]),
      );
      if (pickerDeviceNames.length > 0) {
        setPlannedPickerDeviceNames(pickerDeviceNames);
      }
      if (
        devicesDis?.ask_user_selection &&
        Array.isArray(devicesDis.candidate_devices) &&
        devicesDis.candidate_devices.length > 0
      ) {
        const menuMsgId = crypto.randomUUID();
        setDeviceDisambiguation({
          messageId: menuMsgId,
          question:
            devicesDis.question ||
            'Select one or more target devices from planned inventory, then continue.',
          candidateDevices: devicesDis.candidate_devices,
          intentText,
          kind: devicesDis.disambiguation_kind,
        });
        setSelectedDevices(new Set());
        setMessages((prev) => [
          ...prev,
          {
            id: menuMsgId,
            role: 'assistant',
            content:
              devicesDis.question ||
              'Select target device(s) from planned inventory (multi-select), then continue.',
            timestamp: Date.now(),
            deviceDisambiguation: {
              question: devicesDis.question,
              candidate_devices: devicesDis.candidate_devices,
            },
          } as AnsibleMessage,
        ]);
        return true;
      }

      const dis = res?.parsed_intent?.disambiguation || res?.disambiguation;
      if (dis && Array.isArray(dis.options) && dis.options.length) {
        const menuMsgId = crypto.randomUUID();
        setDisambiguation({
          messageId: menuMsgId,
          question: dis.question || 'Which devices did you mean?',
          options: dis.options,
          originalText: intentText,
        });
        setDisambiguationChoice(dis.options.find((o: { id: string }) => o.id === 'access') ? 'access' : dis.options[0].id);
        setDisambiguationCustom('');
        setMessages((prev) => [
          ...prev,
          {
            id: menuMsgId,
            role: 'assistant',
            content: dis.question || 'Which devices did you mean?',
            timestamp: Date.now(),
            disambiguation: { question: dis.question || 'Which devices did you mean?', options: dis.options },
          },
        ]);
        return true;
      }

      // Params wizard BEFORE interface picker: when both are returned (e.g. missing vlan_name + invalid Gi…),
      // handling interfaces first would hide the VLAN name step forever (early return).
      const paramsDis = res?.params_disambiguation;
      if (paramsDis?.fields?.length) {
        const fieldsList = (paramsDis.fields || []) as Array<{ field?: string }>;
        const askedFields = fieldsList.map((f) => String(f.field || "")).filter(Boolean);
        const ifaceForSel = res?.interfaces_for_selection;
        const wantsOnlyTargetDevices = askedFields.length === 1 && askedFields[0] === 'target_devices';

        if (wantsOnlyTargetDevices) {
          const plannedDevices: string[] =
            (ifaceForSel?.devices ?? []).map((d: any) => String(d.hostname || '').trim()).filter((s: string) => Boolean(s));

          if (plannedDevices.length > 0) {
            const menuMsgId = crypto.randomUUID();
            const candidateDevices = Array.from(new Set(plannedDevices));
            setDeviceDisambiguation({
              messageId: menuMsgId,
              question: paramsDis.question || 'Select target device(s) from planned inventory, then continue.',
              candidateDevices,
              intentText,
            });
            setSelectedDevices(new Set());
            setMessages((prev) => [
              ...prev,
              {
                id: menuMsgId,
                role: 'assistant',
                content: paramsDis.question || 'Select target device(s) from planned inventory, then continue.',
                timestamp: Date.now(),
                deviceDisambiguation: {
                  question: paramsDis.question,
                  candidate_devices: candidateDevices,
                },
              } as AnsibleMessage,
            ]);
            return true;
          }
        }
        const isOnlyInterfaceShutdownField =
          askedFields.length > 0 &&
          askedFields.every((f) => f === "interfaces_to_shutdown" || f === "interfaces");

        // If backend already provides a planned inventory interface picker (multi-select),
        // never show the free-text input. The interface picker must take precedence.
        const ifaceDis = res?.interfaces_disambiguation;
        if (
          isOnlyInterfaceShutdownField &&
          (ifaceDis?.devices?.length || ifaceForSel?.devices?.length)
        ) {
          // Do not handle here; fall through to the interface picker branch below.
        } else {
        const menuMsgId = crypto.randomUUID();
        const fieldsList = (paramsDis.fields || []) as PreviewParamField[];
        const initVals: Record<string, string | number | string[] | number[]> = {};
        const pParams = (res?.parsed_intent?.parameters || {}) as Record<string, unknown>;
        const rawVid = pParams.vlan_id ?? (pParams.vlan as { id?: number } | undefined)?.id;
        const vid =
          rawVid !== undefined && rawVid !== null && rawVid !== ''
            ? parseInt(String(rawVid), 10)
            : NaN;
        if (!Number.isNaN(vid)) {
          for (const f of fieldsList) {
            if (
              f.field === 'trunk_vlans' &&
              f.control === 'multi_select_integers' &&
              Array.isArray(f.options) &&
              f.options.includes(vid)
            ) {
              initVals[previewFieldValueKey(f)] = [vid];
            }
          }
        }
        setParamsDisambiguation({
          messageId: menuMsgId,
          question: paramsDis.question || 'Please provide the following.',
          fields: paramsDis.fields,
          intentText,
        });
        setParamsDisambiguationValues(initVals);
        setParamsDisambiguationStep(0);
        setMessages((prev) => [
          ...prev,
          {
            id: menuMsgId,
            role: 'assistant',
            content: 'Clarifying for some missing information',
            timestamp: Date.now(),
            paramsDisambiguation: { question: paramsDis.question, fields: paramsDis.fields },
          } as AnsibleMessage,
        ]);
        return true;
        }
      }

      const ifaceDis = res?.interfaces_disambiguation;
      const ifaceForSel = res?.interfaces_for_selection;
      const ifaceDevices = ifaceDis?.devices?.length ? ifaceDis.devices : ifaceForSel?.devices?.length ? ifaceForSel.devices : null;
      if (ifaceDevices) {
        const alreadyAnswered = hasAuthoritativeInterfaceSelection(previewAccumulatedParamsRef.current);
        const hasInvalid = Array.isArray((ifaceDis as any)?.invalid_entities) && (ifaceDis as any).invalid_entities.length > 0;
        if (alreadyAnswered && !hasInvalid) {
          return false;
        }
        const question =
          ifaceDis?.question ||
          ifaceForSel?.question ||
          'Please select the interfaces you want to shutdown.';
        const menuMsgId = crypto.randomUUID();
        const vNotice = ifaceDis?.validation_notice as string | undefined;
        setInterfaceDisambiguation({
          messageId: menuMsgId,
          question,
          devices: ifaceDevices,
          intentText,
          schemaAction: (res as any)?.schema_action,
          validationNotice: vNotice,
          invalidEntities: (ifaceDis as any)?.invalid_entities,
          paramsNeedContextFix: Boolean((ifaceDis as any)?.params_need_context_fix),
        });
        {
          const parsed = (res?.parsed_intent ?? null) as Record<string, unknown> | null;
          const chips = buildPlannedInventoryPreselectChips(parsed, {
            extraInvalidEntities: (ifaceDis as any)?.invalid_entities as unknown[] | undefined,
          });
          setSelectedInterfacesByDevice(
            preselectPlannedInventoryInterfacesByDevice(chips, { devices: ifaceDevices }),
          );
        }

        // If the prompt asks for "admin-down all interfaces", preselect *all* planned-context
        // interfaces for each selected host (management IP interfaces are already excluded in the backend).
        // This avoids the need for any free-text input.
        const tl = (intentText || "").toLowerCase();
        const wantsAllIfaces = promptRequestsAllInterfaces(tl);
        if (wantsAllIfaces) {
          const allByDevice: Record<string, Set<string>> = {};
          for (const d of ifaceDevices) {
            const hn = String(d.hostname || "").trim();
            if (!hn) continue;
            const names = Array.isArray(d.interfaces) ? d.interfaces.map((i: any) => String(i.name || "").trim()).filter(Boolean) : [];
            allByDevice[hn] = new Set(names);
          }
          setSelectedInterfacesByDevice(allByDevice);
        }

        setMessages((prev) => [
          ...prev,
          {
            id: menuMsgId,
            role: 'assistant',
            content: vNotice
              ? `${vNotice}\n\n${question || 'Please select the interfaces to continue.'}`
              : question || 'Please select the interfaces to continue.',
            timestamp: Date.now(),
            interfaceDisambiguation: { question, devices: ifaceDevices },
          } as AnsibleMessage,
        ]);
        return true;
      }

      return false;
    },
    [],
  );

  const submitDeviceDisambiguation = useCallback(async () => {
    if (!deviceDisambiguation) return;
    const hosts = Array.from(selectedDevices);
    if (hosts.length === 0) {
      Toast.warning('Select at least one device', 'Choose target device(s) from planned inventory to continue.');
      return;
    }
    const msgId = deviceDisambiguation.messageId;
    const intentText = deviceDisambiguation.intentText;
    setDeviceDisambiguation(null);
    setMessages((prev) => prev.filter((m) => m.id !== msgId));
    setGenerating(true);
    const progressId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      {
        id: progressId,
        role: 'assistant',
        content: 'Applying device selection and refreshing the preview…',
        timestamp: Date.now(),
      },
    ]);
    const ac = new AbortController();
    abortControllerRef.current = ac;
    try {
      const { vendor, feature, params: parsedParams } = parseIntent(intentText);
      const merged = { ...parsedParams, ...previewAccumulatedParams, target_hosts: hosts };

      // Fallback hydration: when preview response omits interfaces_for_selection/interfaces_disambiguation,
      // build per-host interface options from local planned topology so params wizard never renders blank.
      try {
        const topo = await netboxApi.getLocalPlannedTopology();
        const nodes = (topo?.nodes || {}) as Record<string, any>;
        const picked = hosts
          .map((hostname) => {
            const node = nodes[hostname];
            if (!node) return null;
            const mgmtIp = String(node.mgmt_ip || '').trim();
            const rawIfaceMaps = Array.isArray(node.interfaceList) ? node.interfaceList : [];
            const firstMap = (rawIfaceMaps.find((x: unknown) => x && typeof x === 'object') || {}) as Record<string, unknown>;
            const ifNames = Object.entries(firstMap)
              .filter(([rawName, rawIp]) => {
                const name = String(rawName || '').trim();
                if (!name) return false;
                const ip = String(rawIp || '').trim();
                if (/management|mgmt/i.test(name)) return false;
                if (mgmtIp && ip && ip === mgmtIp) return false;
                return true;
              })
              .map(([rawName]) => String(rawName).trim())
              .filter(Boolean);
            return {
              hostname,
              interfaces: ifNames.map((name) => ({ name })),
            };
          })
          .filter((d): d is PlannedDeviceInterfaces => Boolean(d && d.interfaces.length > 0));
        if (picked.length > 0) {
          setPlannedPickerDevices((prev) => mergePlannedDeviceInterfaces(prev, picked));
        }
        if (hosts.length > 0) {
          setPlannedPickerDeviceNames(Array.from(new Set(hosts)));
        }
      } catch (_) {
        // Ignore fallback-hydration errors; preview flow still continues.
      }

      // For shutdown-all phrasing, never ask for interface text.
      // Instead, instruct backend to expand from planned nettopo_interfaces (mgmt IP excluded)
      // for the selected target host(s).
      const tl = (intentText || "").toLowerCase();
      const wantsShutdownAllInterfaces = promptRequestsAllInterfaces(tl);
      if (wantsShutdownAllInterfaces) {
        (merged as any).interfaces_shutdown_use_nettopo = true;
        (merged as any).interfaces_to_shutdown = ["ALL"];
        // Safety: explicitly set admin enabled false (shutdown).
        (merged as any).interfaces_admin_enabled = false;
      }

      setPreviewLockedTargetHosts(hosts);
      setPreviewAccumulatedParams(merged);
      const res = await ansibleApi.previewIntent(
        {
          intent_description: intentText,
          target_hosts: hosts,
          // Duplicate under params so preview merge + _preview_parse_and_rag always see explicit picks
          params: merged,
          vendor: vendor || undefined,
          feature: feature || undefined,
          provider: ansibleModel.provider,
          model: ansibleModel.model,
          ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
          skip_rag: true,
        },
        { signal: ac.signal },
      );
      abortControllerRef.current = null;
      setMessages((prev) => prev.filter((m) => m.id !== progressId));
      if (applyPreviewFollowUps(res, intentText)) {
        setGenerating(false);
        return;
      }
      const valErr = (res as { parsed_intent?: { validation_errors?: string[] } })?.parsed_intent?.validation_errors;
      if (Array.isArray(valErr) && valErr.length > 0) {
        addMsg({
          role: 'assistant',
          content: `**Validation issues**\n\n${valErr.join('\n')}`,
        });
      } else {
        setStudioInitialPreview(res as any);
        setStudioIntent(intentText);
        setStudioOpen(true);
      }
    } catch (err: unknown) {
      abortControllerRef.current = null;
      setMessages((prev) => prev.filter((m) => m.id !== progressId));
      const isAbort = err instanceof DOMException && err.name === 'AbortError';
      if (isAbort) {
        addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
      }
      setStudioInitialPreview(null);
      setStudioIntent(intentText);
      setStudioOpen(true);
    }
    setGenerating(false);
  }, [
    deviceDisambiguation,
    selectedDevices,
    previewAccumulatedParams,
    parseIntent,
    ansibleModel,
    applyPreviewFollowUps,
    addMsg,
  ]);

  const submitInterfaceDisambiguation = useCallback(async () => {
    if (!interfaceDisambiguation) return;
    const devices = interfaceDisambiguation.devices;
    const qualified: { hostname: string; ifName: string }[] = [];
    for (const d of devices) {
      const hn = d.hostname;
      const set = selectedInterfacesByDevice[hn] || new Set<string>();
      for (const ifName of set) {
        qualified.push({ hostname: hn, ifName });
      }
    }
    if (qualified.length === 0) {
      if (promptRequestsAllInterfaces(interfaceDisambiguation.intentText)) {
        for (const d of devices) {
          const hn = String(d.hostname || '').trim();
          if (!hn) continue;
          const names = (d.interfaces || [])
            .map((i) => String(i.name || '').trim())
            .filter((n) => Boolean(n) && !/management|mgmt/i.test(n));
          for (const ifName of names) qualified.push({ hostname: hn, ifName });
        }
      }
    }
    if (qualified.length === 0) {
      Toast.warning('Select at least one interface', 'Choose one or more interfaces per device to continue.');
      return;
    }
    const msgId = interfaceDisambiguation.messageId;
    const intentText = interfaceDisambiguation.intentText;
    const schemaAction = (interfaceDisambiguation.schemaAction || 'shutdown_interface').toLowerCase();
    setInterfaceDisambiguation(null);
    setMessages(prev => prev.filter(m => m.id !== msgId));
    setGenerating(true);
    const progressId = crypto.randomUUID();
    setMessages(prev => [...prev, {
      id: progressId,
      role: 'assistant',
      content: 'Applying your interface selection and refreshing the preview…',
      timestamp: Date.now(),
    }]);
    const ac = new AbortController();
    abortControllerRef.current = ac;
    try {
      const { vendor, feature, params: parsedParams } = parseIntent(intentText);
      const selectedHosts = Array.from(selectedDevices);
      const selectedHostsFromInterfaces = Array.from(new Set(qualified.map((q) => q.hostname)));
      const effectiveTargetHosts =
        selectedHosts.length > 0
          ? selectedHosts
          : selectedHostsFromInterfaces.length > 0
            ? selectedHostsFromInterfaces
            : previewLockedTargetHosts.length > 0
              ? previewLockedTargetHosts
              : safeParsedHostsForPreview(intentText);
      const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const inferDirection = (ifaceName: string): 'in' | 'out' | null => {
        const esc = escapeRegExp(ifaceName);
        const re = new RegExp(`${esc}\\s*(in|out)\\b`, 'i');
        const m = intentText.match(re);
        const dir = m?.[1]?.toLowerCase();
        return dir === 'in' || dir === 'out' ? dir : null;
      };
      const qTok = (x: { hostname: string; ifName: string }) => `${x.hostname}:${x.ifName}`;
      const vidRaw =
        (previewAccumulatedParams.vlan_id as number | string | undefined) ??
        parsedParams.vlan_id;
      const vlanNum =
        vidRaw !== undefined && vidRaw !== ''
          ? typeof vidRaw === 'number'
            ? vidRaw
            : parseInt(String(vidRaw), 10)
          : NaN;
      const wantNativeVlan = userPromptSuggestsNativeVlanIntent(intentText);
      const nativeParsed = parseNativeVlanNumberFromPrompt(intentText);
      const wantsTrunk = userPromptSuggestsTrunkPortIntent(intentText);
      const wantsAccess = userPromptSuggestsAccessPortIntent(intentText);
      const buildL2InterfaceObjects = (): Array<Record<string, unknown>> => {
        if (wantNativeVlan) {
          const nv =
            nativeParsed !== null
              ? nativeParsed
              : !Number.isNaN(vlanNum)
                ? vlanNum
                : null;
          if (nv !== null) {
            const allowed = String(!Number.isNaN(vlanNum) ? vlanNum : nv);
            return qualified.map(({ hostname, ifName }) => ({
              name: qTok({ hostname, ifName }),
              mode: 'trunk' as const,
              trunk_vlans: allowed,
              native_vlan: nv,
            }));
          }
        }
        if (wantsTrunk && !wantsAccess) {
          const allowed =
            !Number.isNaN(vlanNum) && Number.isFinite(vlanNum)
              ? [vlanNum]
              : [];
          return qualified.map(({ hostname, ifName }) => ({
            name: qTok({ hostname, ifName }),
            mode: 'trunk' as const,
            ...(allowed.length > 0 ? { trunk_vlans: allowed } : {}),
          }));
        }
        return qualified.map(({ hostname, ifName }) => ({
          name: qTok({ hostname, ifName }),
          mode: 'access' as const,
          ...(!Number.isNaN(vlanNum) ? { access_vlan: vlanNum } : {}),
        }));
      };
      let ifaceParams: Record<string, any>;
      const secondaryIntents = (previewAccumulatedParams.secondary_intents as
        | Array<{ action?: string; feature?: string; parameters?: Record<string, unknown> }>
        | undefined) || [];
      const ifaceSecondaryIdx = secondaryIntents.findIndex(
        (s) =>
          (s.action || '').toLowerCase().replace(/-/g, '_') === 'configure_interface' ||
          (s.feature || '').toLowerCase() === 'interface',
      );
      const secondaryIsIface = ifaceSecondaryIdx >= 0;
      if (secondaryIsIface) {
        const ifaceList = { interfaces: buildL2InterfaceObjects() };
        const mergedSec = [...secondaryIntents];
        const pPrev = (mergedSec[ifaceSecondaryIdx]?.parameters as Record<string, unknown>) || {};
        mergedSec[ifaceSecondaryIdx] = {
          ...mergedSec[ifaceSecondaryIdx],
          action: mergedSec[ifaceSecondaryIdx]?.action || 'configure_interface',
          feature: mergedSec[ifaceSecondaryIdx]?.feature || 'interface',
          parameters: { ...pPrev, ...ifaceList },
        };
        ifaceParams = { secondary_intents: mergedSec };
      } else if (schemaAction === 'configure_interface' || schemaAction === 'create_vlan') {
        ifaceParams = { interfaces: buildL2InterfaceObjects() };
      } else if (schemaAction === 'configure_acl') {
        ifaceParams = {
          apply_to: qualified.map(({ hostname, ifName }) => {
            const q = qTok({ hostname, ifName });
            const dir = inferDirection(ifName);
            return dir ? `${q} ${dir}` : q;
          }),
        };
      } else if (schemaAction === 'configure_qos') {
        ifaceParams = {
          interface_apply: qualified.map(({ hostname, ifName }) => {
            const q = qTok({ hostname, ifName });
            const dir = inferDirection(ifName);
            return dir ? `${q} ${dir}` : q;
          }),
        };
      } else if (schemaAction === 'configure_ospf') {
        ifaceParams = { passive_interfaces: qualified.map(qTok) };
      } else if (schemaAction === 'configure_static_route') {
        ifaceParams = { next_hop: qTok(qualified[0]) };
      } else {
        ifaceParams = { interfaces_to_shutdown: qualified.map(qTok) };
      }
      const mergedIface: Record<string, unknown> = {
        ...parsedParams,
        ...previewAccumulatedParams,
        ...ifaceParams,
      };
      if (effectiveTargetHosts?.length) {
        mergedIface.target_hosts = effectiveTargetHosts;
      }
      setPreviewAccumulatedParams(mergedIface);
      if (effectiveTargetHosts?.length) {
        setPreviewLockedTargetHosts(effectiveTargetHosts);
      }
      const res = await ansibleApi.previewIntent({
        intent_description: intentText,
        target_hosts: effectiveTargetHosts,
        vendor: vendor || undefined,
        feature: feature || undefined,
        provider: ansibleModel.provider,
        model: ansibleModel.model,
        ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
        params: mergedIface,
        skip_rag: true,
      }, { signal: ac.signal });
      abortControllerRef.current = null;
      setMessages(prev => prev.filter(m => m.id !== progressId));
      if (applyPreviewFollowUps(res, intentText)) {
        setGenerating(false);
        return;
      }
      const valErr = (res as { parsed_intent?: { validation_errors?: string[] } })?.parsed_intent?.validation_errors;
      if (Array.isArray(valErr) && valErr.length > 0) {
        addMsg({
          role: 'assistant',
          content: `**Validation issues**\n\n${valErr.join('\n')}`,
        });
      } else {
        setStudioInitialPreview(res as any);
        setStudioIntent(intentText);
        setStudioOpen(true);
      }
    } catch (err: unknown) {
      abortControllerRef.current = null;
      setMessages(prev => prev.filter(m => m.id !== progressId));
      const isAbort = err instanceof DOMException && err.name === 'AbortError';
      if (isAbort) {
        addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
      }
      setStudioInitialPreview(null);
      setStudioIntent(intentText);
      setStudioOpen(true);
    }
    setSelectedInterfacesByDevice({});
    setGenerating(false);
  }, [
    interfaceDisambiguation,
    selectedInterfacesByDevice,
    selectedDevices,
    previewLockedTargetHosts,
    previewAccumulatedParams,
    ansibleModel,
    applyPreviewFollowUps,
    addMsg,
  ]);

  useEffect(() => {
    if (!interfaceDisambiguation) return;
    if (!promptRequestsAllInterfaces(interfaceDisambiguation.intentText)) return;
    const msgId = interfaceDisambiguation.messageId;
    if (autoSubmittedInterfaceDisambiguationRef.current.has(msgId)) return;
    autoSubmittedInterfaceDisambiguationRef.current.add(msgId);
    void submitInterfaceDisambiguation();
  }, [interfaceDisambiguation, submitInterfaceDisambiguation]);

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

    // ── /template slash-command: generate from Config Engine template ────
    const templateMatch = baseText.match(/^\/template\s+(\S+)\s+(\S+)(?:\s+(.+))?$/i);

    if (templateMatch) {
      const loadingId = crypto.randomUUID();
      setMessages(prev => [...prev, {
        id: loadingId, role: 'assistant', content: '', isLoading: true, timestamp: Date.now(),
      }]);
      setGenerating(true);
      const ac = new AbortController();
      abortControllerRef.current = ac;
      try {
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
        }, { signal: ac.signal });
        abortControllerRef.current = null;

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
      } catch (err: any) {
        abortControllerRef.current = null;
        const isAbort = err instanceof DOMException && err.name === 'AbortError';
        if (isAbort) {
          setMessages(prev => prev.filter(m => m.id !== loadingId));
          addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
        } else {
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
        }
      } finally {
        setGenerating(false);
      }
    } else {
      // ── Standard flow: run preview first to detect ambiguity ──
      setPreviewLockedTargetHosts([]);
      setPreviewAccumulatedParams({});
      const loadingId = crypto.randomUUID();
      const loadingPhases = ['Parsing intent…', 'Resolving targets…', 'Almost done…'];
      setMessages(prev => [...prev, {
        id: loadingId,
        role: 'assistant',
        content: loadingPhases[0],
        isLoading: true,
        timestamp: Date.now(),
      }]);
      setGenerating(true);
      const ac = new AbortController();
      abortControllerRef.current = ac;
      const t1 = setTimeout(() => {
        setMessages(prev => prev.map((m) =>
          m.id === loadingId ? { ...m, content: loadingPhases[1] } : m
        ));
      }, 450);
      const t2 = setTimeout(() => {
        setMessages(prev => prev.map((m) =>
          m.id === loadingId ? { ...m, content: loadingPhases[2] } : m
        ));
      }, 1800);
      loadingPhaseTimeoutRef.current = [t1, t2];
      const { vendor, feature, params: lightParams } = parseIntent(text);
      const initialHosts = safeParsedHostsForPreview(text);
      const hasLight = lightParams && Object.keys(lightParams).length > 0;
      let previewResultForStudio: Record<string, unknown> | null = null;
      try {
        const res = await ansibleApi.previewIntent({
          intent_description: text,
          target_hosts: initialHosts,
          vendor: vendor || undefined,
          feature: feature || undefined,
          ...(hasLight ? { params: { ...lightParams } } : {}),
          provider: ansibleModel.provider,
          model: ansibleModel.model,
          ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
          skip_rag: true,
        }, { signal: ac.signal });
        loadingPhaseTimeoutRef.current.forEach(clearTimeout);
        loadingPhaseTimeoutRef.current = [];
        abortControllerRef.current = null;
        loadingPhaseTimeoutRef.current.forEach(clearTimeout);
        loadingPhaseTimeoutRef.current = [];
        setMessages(prev => prev.filter(m => m.id !== loadingId));
        setGenerating(false);
        if (applyPreviewFollowUps(res, text)) {
          return;
        }
        // No disambiguation: show reasoning / open studio below.
        const trace = (res as any)?.reasoning_trace as { steps?: { title: string; detail: string }[] } | undefined;
        if (trace?.steps && trace.steps.length > 0) {
          const lines: string[] = ['**Reasoning steps**'];
          trace.steps.forEach((step, idx) => {
            const title = step.title || `Step ${idx + 1}`;
            const detail = (step.detail || '').trim();
            if (detail) {
              lines.push(`${idx + 1}. ${title.replace(/^\d+\.\s*/, '')}`);
              lines.push('');
              lines.push(detail);
              lines.push('');
            } else {
              lines.push(`${idx + 1}. ${title}`);
            }
          });
          const secondaryPreviews = (res as { secondary_intent_previews?: Array<{ intent_index: number; action: string; feature: string; missing_fields?: string[]; validation_errors?: string[] }> })?.secondary_intent_previews;
          if (Array.isArray(secondaryPreviews) && secondaryPreviews.length > 0) {
            lines.push('');
            lines.push('**Also detected (secondary intents)**');
            secondaryPreviews.forEach((s) => {
              const missing = (s.missing_fields ?? []).length ? ` — missing: ${(s.missing_fields ?? []).join(', ')}` : '';
              const errs = (s.validation_errors ?? []).length ? ` — ${(s.validation_errors ?? []).join('; ')}` : '';
              lines.push(`- ${s.feature || s.action}${missing}${errs}`);
            });
            lines.push('');
            lines.push('Primary intent will be generated first. You can run a follow-up for the others.');
          }
          addMsg({
            role: 'assistant',
            content: lines.join('\n').trim(),
          });
        } else {
          const reasoning = (res as any)?.reasoning as string[] | undefined;
          if (Array.isArray(reasoning) && reasoning.length) {
            let snippet = reasoning.slice(0, 3).join('\n');
            const secondaryPreviews = (res as { secondary_intent_previews?: Array<{ feature: string; action: string; missing_fields?: string[] }> })?.secondary_intent_previews;
            if (Array.isArray(secondaryPreviews) && secondaryPreviews.length > 0) {
              snippet += '\n\n**Also detected:** ' + secondaryPreviews.map((s) => s.feature || s.action).join(', ') + '. Primary intent will be generated first.';
            }
            addMsg({
              role: 'assistant',
              content: `Thinking:\n${snippet}`,
            });
          }
        }
        const valErr = (res as any)?.parsed_intent?.validation_errors as string[] | undefined;
        if (Array.isArray(valErr) && valErr.length > 0) {
          addMsg({
            role: 'assistant',
            content: `**Validation issues**\n\n${valErr.join('\n')}`,
          });
          setGenerating(false);
          return;
        }
        previewResultForStudio = res as Record<string, unknown>;
      } catch (err: unknown) {
        loadingPhaseTimeoutRef.current.forEach(clearTimeout);
        loadingPhaseTimeoutRef.current = [];
        abortControllerRef.current = null;
        setMessages(prev => prev.filter(m => m.id !== loadingId));
        const isAbort = err instanceof DOMException && err.name === 'AbortError';
        if (isAbort) {
          addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
        }
        setGenerating(false);
        return;
      }
      setGenerating(false);
      if (previewResultForStudio) {
        setStudioInitialPreview(previewResultForStudio as any);
        setStudioIntent(text);
        setStudioOpen(true);
      }
    }
  }, [input, isGenerating, addMsg, onPlaybookReady, attachedFiles, ansibleModel, parseIntent, applyPreviewFollowUps]);

  const submitDisambiguation = useCallback(async () => {
    if (!disambiguation) return;
    const picked = disambiguation.options.find((o) => o.id === disambiguationChoice);
    const clarification =
      disambiguationChoice === 'custom'
        ? disambiguationCustom.trim()
        : (picked?.clarification ?? picked?.label ?? '').trim();
    if (!clarification) return;
    const clarifiedText = `${disambiguation.originalText}\n\nClarification: ${clarification}`;
    const msgId = disambiguation.messageId;
    setDisambiguation(null);
    setMessages(prev => prev.filter(m => m.id !== msgId));
    setGenerating(true);
    const loadingId = crypto.randomUUID();
    const ac = new AbortController();
    abortControllerRef.current = ac;
    setMessages(prev => [...prev, { id: loadingId, role: 'assistant', content: '', isLoading: true, timestamp: Date.now() }]);
    try {
      const { vendor, feature, target_hosts } = parseIntent(clarifiedText);
      const res = await ansibleApi.previewIntent({
        intent_description: clarifiedText,
        target_hosts: target_hosts?.length ? target_hosts : undefined,
        vendor: vendor || undefined,
        feature: feature || undefined,
        provider: ansibleModel.provider,
        model: ansibleModel.model,
        ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
        skip_rag: true,
      }, { signal: ac.signal });
      abortControllerRef.current = null;
      setMessages(prev => prev.filter(m => m.id !== loadingId));
      if (applyPreviewFollowUps(res, clarifiedText)) {
        setGenerating(false);
        return;
      }
      const valErr = (res as { parsed_intent?: { validation_errors?: string[] } })?.parsed_intent?.validation_errors;
      if (Array.isArray(valErr) && valErr.length > 0) {
        addMsg({
          role: 'assistant',
          content: `**Validation issues**\n\n${valErr.join('\n')}`,
        });
      } else {
        setStudioInitialPreview(res as any);
        setStudioIntent(clarifiedText);
        setStudioOpen(true);
      }
    } catch (err: unknown) {
      abortControllerRef.current = null;
      setMessages(prev => prev.filter(m => m.id !== loadingId));
      const isAbort = err instanceof DOMException && err.name === 'AbortError';
      if (isAbort) {
        addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
      }
      setStudioInitialPreview(null);
      setStudioIntent(clarifiedText);
      setStudioOpen(true);
    }
    setGenerating(false);
  }, [disambiguation, disambiguationChoice, disambiguationCustom, ansibleModel, parseIntent, addMsg, applyPreviewFollowUps]);

  const handleStop = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    loadingPhaseTimeoutRef.current.forEach(clearTimeout);
    loadingPhaseTimeoutRef.current = [];
    setMessages(prev => prev.filter(m => !(m as AnsibleMessage & { isLoading?: boolean }).isLoading));
    addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
    setGenerating(false);
  }, [addMsg]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const clearMessages = () => {
    setPreviewLockedTargetHosts([]);
    setPreviewAccumulatedParams({});
    setMessages([{
      id: 'welcome', role: 'assistant', timestamp: Date.now(),
      content: '**Ansible AI** is ready. Describe your configuration intent.',
    }]);
  };

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
      {/* Playbook Studio overlay: intent categories → RAG templates → confirm → dual editor */}
      <PlaybookStudioOverlay
        open={studioOpen}
        onClose={() => { setStudioOpen(false); setStudioInitialPreview(null); }}
        intentDescription={studioIntent}
        initialPreviewResult={studioInitialPreview ?? undefined}
        authoritativeMergedParams={previewAccumulatedParams}
        onPlaybookReady={(payload) => {
          onPlaybookReady(payload);
          addMsg({
            role: 'assistant',
            content:
              `✅ **Playbook generated** → \`${payload.name}\`\n\n` +
              `- **Job ID:** ${payload.jobId}\n` +
              (payload.files?.length ? `- **Files:** ${payload.files.length} generated\n\n` : '\n') +
              `Recommend running **Check** to see the dry-run diff before deploying.`,
            jobId: payload.jobId,
            playbookPath: payload.name,
            generatedPaths: payload.files?.map((f) => f.path) ?? [payload.name],
          });
          setStudioOpen(false);
        }}
        ansibleModel={ansibleModel}
        parseIntent={parseIntent}
      />

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
          <span className="text-[11px] font-semibold tracking-wider whitespace-nowrap uppercase text-base-content/90">Ansible AI</span>
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
              <span style={{ maxWidth: 60, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{currentModelLabel}</span>
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
                      {msg.content || 'Generating the playbook…'}
                    </div>
                  ) : msg.isError ? (
                    <div className="flex items-start gap-2 text-error text-sm">
                      <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      {msg.content}
                    </div>
                  ) : msg.disambiguation ? (
                    <div className="space-y-2">
                      <div className="text-sm text-base-content/90">
                        {msg.disambiguation.question}
                      </div>
                      <div className="flex flex-col gap-2">
                        {msg.disambiguation.options.map((opt) => (
                          <button
                            key={opt.id}
                            type="button"
                            className={`btn btn-sm justify-start ${disambiguationChoice === opt.id ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => setDisambiguationChoice(opt.id)}
                          >
                            <span className="text-left flex-1">
                              {opt.label}
                              {opt.matches?.length ? (
                                <span className="block text-[11px] opacity-70 font-mono">
                                  {opt.matches.join(', ')}
                                </span>
                              ) : null}
                            </span>
                          </button>
                        ))}
                      </div>
                      {disambiguationChoice === 'custom' && (
                        <input
                          className="input input-sm input-bordered w-full"
                          value={disambiguationCustom}
                          onChange={(e) => setDisambiguationCustom(e.target.value)}
                          placeholder="Enter your custom answer…"
                        />
                      )}
                      <div className="flex items-center gap-2">
                        <button type="button" className="btn btn-sm btn-ghost" onClick={() => {
                          if (disambiguation?.messageId === msg.id) setDisambiguation(null);
                          setMessages(prev => prev.filter(m => m.id !== msg.id));
                        }}>
                          Cancel
                        </button>
                        <button type="button" className="btn btn-sm btn-primary" onClick={submitDisambiguation}>
                          Continue
                        </button>
                      </div>
                    </div>
                  ) : (msg as any).deviceDisambiguation && deviceDisambiguation?.messageId === msg.id ? (
                    <div className="space-y-3">
                      <div className="text-sm text-base-content/90 whitespace-pre-line">
                        {deviceDisambiguation.question}
                      </div>
                      {deviceDisambiguation.kind ? (
                        <div className="text-xs text-base-content/60">
                          {deviceDisambiguation.kind === 'ambiguous_hint'
                            ? 'Several inventory hosts match your hint — select the correct target device(s).'
                            : deviceDisambiguation.kind === 'not_in_inventory'
                              ? 'That name is not in planned inventory — pick from the list below.'
                              : deviceDisambiguation.kind === 'missing_targets'
                                ? 'Select which device(s) this change should apply to.'
                                : null}
                        </div>
                      ) : null}
                      <div className="flex flex-wrap gap-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-52 overflow-y-auto">
                        {deviceDisambiguation.candidateDevices.map((host) => (
                          <label key={host} className="flex items-center gap-1.5 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={selectedDevices.has(host)}
                              onChange={() => {
                                setSelectedDevices((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(host)) next.delete(host);
                                  else next.add(host);
                                  return next;
                                });
                              }}
                              className="checkbox checkbox-sm checkbox-primary"
                            />
                            <span className="text-xs font-mono">{host}</span>
                          </label>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <button type="button" className="btn btn-sm btn-ghost" onClick={() => {
                          setDeviceDisambiguation(null);
                          setMessages(prev => prev.filter(m => m.id !== msg.id));
                        }}>
                          Cancel
                        </button>
                        <button type="button" className="btn btn-sm btn-primary" onClick={submitDeviceDisambiguation}>
                          Continue
                        </button>
                      </div>
                    </div>
                  ) : (msg as any).interfaceDisambiguation && interfaceDisambiguation?.messageId === msg.id ? (
                    <div className="space-y-3">
                      {interfaceDisambiguation.validationNotice ? (
                        <div className="rounded-md border border-warning/60 bg-warning/10 px-3 py-2 text-sm text-warning-content whitespace-pre-line">
                          {interfaceDisambiguation.validationNotice}
                        </div>
                      ) : null}
                      <div className="text-sm text-base-content/90">
                        {interfaceDisambiguation.question}
                      </div>
                      {interfaceDisambiguation.devices.map((dev) => (
                        <div key={dev.hostname} className="rounded border border-base-300 bg-base-300/50 p-2">
                          <div className="text-xs font-semibold text-base-content/80 mb-1.5">{dev.hostname}</div>
                          <div className="flex flex-wrap gap-2">
                            {dev.interfaces.map((iface) => {
                              const name = iface.name;
                              const host = dev.hostname;
                              const checked = Boolean(selectedInterfacesByDevice[host]?.has(name));
                              return (
                                <label key={`${host}:${name}`} className="flex items-center gap-1.5 cursor-pointer">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => {
                                      setSelectedInterfacesByDevice((prev) => {
                                        const next: Record<string, Set<string>> = { ...prev };
                                        const cur = new Set(next[host] || []);
                                        if (cur.has(name)) cur.delete(name);
                                        else cur.add(name);
                                        next[host] = cur;
                                        return next;
                                      });
                                    }}
                                    className="checkbox checkbox-sm checkbox-primary"
                                  />
                                  <span className="text-xs font-mono">{name}</span>
                                </label>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                      <div className="flex items-center gap-2">
                        <button type="button" className="btn btn-sm btn-ghost" onClick={() => {
                          setInterfaceDisambiguation(null);
                          setSelectedInterfacesByDevice({});
                          setMessages(prev => prev.filter(m => m.id !== msg.id));
                        }}>
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          onClick={submitInterfaceDisambiguation}
                        >
                          Continue
                        </button>
                      </div>
                    </div>
                  ) : (msg as any).paramsDisambiguation && paramsDisambiguation?.messageId === msg.id ? (
                    <div className="space-y-3">
                      <div className="text-sm text-base-content/90">
                        Clarifying for some missing information
                      </div>
                      {paramsDisambiguation.fields.length > 0 && (
                        <>
                          <div className="text-xs text-base-content/60">
                            Question {paramsDisambiguationStep + 1} of {paramsDisambiguation.fields.length}
                          </div>
                          <div className="flex flex-col gap-2">
                            {(() => {
                              const f = paramsDisambiguation.fields[paramsDisambiguationStep];
                              if (!f) return null;
                              const vk = previewFieldValueKey(f);
                              return (
                                <div key={vk}>
                                  {f.label ? (
                                    <div className="text-xs text-amber-600/90 dark:text-amber-400/90 mb-1">{f.label}</div>
                                  ) : null}
                                  <label className="text-sm text-base-content/90 block mb-0.5">{f.question}</label>
                                  {(() => {
                                    const normalizedField = String(f.field || '').toLowerCase();
                                    const isTargetDeviceField =
                                      normalizedField === 'target_devices' ||
                                      normalizedField === 'target_hosts' ||
                                      normalizedField.includes('device');
                                    const isInterfaceField =
                                      normalizedField === 'interfaces' ||
                                      normalizedField === 'interfaces_to_shutdown' ||
                                      normalizedField.includes('interface');
                                    if (!isTargetDeviceField && !isInterfaceField) return null;

                                    const selectedForTargetDevices = Object.entries(paramsDisambiguationValues)
                                      .find(([key]) => key.startsWith('target_devices'))?.[1];
                                    const selectedTargets: string[] = Array.isArray(selectedForTargetDevices)
                                      ? selectedForTargetDevices.map((x) => String(x).trim()).filter(Boolean)
                                      : typeof selectedForTargetDevices === 'string'
                                        ? selectedForTargetDevices.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
                                        : previewLockedTargetHosts.length > 0
                                          ? previewLockedTargetHosts
                                          : Array.from(selectedDevices);

                                    const filteredDevices =
                                      isInterfaceField && selectedTargets.length > 0
                                        ? plannedPickerDevices.filter((d) => selectedTargets.includes(d.hostname))
                                        : plannedPickerDevices;
                                    const visibleInterfaceDevices =
                                      filteredDevices.length > 0 ? filteredDevices : plannedPickerDevices;
                                    const candidateDevices =
                                      plannedPickerDeviceNames.length > 0
                                        ? plannedPickerDeviceNames
                                        : filteredDevices.map((d) => d.hostname);

                                    if (isTargetDeviceField) {
                                      const cur = (paramsDisambiguationValues[vk] as string[] | undefined) ?? [];
                                      return (
                                        <div className="flex flex-wrap gap-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-52 overflow-y-auto">
                                          {candidateDevices.map((host) => {
                                            const checked = cur.includes(host);
                                            return (
                                              <label key={`${vk}-${host}`} className="flex items-center gap-1.5 cursor-pointer">
                                                <input
                                                  type="checkbox"
                                                  checked={checked}
                                                  onChange={() => {
                                                    setParamsDisambiguationValues((prev) => {
                                                      const prevArr = (prev[vk] as string[] | undefined) ?? [];
                                                      const next = prevArr.includes(host)
                                                        ? prevArr.filter((x) => x !== host)
                                                        : [...prevArr, host];
                                                      return { ...prev, [vk]: next };
                                                    });
                                                  }}
                                                  className="checkbox checkbox-sm checkbox-primary"
                                                />
                                                <span className="text-xs font-mono">{host}</span>
                                              </label>
                                            );
                                          })}
                                        </div>
                                      );
                                    }

                                    const cur = (paramsDisambiguationValues[vk] as string[] | undefined) ?? [];
                                    const autoAllInterfaces = promptRequestsAllInterfaces(paramsDisambiguation.intentText);
                                    if (autoAllInterfaces) {
                                      // For "all interfaces" prompts, values are auto-filled in state.
                                      // Do not render an extra picker/info box here.
                                      return null;
                                    }
                                    if (visibleInterfaceDevices.length === 0) {
                                      return (
                                        <div className="rounded border border-warning/60 bg-warning/10 px-3 py-2 text-xs text-warning-content">
                                          No planned interfaces found for the selected target device(s). Refresh planned topology/local DB and try again.
                                        </div>
                                      );
                                    }
                                    return (
                                      <div className="space-y-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-56 overflow-y-auto">
                                        {visibleInterfaceDevices.map((dev) => (
                                          <div key={`${vk}-${dev.hostname}`} className="rounded border border-base-300 bg-base-100/50 p-2">
                                            <div className="text-[11px] font-semibold text-base-content/80 mb-1">{dev.hostname}</div>
                                            <div className="flex flex-wrap gap-2">
                                              {(dev.interfaces || []).map((iface) => {
                                                const q = `${dev.hostname}:${String(iface.name || '').trim()}`;
                                                if (!q.endsWith(':')) {
                                                  const checked = cur.includes(q);
                                                  return (
                                                    <label key={`${vk}-${q}`} className="flex items-center gap-1.5 cursor-pointer">
                                                      <input
                                                        type="checkbox"
                                                        checked={checked}
                                                        onChange={() => {
                                                          setParamsDisambiguationValues((prev) => {
                                                            const prevArr = (prev[vk] as string[] | undefined) ?? [];
                                                            const next = prevArr.includes(q)
                                                              ? prevArr.filter((x) => x !== q)
                                                              : [...prevArr, q];
                                                            return { ...prev, [vk]: next };
                                                          });
                                                        }}
                                                        className="checkbox checkbox-sm checkbox-primary"
                                                      />
                                                      <span className="text-xs font-mono">{iface.name}</span>
                                                    </label>
                                                  );
                                                }
                                                return null;
                                              })}
                                            </div>
                                          </div>
                                        ))}
                                      </div>
                                    );
                                  })() || (
                                    f.control === 'multi_select_integers' &&
                                    Array.isArray(f.options) &&
                                    f.options.length > 0 ? (
                                      <div className="flex flex-wrap gap-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-52 overflow-y-auto">
                                        {f.options.map((n) => {
                                          const cur = (paramsDisambiguationValues[vk] as number[] | undefined) ?? [];
                                          const checked = cur.includes(n);
                                          return (
                                            <label
                                              key={`${vk}-${n}`}
                                              className="flex items-center gap-1.5 cursor-pointer"
                                            >
                                              <input
                                                type="checkbox"
                                                checked={checked}
                                                onChange={() => {
                                                  setParamsDisambiguationValues((prev) => {
                                                    const prevArr = (prev[vk] as number[] | undefined) ?? [];
                                                    const next = prevArr.includes(n)
                                                      ? prevArr.filter((x) => x !== n)
                                                      : [...prevArr, n].sort((a, b) => a - b);
                                                    return { ...prev, [vk]: next };
                                                  });
                                                }}
                                                className="checkbox checkbox-sm checkbox-primary"
                                              />
                                              <span className="text-xs font-mono">VLAN {n}</span>
                                            </label>
                                          );
                                        })}
                                      </div>
                                    ) : (() => {
                                      const normalizedField = String(f.field || '').toLowerCase();
                                      const isInterfaceOrDeviceRelated =
                                        normalizedField.includes('interface') ||
                                        normalizedField.includes('device') ||
                                        normalizedField.includes('target_hosts') ||
                                        normalizedField.includes('target_devices');
                                      if (isInterfaceOrDeviceRelated) {
                                        if (
                                          process.env.NODE_ENV !== 'production' &&
                                          !freeTextGuardWarnedRef.current.has(vk)
                                        ) {
                                          freeTextGuardWarnedRef.current.add(vk);
                                          // Strict regression guard for interface/device clarifications.
                                          console.warn(
                                            `[AnsibleChatPanel] Free-text fallback blocked for "${f.field}" (${vk}).` +
                                            ' Expected planned-context option picker.',
                                          );
                                        }
                                        return (
                                          <div className="rounded border border-error/50 bg-error/10 px-3 py-2 text-xs text-error-content">
                                            Interface/device clarification cannot use free text. No planned options were provided.
                                          </div>
                                        );
                                      }
                                      return (
                                        <input
                                          type={f.field === 'vlan_id' || f.field === 'process_id' ? 'number' : 'text'}
                                          className="input input-sm input-bordered w-full font-mono"
                                          placeholder={f.field === 'vlan_id' ? '1-4094' : ''}
                                          value={String((paramsDisambiguationValues[vk] ?? '') as string | number)}
                                          onChange={(e) => {
                                            const v = e.target.value;
                                            setParamsDisambiguationValues((prev) => ({
                                              ...prev,
                                              [vk]: f.field === 'vlan_id' || f.field === 'process_id' ? (v ? parseInt(v, 10) : '') : v,
                                            }));
                                          }}
                                        />
                                      );
                                    })()
                                  )}
                                </div>
                              );
                            })()}
                          </div>
                          <div className="flex items-center gap-2">
                            <button type="button" className="btn btn-sm btn-ghost" onClick={() => {
                              setParamsDisambiguation(null);
                              setParamsDisambiguationValues({});
                              setParamsDisambiguationStep(0);
                              setMessages(prev => prev.filter(m => m.id !== msg.id));
                            }}>
                              Cancel
                            </button>
                            {paramsDisambiguationStep > 0 ? (
                              <button
                                type="button"
                                className="btn btn-sm btn-ghost"
                                onClick={() => setParamsDisambiguationStep((s) => s - 1)}
                              >
                                Back
                              </button>
                            ) : null}
                            {paramsDisambiguationStep < paramsDisambiguation.fields.length - 1 ? (
                              <button
                                type="button"
                                className="btn btn-sm btn-primary"
                                onClick={() => setParamsDisambiguationStep((s) => s + 1)}
                              >
                                Next
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="btn btn-sm btn-primary"
                                disabled={
                                  paramsDisambiguation.fields.some(
                                    (f) => {
                                      const v = paramsDisambiguationValues[previewFieldValueKey(f)];
                                      if (v === undefined || v === '' || v === null) return true;
                                      if (Array.isArray(v) && v.length === 0) return true;
                                      if (typeof v === 'number' && Number.isNaN(v)) return true;
                                      return false;
                                    }
                                  )
                                }
                                onClick={async () => {
                                  if (!paramsDisambiguation) return;
                                  const intentText = paramsDisambiguation.intentText;
                                  const primaryParams: Record<string, unknown> = {};
                                  const secondaryPatches: Record<number, Record<string, unknown>> = {};
                                  paramsDisambiguation.fields.forEach((f) => {
                                    const vk = previewFieldValueKey(f);
                                    const v = paramsDisambiguationValues[vk];
                                    if (v === undefined || v === '' || v === null) return;
                                    if (Array.isArray(v) && v.length === 0) return;
                                    const sidx = f.secondary_intent_index;
                                    if (sidx !== undefined && sidx !== null) {
                                      if (!secondaryPatches[sidx]) secondaryPatches[sidx] = {};
                                      if (f.field === 'target_devices' && Array.isArray(v)) {
                                        secondaryPatches[sidx].target_hosts = v.map((s) => String(s).trim()).filter(Boolean);
                                      } else if (f.field === 'target_devices' && typeof v === 'string') {
                                        secondaryPatches[sidx].target_hosts = v.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
                                      } else {
                                        secondaryPatches[sidx][f.field] = v;
                                      }
                                    } else if (f.field === 'target_devices' && Array.isArray(v)) {
                                      primaryParams.target_hosts = v.map((s) => String(s).trim()).filter(Boolean);
                                    } else if (f.field === 'target_devices' && typeof v === 'string') {
                                      primaryParams.target_hosts = v.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
                                    } else {
                                      primaryParams[f.field] = v;
                                    }
                                  });
                                  const prevSecondary = (previewAccumulatedParams.secondary_intents as Array<Record<string, unknown>>) || [];
                                  const mergedSecondary = [...prevSecondary];
                                  for (const [idxStr, patch] of Object.entries(secondaryPatches)) {
                                    const idx = parseInt(idxStr, 10);
                                    const prev = mergedSecondary[idx] || {};
                                    const p0 = (prev.parameters as Record<string, unknown>) || {};
                                    mergedSecondary[idx] = {
                                      ...prev,
                                      action: prev.action || 'configure_interface',
                                      feature: prev.feature || 'interface',
                                      parameters: { ...p0, ...patch },
                                    };
                                  }
                                  const params: Record<string, unknown> = { ...primaryParams };
                                  if (Object.keys(secondaryPatches).length) {
                                    params.secondary_intents = mergedSecondary;
                                  }
                                  const msgId = paramsDisambiguation.messageId;
                                  setParamsDisambiguation(null);
                                  setParamsDisambiguationValues({});
                                  setParamsDisambiguationStep(0);
                                  setMessages(prev => prev.filter(m => m.id !== msgId));
                                  const generatingId = crypto.randomUUID();
                                  setMessages(prev => [...prev, {
                                    id: generatingId,
                                    role: 'assistant',
                                    content: 'Generating the playbook…',
                                    isLoading: true,
                                    timestamp: Date.now(),
                                  }]);
                                  setGenerating(true);
                                  const ac = new AbortController();
                                  abortControllerRef.current = ac;
                                  try {
                                    const { vendor, feature, params: parsedParams } = parseIntent(intentText);
                                    const merged: Record<string, unknown> = {
                                      ...parsedParams,
                                      ...previewAccumulatedParams,
                                      ...params,
                                    };
                                    const th =
                                      previewLockedTargetHosts.length > 0
                                        ? previewLockedTargetHosts
                                        : safeParsedHostsForPreview(intentText);
                                    if (th?.length) {
                                      merged.target_hosts = th;
                                    }
                                    setPreviewAccumulatedParams(merged);
                                    const res = await ansibleApi.previewIntent({
                                      intent_description: intentText,
                                      target_hosts: th,
                                      vendor: vendor || undefined,
                                      feature: feature || undefined,
                                      provider: ansibleModel.provider,
                                      model: ansibleModel.model,
                                      ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
                                      params: merged,
                                      skip_rag: true,
                                    }, { signal: ac.signal });
                                    abortControllerRef.current = null;
                                    setMessages(prev => prev.filter(m => m.id !== generatingId));
                                    if (applyPreviewFollowUps(res, intentText)) {
                                      setGenerating(false);
                                      return;
                                    }
                                    const valErr = (res as { parsed_intent?: { validation_errors?: string[] } })?.parsed_intent?.validation_errors;
                                    if (Array.isArray(valErr) && valErr.length > 0) {
                                      addMsg({
                                        role: 'assistant',
                                        content: `**Validation issues**\n\n${valErr.join('\n')}`,
                                      });
                                    } else {
                                      setStudioInitialPreview(res as any);
                                      setStudioIntent(intentText);
                                      setStudioOpen(true);
                                    }
                                  } catch (err: unknown) {
                                    abortControllerRef.current = null;
                                    setMessages(prev => prev.filter(m => m.id !== generatingId));
                                    const isAbort = err instanceof DOMException && err.name === 'AbortError';
                                    if (isAbort) {
                                      addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
                                    }
                                    setStudioInitialPreview(null);
                                    setStudioIntent(intentText);
                                    setStudioOpen(true);
                                  }
                                  setGenerating(false);
                                }}
                              >
                                Submit
                              </button>
                            )}
                          </div>
                        </>
                      )}
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
            {/* Send or Stop button */}
            {/* {isGenerating ? ( */}
            {isGenerating ? (
              <button
                type="button"
                onClick={handleStop}
                title="Stop generation"
                className="btn btn-ghost rounded-full btn-sm btn-square text-red-600 hover:bg-red-600/20"
                aria-label="Stop"
              >
                <Square className="w-4 h-4 fill-current" />
              </button>
            ) : (
              <button
                type="button"
                onClick={handleSend}
                disabled={!input.trim() && attachedFiles.length === 0}
                title="Send (Enter)"
                className={`p-1.5 rounded-full border-none shrink-0 self-end transition-colors btn btn-sm btn-square ${
                  (input.trim() || attachedFiles.length > 0) ? 'btn-primary' : 'btn-ghost opacity-50 cursor-not-allowed'
                }`}
              >
                <Send className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <p className="text-[10px] text-base-content/40 text-center mt-1">Enter to send · Shift+Enter for newline · 📎 attach or drag from file tree</p>
      </div>
    </div>
  );
};

export default AnsibleChatPanel;
