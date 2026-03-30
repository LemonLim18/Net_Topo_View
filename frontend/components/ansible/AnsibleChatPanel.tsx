import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import { DiffEditor } from '@monaco-editor/react';
import { Send, Loader2, Bot, Sparkles, AlertCircle, FileCode, ChevronRight, Paperclip, X, TriangleAlert, ChevronDown, Check, Undo2, Square, History, Plus, Trash2, RefreshCw } from 'lucide-react';
import { ansibleApi, llmApi, netboxApi, ragApi } from '../../src/services/api';
import { Toast } from '../../src/utils/swal';
import {
  buildPlannedInventoryPreselectChips,
  preselectPlannedInventoryInterfacesByDevice,
} from '../../src/utils/interfaceInventoryPreselect';
import { parseNativeVlanNumberFromPrompt, userPromptSuggestsNativeVlanIntent } from '../../src/utils/vlanPromptHints';
import type { LlmRuntimeState } from '../../src/services/api';
import type { AnsibleGeneratedArtifactFile, AnsibleMessage, AnsibleJob, AnsibleProposedEdit, ChatSession } from '../../typesLLM';
import { PlaybookStudioOverlay, type IntentSchemaInspectorPayload } from './PlaybookStudioOverlay';
import {
  DEFAULT_LLM_PROVIDER,
  DEFAULT_OLLAMA_BASE_URL,
  DEFAULT_OLLAMA_MODEL,
} from '../../src/config';
import { ANSIBLE_REMEDIATION_INTENT_KEY } from '../../utils/remediationPrompt';

const ANSIBLE_MODEL_STORAGE_KEY = 'ansibleStudio.selectedModel';
const OLLAMA_BASE_URL_STORAGE_KEY = 'llmRuntimeSelector.ollamaBaseUrl';
const ANSIBLE_CHAT_SESSIONS_KEY = 'ansibleChat.sessions.v1';
const ANSIBLE_CHAT_CURRENT_SESSION_KEY = 'ansibleChat.currentSessionId.v1';

type AnsibleChatSessionRecord = ChatSession & { messages: AnsibleMessage[] };

function createWelcomeMessage(): AnsibleMessage {
  return {
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '**Ansible AI** ready.\n\nDescribe what you want to configure in plain English. For example:\n\n> *Configure VLAN 100 named DATA on all access switches*\n\nOr use **`/template`** to generate from a Config Engine template:\n\n> `/template cisco_ios vlan {"vlan_id":100,"vlan_name":"DATA"}`\n\nI will generate an Ansible playbook, which you can then review, dry-run, and deploy.',
    timestamp: Date.now(),
  };
}

function fallbackSessionTitle(messages: AnsibleMessage[]): string {
  const firstUser = messages.find((msg) => msg.role === 'user' && String(msg.content || '').trim());
  if (!firstUser) return 'New Chat';
  const singleLine = String(firstUser.content).replace(/\s+/g, ' ').trim();
  return singleLine.length > 38 ? `${singleLine.slice(0, 38)}...` : singleLine;
}

function normalizeStoredMessages(raw: unknown): AnsibleMessage[] {
  if (!Array.isArray(raw)) return [createWelcomeMessage()];
  const parsed = raw
    .filter((msg): msg is Record<string, unknown> => Boolean(msg) && typeof msg === 'object')
    .map((msg) => ({
      ...msg,
      id: String(msg.id || crypto.randomUUID()),
      role: (msg.role === 'user' || msg.role === 'assistant' || msg.role === 'system') ? msg.role : 'assistant',
      content: String(msg.content || ''),
      timestamp: Number(msg.timestamp || Date.now()),
    })) as AnsibleMessage[];
  return parsed.length > 0 ? parsed : [createWelcomeMessage()];
}

function loadStoredAnsibleSessions(): AnsibleChatSessionRecord[] {
  try {
    const raw = localStorage.getItem(ANSIBLE_CHAT_SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === 'object')
      .map((entry) => ({
        id: String(entry.id || crypto.randomUUID()),
        title: String(entry.title || 'New Chat'),
        updatedAt: Number(entry.updatedAt || Date.now()),
        messages: normalizeStoredMessages(entry.messages),
      }))
      .sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    return [];
  }
}

function loadStoredCurrentAnsibleSessionId(sessions: AnsibleChatSessionRecord[]): string | null {
  const raw = localStorage.getItem(ANSIBLE_CHAT_CURRENT_SESSION_KEY);
  if (raw && sessions.some((session) => session.id === raw)) return raw;
  return sessions[0]?.id ?? null;
}

function normalizeSessionRecord(session: {
  id: string;
  title: string;
  updatedAt: number | string;
  createdAt?: string;
  messages: unknown;
}): AnsibleChatSessionRecord {
  const updatedAt =
    typeof session.updatedAt === 'number'
      ? session.updatedAt
      : Number(session.updatedAt || Date.now());
  return {
    id: String(session.id || crypto.randomUUID()),
    title: String(session.title || 'New Chat'),
    updatedAt: Number.isFinite(updatedAt) ? updatedAt : Date.now(),
    messages: normalizeStoredMessages(session.messages),
  };
}

function createSessionRecord(): AnsibleChatSessionRecord {
  const messages = [createWelcomeMessage()];
  return {
    id: crypto.randomUUID(),
    title: 'New Chat',
    updatedAt: Date.now(),
    messages,
  };
}

/** Renders ``**bold**`` segments as real bold (backend wizard copy uses markdown-style emphasis). */
function renderChatEmphasis(text: string): React.ReactNode {
  if (!text) return null;
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      return (
        <strong key={i} className="font-semibold" style={{ color: '#e2c08d' }}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

function describeError(err: unknown, fallback = 'Unexpected error'): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err.trim()) return err;
  try {
    const asAny = err as { message?: unknown } | null;
    if (typeof asAny?.message === 'string' && asAny.message.trim()) return asAny.message;
    if (asAny?.message !== undefined) return JSON.stringify(asAny.message);
  } catch {
    // ignore stringify issues
  }
  return fallback;
}

function resolveCompanionPreviewPath(input: {
  entrypointPath?: string | null;
  files?: AnsibleGeneratedArtifactFile[];
  name?: string;
}): string {
  const candidate =
    String(input.entrypointPath || '').trim() ||
    String(
      input.files?.find((f) => /\.ya?ml$/i.test(String(f.path || '')))?.path ||
      input.files?.[0]?.path ||
      input.name ||
      'playbook.yml',
    ).trim();
  const normalized = candidate.replace(/\\/g, '/');
  const slash = normalized.lastIndexOf('/');
  const dir = slash >= 0 ? normalized.slice(0, slash) : '';
  return dir ? `${dir}/preview_modal_dialog.md` : 'preview_modal_dialog.md';
}

function buildPreviewDialogContent(args: {
  intentDescription: string;
  jobId: number;
  preview: {
    categories?: string[];
    target_hosts?: string[];
    reasoning?: string[];
    references?: { doc_name: string; feature: string; vendor: string; path: string }[];
    policy_approved?: boolean;
    parsed_intent?: Record<string, unknown>;
  } | null;
}): string {
  const categories = args.preview?.categories || [];
  const targets = args.preview?.target_hosts || [];
  const reasoning = args.preview?.reasoning || [];
  const refs = args.preview?.references || [];
  const parsedIntent = args.preview?.parsed_intent || {};
  const generatedAt = new Date().toISOString();

  const refsBlock = refs.length
    ? refs.map((r) => `- ${r.doc_name} (${r.vendor}/${r.feature}) -> ${r.path}`).join('\n')
    : '- None';

  const reasoningBlock = reasoning.length
    ? reasoning.map((line) => `- ${line}`).join('\n')
    : '- None';

  return [
    '# Preview Modal Dialog',
    '',
    `Generated at: ${generatedAt}`,
    `Job ID: ${args.jobId}`,
    '',
    '## Intent Summary',
    args.intentDescription || '(not provided)',
    '',
    '## Parsed Highlights',
    `- Categories: ${categories.length ? categories.join(', ') : 'N/A'}`,
    `- Target hosts: ${targets.length ? targets.join(', ') : 'N/A'}`,
    `- Policy approved: ${args.preview?.policy_approved === true ? 'yes' : 'no'}`,
    '',
    '## Reasoning Trace',
    reasoningBlock,
    '',
    '## Selected References',
    refsBlock,
    '',
    '## Parsed Intent JSON',
    '```json',
    JSON.stringify(parsedIntent, null, 2),
    '```',
    '',
  ].join('\n');
}

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
    (lower.includes('static route') || lower.includes('default route')) ? 'static_route' :
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
  /** When true, empty values do not block Continue. */
  optional?: boolean;
  /** Typed UI controls from backend disambiguation enrichment. */
  control?: 'multi_select_integers' | 'single_select_integers' | 'single_select_strings';
  options?: number[] | string[];
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

/** Per-host interface rows from local planned topology (mirrors device-disambiguation submit). */
async function plannedInterfacesFromLocalTopology(hosts: string[]): Promise<PlannedDeviceInterfaces[]> {
  if (hosts.length === 0) return [];
  try {
    const topo = await netboxApi.getLocalPlannedTopology();
    const nodes = (topo?.nodes || {}) as Record<string, any>;
    return hosts
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
  } catch {
    return [];
  }
}

function isParamsWizardTargetHostField(f: PreviewParamField): boolean {
  const n = String(f.field || '').toLowerCase();
  return n === 'target_devices' || n === 'target_hosts';
}

function isParamsWizardInterfaceInventoryField(f: PreviewParamField): boolean {
  const raw = String(f.field || '');
  const n = raw.toLowerCase();
  if (raw.startsWith('_chat_')) return false;
  return (
    n === 'apply_to' ||
    n === 'interfaces' ||
    n === 'interfaces_to_shutdown' ||
    n === 'interfaces_config' ||
    n === 'interface_apply' ||
    n === 'passive_interfaces' ||
    (n.includes('interface') && !isParamsWizardTargetHostField(f))
  );
}

function collectTargetHostsFromParamsWizard(
  fields: PreviewParamField[],
  values: Record<string, string | number | string[] | number[] | undefined>,
): string[] {
  const out: string[] = [];
  for (const field of fields) {
    if (!isParamsWizardTargetHostField(field)) continue;
    const vk = previewFieldValueKey(field);
    const v = values[vk];
    if (Array.isArray(v)) {
      out.push(...v.map((x) => String(x ?? '').trim()).filter(Boolean));
    } else if (typeof v === 'string') {
      out.push(...v.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean));
    }
  }
  return Array.from(new Set(out));
}

function mergeInterfacePickersFromPreviewResponse(res: any): PlannedDeviceInterfaces[] {
  const raw =
    (res?.interfaces_disambiguation?.devices as PlannedDeviceInterfaces[] | undefined) ||
    (res?.interfaces_for_selection?.devices as PlannedDeviceInterfaces[] | undefined) ||
    [];
  return Array.isArray(raw) ? raw : [];
}

function isAclAtomicWizardFieldName(fieldName: string): boolean {
  return String(fieldName || '').toLowerCase().startsWith('_chat_acl_');
}

/** Value map key: disambiguate duplicate field names across primary vs secondary intents. */
function previewFieldValueKey(f: PreviewParamField): string {
  if (f.secondary_intent_index !== undefined && f.secondary_intent_index !== null) {
    return `${f.field}::__sec__${f.secondary_intent_index}`;
  }
  return f.field;
}

function isValidIPv4Address(raw: string): boolean {
  const s = String(raw || '').trim();
  if (!s) return false;
  const parts = s.split('.');
  if (parts.length !== 4) return false;
  for (const p of parts) {
    if (!/^\d{1,3}$/.test(p)) return false;
    const n = Number(p);
    if (!Number.isInteger(n) || n < 0 || n > 255) return false;
  }
  return true;
}

function sanitizeIPv4Input(raw: string): string {
  const src = String(raw || '');
  const hasDot = src.includes('.');
  const digitsAndDots = src.replace(/[^\d.]/g, '');

  // If user types pure digits, insert dots every 3 digits (max 4 octets).
  if (!hasDot) {
    const digits = digitsAndDots.replace(/\./g, '').slice(0, 12);
    const chunks: string[] = [];
    for (let i = 0; i < digits.length; i += 3) {
      chunks.push(digits.slice(i, i + 3));
    }
    const octets = chunks.slice(0, 4).map((c) => {
      if (!c) return '';
      const n = parseInt(c, 10);
      if (Number.isNaN(n)) return '';
      return String(Math.max(0, Math.min(255, n)));
    });
    return octets.join('.');
  }

  const parts = digitsAndDots.split('.').slice(0, 4);
  const normalized = parts.map((p) => {
    const s = p.slice(0, 3);
    if (!s) return '';
    const n = parseInt(s, 10);
    if (Number.isNaN(n)) return '';
    return String(Math.max(0, Math.min(255, n)));
  });
  return normalized.join('.');
}

function isValidIPv4Cidr(raw: string): boolean {
  const s = String(raw || '').trim();
  // Allow bare IPv4 (treat as /32 for static-route destination UX).
  if (isValidIPv4Address(s)) return true;
  const m = s.match(/^((?:\d{1,3}\.){3}\d{1,3})\/(\d{1,2})$/);
  if (!m) return false;
  const ip = m[1];
  const pfx = Number(m[2]);
  if (!isValidIPv4Address(ip)) return false;
  return Number.isInteger(pfx) && pfx >= 0 && pfx <= 32;
}

/** True when this wizard field still blocks Continue / Next (single-field check). */
function paramsWizardValueIncomplete(
  f: PreviewParamField,
  allValues: Record<string, string | number | string[] | number[] | undefined>,
): boolean {
  const vk = previewFieldValueKey(f);
  const v = allValues[vk];
  if (f.optional && (v === undefined || v === '' || v === null)) return false;
  if (v === undefined || v === '' || v === null) return true;
  if (Array.isArray(v) && v.length === 0) return true;
  if (typeof v === 'number' && Number.isNaN(v)) return true;
  const fl = String(f.field || '').toLowerCase();
  if (fl === 'next_hop') {
    const modeKey = `${vk}__mode`;
    const modeRaw = String((allValues[modeKey] ?? '') as string).trim().toLowerCase();
    const mode = modeRaw === 'interface' ? 'interface' : 'ip';
    if (mode === 'interface') {
      return !String(v || '').trim();
    }
    const ip = String(v || '').trim().split('/')[0];
    if (!ip) return true;
    if (ip.includes('.') && !isValidIPv4Address(ip)) return true;
    return false;
  }
  if (fl === 'ip_address' && !isValidIPv4Address(String(v))) return true;
  if (fl === 'network_address' && !isValidIPv4Address(String(v))) return true;
  if (fl === 'destination' && !isValidIPv4Cidr(String(v))) return true;
  if (fl === 'access_vlan' || fl === 'native_vlan') {
    if (typeof v === 'number' && (v < 1 || v > 4094)) return true;
  }
  if (fl === 'area') {
    const n = typeof v === 'number' ? v : parseInt(String(v).trim(), 10);
    if (Number.isNaN(n) || n < 0) return true;
  }
  if (fl === 'subnet_mask') {
    const n =
      typeof v === 'number' ? v : parseInt(String(v).replace('/', '').trim(), 10);
    if (Number.isNaN(n) || n < 0 || n > 32) return true;
  }
  if (fl === 'prefix_length') {
    const n =
      typeof v === 'number' ? v : parseInt(String(v).replace('/', '').trim(), 10);
    if (Number.isNaN(n) || n < 0 || n > 32) return true;
  }
  if (fl === '_chat_acl_action') {
    const s = String(v || '').trim().toLowerCase();
    return !(s === 'permit' || s === 'deny');
  }
  if (fl === '_chat_acl_seq') {
    const raw = String(v ?? '').trim();
    if (!/^\d+$/.test(raw)) return true;
    const n = parseInt(raw, 10);
    return Number.isNaN(n) || n < 1;
  }
  if (fl === '_chat_acl_protocol') {
    const s = String(v || '').trim().toLowerCase();
    return !(['tcp', 'udp', 'icmp', 'ip'] as string[]).includes(s);
  }
  if (fl === '_chat_acl_source' || fl === '_chat_acl_destination') {
    const s = String(v || '').trim();
    if (!s) return true;
    if (s.toLowerCase() === 'any') return false;
    return !isValidIPv4Address(s);
  }
  if (fl === '_chat_acl_source_wildcard' || fl === '_chat_acl_dest_wildcard') {
    const addrKey = fl.includes('source') ? '_chat_acl_source' : '_chat_acl_destination';
    const addrVal = String(allValues[addrKey] ?? '').trim().toLowerCase();
    const w = String(v || '').trim();
    if (addrVal === 'any') return false; // wildcard not required
    if (!addrVal) return true;
    if (!w) return true;
    return !isValidIPv4Address(w);
  }
  if (fl === '_chat_acl_source_port' || fl === '_chat_acl_destination_port') {
    const proto = String(allValues['_chat_acl_protocol'] ?? '').trim().toLowerCase();
    const raw = String(v ?? '').trim();
    if (!raw) {
      return proto === 'tcp' || proto === 'udp';
    }
    if (!/^\d+$/.test(raw)) return true;
    const n = parseInt(raw, 10);
    return Number.isNaN(n) || n < 1 || n > 65535;
  }
  return false;
}

function sanitizeIPv4CidrInput(raw: string): string {
  const src = String(raw || '');
  const slashIdx = src.indexOf('/');
  if (slashIdx === -1) {
    // Let the user type the IP part; keep only digits/dots.
    return sanitizeIPv4Input(src);
  }

  const ipPartRaw = src.slice(0, slashIdx);
  const pfxPartRaw = src.slice(slashIdx + 1);
  const ipPart = sanitizeIPv4Input(ipPartRaw);
  const pfxDigits = pfxPartRaw.replace(/[^\d]/g, '').slice(0, 2); // 0-32
  if (!pfxDigits) return ipPart;
  return `${ipPart}/${pfxDigits}`;
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
        return <code style={{ fontFamily: 'monospace', fontSize: 12, background: '#1c1c1c', color: '#ce9178', padding: '1px 6px', borderRadius: 3, border: '1px solid #3c3c3c' }}>{children}</code>;
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
      blockquote: ({ children }) => (
        <blockquote style={{ 
          borderLeft: '3px solid #007acc', 
          background: 'rgba(0, 122, 204, 0.05)',
          margin: '8px 0', 
          padding: '4px 12px',
          color: '#9cdcfe',
          fontStyle: 'italic'
        }}>
          {children}
        </blockquote>
      ),
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
  onPlaybookReady,
  onOpenFile,
  activeJob: _activeJob,
}) => {
  void _activeJob;
  const initialStoredSessions = useMemo(() => loadStoredAnsibleSessions(), []);
  const initialStoredSessionId = useMemo(
    () => loadStoredCurrentAnsibleSessionId(initialStoredSessions),
    [initialStoredSessions],
  );
  const initialStoredMessages = useMemo(() => {
    const existing = initialStoredSessions.find((session) => session.id === initialStoredSessionId) || initialStoredSessions[0];
    return existing?.messages?.length ? existing.messages : [createWelcomeMessage()];
  }, [initialStoredSessionId, initialStoredSessions]);
  const [sessions, setSessions] = useState<AnsibleChatSessionRecord[]>(initialStoredSessions);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(initialStoredSessionId);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sessionsHydrated, setSessionsHydrated] = useState(false);
  const [messages, setMessages] = useState<AnsibleMessage[]>(initialStoredMessages);
  const [input, setInput]               = useState('');
  const [commandHistory, setCommandHistory] = useState<string[]>([]);
  const [commandHistoryIndex, setCommandHistoryIndex] = useState(-1);
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
  const historyDropdownRef = useRef<HTMLDivElement>(null);
  const historyPortalRef = useRef<HTMLDivElement>(null);
  const [historyAnchorRect, setHistoryAnchorRect] = useState<DOMRect | null>(null);
  const sessionsHydratingRef = useRef(true);
  const sessionPersistTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragCounterRef = useRef(0);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  const historyDraftBeforeNavigationRef = useRef('');
  const abortControllerRef = useRef<AbortController | null>(null);
  const loadingPhaseTimeoutRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  /** Hydrated file paths for messages that only had playbookPath (e.g. from before multi-file links). */
  const [hydratedFilePaths, setHydratedFilePaths] = useState<Record<string, string[]>>({});
  const [studioOpen, setStudioOpen] = useState(false);
  const [studioIntent, setStudioIntent] = useState('');

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ANSIBLE_REMEDIATION_INTENT_KEY);
      if (!raw) return;
      sessionStorage.removeItem(ANSIBLE_REMEDIATION_INTENT_KEY);
      const p = JSON.parse(raw) as { intent_description?: string };
      if (p?.intent_description) {
        setInput((prev) => (String(prev || '').trim() ? prev : p.intent_description!));
        Toast.info('Remediation draft loaded', 'Review the prompt and send to Ansible AI when ready.');
      }
    } catch {
      /* ignore */
    }
  }, []);

  // Auto-grow textarea height as user types
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [input]);
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
  /** True while Next is refreshing preview / local topology before an interface step. */
  const [paramsWizardNextLoading, setParamsWizardNextLoading] = useState(false);
  /** Planned-inventory device picker (hints like SW1, wrong names, or missing targets) — before interface/params. */
  const [deviceDisambiguation, setDeviceDisambiguation] = useState<{
    messageId: string;
    question: string;
    candidateDevices: string[];
    closestCandidates?: string[];
    otherCandidates?: string[];
    intentText: string;
    kind?: string;
  } | null>(null);
  const [selectedDevices, setSelectedDevices] = useState<Set<string>>(new Set());
  /** After device picker / explicit selection — always pass these on later preview follow-ups (params wizard, interfaces). */
  const [previewLockedTargetHosts, setPreviewLockedTargetHosts] = useState<string[]>([]);
  /** Merged params (vlan_id/name, target_hosts, interfaces, …) so each follow-up preview does not drop prior answers. */
  const [previewAccumulatedParams, setPreviewAccumulatedParams] = useState<Record<string, unknown>>({});
  /** Which prompt text the accumulated params belong to (prevents cross-prompt leakage). */
  const [previewAccumulatedIntentText, setPreviewAccumulatedIntentText] = useState<string>('');
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

  const resetTransientChatState = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    loadingPhaseTimeoutRef.current.forEach((timeoutId) => clearTimeout(timeoutId));
    loadingPhaseTimeoutRef.current = [];
    setGenerating(false);
    setInput('');
    setAttachedFiles([]);
    setStudioOpen(false);
    setStudioIntent('');
    setStudioInitialPreview(null);
    setDisambiguation(null);
    setDisambiguationChoice('access');
    setDisambiguationCustom('');
    setInterfaceDisambiguation(null);
    setSelectedInterfacesByDevice({});
    setParamsDisambiguation(null);
    setParamsDisambiguationValues({});
    setParamsDisambiguationStep(0);
    setParamsWizardNextLoading(false);
    setDeviceDisambiguation(null);
    setSelectedDevices(new Set());
    setPreviewLockedTargetHosts([]);
    setPreviewAccumulatedParams({});
    setPreviewAccumulatedIntentText('');
    setPlannedPickerDevices([]);
    setPlannedPickerDeviceNames([]);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const hydrateSessions = async () => {
      sessionsHydratingRef.current = true;
      try {
        const remote = await ansibleApi.listChatSessions();
        if (cancelled) return;
        const remoteSessions = (remote.sessions || []).map(normalizeSessionRecord).sort((a, b) => b.updatedAt - a.updatedAt);
        if (remoteSessions.length > 0) {
          const preferredId = loadStoredCurrentAnsibleSessionId(remoteSessions) || remoteSessions[0].id;
          const selected = remoteSessions.find((session) => session.id === preferredId) || remoteSessions[0];
          setSessions(remoteSessions);
          setCurrentSessionId(selected.id);
          setMessages(selected.messages?.length ? selected.messages : [createWelcomeMessage()]);
          return;
        }

        if (initialStoredSessions.length > 0) {
          const migrated = initialStoredSessions.map(normalizeSessionRecord).sort((a, b) => b.updatedAt - a.updatedAt);
          setSessions(migrated);
          const selected = migrated.find((session) => session.id === initialStoredSessionId) || migrated[0];
          setCurrentSessionId(selected?.id ?? null);
          setMessages(selected?.messages?.length ? selected.messages : [createWelcomeMessage()]);
          for (const session of migrated) {
            try {
              await ansibleApi.upsertChatSession({
                id: session.id,
                title: session.title,
                messages: session.messages,
              });
            } catch {
              // best-effort migration
            }
          }
          return;
        }

        const fresh = createSessionRecord();
        setSessions([fresh]);
        setCurrentSessionId(fresh.id);
        setMessages(fresh.messages);
        try {
          await ansibleApi.upsertChatSession({
            id: fresh.id,
            title: fresh.title,
            messages: fresh.messages,
          });
        } catch {
          // keep local-only until backend is reachable
        }
      } catch {
        if (cancelled) return;
        if (initialStoredSessions.length > 0) {
          const fallback = initialStoredSessions.map(normalizeSessionRecord).sort((a, b) => b.updatedAt - a.updatedAt);
          setSessions(fallback);
          const selected = fallback.find((session) => session.id === initialStoredSessionId) || fallback[0];
          setCurrentSessionId(selected?.id ?? null);
          setMessages(selected?.messages?.length ? selected.messages : [createWelcomeMessage()]);
        } else {
          const fresh = createSessionRecord();
          setSessions([fresh]);
          setCurrentSessionId(fresh.id);
          setMessages(fresh.messages);
        }
      } finally {
        if (!cancelled) {
          sessionsHydratingRef.current = false;
          setSessionsHydrated(true);
        }
      }
    };
    hydrateSessions();
    return () => {
      cancelled = true;
    };
  }, [initialStoredSessionId, initialStoredSessions]);

  const startNewSession = useCallback(() => {
    resetTransientChatState();
    const session = createSessionRecord();
    setSessions((prev) => [session, ...prev]);
    setCurrentSessionId(session.id);
    setMessages(session.messages);
    setHistoryOpen(false);
  }, [resetTransientChatState]);

  const handleSelectSession = useCallback((sessionId: string) => {
    if (sessionId === currentSessionId) {
      setHistoryOpen(false);
      return;
    }
    const session = sessions.find((entry) => entry.id === sessionId);
    if (!session) return;
    resetTransientChatState();
    setCurrentSessionId(sessionId);
    setMessages(session.messages?.length ? session.messages : [createWelcomeMessage()]);
    setHistoryOpen(false);
  }, [currentSessionId, resetTransientChatState, sessions]);

  const handleDeleteSession = useCallback((sessionId: string) => {
    ansibleApi.deleteChatSession(sessionId).catch(() => {
      // keep local deletion even if backend cleanup fails
    });
    const remaining = sessions.filter((entry) => entry.id !== sessionId);
    if (remaining.length === 0) {
      const fallback = createSessionRecord();
      resetTransientChatState();
      setSessions([fallback]);
      setCurrentSessionId(fallback.id);
      setMessages(fallback.messages);
      setHistoryOpen(false);
      return;
    }
    setSessions(remaining);
    if (currentSessionId === sessionId) {
      const fallback = remaining[0];
      resetTransientChatState();
      setCurrentSessionId(fallback.id);
      setMessages(fallback.messages?.length ? fallback.messages : [createWelcomeMessage()]);
    }
    setHistoryOpen(false);
  }, [currentSessionId, resetTransientChatState, sessions]);

  const toggleHistory = useCallback(() => {
    if (!historyOpen) {
      const rect = historyDropdownRef.current?.getBoundingClientRect() ?? null;
      setHistoryAnchorRect(rect);
      setHistoryOpen(true);
    } else {
      setHistoryOpen(false);
      setHistoryAnchorRect(null);
    }
  }, [historyOpen]);

  useEffect(() => {
    if (sessionsHydrated && sessions.length === 0) {
      startNewSession();
    }
  }, [sessions.length, sessionsHydrated, startNewSession]);

  useEffect(() => {
    if (!currentSessionId) return;
    setSessions((prev) => {
      const title = fallbackSessionTitle(messages);
      const updatedAt = messages[messages.length - 1]?.timestamp || Date.now();
      const next = prev.map((session) =>
        session.id === currentSessionId
          ? { ...session, title, updatedAt, messages }
          : session,
      );
      if (next.some((session) => session.id === currentSessionId)) {
        return [...next].sort((a, b) => b.updatedAt - a.updatedAt);
      }
      return [{ id: currentSessionId, title, updatedAt, messages }, ...next].sort((a, b) => b.updatedAt - a.updatedAt);
    });
  }, [currentSessionId, messages]);

  useEffect(() => {
    try {
      localStorage.setItem(ANSIBLE_CHAT_SESSIONS_KEY, JSON.stringify(sessions));
    } catch {
      // ignore persistence failures
    }
  }, [sessions]);

  useEffect(() => {
    try {
      if (currentSessionId) localStorage.setItem(ANSIBLE_CHAT_CURRENT_SESSION_KEY, currentSessionId);
      else localStorage.removeItem(ANSIBLE_CHAT_CURRENT_SESSION_KEY);
    } catch {
      // ignore persistence failures
    }
  }, [currentSessionId]);

  useEffect(() => {
    if (!sessionsHydrated || sessionsHydratingRef.current || !currentSessionId) return;
    const current = sessions.find((session) => session.id === currentSessionId);
    if (!current) return;
    if (sessionPersistTimeoutRef.current) clearTimeout(sessionPersistTimeoutRef.current);
    sessionPersistTimeoutRef.current = setTimeout(() => {
      ansibleApi.upsertChatSession({
        id: current.id,
        title: current.title,
        messages: current.messages,
      }).catch(() => {
        // local persistence is still available if backend save fails
      });
    }, 350);
    return () => {
      if (sessionPersistTimeoutRef.current) clearTimeout(sessionPersistTimeoutRef.current);
    };
  }, [currentSessionId, sessions, sessionsHydrated]);

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

  const currentSession = useMemo(
    () => sessions.find((session) => session.id === currentSessionId) ?? null,
    [currentSessionId, sessions],
  );

  const persistAnsibleModel = useCallback((sel: AnsibleModelSelection) => {
    setAnsibleModel(sel);
    try {
      localStorage.setItem(ANSIBLE_MODEL_STORAGE_KEY, JSON.stringify(sel));
    } catch (_) { /* ignore */ }
  }, []);

  const handleRebuildRag = useCallback(async () => {
    if (isRebuildingRag) return;
    setIsRebuildingRag(true);
    try {
      const res = await ragApi.rebuild();
      const errs = Array.isArray(res.errors) ? res.errors.length : 0;
      if (errs > 0) {
        Toast.warning('RAG rebuilt with warnings', `Processed ${res.files_processed} files, ${res.total_chunks} chunks, ${errs} warnings.`);
      } else {
        Toast.success('RAG rebuilt', `Processed ${res.files_processed} files and ${res.total_chunks} chunks.`);
      }
    } catch (err) {
      Toast.error('RAG rebuild failed', describeError(err, 'Could not rebuild RAG.'));
    } finally {
      setIsRebuildingRag(false);
    }
  }, [isRebuildingRag]);

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

  useEffect(() => {
    if (!historyOpen) return;
    const updateRect = () => {
      const anchor = historyDropdownRef.current;
      setHistoryAnchorRect(anchor ? anchor.getBoundingClientRect() : null);
    };
    updateRect();
    const onOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      const insideAnchor = historyDropdownRef.current && historyDropdownRef.current.contains(target);
      const insidePortal = historyPortalRef.current && historyPortalRef.current.contains(target);
      if (!insideAnchor && !insidePortal) {
        setHistoryOpen(false);
      }
    };
    document.addEventListener('mousedown', onOutside);
    window.addEventListener('resize', updateRect);
    window.addEventListener('scroll', updateRect, true);
    return () => {
      document.removeEventListener('mousedown', onOutside);
      window.removeEventListener('resize', updateRect);
      window.removeEventListener('scroll', updateRect, true);
      setHistoryAnchorRect(null);
    };
  }, [historyOpen]);

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
    const ansiblePathRaw =
      e.dataTransfer.getData('application/ansible-path') ||
      e.dataTransfer.getData('text/plain');
    const ansiblePath = String(ansiblePathRaw || '').trim();
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
      } catch (_) { /* ignore — dropped text may not be a valid file path */ }
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
        // Secondary intents are only meaningful for the same prompt text.
        setPreviewAccumulatedParams((prev) => ({ ...prev, secondary_intents: secMerge }));
      }
      const devicesDis = res?.devices_disambiguation as
        | {
            question?: string;
            candidate_devices?: string[];
            closest_candidates?: string[];
            other_candidates?: string[];
            ask_user_selection?: boolean;
            disambiguation_kind?: string;
          }
        | undefined;
      const pickerDevicesFromIface = mergeInterfacePickersFromPreviewResponse(res);
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
          closestCandidates: devicesDis.closest_candidates,
          otherCandidates: devicesDis.other_candidates,
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
        let fieldsList = (paramsDis.fields || []) as Array<{ field?: string }>;
        // If backend provides atomic ACL wizard fields, never ask for the raw `rules` field.
        // (rules is assembled client-side from `_chat_acl_*` inputs.)
        const hasAclAtoms = fieldsList.some((f) => isAclAtomicWizardFieldName(String(f.field || '')));
        if (hasAclAtoms) {
          fieldsList = fieldsList.filter((f) => String(f.field || '').toLowerCase() !== 'rules');
        }
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
          const wizardFields = fieldsList as PreviewParamField[];
          const initVals: Record<string, string | number | string[] | number[]> = {};
          const pParams = (res?.parsed_intent?.parameters || {}) as Record<string, unknown>;
          const rawVid = pParams.vlan_id ?? (pParams.vlan as { id?: number } | undefined)?.id;
          const vid =
            rawVid !== undefined && rawVid !== null && rawVid !== ''
              ? parseInt(String(rawVid), 10)
              : NaN;
          if (!Number.isNaN(vid)) {
            for (const f of wizardFields) {
              if (
                f.field === 'trunk_vlans' &&
                f.control === 'multi_select_integers' &&
                Array.isArray(f.options) &&
                f.options.every((x) => typeof x === 'number') &&
                (f.options as number[]).includes(vid)
              ) {
                initVals[previewFieldValueKey(f)] = [vid];
              }
            }
          }
          setParamsDisambiguation({
            messageId: menuMsgId,
            question: paramsDis.question || 'Please provide the following.',
            fields: wizardFields,
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
          const hasInvalidEntities =
            Array.isArray((ifaceDis as any)?.invalid_entities) &&
            ((ifaceDis as any)?.invalid_entities as unknown[]).length > 0;
          if (hasInvalidEntities || Boolean((ifaceDis as any)?.params_need_context_fix)) {
            // Invalid-name correction flow: never pre-tick interfaces.
            // User must explicitly pick from planned inventory.
            setSelectedInterfacesByDevice({});
          } else {
            const parsed = (res?.parsed_intent ?? null) as Record<string, unknown> | null;
            const chips = buildPlannedInventoryPreselectChips(parsed, {
              extraInvalidEntities: (ifaceDis as any)?.invalid_entities as unknown[] | undefined,
              skipSuggestions: true,
            });
            setSelectedInterfacesByDevice(
              preselectPlannedInventoryInterfacesByDevice(chips, { devices: ifaceDevices }),
            );
          }
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

  const handleParamsWizardNext = useCallback(async () => {
    if (!paramsDisambiguation) return;
    const fields = paramsDisambiguation.fields;
    const step = paramsDisambiguationStep;
    const cur = fields[step];
    const nxt = fields[step + 1];
    if (!cur || nxt === undefined) return;

    // Auto-skip ACL wizard fields that become irrelevant based on earlier answers.
    try {
      const nextField = String(nxt.field || '').toLowerCase();
      const proto = String(paramsDisambiguationValues['_chat_acl_protocol'] ?? '').trim().toLowerCase();
      const src = String(paramsDisambiguationValues['_chat_acl_source'] ?? '').trim().toLowerCase();
      const dst = String(paramsDisambiguationValues['_chat_acl_destination'] ?? '').trim().toLowerCase();
      const isTcpUdp = proto === 'tcp' || proto === 'udp';
      if (
        (nextField === '_chat_acl_source_port' || nextField === '_chat_acl_destination_port') &&
        proto &&
        !isTcpUdp
      ) {
        setParamsDisambiguationValues((prev) => ({ ...prev, [previewFieldValueKey(nxt)]: '' }));
        setParamsDisambiguationStep((s) => s + 1);
        return;
      }
      if (nextField === '_chat_acl_source_wildcard' && src === 'any') {
        setParamsDisambiguationValues((prev) => ({ ...prev, [previewFieldValueKey(nxt)]: '' }));
        setParamsDisambiguationStep((s) => s + 1);
        return;
      }
      if (nextField === '_chat_acl_dest_wildcard' && dst === 'any') {
        setParamsDisambiguationValues((prev) => ({ ...prev, [previewFieldValueKey(nxt)]: '' }));
        setParamsDisambiguationStep((s) => s + 1);
        return;
      }
    } catch (_) {
      // ignore
    }

    if (paramsWizardValueIncomplete(cur, paramsDisambiguationValues)) {
      Toast.warning('Missing or invalid answer', 'Please complete this step before continuing.');
      return;
    }

    const advancingToIface = isParamsWizardInterfaceInventoryField(nxt);
    const hosts = collectTargetHostsFromParamsWizard(fields, paramsDisambiguationValues);

    if (advancingToIface) {
      if (hosts.length === 0) {
        Toast.warning('Select devices first', 'Choose target device(s) before selecting interfaces.');
        return;
      }
      setParamsWizardNextLoading(true);
      try {
        const intentText = paramsDisambiguation.intentText;
        const { vendor, feature, params: parsedParams } = parseIntent(intentText);
        const scopedPrev = previewAccumulatedIntentText === intentText ? previewAccumulatedParams : {};
        const merged: Record<string, unknown> = { ...parsedParams, ...scopedPrev, target_hosts: hosts };

        const localPick = await plannedInterfacesFromLocalTopology(hosts);
        if (localPick.length > 0) {
          setPlannedPickerDevices((prev) => mergePlannedDeviceInterfaces(prev, localPick));
        }
        setPlannedPickerDeviceNames(Array.from(new Set(hosts)));

        const localCoversAll =
          hosts.length > 0 &&
          hosts.every((hn) => {
            const row = localPick.find((d) => d.hostname === hn);
            return row != null && (row.interfaces?.length ?? 0) > 0;
          });

        if (!localCoversAll) {
          const res = await ansibleApi.previewIntent({
            intent_description: intentText,
            target_hosts: hosts,
            params: merged,
            vendor: vendor || undefined,
            feature: feature || undefined,
            provider: ansibleModel.provider,
            model: ansibleModel.model,
            ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
            skip_rag: true,
          });

          const fromPreview = mergeInterfacePickersFromPreviewResponse(res);
          if (fromPreview.length > 0) {
            setPlannedPickerDevices((prev) => mergePlannedDeviceInterfaces(prev, fromPreview));
          }
          const nameSet = new Set<string>(hosts);
          for (const d of fromPreview) {
            const hn = String(d.hostname || '').trim();
            if (hn) nameSet.add(hn);
          }
          setPlannedPickerDeviceNames(Array.from(nameSet));
        }

        setPreviewLockedTargetHosts(hosts);
        setPreviewAccumulatedParams(merged);
        setPreviewAccumulatedIntentText(intentText);
        setParamsDisambiguationStep((s) => s + 1);
      } catch {
        Toast.error(
          'Could not load interfaces',
          'Failed to refresh planned interfaces for the selected devices. Check planned topology / local DB and try again.',
        );
      } finally {
        setParamsWizardNextLoading(false);
      }
      return;
    }

    setParamsDisambiguationStep((s) => s + 1);
  }, [
    paramsDisambiguation,
    paramsDisambiguationStep,
    paramsDisambiguationValues,
    previewAccumulatedParams,
    previewAccumulatedIntentText,
    ansibleModel,
  ]);

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
      const scopedPrev = previewAccumulatedIntentText === intentText ? previewAccumulatedParams : {};
      const merged = { ...parsedParams, ...scopedPrev, target_hosts: hosts };

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
      setPreviewAccumulatedIntentText(intentText);
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
      } else {
        Toast.error('Could not continue with device selection', describeError(err, 'Preview refresh failed.'));
      }
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
    const schemaAction = (
      interfaceDisambiguation.schemaAction ||
      ((previewAccumulatedParams.action as string | undefined) || 'configure_interface')
    ).toLowerCase();
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
      const inferPromptDirection = (): 'in' | 'out' | null => {
        const tl = String(intentText || '').toLowerCase();
        if (/\binbound\b/.test(tl)) return 'in';
        if (/\boutbound\b/.test(tl)) return 'out';
        // If the user wrote "in"/"out" without binding to a specific interface.
        if (/\b(in)\b/.test(tl) && !/\b(out)\b/.test(tl)) return 'in';
        if (/\b(out)\b/.test(tl) && !/\b(in)\b/.test(tl)) return 'out';
        return null;
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
      } else if (schemaAction === 'configure_acl' || schemaAction === 'apply_acl_to_interface') {
        ifaceParams = {
          apply_to: qualified.map(({ hostname, ifName }) => {
            const q = qTok({ hostname, ifName });
            const dir = inferDirection(ifName) ?? inferPromptDirection();
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
      } else if (schemaAction === 'configure_ospf' || schemaAction === 'configure_ospf_passive_interface') {
        ifaceParams = { passive_interfaces: qualified.map(qTok) };
      } else if (schemaAction === 'configure_static_route') {
        ifaceParams = { next_hop: qTok(qualified[0]) };
      } else {
        ifaceParams = { interfaces_to_shutdown: qualified.map(qTok) };
      }
      const mergedIface: Record<string, unknown> = {
        ...parsedParams,
        ...(previewAccumulatedIntentText === intentText ? previewAccumulatedParams : {}),
        ...ifaceParams,
      };
      // Authoritative attachment semantics:
      // When the user explicitly selected attachment targets (ACL/QoS/etc),
      // do not keep stale prompt-derived `interfaces` around (it can override/preselect wrongly).
      if (schemaAction === 'configure_acl' || schemaAction === 'apply_acl_to_interface') {
        if (Array.isArray((mergedIface as any).apply_to) && (mergedIface as any).apply_to.length > 0) {
          delete (mergedIface as any).interfaces;
          delete (mergedIface as any).interfaces_config;
        }
      }
      if (schemaAction === 'configure_qos') {
        if (Array.isArray((mergedIface as any).interface_apply) && (mergedIface as any).interface_apply.length > 0) {
          delete (mergedIface as any).interfaces;
          delete (mergedIface as any).interfaces_config;
        }
      }
      if (effectiveTargetHosts?.length) {
        mergedIface.target_hosts = effectiveTargetHosts;
      }
      setPreviewAccumulatedParams(mergedIface);
      setPreviewAccumulatedIntentText(intentText);
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
      // Hard authoritative override before opening Playbook Studio:
      // when user picked interfaces in UI, never let stale prompt/LLM interface fields
      // (e.g. GigabitEthernet...) leak into preview modal state.
      const forceAuthoritativeInterfacesInPreview = (
        preview: any,
        action: string,
        authoritativeParams: Record<string, unknown>,
      ) => {
        if (!preview || !preview.parsed_intent || typeof preview.parsed_intent !== 'object') return preview;
        const out = { ...preview, parsed_intent: { ...preview.parsed_intent } } as any;
        const p0 = ((out.parsed_intent as any).parameters || {}) as Record<string, unknown>;
        const p: Record<string, unknown> = { ...p0 };
        const secIn = Array.isArray((out.parsed_intent as any).secondary_intents)
          ? ((out.parsed_intent as any).secondary_intents as Array<any>)
          : [];
        const secOut = secIn.map((s) => ({
          ...s,
          parameters: { ...((s && typeof s === 'object' ? s.parameters : {}) || {}) },
        }));

        const clearStaleInterfaces = () => {
          delete p.interfaces;
          delete p.interfaces_config;
          for (const s of secOut) {
            if (!s || typeof s !== 'object') continue;
            const sp = (s.parameters || {}) as Record<string, unknown>;
            delete sp.interfaces;
            delete sp.interfaces_config;
            s.parameters = sp;
          }
        };

        if (action === 'configure_acl' || action === 'apply_acl_to_interface') {
          if (Array.isArray((authoritativeParams as any).apply_to) && (authoritativeParams as any).apply_to.length > 0) {
            p.apply_to = (authoritativeParams as any).apply_to;
            clearStaleInterfaces();
          }
        } else if (action === 'configure_qos') {
          if (Array.isArray((authoritativeParams as any).interface_apply) && (authoritativeParams as any).interface_apply.length > 0) {
            p.interface_apply = (authoritativeParams as any).interface_apply;
            clearStaleInterfaces();
          }
        } else if (action === 'configure_ospf' || action === 'configure_ospf_passive_interface') {
          if (Array.isArray((authoritativeParams as any).passive_interfaces) && (authoritativeParams as any).passive_interfaces.length > 0) {
            p.passive_interfaces = (authoritativeParams as any).passive_interfaces;
            clearStaleInterfaces();
          }
        } else if (action === 'shutdown_interface') {
          if (Array.isArray((authoritativeParams as any).interfaces_to_shutdown) && (authoritativeParams as any).interfaces_to_shutdown.length > 0) {
            p.interfaces_to_shutdown = (authoritativeParams as any).interfaces_to_shutdown;
            clearStaleInterfaces();
          }
        } else if (action === 'configure_interface' || action === 'create_vlan') {
          if (Array.isArray((authoritativeParams as any).interfaces) && (authoritativeParams as any).interfaces.length > 0) {
            p.interfaces = (authoritativeParams as any).interfaces;
          }
        }

        (out.parsed_intent as any).parameters = p;
        (out.parsed_intent as any).secondary_intents = secOut;
        return out;
      };
      const sanitizedRes = forceAuthoritativeInterfacesInPreview(res, schemaAction, mergedIface);
      const valErr = (res as { parsed_intent?: { validation_errors?: string[] } })?.parsed_intent?.validation_errors;
      if (Array.isArray(valErr) && valErr.length > 0) {
        addMsg({
          role: 'assistant',
          content: `**Validation issues**\n\n${valErr.join('\n')}`,
        });
      } else {
        setStudioInitialPreview(sanitizedRes as any);
        setStudioIntent(intentText);
        setStudioOpen(true);
      }
    } catch (err: unknown) {
      abortControllerRef.current = null;
      setMessages(prev => prev.filter(m => m.id !== progressId));
      const isAbort = err instanceof DOMException && err.name === 'AbortError';
      if (isAbort) {
        addMsg({ role: 'assistant', content: 'The generation was cancelled by the user.' });
      } else {
        Toast.error('Could not continue with interface selection', describeError(err, 'Preview refresh failed.'));
      }
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

  const createPreviewDialogArtifact = useCallback(async (input: {
    jobId: number;
    intentDescription: string;
    entrypointPath?: string | null;
    files?: AnsibleGeneratedArtifactFile[];
    name?: string;
    preview?: {
      categories?: string[];
      target_hosts?: string[];
      reasoning?: string[];
      references?: { doc_name: string; feature: string; vendor: string; path: string }[];
      policy_approved?: boolean;
      parsed_intent?: Record<string, unknown>;
    } | null;
  }): Promise<AnsibleGeneratedArtifactFile | null> => {
    let previewPath = resolveCompanionPreviewPath({
      entrypointPath: input.entrypointPath,
      files: input.files,
      name: input.name,
    });

    // If caller only had a filename (no folder context), resolve canonical job paths from backend.
    if (!previewPath.includes('/') && input.jobId) {
      try {
        const latest = await ansibleApi.getResult(input.jobId);
        const latestFiles: AnsibleGeneratedArtifactFile[] = (latest.files || []).map((f) => ({
          path: f.path,
          content: '',
        }));
        const resolved = resolveCompanionPreviewPath({
          entrypointPath: latest.entrypoint_path,
          files: latestFiles,
          name: input.name,
        });
        if (resolved.includes('/')) {
          previewPath = resolved;
        }
      } catch {
        // Keep best-effort local path when result lookup fails.
      }
    }

    const content = buildPreviewDialogContent({
      intentDescription: input.intentDescription,
      jobId: input.jobId,
      preview: input.preview || null,
    });

    try {
      await ansibleApi.createFile(previewPath, content);
      return { path: previewPath, content, writable: true, kind: 'preview_dialog' };
    } catch {
      try {
        await ansibleApi.writeFile(previewPath, content);
        return { path: previewPath, content, writable: true, kind: 'preview_dialog' };
      } catch {
        return null;
      }
    }
  }, []);

  const handleSend = useCallback(async () => {
    const baseText = input.trim();
    if (!baseText && attachedFiles.length === 0) return;
    if (isGenerating) return;

    if (baseText) {
      setCommandHistory((prev) => {
        if (prev[prev.length - 1] === baseText) return prev;
        const next = [...prev, baseText];
        return next.length > 100 ? next.slice(next.length - 100) : next;
      });
    }
    setCommandHistoryIndex(-1);
    historyDraftBeforeNavigationRef.current = '';

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
        const templatePreviewArtifact = await createPreviewDialogArtifact({
          jobId: result.job_id,
          intentDescription: baseText,
          entrypointPath: result.entrypoint_path,
          files: result.files,
          name: playbookName.split('/').pop() ?? playbookName,
          preview: {
            categories: [tplFeature],
            target_hosts: ['all'],
            reasoning: [
              'Used deterministic Config Engine template generation.',
              `Applied vendor=${tplVendor} feature=${tplFeature}.`,
            ],
            references: [],
            policy_approved: true,
            parsed_intent: {
              vendor: tplVendor,
              feature: tplFeature,
              params: tplParams,
              source: 'template_command',
            },
          },
        });
        const generatedFiles = templatePreviewArtifact
          ? [...(result.files || []), templatePreviewArtifact]
          : result.files || [];

        setMessages(prev => prev.map(m =>
          m.id === loadingId ? {
            ...m,
            isLoading: false,
            content:
              `✅ **Template playbook package generated**\n\n` +
              `I rendered your request using the Config Engine template, wrote the playbook artifact, and generated a companion preview dialog file for audit/review context.\n\n` +
              `- **Playbook:** \`${playbookName}\`\n` +
              `- **Preview dialog file:** ${templatePreviewArtifact ? `\`${templatePreviewArtifact.path}\`` : '_not created (write permission/path issue)_'}\n` +
              `- **Job ID:** ${result.job_id}\n` +
              `- **Vendor:** \`${tplVendor}\`  **Feature:** \`${tplFeature}\`\n` +
              `- **Source:** Config Engine template\n\n` +
              `Recommend running **Check** to see the dry-run diff before deploying.`,
            jobId: result.job_id,
            playbookPath: playbookName,
            generatedPaths: generatedFiles.map((file) => file.path) || [playbookName],
          } : m
        ));

        onPlaybookReady({
          jobId: result.job_id,
          content: result.playbook,
          name: playbookName.split('/').pop() ?? playbookName,
          entrypointPath: result.entrypoint_path,
          files: generatedFiles,
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
  }, [input, isGenerating, addMsg, onPlaybookReady, attachedFiles, ansibleModel, parseIntent, applyPreviewFollowUps, createPreviewDialogArtifact]);

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
      // Preserve authoritative device picks across disambiguation steps.
      // Otherwise the backend will ask for target devices again even when the user already selected them.
      const th =
        previewLockedTargetHosts.length > 0
          ? previewLockedTargetHosts
          : Array.from(selectedDevices);
      const res = await ansibleApi.previewIntent({
        intent_description: clarifiedText,
        target_hosts: th.length ? th : (target_hosts?.length ? target_hosts : undefined),
        vendor: vendor || undefined,
        feature: feature || undefined,
        provider: ansibleModel.provider,
        model: ansibleModel.model,
        ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
        params: th.length ? { target_hosts: th } : undefined,
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
      } else {
        Toast.error('Could not continue clarification', describeError(err, 'Preview refresh failed.'));
      }
    }
    setGenerating(false);
  }, [
    disambiguation,
    disambiguationChoice,
    disambiguationCustom,
    ansibleModel,
    parseIntent,
    addMsg,
    applyPreviewFollowUps,
    previewLockedTargetHosts,
    selectedDevices,
  ]);

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

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
      return;
    }

    if ((e.key !== 'ArrowUp' && e.key !== 'ArrowDown') || commandHistory.length === 0) return;

    const el = e.currentTarget;
    const cursor = el.selectionStart ?? 0;
    const value = el.value ?? '';
    const beforeCursor = value.slice(0, cursor);
    const afterCursor = value.slice(cursor);
    const isOnFirstLine = !beforeCursor.includes('\n');
    const isOnLastLine = !afterCursor.includes('\n');

    if (e.key === 'ArrowUp' && !isOnFirstLine) return;
    if (e.key === 'ArrowDown' && !isOnLastLine) return;

    e.preventDefault();

    if (e.key === 'ArrowUp') {
      setCommandHistoryIndex((prev) => {
        const nextIndex = prev < 0 ? commandHistory.length - 1 : Math.max(0, prev - 1);
        if (prev < 0) historyDraftBeforeNavigationRef.current = input;
        setInput(commandHistory[nextIndex] ?? '');
        return nextIndex;
      });
      return;
    }

    setCommandHistoryIndex((prev) => {
      if (prev < 0) return -1;
      const nextIndex = prev + 1;
      if (nextIndex >= commandHistory.length) {
        setInput(historyDraftBeforeNavigationRef.current || '');
        return -1;
      }
      setInput(commandHistory[nextIndex] ?? '');
      return nextIndex;
    });
  };

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
          void (async () => {
            const previewArtifact = await createPreviewDialogArtifact({
              jobId: payload.jobId,
              intentDescription: studioIntent,
              entrypointPath: payload.entrypointPath,
              files: payload.files,
              name: payload.name,
              preview: studioInitialPreview,
            });
            const generatedFiles = previewArtifact
              ? [...(payload.files || []), previewArtifact]
              : payload.files || [];

            onPlaybookReady({ ...payload, files: generatedFiles });
            addMsg({
              role: 'assistant',
              content:
                `✅ **Playbook package generated successfully**\n\n` +
                `I parsed your intent, selected applicable references, and produced both deployment and review artifacts in the same job folder.\n\n` +
                `- **Playbook:** \`${payload.name}\`\n` +
                `- **Preview dialog file:** ${previewArtifact ? `\`${previewArtifact.path}\`` : '_not created (write permission/path issue)_'}\n` +
                `- **Job ID:** ${payload.jobId}\n` +
                (generatedFiles.length ? `- **Files generated:** ${generatedFiles.length}\n\n` : '\n') +
                `Recommend running **Check** to see the dry-run diff before deploying.`,
              jobId: payload.jobId,
              playbookPath: payload.name,
              generatedPaths: generatedFiles.map((f) => f.path) ?? [payload.name],
            });
            setStudioOpen(false);
          })();
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
          <span className="text-[11px] text-base-content/55 max-w-[150px] truncate" title={currentSession?.title || 'New Chat'}>
            {currentSession?.title || 'New Chat'}
          </span>
        </div>
        <div className="flex items-center gap-1 z-999">
          <div ref={historyDropdownRef} style={{ position: 'relative' }}>
            <button
              type="button"
              onClick={toggleHistory}
              title="Session history"
              className={`p-1 rounded transition-colors ${
                historyOpen
                  ? 'bg-primary/15 text-primary'
                  : 'text-base-content/60 hover:text-base-content hover:bg-base-content/10'
              }`}
            >
              <History style={{ width: 13, height: 13 }} />
            </button>
            {historyOpen && historyAnchorRect && createPortal(
              <div
                ref={historyPortalRef}
                style={{
                  position: 'fixed',
                  top: `${historyAnchorRect.bottom + 4}px`,
                  left: `${Math.min(Math.max(8, historyAnchorRect.right - 300), window.innerWidth - 308)}px`,
                  width: '300px',
                }}
                className="z-9999 max-h-[360px] overflow-hidden flex flex-col bg-base-200 border border-base-300 rounded-md shadow-xl"
              >
                <div className="px-2.5 py-2 border-b border-base-300 text-[11px] font-semibold text-base-content/80">
                  Session History
                </div>
                <div className="overflow-y-auto max-h-80 p-1.5">
                  {sessions.length === 0 ? (
                    <div className="p-2.5 text-[11px] text-base-content/55">No saved sessions</div>
                  ) : (
                    sessions.map((session) => {
                      const selected = session.id === currentSessionId;
                      return (
                        <div
                          key={session.id}
                          className={`flex items-center gap-2 px-2 py-1.5 rounded mb-1 transition-colors ${
                            selected
                              ? 'bg-primary text-primary-content'
                              : 'text-base-content/80 hover:bg-base-300'
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => handleSelectSession(session.id)}
                            className="flex-1 text-left bg-transparent border-0 text-inherit cursor-pointer"
                          >
                            <div className="text-xs whitespace-nowrap overflow-hidden text-ellipsis">
                              {session.title}
                            </div>
                            <div className="text-[10px] opacity-75">
                              {new Date(session.updatedAt).toLocaleString()}
                            </div>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteSession(session.id)}
                            title="Delete session"
                            className={`bg-transparent border-0 cursor-pointer p-0.5 transition-colors ${
                              selected
                                ? 'text-primary-content/85 hover:text-primary-content'
                                : 'text-base-content/45 hover:text-error'
                            }`}
                          >
                            <Trash2 style={{ width: 12, height: 12 }} />
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>,
              document.body,
            )}
          </div>
          <button
            type="button"
            onClick={startNewSession}
            title="New session"
            className="p-1 rounded text-base-content/60 hover:text-base-content hover:bg-base-content/10 transition-colors"
          >
            <Plus style={{ width: 13, height: 13 }} />
          </button>
          {/* {activeJob && (
            <span style={{
              fontSize: 10, padding: '1px 7px', borderRadius: 10,
              background: activeJob.status === 'success' ? '#1b3a1f' : activeJob.status === 'failed' ? '#3a1010' : activeJob.status === 'running' ? '#3a2e00' : activeJob.status === 'pending' ? '#2f2a12' : '#2d2d2d',
              color:      activeJob.status === 'success' ? '#69f0ae' : activeJob.status === 'failed' ? '#ff8a80' : activeJob.status === 'running' ? '#ffd740' : activeJob.status === 'pending' ? '#ffe082' : '#858585',
              border:     `1px solid ${activeJob.status === 'success' ? '#2e7d32' : activeJob.status === 'failed' ? '#c62828' : activeJob.status === 'running' ? '#f9a825' : activeJob.status === 'pending' ? '#8d6e00' : '#3c3c3c'}`,
            }}>#{activeJob.jobId} · {activeJob.status}</span>
          )} */}
          <button
            onClick={handleRebuildRag}
            disabled={isRebuildingRag}
            title="Rebuild RAG (re-ingest reference docs so the LLM uses the latest Ansible reference)"
            className="p-1 rounded text-base-content/60 hover:text-base-content hover:bg-base-content/10 flex items-center gap-1 transition-colors disabled:opacity-50 disabled:cursor-wait"
          >
            <RefreshCw style={{ width: 10, height: 10 }} className={isRebuildingRag ? 'animate-spin' : ''} />
          </button>
          {/* <button onClick={clearMessages} title="Clear"
            style={{ padding: '3px 4px', borderRadius: 3, background: 'transparent', border: 'none', color: '#666', cursor: 'pointer' }}
            className="hover:bg-[#2d2d2d] transition-colors">
            <RotateCcw style={{ width: 13, height: 13 }} />
          </button> */}
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
                <div className="pl-6 select-text">
                  <VscodeMarkdown content={msg.content} />
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
                        {renderChatEmphasis(msg.disambiguation.question)}
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
                        {renderChatEmphasis(deviceDisambiguation.question)}
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
                        {(deviceDisambiguation.closestCandidates?.length ?? 0) > 0 ? (
                          <>
                            <div className="w-full text-[11px] font-semibold text-primary/90">You might be referring:</div>
                            {deviceDisambiguation.closestCandidates!.map((host) => (
                              <label key={`closest-${host}`} className="flex items-center gap-1.5 cursor-pointer">
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
                            {(deviceDisambiguation.otherCandidates?.length ?? 0) > 0 ? (
                              <>
                                <div className="w-full mt-1 text-[11px] font-semibold text-base-content/70">Devices not found?</div>
                                {deviceDisambiguation.otherCandidates!.map((host) => (
                                  <label key={`other-${host}`} className="flex items-center gap-1.5 cursor-pointer">
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
                              </>
                            ) : null}
                          </>
                        ) : (
                          deviceDisambiguation.candidateDevices.map((host) => (
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
                          ))
                        )}
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
                        {renderChatEmphasis(interfaceDisambiguation.question)}
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
                                  <label className="text-sm text-base-content/90 block mb-0.5">
                                    {renderChatEmphasis(f.question)}
                                  </label>
                                  {(() => {
                                    const normalizedField = String(f.field || '').toLowerCase();
                                    if (
                                      f.control === 'single_select_strings' &&
                                      Array.isArray(f.options) &&
                                      f.options.length > 0 &&
                                      f.options.every((x) => typeof x === 'string')
                                    ) {
                                      const opts = f.options as string[];
                                      const curVal = String((paramsDisambiguationValues[vk] ?? '') as string);
                                      const optLabel = (opt: string) => {
                                        if (opt === 'l2') return 'Layer 2 (switchport — access or trunk)';
                                        if (opt === 'l3') return 'Layer 3 (routed — IP address)';
                                        if (opt === 'access') return 'Access';
                                        if (opt === 'trunk') return 'Trunk';
                                        return opt;
                                      };
                                      return (
                                        <div className="flex flex-col gap-2 rounded border border-base-300 bg-base-300/50 p-2">
                                          {opts.map((opt) => (
                                            <label key={`${vk}-${opt}`} className="flex items-center gap-2 cursor-pointer">
                                              <input
                                                type="radio"
                                                name={vk}
                                                checked={curVal === opt}
                                                onChange={() =>
                                                  setParamsDisambiguationValues((prev) => ({ ...prev, [vk]: opt }))
                                                }
                                                className="radio radio-sm radio-primary"
                                              />
                                              <span className="text-sm">{optLabel(opt)}</span>
                                            </label>
                                          ))}
                                        </div>
                                      );
                                    }
                                    if (
                                      f.control === 'single_select_integers' &&
                                      Array.isArray(f.options) &&
                                      f.options.length > 0 &&
                                      f.options.every((x) => typeof x === 'number')
                                    ) {
                                      const opts = f.options as number[];
                                      const curValRaw = paramsDisambiguationValues[vk];
                                      const curVal =
                                        typeof curValRaw === 'number'
                                          ? curValRaw
                                          : parseInt(String(curValRaw ?? ''), 10);
                                      return (
                                        <div className="flex flex-wrap gap-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-52 overflow-y-auto">
                                          {opts.map((n) => (
                                            <label key={`${vk}-${n}`} className="flex items-center gap-1.5 cursor-pointer">
                                              <input
                                                type="radio"
                                                name={vk}
                                                checked={curVal === n}
                                                onChange={() =>
                                                  setParamsDisambiguationValues((prev) => ({ ...prev, [vk]: n }))
                                                }
                                                className="radio radio-sm radio-primary"
                                              />
                                              <span className="text-xs font-mono">VLAN {n}</span>
                                            </label>
                                          ))}
                                        </div>
                                      );
                                    }

                                    // ACL type: render as a strict dropdown even when backend schema doesn't provide options.
                                    if (normalizedField === 'acl_type') {
                                      const opts = ['ipv4', 'ipv6', 'mac', 'l2'];
                                      const curVal = String((paramsDisambiguationValues[vk] ?? '') as string).trim().toLowerCase();
                                      return (
                                        <select
                                          className="select select-sm select-bordered w-full font-mono mt-1"
                                          value={curVal}
                                          onChange={(e) =>
                                            setParamsDisambiguationValues((prev) => ({
                                              ...prev,
                                              [vk]: String(e.target.value || '').trim(),
                                            }))
                                          }
                                        >
                                          <option value="" disabled>
                                            Select ACL type…
                                          </option>
                                          {opts.map((o) => (
                                            <option key={`${vk}-${o}`} value={o}>
                                              {o}
                                            </option>
                                          ))}
                                        </select>
                                      );
                                    }
                                    const isChatField = String(f.field || '').startsWith('_chat_');
                                    const isTargetDeviceField =
                                      normalizedField === 'target_devices' ||
                                      normalizedField === 'target_hosts' ||
                                      (normalizedField.includes('device') && !isChatField);
                                    const isInterfaceField =
                                      !isChatField &&
                                      (normalizedField === 'interfaces' ||
                                        normalizedField === 'interfaces_to_shutdown' ||
                                        normalizedField === 'apply_to' ||
                                        normalizedField === 'interface_apply' ||
                                        normalizedField === 'passive_interfaces' ||
                                        normalizedField.includes('interface'));
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
                                      (() => {
                                        // Prefer backend-provided options for target_devices questions.
                                        // This avoids stale UI state (e.g. previous single-host picker) from
                                        // shrinking the current device-choice list.
                                        if (
                                          isTargetDeviceField &&
                                          Array.isArray((f as any).options) &&
                                          (f as any).options.every((x: unknown) => typeof x === 'string')
                                        ) {
                                          const opts = ((f as any).options as string[])
                                            .map((x) => String(x || '').trim())
                                            .filter(Boolean);
                                          if (opts.length > 0) return opts;
                                        }
                                        return plannedPickerDeviceNames.length > 0
                                          ? plannedPickerDeviceNames
                                          : filteredDevices.map((d) => d.hostname);
                                      })();

                                    if (isTargetDeviceField) {
                                      const cur = (paramsDisambiguationValues[vk] as string[] | undefined) ?? [];
                                      const fc = f as {
                                        closest_candidates?: string[];
                                        other_candidates?: string[];
                                      };
                                      const closestList = Array.isArray(fc.closest_candidates)
                                        ? fc.closest_candidates
                                            .map((x) => String(x || '').trim())
                                            .filter(Boolean)
                                        : [];
                                      const otherList = Array.isArray(fc.other_candidates)
                                        ? fc.other_candidates
                                            .map((x) => String(x || '').trim())
                                            .filter(Boolean)
                                        : [];
                                      const useGroupedPicker =
                                        closestList.length > 0 && otherList.length > 0;
                                      const toggleHost = (host: string) => {
                                        setParamsDisambiguationValues((prev) => {
                                          const prevArr = (prev[vk] as string[] | undefined) ?? [];
                                          const next = prevArr.includes(host)
                                            ? prevArr.filter((x) => x !== host)
                                            : [...prevArr, host];
                                          return { ...prev, [vk]: next };
                                        });
                                      };
                                      const hostCheckbox = (host: string) => {
                                        const checked = cur.includes(host);
                                        return (
                                          <label
                                            key={`${vk}-${host}`}
                                            className="flex items-center gap-1.5 cursor-pointer"
                                          >
                                            <input
                                              type="checkbox"
                                              checked={checked}
                                              onChange={() => toggleHost(host)}
                                              className="checkbox checkbox-sm checkbox-primary"
                                            />
                                            <span className="text-xs font-mono">{host}</span>
                                          </label>
                                        );
                                      };
                                      if (useGroupedPicker) {
                                        return (
                                          <div className="space-y-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-60 overflow-y-auto">
                                            <div>
                                              <div className="w-full text-[11px] font-semibold text-primary/90 mb-1">
                                                You might be referring:
                                              </div>
                                              <div className="flex flex-wrap gap-2">{closestList.map(hostCheckbox)}</div>
                                            </div>
                                            <div>
                                              <div className="w-full text-[11px] font-semibold text-base-content/70 mb-1">
                                                Devices not found?
                                              </div>
                                              <div className="flex flex-wrap gap-2">{otherList.map(hostCheckbox)}</div>
                                            </div>
                                          </div>
                                        );
                                      }
                                      return (
                                        <div className="flex flex-wrap gap-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-52 overflow-y-auto">
                                          {candidateDevices.map((host) => {
                                            const checked = cur.includes(host);
                                            return (
                                              <label key={`${vk}-${host}`} className="flex items-center gap-1.5 cursor-pointer">
                                                <input
                                                  type="checkbox"
                                                  checked={checked}
                                                  onChange={() => toggleHost(host)}
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
                                    f.options.length > 0 &&
                                    f.options.every((x) => typeof x === 'number') ? (
                                      <div className="flex flex-wrap gap-2 rounded border border-base-300 bg-base-300/50 p-2 max-h-52 overflow-y-auto">
                                        {(f.options as number[]).map((n) => {
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

                                      if (normalizedField === 'ip_address') {
                                        const subnetField = paramsDisambiguation.fields.find(
                                          (sf) =>
                                            String(sf.field || '').toLowerCase() === 'subnet_mask' &&
                                            sf.secondary_intent_index === f.secondary_intent_index,
                                        );
                                        const subnetKey = subnetField ? previewFieldValueKey(subnetField) : null;
                                        const subnetValue = subnetKey ? paramsDisambiguationValues[subnetKey] : '';
                                        const subnetText =
                                          typeof subnetValue === 'number'
                                            ? String(subnetValue)
                                            : String(subnetValue ?? '').replace('/', '').trim();
                                        return (
                                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-1">
                                            <div>
                                              <div className="text-xs text-base-content/60 mb-1">IP address</div>
                                              <input
                                                type="text"
                                                className="input input-sm input-bordered w-full font-mono"
                                                placeholder="192.168.10.1"
                                                value={String((paramsDisambiguationValues[vk] ?? '') as string | number)}
                                                onChange={(e) =>
                                                  setParamsDisambiguationValues((prev) => ({
                                                    ...prev,
                                                    [vk]: e.target.value,
                                                  }))
                                                }
                                              />
                                            </div>
                                            <div>
                                              <div className="text-xs text-base-content/60 mb-1">Subnet mask</div>
                                              <div className="flex items-center gap-2 relative group">
                                                <div className="flex items-center justify-center h-8 px-2 rounded-l bg-base-300 border border-base-content/20 text-base-content/60 font-mono text-sm">
                                                  /
                                                </div>
                                                <input
                                                  type="number"
                                                  min={0}
                                                  max={32}
                                                  className="input input-sm input-bordered flex-1 rounded-l-none font-mono"
                                                  placeholder="0-32"
                                                  value={subnetText}
                                                  onChange={(e) => {
                                                    if (!subnetKey) return;
                                                    const v = e.target.value;
                                                    const n = v === '' ? '' : parseInt(v, 10);
                                                    if (v === '' || (!isNaN(n as number) && (n as number) >= 0 && (n as number) <= 32)) {
                                                      setParamsDisambiguationValues((prev) => ({
                                                        ...prev,
                                                        [subnetKey]: n,
                                                      }));
                                                    }
                                                  }}
                                                  onKeyDown={(e) => {
                                                    if (['e', 'E', '+', '-', '.'].includes(e.key)) {
                                                      e.preventDefault();
                                                    }
                                                  }}
                                                />
                                              </div>
                                            </div>
                                          </div>
                                        );
                                      }

                                      if (normalizedField === 'network_address') {
                                        return (
                                          <input
                                            type="text"
                                            className="input input-sm input-bordered w-full font-mono mt-1"
                                            placeholder="192.168.1.0"
                                            value={String((paramsDisambiguationValues[vk] ?? '') as string | number)}
                                            onChange={(e) =>
                                              setParamsDisambiguationValues((prev) => ({
                                                ...prev,
                                                [vk]: sanitizeIPv4Input(e.target.value),
                                              }))
                                            }
                                          />
                                        );
                                      }

                                      if (normalizedField === 'destination') {
                                        const ipKey = `${vk}__ip`;
                                        const pfxKey = `${vk}__pfx`;
                                        const rawCombined = String((paramsDisambiguationValues[vk] ?? '') as string | number).trim();
                                        const [rawIpPart, rawPfxPart] = rawCombined.split('/');
                                        const ipState = String((paramsDisambiguationValues[ipKey] ?? rawIpPart ?? '') as string).trim();
                                        const pfxStateRaw = paramsDisambiguationValues[pfxKey];
                                        const pfxState =
                                          typeof pfxStateRaw === 'number'
                                            ? String(pfxStateRaw)
                                            : String(pfxStateRaw ?? (rawPfxPart ?? '')).replace('/', '').trim();

                                        const setCombined = (ip: string, pfx: string) => {
                                          const ipClean = String(ip || '').trim();
                                          const pfxClean = String(pfx || '').replace('/', '').trim();
                                          const combined = ipClean && pfxClean !== '' ? `${ipClean}/${pfxClean}` : ipClean;
                                          setParamsDisambiguationValues((prev) => ({
                                            ...prev,
                                            [ipKey]: ipClean,
                                            [pfxKey]: pfxClean === '' ? '' : parseInt(pfxClean, 10),
                                            [vk]: combined,
                                          }));
                                        };

                                        return (
                                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-1">
                                            <div>
                                              <div className="text-xs text-base-content/60 mb-1">Network address</div>
                                              <input
                                                type="text"
                                                className="input input-sm input-bordered w-full font-mono"
                                                placeholder="172.16.0.0"
                                                value={ipState}
                                                onChange={(e) => {
                                                  const ip = sanitizeIPv4Input(e.target.value);
                                                  setCombined(ip, pfxState);
                                                }}
                                              />
                                            </div>
                                            <div>
                                              <div className="text-xs text-base-content/60 mb-1">Prefix length</div>
                                              <div className="flex items-center gap-2 relative group">
                                                <div className="flex items-center justify-center h-8 px-2 rounded-l bg-base-300 border border-base-content/20 text-base-content/60 font-mono text-sm">
                                                  /
                                                </div>
                                                <input
                                                  type="number"
                                                  min={0}
                                                  max={32}
                                                  className="input input-sm input-bordered flex-1 rounded-l-none font-mono"
                                                  placeholder="0-32"
                                                  value={pfxState}
                                                  onChange={(e) => {
                                                    const v = e.target.value;
                                                    const n = v === '' ? '' : parseInt(v, 10);
                                                    if (
                                                      v === '' ||
                                                      (!isNaN(n as number) && (n as number) >= 0 && (n as number) <= 32)
                                                    ) {
                                                      setCombined(ipState, v);
                                                    }
                                                  }}
                                                  onKeyDown={(e) => {
                                                    if (['e', 'E', '+', '-', '.'].includes(e.key)) {
                                                      e.preventDefault();
                                                    }
                                                  }}
                                                />
                                              </div>
                                            </div>
                                          </div>
                                        );
                                      }

                                      if (normalizedField === 'prefix_length' || normalizedField === 'subnet_mask') {
                                        return (
                                          <div className="flex items-center gap-2 relative group mt-1">
                                            <div className="flex items-center justify-center h-8 px-2 rounded-l bg-base-300 border border-base-content/20 text-base-content/60 font-mono text-sm">
                                              /
                                            </div>
                                            <input
                                              type="number"
                                              min={0}
                                              max={32}
                                              className="input input-sm input-bordered flex-1 rounded-l-none font-mono"
                                              placeholder="0-32"
                                              value={String((paramsDisambiguationValues[vk] ?? '') as string | number)}
                                              onChange={(e) => {
                                                const v = e.target.value;
                                                const n = v === '' ? '' : parseInt(v, 10);
                                                if (v === '' || (!isNaN(n as number) && (n as number) >= 0 && (n as number) <= 32)) {
                                                  setParamsDisambiguationValues((prev) => ({
                                                    ...prev,
                                                    [vk]: n,
                                                  }));
                                                }
                                              }}
                                              onKeyDown={(e) => {
                                                if (['e', 'E', '+', '-', '.'].includes(e.key)) {
                                                  e.preventDefault();
                                                }
                                              }}
                                            />
                                            <div className="absolute right-3 top-1/2 -translate-y-1/2 text-xs opacity-0 group-focus-within:opacity-40 pointer-events-none transition-opacity text-base-content/70">
                                              Range: 0-32
                                            </div>
                                          </div>
                                        );
                                      }

                                      if (normalizedField === 'next_hop') {
                                        const modeKey = `${vk}__mode`;
                                        const devKey = `${vk}__device`;
                                        const modeRaw = String((paramsDisambiguationValues[modeKey] ?? '') as string).trim().toLowerCase();
                                        const mode = modeRaw === 'interface' ? 'interface' : 'ip';

                                        const selectedForTargetDevices = Object.entries(paramsDisambiguationValues)
                                          .find(([key]) => key.startsWith('target_devices'))?.[1];
                                        const selectedTargets: string[] = Array.isArray(selectedForTargetDevices)
                                          ? selectedForTargetDevices.map((x) => String(x).trim()).filter(Boolean)
                                          : typeof selectedForTargetDevices === 'string'
                                            ? selectedForTargetDevices.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
                                            : previewLockedTargetHosts.length > 0
                                              ? previewLockedTargetHosts
                                              : Array.from(selectedDevices);

                                        const candidateDevices =
                                          selectedTargets.length > 0
                                            ? plannedPickerDevices.filter((d) => selectedTargets.includes(d.hostname))
                                            : plannedPickerDevices;
                                        const chosenDevice =
                                          String((paramsDisambiguationValues[devKey] ?? '') as string).trim() ||
                                          (candidateDevices.length === 1 ? candidateDevices[0]!.hostname : '');
                                        const deviceObj = candidateDevices.find((d) => d.hostname === chosenDevice) || candidateDevices[0];
                                        const ifaceOptions = (deviceObj?.interfaces || []).map((i) => String(i.name || '').trim()).filter(Boolean);

                                        const curNextHop = String((paramsDisambiguationValues[vk] ?? '') as string).trim();
                                        const curIp = curNextHop.includes(':') && mode === 'ip' ? '' : curNextHop.split('/')[0] || '';
                                        const curIface =
                                          mode === 'interface'
                                            ? (curNextHop.includes(':') ? curNextHop.split(':')[1] : '')
                                            : '';

                                        return (
                                          <div className="space-y-2 mt-1">
                                            <div className="flex flex-wrap gap-2">
                                              <button
                                                type="button"
                                                className={`btn btn-xs ${mode === 'ip' ? 'btn-primary' : 'btn-ghost'}`}
                                                onClick={() => {
                                                  setParamsDisambiguationValues((prev) => {
                                                    const next = { ...prev, [modeKey]: 'ip' as any };
                                                    const v = String((next[vk] ?? '') as string).trim();
                                                    if (v.includes(':')) next[vk] = '';
                                                    return next;
                                                  });
                                                }}
                                              >
                                                Next-hop IP
                                              </button>
                                              <button
                                                type="button"
                                                className={`btn btn-xs ${mode === 'interface' ? 'btn-primary' : 'btn-ghost'}`}
                                                onClick={() => {
                                                  setParamsDisambiguationValues((prev) => {
                                                    const next = { ...prev, [modeKey]: 'interface' as any };
                                                    const v = String((next[vk] ?? '') as string).trim();
                                                    if (v && !v.includes(':')) next[vk] = '';
                                                    return next;
                                                  });
                                                }}
                                              >
                                                Egress interface
                                              </button>
                                            </div>

                                            {mode === 'ip' ? (
                                              <div className="grid grid-cols-1 gap-2">
                                                <div>
                                                  <div className="text-xs text-base-content/60 mb-1">IP address</div>
                                                  <input
                                                    type="text"
                                                    className="input input-sm input-bordered w-full font-mono"
                                                    placeholder="10.10.1.1"
                                                    value={String(curIp)}
                                                    onChange={(e) => {
                                                      const raw = sanitizeIPv4Input(e.target.value);
                                                      setParamsDisambiguationValues((prev) => ({
                                                        ...prev,
                                                        [vk]: raw,
                                                      }));
                                                    }}
                                                  />
                                                </div>
                                              </div>
                                            ) : (
                                              <div className="space-y-2">
                                                {candidateDevices.length > 1 ? (
                                                  <div>
                                                    <div className="text-xs text-base-content/60 mb-1">Target device</div>
                                                    <select
                                                      className="select select-sm select-bordered w-full font-mono"
                                                      value={chosenDevice}
                                                      onChange={(e) => {
                                                        const host = String(e.target.value || '').trim();
                                                        setParamsDisambiguationValues((prev) => ({
                                                          ...prev,
                                                          [devKey]: host,
                                                          [vk]: '',
                                                        }));
                                                      }}
                                                    >
                                                      <option value="" disabled>
                                                        Select device…
                                                      </option>
                                                      {candidateDevices.map((d) => (
                                                        <option key={`${vk}-dev-${d.hostname}`} value={d.hostname}>
                                                          {d.hostname}
                                                        </option>
                                                      ))}
                                                    </select>
                                                  </div>
                                                ) : chosenDevice ? (
                                                  <div className="text-xs text-base-content/60">
                                                    Device: <span className="font-mono">{chosenDevice}</span>
                                                  </div>
                                                ) : null}

                                                <div>
                                                  <div className="text-xs text-base-content/60 mb-1">Egress interface</div>
                                                  <select
                                                    className="select select-sm select-bordered w-full font-mono"
                                                    value={curIface}
                                                    onChange={(e) => {
                                                      const iface = String(e.target.value || '').trim();
                                                      const host = chosenDevice || deviceObj?.hostname || '';
                                                      const qualified = host && iface ? `${host}:${iface}` : '';
                                                      setParamsDisambiguationValues((prev) => ({
                                                        ...prev,
                                                        [devKey]: host,
                                                        [vk]: qualified,
                                                      }));
                                                    }}
                                                    disabled={!deviceObj || ifaceOptions.length === 0}
                                                  >
                                                    <option value="" disabled>
                                                      {deviceObj ? 'Select interface…' : 'Select device first…'}
                                                    </option>
                                                    {ifaceOptions.map((name) => (
                                                      <option key={`${vk}-iface-${name}`} value={name}>
                                                        {name}
                                                      </option>
                                                    ))}
                                                  </select>
                                                  {!deviceObj || ifaceOptions.length === 0 ? (
                                                    <div className="text-[11px] text-warning-content/80 mt-1">
                                                      No planned interfaces found for this device. Refresh planned topology/local DB and try again.
                                                    </div>
                                                  ) : null}
                                                </div>
                                              </div>
                                            )}
                                          </div>
                                        );
                                      }

                                      return (
                                        <input
                                          type={
                                            f.field === 'vlan_id' ||
                                            f.field === 'process_id' ||
                                            f.field === 'area' ||
                                            f.field === 'access_vlan' ||
                                            f.field === 'native_vlan'
                                              ? 'number'
                                              : 'text'
                                          }
                                          className="input input-sm input-bordered w-full font-mono mt-1"
                                          placeholder={
                                            f.field === 'vlan_id' || f.field === 'access_vlan' || f.field === 'native_vlan'
                                              ? '1-4094'
                                              : ''
                                          }
                                          value={String((paramsDisambiguationValues[vk] ?? '') as string | number)}
                                          onChange={(e) => {
                                            const raw = e.target.value;
                                        const fl = String(f.field || '').toLowerCase();
                                            const v =
                                              f.field === 'destination'
                                                ? sanitizeIPv4CidrInput(raw)
                                                : f.field === 'next_hop'
                                                  ? String(raw || '').trim()
                                              : (
                                                fl === '_chat_acl_source' ||
                                                fl === '_chat_acl_destination' ||
                                                fl === '_chat_acl_source_wildcard' ||
                                                fl === '_chat_acl_dest_wildcard'
                                              )
                                                ? sanitizeIPv4Input(raw)
                                                : raw;
                                            setParamsDisambiguationValues((prev) => ({
                                              ...prev,
                                              [vk]:
                                                f.field === 'vlan_id' ||
                                                f.field === 'process_id' ||
                                              f.field === 'area' ||
                                                f.field === 'access_vlan' ||
                                                f.field === 'native_vlan'
                                                  ? v === ''
                                                    ? ''
                                                    : parseInt(v, 10)
                                              : (
                                                fl === '_chat_acl_seq' ||
                                                fl === '_chat_acl_source_port' ||
                                                fl === '_chat_acl_destination_port'
                                              )
                                                ? String(v || '').replace(/[^\d]/g, '')
                                                : v,
                                            }));
                                          }}
                                      onKeyDown={(e) => {
                                        const fl = String(f.field || '').toLowerCase();
                                        if (
                                          fl === '_chat_acl_seq' ||
                                          fl === '_chat_acl_source_port' ||
                                          fl === '_chat_acl_destination_port'
                                        ) {
                                          if (['e', 'E', '+', '-', '.'].includes(e.key)) {
                                            e.preventDefault();
                                          }
                                        }
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
                              setParamsWizardNextLoading(false);
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
                                className="btn btn-sm btn-primary gap-1.5"
                                disabled={
                                  paramsWizardNextLoading ||
                                  !paramsDisambiguation.fields[paramsDisambiguationStep] ||
                                  paramsWizardValueIncomplete(
                                    paramsDisambiguation.fields[paramsDisambiguationStep],
                                    paramsDisambiguationValues as Record<
                                      string,
                                      string | number | string[] | number[] | undefined
                                    >,
                                  )
                                }
                                onClick={() => void handleParamsWizardNext()}
                              >
                                {paramsWizardNextLoading ? (
                                  <>
                                    <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                                    Loading…
                                  </>
                                ) : (
                                  'Next'
                                )}
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="btn btn-sm btn-primary"
                                disabled={
                                  paramsDisambiguation.fields.some((f) =>
                                    paramsWizardValueIncomplete(
                                      f,
                                      paramsDisambiguationValues as Record<
                                        string,
                                        string | number | string[] | number[] | undefined
                                      >,
                                    ),
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
                                    // Special handling: next_hop wizard can be IP-mode or interface-mode.
                                    // - IP-mode: keep plain IPv4 (ignore optional /prefix UI)
                                    // - interface-mode: keep qualified token "host:Interface"
                                    if (String(f.field || '').toLowerCase() === 'next_hop') {
                                      const modeKey = `${vk}__mode`;
                                      const modeRaw = String((paramsDisambiguationValues[modeKey] ?? '') as string)
                                        .trim()
                                        .toLowerCase();
                                      const mode = modeRaw === 'interface' ? 'interface' : 'ip';
                                      if (mode === 'ip') {
                                        const ip = String(v || '').trim().split('/')[0];
                                        if (!ip) return;
                                        if (f.secondary_intent_index !== undefined && f.secondary_intent_index !== null) {
                                          const sidx = f.secondary_intent_index;
                                          if (!secondaryPatches[sidx]) secondaryPatches[sidx] = {};
                                          secondaryPatches[sidx][f.field] = ip;
                                        } else {
                                          primaryParams[f.field] = ip;
                                        }
                                        return;
                                      }
                                    }
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
                                      if (f.field === 'acl_name') {
                                        (primaryParams as any)._user_supplied_acl_name = true;
                                      }
                                      if (f.field === 'acl_type') {
                                        (primaryParams as any)._user_supplied_acl_type = true;
                                      }
                                    }
                                  });
                                  // ACL atomic wizard fields → build `rules[]` and strip chat-only fields.
                                  try {
                                    const seqRaw = String((primaryParams as any)._chat_acl_seq ?? '').trim();
                                    const seq = /^\d+$/.test(seqRaw) ? parseInt(seqRaw, 10) : 10;
                                    const a = String((primaryParams as any)._chat_acl_action ?? '').trim().toLowerCase();
                                    const proto = String((primaryParams as any)._chat_acl_protocol ?? '').trim().toLowerCase();
                                    const srcRaw = String((primaryParams as any)._chat_acl_source ?? '').trim();
                                    const src = srcRaw.toLowerCase() === 'any' ? 'any' : srcRaw;
                                    const swc = String((primaryParams as any)._chat_acl_source_wildcard ?? '').trim();
                                    const sportRaw = String((primaryParams as any)._chat_acl_source_port ?? '').trim();
                                    const dstRaw = String((primaryParams as any)._chat_acl_destination ?? '').trim();
                                    const dst = dstRaw.toLowerCase() === 'any' ? 'any' : dstRaw;
                                    const dwc = String((primaryParams as any)._chat_acl_dest_wildcard ?? '').trim();
                                    const dportRaw = String((primaryParams as any)._chat_acl_destination_port ?? '').trim();
                                    const hasAnyAclAtom = Boolean(seqRaw || a || proto || src || dst || swc || dwc || sportRaw || dportRaw);
                                    if (hasAnyAclAtom) {
                                      const rule: Record<string, unknown> = {
                                        seq,
                                        action: a || 'deny',
                                        protocol: proto || 'ip',
                                        source: src || 'any',
                                        destination: dst || 'any',
                                      };
                                      if (src !== 'any' && swc) rule.source_wildcard = swc;
                                      if (dst !== 'any' && dwc) rule.dest_wildcard = dwc;
                                      if ((proto === 'tcp' || proto === 'udp') && sportRaw) {
                                        rule.source_port = sportRaw;
                                      }
                                      if ((proto === 'tcp' || proto === 'udp') && dportRaw) {
                                        rule.port = dportRaw;
                                      }
                                      (primaryParams as any).rules = [rule];
                                      (primaryParams as any)._user_supplied_acl_rules = true;
                                      delete (primaryParams as any)._chat_acl_seq;
                                      delete (primaryParams as any)._chat_acl_action;
                                      delete (primaryParams as any)._chat_acl_protocol;
                                      delete (primaryParams as any)._chat_acl_source;
                                      delete (primaryParams as any)._chat_acl_source_wildcard;
                                      delete (primaryParams as any)._chat_acl_source_port;
                                      delete (primaryParams as any)._chat_acl_destination;
                                      delete (primaryParams as any)._chat_acl_dest_wildcard;
                                      delete (primaryParams as any)._chat_acl_destination_port;
                                    }
                                  } catch (_) {
                                    // ignore
                                  }
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
                                    const scopedPrev = previewAccumulatedIntentText === intentText ? previewAccumulatedParams : {};
                                    const merged: Record<string, unknown> = {
                                      ...parsedParams,
                                      ...scopedPrev,
                                      ...params,
                                    };
                                    // OSPF wizard uses network_address + prefix_length + area; drop stale
                                    // networks[] from earlier previews/LLM so the API does not keep both.
                                    const na = String((merged as any).network_address ?? '').trim();
                                    const pl = (merged as any).prefix_length;
                                    const ar = (merged as any).area;
                                    const atomicOspf =
                                      na &&
                                      pl !== undefined &&
                                      pl !== null &&
                                      String(pl).trim() !== '' &&
                                      ar !== undefined &&
                                      ar !== null &&
                                      String(ar).trim() !== '';
                                    if (atomicOspf) {
                                      delete (merged as any).networks;
                                      delete (merged as any).ospf_networks;
                                    }
                                    // Preserve user-picked target devices from this wizard round.
                                    // Previously, previewLockedTargetHosts could overwrite `target_devices`
                                    // and force a stale host (e.g. Acc_Cisco_1) back into the request.
                                    const mergedTargetsFromWizard = Array.isArray((merged as any).target_hosts)
                                      ? ((merged as any).target_hosts as unknown[])
                                          .map((x) => String(x || '').trim())
                                          .filter(Boolean)
                                      : [];
                                    const th =
                                      mergedTargetsFromWizard.length > 0
                                        ? mergedTargetsFromWizard
                                        : previewLockedTargetHosts.length > 0
                                          ? previewLockedTargetHosts
                                          : (safeParsedHostsForPreview(intentText) ?? []);
                                    if (th.length > 0) {
                                      merged.target_hosts = th;
                                    }
                                    setPreviewAccumulatedParams(merged);
                                    setPreviewAccumulatedIntentText(intentText);
                                    const res = await ansibleApi.previewIntent({
                                      intent_description: intentText,
                                      target_hosts: th.length > 0 ? th : undefined,
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
                                    } else {
                                      Toast.error('Could not continue with the missing parameters', describeError(err, 'Preview refresh failed.'));
                                    }
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

          {/* Textarea Area */}
          <div className="p-2 pb-0">
            <textarea
              ref={textareaRef}
              placeholder={attachedFiles.length ? 'Add a message or send file as-is…' : 'Ask Ansible AI or drop a file…'}
              value={input}
              onChange={e => {
                setInput(e.target.value);
                if (commandHistoryIndex >= 0) {
                  setCommandHistoryIndex(-1);
                  historyDraftBeforeNavigationRef.current = '';
                }
              }}
              onKeyDown={handleKeyDown}
              disabled={isGenerating}
              rows={1}
              className="w-full bg-transparent border-none outline-none resize-none text-sm text-base-content placeholder-base-content/50 min-h-[38px] max-h-[250px] leading-md overflow-hidden"
            />
          </div>

          {/* Action Row - Segments similar to the example */}
          <div className="flex items-center justify-between px-3 py-2 border-t border-base-content/5 bg-base-200/50">
            <div className="flex items-center gap-2">
              {/* Restore Paperclip Attachment Button */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                title="Attach File (📎)"
                className="btn btn-ghost btn-xs btn-square opacity-60 hover:opacity-100 hover:text-primary"
              >
                <Paperclip className="w-4 h-4" />
              </button>

              {/* Model Selector Trigger */}
              <div ref={modelDropdownRef} className="relative">
                <button
                  type="button"
                  onClick={() => setModelDropdownOpen((o) => !o)}
                  className={`flex items-center gap-1.5 px-2 py-1.5 rounded-md transition-all text-[11px] font-medium
                    ${modelDropdownOpen ? 'bg-base-content/10 text-primary' : 'hover:bg-base-content/5 text-base-content/70 hover:text-base-content'}
                  `}
                >
                  <Sparkles className="w-3 h-3 text-primary" />
                  <span className="truncate max-w-[120px]">{currentModelLabel}</span>
                  <ChevronDown className={`w-3 h-3 transition-transform duration-200 ${modelDropdownOpen ? 'rotate-180' : ''}`} />
                </button>

                {modelDropdownOpen && (
                  <div
                    style={{
                      position: 'absolute',
                      bottom: 'calc(100% + 8px)',
                      left: 0,
                      zIndex: 300,
                      width: 210,
                      backgroundColor: '#18181b', // Solid zinc-900 (fully opaque)
                      border: '1px solid #3f3f46', // Solid zinc-700 border
                      borderRadius: 10,
                      boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.8), 0 10px 10px -5px rgba(0, 0, 0, 0.8)',
                      display: 'flex',
                      flexDirection: 'column',
                      overflow: 'hidden',
                    }}
                    className="animate-in fade-in zoom-in-95 duration-100"
                  >
                    <div 
                      style={{ backgroundColor: '#27272a' }} // Solid zinc-800 header
                      className="px-3 py-2 text-[10px] font-semibold text-zinc-400 border-b border-white/5 uppercase tracking-wider"
                    >
                      Model
                    </div>
                    <div 
                      style={{ backgroundColor: '#18181b' }} // Solid zinc-900 (fully opaque list)
                      className="overflow-y-auto max-h-60 p-1 custom-scrollbar"
                    >
                      {filteredModelOptions.map((opt) => {
                        const isSelected = ansibleModel.provider === opt.providerName && ansibleModel.model === opt.modelName;
                        return (
                          <button
                            key={`${opt.providerName}::${opt.modelName}`}
                            type="button"
                            onClick={() => handleSelectModel(opt.providerName, opt.modelName)}
                            className={`flex items-center gap-2 w-full text-left px-2.5 py-1.5 rounded-md text-[11px] transition-colors mb-0.5
                              ${isSelected 
                                ? 'bg-primary text-primary-content font-semibold' 
                                : 'hover:bg-base-content/10 text-base-content/70 hover:text-base-content'
                              }
                            `}
                          >
                            <span className="flex-1 truncate">{opt.displayLabel}</span>
                            <span className={`text-[8px] px-1 rounded uppercase font-bold ${
                              isSelected ? 'bg-primary-content/20' : 'bg-base-content/10 text-base-content/40'
                            }`}>
                              {opt.providerName === 'ollama' ? 'Local' : 'Cloud'}
                            </span>
                            {isSelected && <Check className="w-3.5 h-3.5" />}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Right Group: Action Buttons */}
            <div className="flex items-center gap-2">
              {isGenerating ? (
                <button
                  type="button"
                  onClick={handleStop}
                  title="Stop generation"
                  className="btn btn-ghost btn-xs btn-square text-error hover:bg-error/10"
                >
                  <Square className="w-3.5 h-3.5 fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleSend}
                  disabled={!input.trim() && attachedFiles.length === 0}
                  title="Send (Enter)"
                  className={`btn btn-xs btn-square rounded-lg transition-all ${
                    (input.trim() || attachedFiles.length > 0)
                      ? 'btn-primary shadow-sm'
                      : 'btn-ghost text-primary cursor-not-allowed'
                  }`}
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        <p className="text-[10px] text-base-content/40 text-center mt-1">Enter to send · Shift+Enter newline · Up/Down command history · 📎 attach or drag from file tree</p>
      </div>
    </div>
  );
};

export default AnsibleChatPanel;
