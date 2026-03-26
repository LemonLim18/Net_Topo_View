import { useState, useEffect, useCallback, useRef } from 'react';
import { Editor } from '@monaco-editor/react';
import ReactMarkdown from 'react-markdown';
import {
  X,
  Loader2,
  FileCode,
  Check,
  ChevronRight,
  Sparkles,
  AlertCircle,
  Plus,
  ChevronDown,
  ChevronUp,
  Lightbulb,
  Square,
} from 'lucide-react';
import { ansibleApi } from '../../src/services/api';
import { Toast } from '../../src/utils/swal';
import {
  buildPlannedInventoryPreselectChips,
  extractTargetInterfaceNamesFromAuthoritativeParams,
  extractTargetInterfaceNamesFromParsedIntent,
  preselectPlannedInventoryInterfaces,
} from '../../src/utils/interfaceInventoryPreselect';
import { parseNativeVlanNumberFromPrompt, userPromptSuggestsNativeVlanIntent } from '../../src/utils/vlanPromptHints';
import type { AnsibleGeneratedArtifactFile } from '../../typesLLM';

const FEATURES_DISPLAY: Record<string, string> = {
  vlan: 'VLAN',
  interface: 'Interface',
  acl: 'ACL',
  static_route: 'Static route',
  ospf: 'OSPF',
  qos: 'QoS',
  routing: 'Routing',
  general: 'General',
};

type AnsibleApiErrorDetail = {
  message?: string;
  issues?: string[];
  validation_notice?: string;
  interfaces_disambiguation?: { devices?: unknown[] };
};

/** Build user-visible message from fetch error (includes inventory validation from create-and-plan / preview). */
function formatAnsibleApiError(e: unknown): string {
  const err = e as { message?: string; detail?: AnsibleApiErrorDetail };
  let msg =
    err?.message ||
    (typeof err?.detail === 'object' && err.detail?.message
      ? err.detail.message +
        (Array.isArray(err.detail.issues) && err.detail.issues.length ? '\n' + err.detail.issues.join('\n') : '')
      : String(e));
  const d = err?.detail;
  if (typeof d === 'object' && d?.validation_notice) {
    msg = `${d.validation_notice}\n\n${msg}`;
  }
  if (typeof d === 'object' && d?.interfaces_disambiguation?.devices?.length) {
    msg +=
      '\n\nPick interface names that exist in planned inventory (adjust the Interface list in this dialog when shown).';
  }
  return msg;
}

const isWildcardInterfaceToken = (v: string): boolean => {
  const t = String(v || '').trim().toLowerCase();
  return t === 'all' || t === '*' || t === 'any' || t === 'every';
};

const expandWildcardInterfacesFromPlannedPreview = (
  current: string[],
  planned: { devices?: { hostname: string; interfaces: { name: string }[] }[] } | null,
): string[] => {
  if (!Array.isArray(current) || current.length === 0) return current;
  if (!current.some((x) => isWildcardInterfaceToken(x))) return current;
  const devices = planned?.devices || [];
  if (!devices.length) return current;
  const expanded = Array.from(
    new Set(
      devices.flatMap((d) =>
        (d.interfaces || [])
          .map((i) => String(i.name || '').trim())
          .filter(Boolean),
      ),
    ),
  );
  return expanded.length > 0 ? expanded : current;
};

/** All L2 interface objects from primary + secondary (order: primary first, then secondaries). */
function collectAllInterfaceObjectsFromParsedIntent(
  parsed: Record<string, unknown> | null | undefined,
): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  if (!parsed) return out;
  const p = (parsed as { parameters?: Record<string, unknown> }).parameters ?? {};
  if (Array.isArray(p.interfaces)) {
    for (const x of p.interfaces) {
      if (x && typeof x === 'object') out.push(x as Record<string, unknown>);
    }
  }
  const secs = (parsed as { secondary_intents?: unknown }).secondary_intents;
  if (Array.isArray(secs)) {
    for (const sec of secs) {
      if (!sec || typeof sec !== 'object') continue;
      const sp = (sec as { parameters?: Record<string, unknown> }).parameters ?? {};
      if (Array.isArray(sp.interfaces)) {
        for (const x of sp.interfaces) {
          if (x && typeof x === 'object') out.push(x as Record<string, unknown>);
        }
      }
    }
  }
  return out;
}

/** True when the user text clearly asks for a trunk port (overrides wrong LLM mode: access). */
function userPromptSuggestsTrunkPortIntent(text: string): boolean {
  const t = (text || '').trim().toLowerCase();
  if (!t) return false;
  if (/\btrunk\s+port\b/.test(t)) return true;
  if (/\btrunk\s+(interface|intf|if)\b/.test(t)) return true;
  if (/\bon\s+a\s+trunk\b/.test(t)) return true;
  if (/\ballow\s+(it\s+)?on\s+trunk\b/.test(t)) return true;
  if (/\btrunk\s+VLANs?\b/.test(t)) return true;
  if (/\btrunk\b/.test(t) && /\b(port|interface|gi|gigabit|ethernet|eth)\b/.test(t)) return true;
  return false;
}

/** Initial Playbook Studio binding mode + VLAN fields from preview + natural language (fixes LLM access vs user trunk). */
function inferInterfaceBindingFromParsedIntent(
  parsed: Record<string, unknown> | null | undefined,
  intentDescription: string,
): {
  mode: 'no' | 'access' | 'trunk';
  accessVlan: number;
  allowedCsv: string;
  nativeVlan: number | null;
} {
  const base = { mode: 'no' as const, accessVlan: 1, allowedCsv: '', nativeVlan: null as number | null };
  if (!parsed) return base;
  const params = (parsed as { parameters?: Record<string, unknown> }).parameters ?? {};
  const vid = params.vlan_id;
  const vlanNum =
    typeof vid === 'number'
      ? vid
      : vid !== undefined && vid !== null && vid !== ''
        ? parseInt(String(vid), 10)
        : NaN;

  const allIfaces = collectAllInterfaceObjectsFromParsedIntent(parsed);
  const anyTrunk = allIfaces.find((i) => String(i.mode || '').toLowerCase() === 'trunk');
  const anyAccess = allIfaces.find((i) => String(i.mode || '').toLowerCase() === 'access');
  const iface = anyTrunk || anyAccess || allIfaces[0] || null;

  const buildTrunkFromIface = (i: Record<string, unknown>) => {
    const tv = i.trunk_vlans ?? (i as { allowed_vlans?: unknown }).allowed_vlans;
    let csv = '';
    if (Array.isArray(tv)) csv = tv.map((x) => String(x)).join(',');
    else if (typeof tv === 'string') csv = tv;
    if (!csv && !Number.isNaN(vlanNum)) csv = String(vlanNum);
    const nv = i.native_vlan;
    let native: number | null = null;
    if (typeof nv === 'number' && !Number.isNaN(nv)) {
      native = nv;
    } else if (nv !== undefined && nv !== null && nv !== '') {
      const p = parseInt(String(nv), 10);
      native = Number.isNaN(p) ? null : p;
    }
    return {
      mode: 'trunk' as const,
      accessVlan: Number.isNaN(vlanNum) ? 1 : vlanNum,
      allowedCsv: csv,
      nativeVlan: native,
    };
  };

  const buildAccessFromIface = (i: Record<string, unknown>) => {
    const av = i.access_vlan;
    const avn =
      typeof av === 'number'
        ? av
        : av !== undefined && av !== null && av !== ''
          ? parseInt(String(av), 10)
          : NaN;
    return {
      mode: 'access' as const,
      accessVlan: !Number.isNaN(avn) ? avn : Number.isNaN(vlanNum) ? 1 : vlanNum,
      allowedCsv: '',
      nativeVlan: null,
    };
  };

  // 1) Any parsed interface is explicitly trunk → trunk
  if (anyTrunk) {
    return buildTrunkFromIface(anyTrunk);
  }

  // 2) User said "trunk port" but LLM used access → trust the prompt when VLAN id exists
  if (userPromptSuggestsTrunkPortIntent(intentDescription) && !Number.isNaN(vlanNum)) {
    return {
      mode: 'trunk' as const,
      accessVlan: vlanNum,
      allowedCsv: String(vlanNum),
      nativeVlan: null,
    };
  }

  // 3) "Native VLAN" / "set native VLAN N" → trunk + native_vlan (not access VLAN on the port)
  if (userPromptSuggestsNativeVlanIntent(intentDescription)) {
    const nvParsed = parseNativeVlanNumberFromPrompt(intentDescription);
    if (nvParsed !== null) {
      const vidForTrunk = !Number.isNaN(vlanNum) ? vlanNum : nvParsed;
      return {
        mode: 'trunk' as const,
        accessVlan: vidForTrunk,
        allowedCsv: String(vidForTrunk),
        nativeVlan: nvParsed,
      };
    }
    if (!Number.isNaN(vlanNum)) {
      return {
        mode: 'trunk' as const,
        accessVlan: vlanNum,
        allowedCsv: String(vlanNum),
        nativeVlan: null,
      };
    }
  }

  if (!iface) return base;

  const m = String(iface.mode || '').toLowerCase();
  if (m === 'trunk') {
    return buildTrunkFromIface(iface);
  }
  if (m === 'access') {
    return buildAccessFromIface(iface);
  }
  return base;
}

function intentInspectorStatusBadgeClass(status: string): string {
  if (status === 'missing') return 'badge-error badge-outline';
  if (status === 'filled') return 'badge-success badge-outline';
  return 'badge-ghost';
}

function intentInspectorStatusLabel(status: string): string {
  if (status === 'missing') return 'Required — missing';
  if (status === 'filled') return 'Filled';
  if (status === 'optional_empty') return 'Optional — empty';
  return status;
}

function sanitizeIPv4Input(raw: string): string {
  const src = String(raw || '');
  const hasDot = src.includes('.');
  const digitsAndDots = src.replace(/[^\d.]/g, '');
  if (!hasDot) {
    const digits = digitsAndDots.replace(/\./g, '').slice(0, 12);
    const chunks: string[] = [];
    for (let i = 0; i < digits.length; i += 3) chunks.push(digits.slice(i, i + 3));
    return chunks
      .slice(0, 4)
      .map((c) => {
        if (!c) return '';
        const n = parseInt(c, 10);
        if (Number.isNaN(n)) return '';
        return String(Math.max(0, Math.min(255, n)));
      })
      .join('.');
  }
  return digitsAndDots
    .split('.')
    .slice(0, 4)
    .map((p) => {
      const s = p.slice(0, 3);
      if (!s) return '';
      const n = parseInt(s, 10);
      if (Number.isNaN(n)) return '';
      return String(Math.max(0, Math.min(255, n)));
    })
    .join('.');
}

/** Prefer chat wizard / accumulated preview params over LLM-only parsed_intent.parameters. */
function buildSchemaParamValuesFromSources(
  fields: SchemaEditableField[],
  parsedParams: Record<string, unknown>,
  authoritative?: Record<string, unknown> | null,
  fallbackParams?: Record<string, unknown> | null,
): Record<string, string | number> {
  const auth =
    authoritative && typeof authoritative === 'object' ? authoritative : {};
  const fallback =
    fallbackParams && typeof fallbackParams === 'object' ? fallbackParams : {};
  const out: Record<string, string | number> = {};
  for (const f of fields) {
    const key = f.field;
    const a = auth[key];
    const p = parsedParams[key];
    const fb = fallback[key];
    const hasAuth =
      a !== undefined &&
      a !== null &&
      a !== '' &&
      !(typeof a === 'number' && Number.isNaN(a));
    const hasParsed =
      p !== undefined &&
      p !== null &&
      p !== '' &&
      !(typeof p === 'number' && Number.isNaN(p));
    const raw = hasAuth ? a : hasParsed ? p : fb;
    if (raw === undefined || raw === null || raw === '') {
      out[key] = '';
      continue;
    }
    if (f.type === 'number' || key === 'vlan_id' || key === 'process_id') {
      const n = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
      out[key] = Number.isNaN(n) ? '' : n;
    } else if (Array.isArray(raw) || (raw && typeof raw === 'object')) {
      // Keep structured values editable/readable in schema form instead of "[object Object]".
      try {
        out[key] = JSON.stringify(raw);
      } catch {
        out[key] = String(raw);
      }
    } else {
      out[key] = String(raw);
    }
  }
  return out;
}

export interface SchemaEditableField {
  field: string;
  label: string;
  required: boolean;
  type: string;
}

/** Mirrors `build_intent_schema_inspector_payload` from the preview-intent API. */
export interface IntentSchemaInspectorSlice {
  role: string;
  index: number;
  title: string;
  action?: string | null;
  feature?: string | null;
  schema_action?: string | null;
  schema_file?: string | null;
  missing_required?: string[];
  validation_errors?: string[];
  fields?: Array<{
    schema_field: string;
    param_key: string;
    label: string;
    required: boolean;
    status: string;
    value_preview: string;
    question?: string;
  }>;
  note?: string | null;
}

export interface IntentSchemaInspectorPayload {
  slices?: IntentSchemaInspectorSlice[];
  error?: string;
}

export interface PlaybookStudioOverlayProps {
  open: boolean;
  onClose: () => void;
  intentDescription: string;
  initialPreviewResult?: {
    categories: string[];
    references: { doc_name: string; feature: string; vendor: string; path: string }[];
    reasoning: string[];
    policy_approved: boolean;
    target_hosts?: string[];
    reasoning_trace?: { steps: { title: string; detail: string }[] };
    parsed_intent?: Record<string, unknown>;
    schema_editable_fields?: SchemaEditableField[];
    interfaces_for_selection?: { question: string; devices: { hostname: string; interfaces: { name: string }[] }[] };
    secondary_intent_previews?: Array<{ intent_index: number; action: string; feature: string; missing_fields?: string[] }>;
    intent_schema_inspector?: IntentSchemaInspectorPayload;
  } | null;
  onPlaybookReady: (payload: {
    jobId: number;
    content: string;
    name: string;
    entrypointPath?: string | null;
    files?: AnsibleGeneratedArtifactFile[];
  }) => void;
  ansibleModel: { provider: string; model: string; ollama_base_url?: string | null };
  parseIntent: (text: string) => { vendor: string; feature: string; target_hosts: string[]; params?: Record<string, any> };
  /** Chat wizards (devices, params, interfaces) — wins over parsed_intent for form defaults. */
  authoritativeMergedParams?: Record<string, unknown> | null;
}

type Step = 'thinking' | 'confirm' | 'generating' | 'result' | 'error';

export function PlaybookStudioOverlay({
  open,
  onClose,
  intentDescription,
  initialPreviewResult,
  onPlaybookReady,
  ansibleModel,
  parseIntent,
  authoritativeMergedParams = null,
}: PlaybookStudioOverlayProps) {
  const [step, setStep] = useState<Step>('thinking');
  const [categories, setCategories] = useState<string[]>([]);
  const [references, setReferences] = useState<{ doc_name: string; feature: string; vendor: string; path: string }[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<typeof references>([]);
  const [reasoning, setReasoning] = useState<string[]>([]);
  const [policyApproved, setPolicyApproved] = useState(false);
  const [referenceContent, setReferenceContent] = useState<string>('');
  const [generatedContent, setGeneratedContent] = useState<string>('');
  const [generatedJobId, setGeneratedJobId] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showReasoning, setShowReasoning] = useState(true);
  const [newCategory, setNewCategory] = useState('');
  const [availablePaths, setAvailablePaths] = useState<{ path: string; feature: string; vendor: string }[]>([]);
  const [addRefPath, setAddRefPath] = useState('');
  const [targetHosts, setTargetHosts] = useState<string[]>([]);
  const [newTargetHost, setNewTargetHost] = useState('');
  const [targetInterfaces, setTargetInterfaces] = useState<string[]>([]);
  const [newTargetInterface, setNewTargetInterface] = useState('');
  const [schemaEditableFields, setSchemaEditableFields] = useState<SchemaEditableField[]>([]);
  const [schemaParamValues, setSchemaParamValues] = useState<Record<string, string | number>>({});
  const [interfacesForSelection, setInterfacesForSelection] = useState<{ question: string; devices: { hostname: string; interfaces: { name: string }[] }[] } | null>(null);
  const [plannedAllInterfacesPreview, setPlannedAllInterfacesPreview] = useState<{
    devices?: { hostname?: string; interfaces?: { name?: string }[] }[];
    note?: string;
  } | null>(null);
  const [interfaceBindingMode, setInterfaceBindingMode] = useState<'no' | 'access' | 'trunk'>('no');
  const [vlanSelectedInterfaces, setVlanSelectedInterfaces] = useState<Set<string>>(new Set());
  const [vlanAccessVlan, setVlanAccessVlan] = useState<number>(1);
  const [vlanAllowedVlans, setVlanAllowedVlans] = useState('');
  const [vlanNativeVlan, setVlanNativeVlan] = useState<number | null>(null);
  /** Last preview API parsed_intent — used so create-and-plan sends correct feature (avoid heuristic "general"). */
  const [previewParsedIntent, setPreviewParsedIntent] = useState<Record<string, unknown> | null>(null);
  const previewParsedIntentRef = useRef<Record<string, unknown> | null>(null);
  const [intentSchemaInspector, setIntentSchemaInspector] = useState<IntentSchemaInspectorPayload | null>(null);
  /** Skip the first debounced refresh after a fresh preview (avoid duplicate POST on open). */
  const skipNextInventoryRefreshRef = useRef(true);
  useEffect(() => {
    previewParsedIntentRef.current = previewParsedIntent;
  }, [previewParsedIntent]);
  const [resultPayload, setResultPayload] = useState<{
    jobId: number;
    content: string;
    name: string;
    entrypointPath?: string | null;
    files?: AnsibleGeneratedArtifactFile[];
  } | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const runPreview = useCallback(async () => {
    if (!intentDescription.trim()) return;
    setStep('thinking');
    setErrorMessage(null);
    const { vendor, feature, target_hosts } = parseIntent(intentDescription);
    try {
      const res = await ansibleApi.previewIntent({
        intent_description: intentDescription,
        target_hosts: target_hosts?.length ? target_hosts : undefined,
        vendor: vendor || undefined,
        feature: feature || undefined,
        provider: ansibleModel.provider,
        model: ansibleModel.model,
        ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
      });
      setCategories(res.categories || []);
      setReferences(res.references || []);
      setSelectedRefs(res.references || []);
      setReasoning(res.reasoning || []);
      setPolicyApproved(res.policy_approved ?? false);
      {
        const fromPreview = res.target_hosts?.length ? res.target_hosts : target_hosts?.length ? target_hosts : [];
        const rd = (res.parsed_intent as { resolved_devices?: string[] } | null)?.resolved_devices;
        setTargetHosts(Array.isArray(rd) && rd.length > 0 ? rd : fromPreview);
      }
      setTargetInterfaces(
        expandWildcardInterfacesFromPlannedPreview(
          (() => {
            const fromAuth = extractTargetInterfaceNamesFromAuthoritativeParams(
              authoritativeMergedParams as Record<string, unknown> | null | undefined,
            );
            if (fromAuth.length > 0) return fromAuth;
            return extractTargetInterfaceNamesFromParsedIntent(
              (res.parsed_intent as Record<string, unknown>) ?? null,
            );
          })(),
          ((res.parsed_intent as any)?.planned_all_interfaces_preview as any) ?? null,
        ),
      );
      setPlannedAllInterfacesPreview(
        ((res.parsed_intent as any)?.planned_all_interfaces_preview as any) ?? null
      );
      setSchemaEditableFields(res.schema_editable_fields ?? []);
      const params = (res.parsed_intent as any)?.parameters ?? {};
      const parseFallback = (parseIntent(intentDescription)?.params ?? {}) as Record<string, unknown>;
      setSchemaParamValues(
        buildSchemaParamValuesFromSources(
          res.schema_editable_fields ?? [],
          params,
          authoritativeMergedParams,
          parseFallback,
        ),
      );
      setInterfacesForSelection((res as any).interfaces_for_selection ?? null);
      setIntentSchemaInspector((res as { intent_schema_inspector?: IntentSchemaInspectorPayload }).intent_schema_inspector ?? null);
      setPreviewParsedIntent((res.parsed_intent as Record<string, unknown>) ?? null);
      {
        const parsedPi = (res.parsed_intent as Record<string, unknown>) ?? null;
        const inf = inferInterfaceBindingFromParsedIntent(parsedPi, intentDescription);
        setInterfaceBindingMode(inf.mode);
        setVlanAccessVlan(inf.accessVlan);
        setVlanAllowedVlans(inf.allowedCsv);
        setVlanNativeVlan(inf.nativeVlan);
        // Never auto-pick "closest in inventory" suggestions here.
        // If the user/LLM provided an invalid interface (e.g. Gi1/0/1) and inventory suggests Eth0/1,
        // we must show the mismatch instead of silently remapping the selection.
        const chips = buildPlannedInventoryPreselectChips(parsedPi, { skipSuggestions: true });
        setVlanSelectedInterfaces(
          preselectPlannedInventoryInterfaces(
            chips,
            (res as { interfaces_for_selection?: { devices: { hostname: string; interfaces: { name: string }[] }[] } })
              .interfaces_for_selection ?? null,
          ),
        );
      }
      skipNextInventoryRefreshRef.current = true;
      setStep('confirm');
    } catch (e: unknown) {
      setIntentSchemaInspector(null);
      setErrorMessage(formatAnsibleApiError(e));
      setStep('error');
    }
  }, [intentDescription, ansibleModel, parseIntent, authoritativeMergedParams]);

  useEffect(() => {
    if (!open || !intentDescription.trim()) return;
    if (initialPreviewResult) {
      setCategories(initialPreviewResult.categories || []);
      setReferences(initialPreviewResult.references || []);
      setSelectedRefs(initialPreviewResult.references || []);
      const baseReasoning = initialPreviewResult.reasoning || [];
      const secondaryPreviews = initialPreviewResult.secondary_intent_previews;
      const secondaryLine =
        Array.isArray(secondaryPreviews) && secondaryPreviews.length > 0
          ? `**Also detected (secondary intents):** ${secondaryPreviews.map((s) => s.feature || s.action).join(', ')}. This run will generate the primary intent only; you can run a follow-up for the others.`
          : null;
      setReasoning(secondaryLine ? [...baseReasoning, secondaryLine] : baseReasoning);
      setPolicyApproved(initialPreviewResult.policy_approved ?? false);
      {
        const authH = Array.isArray(authoritativeMergedParams?.target_hosts)
          ? (authoritativeMergedParams!.target_hosts as unknown[]).filter(
              (h): h is string => typeof h === 'string' && Boolean(String(h).trim()),
            )
          : [];
        setTargetHosts(
          authH.length > 0
            ? authH
            : initialPreviewResult.target_hosts?.length
              ? initialPreviewResult.target_hosts
              : [],
        );
      }
      setTargetInterfaces(
        expandWildcardInterfacesFromPlannedPreview(
          (() => {
            const fromAuth = extractTargetInterfaceNamesFromAuthoritativeParams(
              authoritativeMergedParams as Record<string, unknown> | null | undefined,
            );
            if (fromAuth.length > 0) return fromAuth;
            return extractTargetInterfaceNamesFromParsedIntent(
              (initialPreviewResult.parsed_intent as Record<string, unknown>) ?? null,
            );
          })(),
          ((initialPreviewResult.parsed_intent as any)?.planned_all_interfaces_preview as any) ?? null,
        ),
      );
      setPlannedAllInterfacesPreview(
        ((initialPreviewResult.parsed_intent as any)?.planned_all_interfaces_preview as any) ?? null
      );
      setSchemaEditableFields(initialPreviewResult.schema_editable_fields ?? []);
      const params = (initialPreviewResult.parsed_intent as any)?.parameters ?? {};
      const parseFallback = (parseIntent(intentDescription)?.params ?? {}) as Record<string, unknown>;
      setSchemaParamValues(
        buildSchemaParamValuesFromSources(
          initialPreviewResult.schema_editable_fields ?? [],
          params,
          authoritativeMergedParams,
          parseFallback,
        ),
      );
      setInterfacesForSelection(initialPreviewResult.interfaces_for_selection ?? null);
      setIntentSchemaInspector(initialPreviewResult.intent_schema_inspector ?? null);
      setPreviewParsedIntent((initialPreviewResult.parsed_intent as Record<string, unknown>) ?? null);
      {
        const parsedPi = (initialPreviewResult.parsed_intent as Record<string, unknown>) ?? null;
        const inf = inferInterfaceBindingFromParsedIntent(parsedPi, intentDescription);
        setInterfaceBindingMode(inf.mode);
        setVlanAccessVlan(inf.accessVlan);
        setVlanAllowedVlans(inf.allowedCsv);
        setVlanNativeVlan(inf.nativeVlan);
        // Never auto-pick "closest in inventory" suggestions here; keep user selection authoritative.
        const chips = buildPlannedInventoryPreselectChips(parsedPi, { skipSuggestions: true });
        setVlanSelectedInterfaces(
          preselectPlannedInventoryInterfaces(chips, initialPreviewResult.interfaces_for_selection ?? null),
        );
      }
      skipNextInventoryRefreshRef.current = true;
      setStep('confirm');
    } else {
      runPreview();
    }
  }, [open, intentDescription, runPreview, initialPreviewResult, authoritativeMergedParams]);

  // When backend expanded "all interfaces" via planned network context, pre-fill the UI chips
  // so user can review/select from real interface names (no free-text typing).
  useEffect(() => {
    const opts = plannedAllInterfacesPreview;
    if (!opts || !opts.devices || opts.devices.length === 0) return;
    const shouldExpandWildcardOnly =
      targetInterfaces.length > 0 && targetInterfaces.every((x) => isWildcardInterfaceToken(x));
    if (targetInterfaces.length !== 0 && !shouldExpandWildcardOnly) return; // don't clobber user edits

    const perDevice = opts.devices.map((d) =>
      (d.interfaces || []).map((i) => String(i.name || '').trim()).filter(Boolean)
    );
    if (perDevice.length === 0) return;

    const union = Array.from(new Set(perDevice.flat()));
    if (union.length === 0) return;

    const intersection =
      perDevice.length > 1
        ? perDevice[0].filter((name) => perDevice.every((arr) => arr.includes(name)))
        : union;

    setTargetInterfaces(intersection.length > 0 ? intersection : union);
  }, [plannedAllInterfacesPreview, targetInterfaces.length]);

  useEffect(() => {
    if (step === 'confirm') {
      ansibleApi.listReferencePaths().then((r) => setAvailablePaths(r.paths || [])).catch(() => {});
    }
  }, [step]);

  const removeRef = (path: string) => {
    setSelectedRefs((prev) => prev.filter((r) => r.path !== path));
  };

  const addRef = () => {
    const p = addRefPath.trim();
    if (!p || selectedRefs.some((r) => r.path === p)) return;
    const fromApi = availablePaths.find((x) => x.path === p);
    setSelectedRefs((prev) => [...prev, { doc_name: fromApi ? p : p, feature: fromApi?.feature ?? '', vendor: fromApi?.vendor ?? '', path: p }]);
    setAddRefPath('');
  };

  const addCategory = () => {
    const c = newCategory.trim().toLowerCase().replace(/\s+/g, '_');
    if (!c || categories.includes(c)) return;
    setCategories((prev) => [...prev, c]);
    setNewCategory('');
  };

  const removeCategory = (cat: string) => {
    setCategories((prev) => prev.filter((x) => x !== cat));
  };

  const loadReferenceContent = useCallback(async (path: string) => {
    try {
      const { content } = await ansibleApi.readFile(path);
      setReferenceContent(content);
    } catch {
      setReferenceContent('# Could not load reference file: ' + path);
    }
  }, []);

  /** Same payload the backend expects for preview + create-and-plan (inventory override reads top-level `interfaces`). */
  const buildStudioMergedParams = useCallback((): Record<string, unknown> => {
    const { params: baseParams } = parseIntent(intentDescription);
    const pip = previewParsedIntentRef.current;
    const params: Record<string, unknown> = { ...(baseParams || {}) };
    Object.entries(schemaParamValues).forEach(([k, v]) => {
      if (v === '' || v === undefined) return;
      const field = schemaEditableFields.find((f) => f.field === k);
      params[k] = field?.type === 'number' ? Number(v) : v;
    });
    // Chat / wizard answers win over parseIntent defaults and must reach create-and-plan unchanged.
    const auth =
      authoritativeMergedParams && typeof authoritativeMergedParams === 'object'
        ? (authoritativeMergedParams as Record<string, unknown>)
        : null;
    if (auth) {
      const copyIfMeaningful = (key: string, raw: unknown) => {
        if (raw === undefined || raw === null) return;
        if (typeof raw === 'string' && !raw.trim()) return;
        if (Array.isArray(raw) && raw.length === 0) return;
        if (typeof raw === 'object' && !Array.isArray(raw) && Object.keys(raw as object).length === 0) return;
        params[key] = raw;
      };
      for (const key of [
        'acl_direction',
        'acl_name',
        'acl_type',
        'rules',
        'apply_to',
        'interface_apply',
        'passive_interfaces',
        'secondary_intents',
        '_secondary_intents',
      ]) {
        copyIfMeaningful(key, auth[key]);
      }
      const dirAlt = auth.direction;
      if (!params.acl_direction && (typeof dirAlt === 'string' ? dirAlt.trim() : dirAlt != null)) {
        const d = String(dirAlt ?? '')
          .trim()
          .toLowerCase();
        if (d) params.acl_direction = d.startsWith('out') ? 'out' : 'in';
      }
    }
    if (targetHosts.length > 0) {
      params.target_hosts = targetHosts;
    }
    const parsedActionRaw = String((pip?.action as string) || '')
      .toLowerCase()
      .replace(/-/g, '_');
    // Normalize common ACL-apply alias so downstream params logic matches backend schema.
    const parsedAction =
      parsedActionRaw === 'apply_acl' ? 'apply_acl_to_interface' : parsedActionRaw;
    const secondariesFromPreview = Array.isArray(
      (pip as { secondary_intents?: unknown } | null)?.secondary_intents,
    )
      ? ((pip as { secondary_intents: unknown[] }).secondary_intents as Array<{
          action?: string;
          feature?: string;
          parameters?: Record<string, unknown>;
        }>)
      : [];

    if (secondariesFromPreview.length) {
      params._secondary_intents = secondariesFromPreview.map((s) => ({
        ...s,
        parameters: { ...(s.parameters || {}) },
      }));
    }

    if (targetInterfaces.length > 0) {
      const vidRaw = params.vlan_id ?? schemaParamValues.vlan_id;
      const vid =
        typeof vidRaw === 'number'
          ? vidRaw
          : vidRaw !== undefined && vidRaw !== ''
            ? parseInt(String(vidRaw), 10)
            : NaN;
      const accessVlan = Number.isFinite(vid) && !Number.isNaN(vid) ? vid : undefined;

      if (parsedAction === 'shutdown_interface') {
        params.interfaces_to_shutdown = targetInterfaces;
      } else if (parsedAction === 'create_vlan') {
        // Studio "No" = VLAN database only — do not send L2 interfaces from chips (they were misleading access/trunk).
        if (interfaceBindingMode === 'no') {
          delete params.interfaces;
          delete params.interfaces_config;
          if (Array.isArray(params._secondary_intents)) {
            params._secondary_intents = params._secondary_intents.map((s) => {
              const p0 = { ...(s.parameters || {}) };
              delete p0.interfaces;
              delete p0.interfaces_config;
              return { ...s, parameters: p0 };
            });
          }
        } else {
          const names =
            vlanSelectedInterfaces.size > 0 ? Array.from(vlanSelectedInterfaces) : targetInterfaces;
          if (names.length > 0) {
            let ifaceList: Array<Record<string, unknown>>;
            if (interfaceBindingMode === 'access') {
              ifaceList = names.map((name) => ({
                name,
                mode: 'access' as const,
                access_vlan: vlanAccessVlan,
              }));
            } else {
              const trunkParsed = vlanAllowedVlans
                .split(',')
                .map((s) => parseInt(s.trim(), 10))
                .filter((n) => !Number.isNaN(n));
              const trunkVlans =
                trunkParsed.length > 0 ? trunkParsed : accessVlan !== undefined ? [accessVlan] : [];
              ifaceList = names.map((name) => {
                const row: Record<string, unknown> = {
                  name,
                  mode: 'trunk' as const,
                  trunk_vlans: trunkVlans,
                };
                if (vlanNativeVlan !== null && vlanNativeVlan !== undefined) {
                  row.native_vlan = vlanNativeVlan;
                }
                return row;
              });
            }
            params.interfaces = ifaceList;
            const ifaceIdx = secondariesFromPreview.findIndex(
              (s) =>
                String(s?.action || '')
                  .toLowerCase()
                  .replace(/-/g, '_') === 'configure_interface' ||
                String(s?.feature || '').toLowerCase() === 'interface',
            );
            if (ifaceIdx >= 0) {
              const base = (params._secondary_intents as typeof secondariesFromPreview) || [];
              params._secondary_intents = base.map((s, i) => {
                if (i !== ifaceIdx) return s;
                const p0 = (s.parameters || {}) as Record<string, unknown>;
                return {
                  ...s,
                  parameters: {
                    ...p0,
                    interfaces: ifaceList,
                  },
                };
              });
            }
          }
        }
      } else if (parsedAction === 'configure_interface') {
        const pipIf = pip?.parameters as { interfaces?: unknown } | undefined;
        const existing = pipIf?.interfaces;
        if (Array.isArray(existing) && existing.length > 0) {
          params.interfaces = existing;
        } else {
          params.interfaces = targetInterfaces.map((name) => ({
            name,
            mode: 'access' as const,
            ...(accessVlan !== undefined ? { access_vlan: accessVlan } : {}),
          }));
        }
      } else {
        const ifaceIdx = secondariesFromPreview.findIndex(
          (s) =>
            String(s?.action || '')
              .toLowerCase()
              .replace(/-/g, '_') === 'configure_interface' ||
            String(s?.feature || '').toLowerCase() === 'interface',
        );
        if (ifaceIdx >= 0) {
          const base = (params._secondary_intents as typeof secondariesFromPreview) || [];
          params._secondary_intents = base.map((s, i) => {
            if (i !== ifaceIdx) return s;
            const p0 = (s.parameters || {}) as Record<string, unknown>;
            return {
              ...s,
              parameters: {
                ...p0,
                interfaces: targetInterfaces.map((name) => ({
                  name,
                  mode: 'access',
                  ...(accessVlan !== undefined ? { access_vlan: accessVlan } : {}),
                })),
              },
            };
          });
        }
      }
    }

    // Authoritative interface attachment semantics:
    // If the user selected planned-inventory attachment targets (e.g. apply_to for ACL),
    // never send stale prompt-derived `interfaces` alongside it.
    if (parsedAction === 'apply_acl_to_interface' || parsedAction === 'configure_acl') {
      if (Array.isArray(params.apply_to) && params.apply_to.length > 0) {
        delete params.interfaces;
        delete params.interfaces_config;
      }
      if (String(params.acl_name ?? '').trim()) {
        (params as any)._user_supplied_acl_name = true;
      }
      if (String(params.acl_type ?? '').trim()) {
        (params as any)._user_supplied_acl_type = true;
      }
      if (Array.isArray(params.rules) && (params.rules as unknown[]).length > 0) {
        (params as any)._user_supplied_acl_rules = true;
      }
    }
    if (parsedAction === 'configure_qos') {
      if (Array.isArray(params.interface_apply) && params.interface_apply.length > 0) {
        delete params.interfaces;
        delete params.interfaces_config;
      }
    }
    return params;
  }, [
    intentDescription,
    parseIntent,
    targetHosts,
    targetInterfaces,
    schemaParamValues,
    schemaEditableFields,
    interfaceBindingMode,
    vlanSelectedInterfaces,
    vlanAccessVlan,
    vlanAllowedVlans,
    vlanNativeVlan,
    authoritativeMergedParams,
  ]);

  const getStudioVendorFeature = useCallback(() => {
    const { vendor, feature: heuristicFeature } = parseIntent(intentDescription);
    const pip = previewParsedIntentRef.current;
    const parsedFeat = String((pip?.feature as string) || '').trim().toLowerCase();
    const fromCategories = categories.find((c) => c && c !== 'general');
    const feature =
      parsedFeat ||
      (fromCategories && fromCategories !== 'general' ? fromCategories : '') ||
      heuristicFeature;
    return { vendor, feature };
  }, [intentDescription, parseIntent, categories]);

  /** Re-run preview with current chips + schema so inventory validation and reasoning stay in sync (no stale Gi… errors). */
  const refreshPreviewWithMergedParams = useCallback(async () => {
    if (!open || !intentDescription.trim()) return;
    const pip = previewParsedIntentRef.current;
    if (!pip) return;
    const { vendor, feature } = getStudioVendorFeature();
    const params = buildStudioMergedParams();
    try {
      const res = await ansibleApi.previewIntent({
        intent_description: intentDescription,
        target_hosts: targetHosts.length ? targetHosts : undefined,
        vendor: vendor || undefined,
        feature: feature || undefined,
        provider: ansibleModel.provider,
        model: ansibleModel.model,
        ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
        params,
      });
      setReasoning(res.reasoning || []);
      setPolicyApproved(res.policy_approved ?? false);
      setPreviewParsedIntent((res.parsed_intent as Record<string, unknown>) ?? null);
      setInterfacesForSelection((res as { interfaces_for_selection?: typeof interfacesForSelection }).interfaces_for_selection ?? null);
      setIntentSchemaInspector((res as { intent_schema_inspector?: IntentSchemaInspectorPayload }).intent_schema_inspector ?? null);
      setErrorMessage(null);
    } catch {
      /* ignore transient refresh errors */
    }
  }, [
    open,
    intentDescription,
    targetHosts,
    buildStudioMergedParams,
    getStudioVendorFeature,
    ansibleModel.provider,
    ansibleModel.model,
    ansibleModel.ollama_base_url,
  ]);

  useEffect(() => {
    if (!open || step !== 'confirm') return;
    if (!previewParsedIntentRef.current) return;
    if (skipNextInventoryRefreshRef.current) {
      skipNextInventoryRefreshRef.current = false;
      return;
    }
    const t = window.setTimeout(() => {
      void refreshPreviewWithMergedParams();
    }, 750);
    return () => window.clearTimeout(t);
  }, [
    open,
    step,
    targetInterfaces,
    targetHosts,
    schemaParamValues,
    interfaceBindingMode,
    vlanAccessVlan,
    vlanAllowedVlans,
    vlanNativeVlan,
    vlanSelectedInterfaces,
    refreshPreviewWithMergedParams,
  ]);

  useEffect(() => {
    skipNextInventoryRefreshRef.current = true;
  }, [open, intentDescription]);

  const handleConfirm = useCallback(async () => {
    setStep('generating');
    setErrorMessage(null);
    const { vendor, feature } = getStudioVendorFeature();
    const params = buildStudioMergedParams();
    const firstRefPath = selectedRefs[0]?.path;
    if (firstRefPath) {
      loadReferenceContent(firstRefPath);
    } else {
      setReferenceContent('# No reference template selected');
    }
    const fromParamsHosts = Array.isArray(params.target_hosts)
      ? (params.target_hosts as string[]).filter((h) => typeof h === 'string' && h.trim())
      : [];
    const hostsToUse = targetHosts.length > 0 ? targetHosts : fromParamsHosts;
    if (hostsToUse.length === 0) {
      setStep('confirm');
      Toast.error(
        'No target devices',
        'Select at least one device in Target inventory (or run preview so hosts are filled), then generate.',
      );
      return;
    }
    const ac = new AbortController();
    abortControllerRef.current = ac;
    try {
      const result = await ansibleApi.createAndPlan({
        intent_description: intentDescription,
        target_hosts: hostsToUse,
        vendor,
        feature,
        params,
        provider: ansibleModel.provider,
        model: ansibleModel.model,
        ollama_base_url: ansibleModel.ollama_base_url ?? undefined,
      }, { signal: ac.signal });
      abortControllerRef.current = null;
      setGeneratedContent(result.playbook ?? '');
      setGeneratedJobId(result.job_id ?? null);
      setResultPayload({
        jobId: result.job_id,
        content: result.playbook ?? '',
        name: (result.entrypoint_path || `playbook_${result.job_id}.yml`).split('/').pop() ?? 'playbook.yml',
        entrypointPath: result.entrypoint_path,
        files: result.files,
      });
      setStep('result');
      // Stay on modal so user can compare template vs generated; they click Confirm to proceed
    } catch (e: unknown) {
      abortControllerRef.current = null;
      const isAbort = e instanceof DOMException && e.name === 'AbortError';
      if (isAbort) {
        setStep('confirm');
        return;
      }
      const msg = formatAnsibleApiError(e);
      setErrorMessage(msg);
      setStep('error');
      Toast.error('Generation failed', msg);
    }
  }, [
    getStudioVendorFeature,
    buildStudioMergedParams,
    selectedRefs,
    targetHosts,
    intentDescription,
    ansibleModel,
    loadReferenceContent,
    onPlaybookReady,
  ]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-100 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        className="bg-base-100 border border-base-300 rounded-xl shadow-2xl flex flex-col overflow-hidden animate-in fade-in duration-200"
        style={{ width: 'min(92vw, 900px)', maxHeight: '88vh' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-base-300 bg-base-200">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-primary" />
            <span className="font-semibold text-base-content">Playbook Studio</span>
            <span className="text-xs text-base-content/60">
              {step === 'thinking' && 'Understanding your intent…'}
              {step === 'confirm' && 'Confirm templates & generate'}
              {step === 'generating' && 'Composing playbook…'}
              {step === 'result' && 'Generated'}
              {step === 'error' && 'Error'}
            </span>
          </div>
          <div className="flex items-center gap-1">
            {step === 'generating' && (
              <button
                type="button"
                onClick={() => {
                  if (abortControllerRef.current) {
                    abortControllerRef.current.abort();
                  }
                }}
                title="Stop generation"
                className="btn btn-ghost rounded-full btn-sm btn-square text-red-600 hover:bg-red-600/20"
                aria-label="Stop"
              >
                <Square className="w-4 h-4 fill-current" />
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="btn btn-ghost btn-sm btn-square"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4">
          {step === 'thinking' && (
            <div className="flex flex-col items-center justify-center py-12 gap-4">
              <div className="relative">
                <div className="w-14 h-14 rounded-full border-2 border-primary/40 border-t-primary flex items-center justify-center animate-spin" />
                <Sparkles className="w-6 h-6 text-primary absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />
              </div>
              <p className="text-sm text-base-content/80">Detecting categories and finding reference templates…</p>
            </div>
          )}

          {step === 'confirm' && (
            <>
              {reasoning.length > 0 && (
                <div className="mb-4 rounded-lg border border-base-300 bg-base-200/50 overflow-hidden">
                  <button type="button" className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-base-300/50 transition-colors" onClick={() => setShowReasoning((v) => !v)}>
                    <span className="text-xs font-semibold text-base-content/80 flex items-center gap-2"><Lightbulb className="w-3.5 h-3.5 text-primary" /> How we got here — LLM reasoning</span>
                    {showReasoning ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                  </button>
                  {showReasoning && (
                    <div className="px-3 pb-3 pt-0 text-sm text-base-content/80 space-y-4 border-t border-base-300">
                      {reasoning.map((line, i) => (
                        <div key={i} className="prose prose-sm max-w-none prose-headings:mt-0 prose-headings:mb-1.5 prose-p:my-0.5 prose-ul:my-1">
                          <ReactMarkdown>{line}</ReactMarkdown>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {(intentSchemaInspector?.error || (intentSchemaInspector?.slices && intentSchemaInspector.slices.length > 0)) && (
                <div className="mb-4 rounded-lg border border-primary/30 bg-primary/5 overflow-hidden">
                  <div className="px-3 py-2 border-b border-primary/20 bg-primary/10">
                    <p className="text-xs font-semibold text-base-content flex items-center gap-2">
                      <FileCode className="w-3.5 h-3.5 text-primary" />
                      Intent schema inspection
                    </p>
                    <p className="text-[11px] text-base-content/60 mt-0.5">
                      Operation-specific YAML schema per detected slice (primary + secondary). Required vs optional follows each action — same category can differ by operation.
                    </p>
                  </div>
                  <div className="p-3 space-y-4 max-h-[min(50vh,420px)] overflow-y-auto">
                    {intentSchemaInspector?.error ? (
                      <p className="text-xs text-warning">Could not build schema inspector: {intentSchemaInspector.error}</p>
                    ) : null}
                    {(intentSchemaInspector?.slices || []).map((slice) => (
                      <div
                        key={`${slice.role}-${slice.index}`}
                        className="rounded-lg border border-base-300 bg-base-100/80 overflow-hidden"
                      >
                        <div className="px-2 py-1.5 bg-base-200/80 border-b border-base-300 flex flex-wrap items-center gap-2">
                          <span className="text-xs font-semibold text-base-content">{slice.title}</span>
                          <span className="badge badge-xs badge-outline capitalize">{slice.role}</span>
                          {slice.schema_file ? (
                            <span className="text-[10px] font-mono text-base-content/70" title="intent_schemas file">
                              {slice.schema_file}
                            </span>
                          ) : null}
                        </div>
                        <div className="px-2 py-1.5 text-[11px] text-base-content/80 flex flex-wrap gap-x-3 gap-y-1">
                          <span>
                            <span className="text-base-content/50">action</span>{' '}
                            <code className="text-xs bg-base-200 px-1 rounded">{slice.action ?? '—'}</code>
                          </span>
                          <span>
                            <span className="text-base-content/50">feature</span>{' '}
                            <code className="text-xs bg-base-200 px-1 rounded">{slice.feature ?? '—'}</code>
                          </span>
                          {slice.schema_action ? (
                            <span>
                              <span className="text-base-content/50">schema action</span>{' '}
                              <code className="text-xs bg-base-200 px-1 rounded">{slice.schema_action}</code>
                            </span>
                          ) : null}
                        </div>
                        {slice.note ? <p className="px-2 pb-2 text-xs text-base-content/60">{slice.note}</p> : null}
                        {slice.missing_required && slice.missing_required.length > 0 ? (
                          <p className="px-2 pb-1 text-xs text-error">
                            Missing required:{' '}
                            <span className="font-mono">{slice.missing_required.join(', ')}</span>
                          </p>
                        ) : null}
                        {slice.validation_errors && slice.validation_errors.length > 0 ? (
                          <ul className="px-2 pb-2 text-xs text-warning list-disc list-inside">
                            {slice.validation_errors.map((err, i) => (
                              <li key={i}>{err}</li>
                            ))}
                          </ul>
                        ) : null}
                        {slice.fields && slice.fields.length > 0 ? (
                          <div className="overflow-x-auto border-t border-base-300">
                            <table className="table table-xs w-full">
                              <thead>
                                <tr className="bg-base-200/50">
                                  <th className="text-[10px] uppercase tracking-wide">Field</th>
                                  <th className="text-[10px] uppercase tracking-wide">Param key</th>
                                  <th className="text-[10px] uppercase tracking-wide">Required</th>
                                  <th className="text-[10px] uppercase tracking-wide">Status</th>
                                  <th className="text-[10px] uppercase tracking-wide">Value</th>
                                </tr>
                              </thead>
                              <tbody>
                                {slice.fields.map((row) => (
                                  <tr key={row.schema_field}>
                                    <td className="align-top text-xs font-medium">{row.label}</td>
                                    <td className="align-top">
                                      <code className="text-[10px] bg-base-200 px-1 rounded">{row.param_key}</code>
                                    </td>
                                    <td className="align-top">{row.required ? 'Yes' : 'No'}</td>
                                    <td className="align-top">
                                      <span className={`badge badge-xs ${intentInspectorStatusBadgeClass(row.status)}`}>
                                        {intentInspectorStatusLabel(row.status)}
                                      </span>
                                    </td>
                                    <td className="align-top text-[11px] font-mono break-all max-w-[200px]">
                                      {row.value_preview || (row.status === 'missing' ? '—' : '')}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="mb-4">
                <p className="text-xs font-semibold text-base-content/70 uppercase tracking-wider mb-2">Intent categories — add or remove</p>
                <div className="flex flex-wrap gap-2 items-center">
                  {categories.map((c) => (
                    <span key={c} className="badge badge-primary badge-sm gap-1 pr-1">
                      {FEATURES_DISPLAY[c] ?? c}
                      <button type="button" className="hover:bg-primary-content/20 rounded-full p-0.5" onClick={() => removeCategory(c)} aria-label={`Remove ${c}`}><X className="w-3 h-3" /></button>
                    </span>
                  ))}
                  <input type="text" placeholder="Add category..." className="input input-bordered input-xs w-28" value={newCategory} onChange={(e) => setNewCategory(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addCategory())} />
                  <button type="button" className="btn btn-ghost btn-xs gap-0.5" onClick={addCategory}><Plus className="w-3 h-3" /> Add</button>
                  {categories.length === 0 && !newCategory && <span className="text-sm text-base-content/50">No category yet — add one or continue</span>}
                </div>
                {!policyApproved && categories.length > 0 && (
                  <p className="text-xs text-warning mt-2 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3" /> Policy may require review before execution.
                  </p>
                )}
              </div>
              <div className="mb-4">
                <p className="text-xs font-semibold text-base-content/70 uppercase tracking-wider mb-2">
                  Reference templates — add or remove
                </p>
                <ul className="space-y-1.5 max-h-36 overflow-y-auto rounded-lg border border-base-300 bg-base-200/50 p-2">
                  {selectedRefs.length === 0 ? (
                    <li className="text-sm text-base-content/50 py-2">No reference templates — add one below</li>
                  ) : (
                    selectedRefs.map((ref) => (
                      <li
                        key={ref.path}
                        className="flex items-center justify-between gap-2 py-1.5 px-2 rounded bg-base-100 border border-base-300"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <FileCode className="w-3.5 h-3.5 text-primary shrink-0" />
                          <span className="text-xs font-mono truncate" title={ref.path}>{ref.path}</span>
                          <span className="badge badge-ghost badge-xs shrink-0">{ref.feature}</span>
                        </div>
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs text-error hover:bg-error/10"
                          onClick={() => removeRef(ref.path)}
                        >
                          Remove
                        </button>
                      </li>
                    ))
                  )}
                </ul>
                <div className="flex gap-2 mt-2 flex-wrap items-center">
                  <select className="select select-bordered select-sm flex-1 min-w-0 max-w-xs" value={addRefPath} onChange={(e) => setAddRefPath(e.target.value)}>
                    <option value="">Choose a reference to add...</option>
                    {availablePaths.filter((p) => !selectedRefs.some((r) => r.path === p.path)).map((p) => (
                      <option key={p.path} value={p.path}>{p.path} ({p.feature} / {p.vendor})</option>
                    ))}
                  </select>
                  <button type="button" className="btn btn-ghost btn-sm gap-0.5" onClick={addRef} disabled={!addRefPath}><Plus className="w-3 h-3" /> Add reference</button>
                </div>
              </div>
              {schemaEditableFields.filter((f) => f.field !== 'target_hosts' && f.field !== 'interfaces_to_shutdown' && f.field !== 'interfaces').length > 0 && (
                <div className="mb-4">
                  <p className="text-xs font-semibold text-base-content/70 uppercase tracking-wider mb-2">
                    Parameters (schema)
                  </p>
                  <p className="text-xs text-base-content/50 mb-2">Edit values before generating. Required fields are marked.</p>
                  <div className="space-y-2">
                    {schemaEditableFields
                      .filter((f) => f.field !== 'target_hosts' && f.field !== 'interfaces_to_shutdown' && f.field !== 'interfaces')
                      .map((f) =>
                        (f as SchemaEditableField & { type?: string }).type === 'interfaces_choice' ? (
                          <div key={f.field} className="space-y-2 rounded-lg border border-base-300 bg-base-200/50 p-3">
                            <label className="text-sm font-medium text-base-content/80">{f.label}</label>
                            <div className="flex flex-wrap gap-2 items-center">
                              <span className="text-xs text-base-content/60">Mode:</span>
                              <select
                                className="select select-bordered select-sm"
                                value={interfaceBindingMode}
                                onChange={(e) => setInterfaceBindingMode((e.target.value as 'no' | 'access' | 'trunk'))}
                              >
                                <option value="no">No (VLAN DB only — no L2 on ports)</option>
                                <option value="access">Access</option>
                                <option value="trunk">Trunk</option>
                              </select>
                            </div>
                            {interfaceBindingMode === 'no' ? (
                              <p className="text-xs text-base-content/50 max-w-xl">
                                Target interface chips below are for reference only until you choose Access or Trunk; generate
                                with <strong>No</strong> to produce a VLAN-only playbook (no{' '}
                                <span className="font-mono">ios_l2_interfaces</span>).
                              </p>
                            ) : null}
                            {interfaceBindingMode === 'access' && (
                              <div className="flex flex-wrap gap-2 items-center">
                                <label className="text-xs text-base-content/70">Access VLAN</label>
                                <input
                                  type="number"
                                  min={1}
                                  max={4094}
                                  className="input input-bordered input-sm w-24"
                                  value={vlanAccessVlan}
                                  onChange={(e) => setVlanAccessVlan(parseInt(e.target.value, 10) || 1)}
                                />
                              </div>
                            )}
                            {interfaceBindingMode === 'trunk' && (
                              <div className="space-y-1">
                                <div className="flex flex-wrap gap-2 items-center">
                                  <label className="text-xs text-base-content/70">Allowed VLANs (comma-separated)</label>
                                  <input
                                    type="text"
                                    className="input input-bordered input-sm flex-1 min-w-0 max-w-xs"
                                    placeholder="e.g. 10,20,100"
                                    value={vlanAllowedVlans}
                                    onChange={(e) => setVlanAllowedVlans(e.target.value)}
                                  />
                                </div>
                                <div className="flex flex-wrap gap-2 items-center">
                                  <label className="text-xs text-base-content/70">Native VLAN (optional)</label>
                                  <input
                                    type="number"
                                    min={1}
                                    max={4094}
                                    className="input input-bordered input-sm w-24"
                                    placeholder="omit = unchanged"
                                    value={vlanNativeVlan === null ? '' : vlanNativeVlan}
                                    onChange={(e) => {
                                      const v = e.target.value.trim();
                                      if (v === '') setVlanNativeVlan(null);
                                      else {
                                        const n = parseInt(v, 10);
                                        setVlanNativeVlan(Number.isNaN(n) ? null : n);
                                      }
                                    }}
                                  />
                                </div>
                              </div>
                            )}
                            {(interfaceBindingMode === 'access' || interfaceBindingMode === 'trunk') && interfacesForSelection?.devices?.length
                              ? (
                                  <div className="mt-2">
                                    <p className="text-xs text-base-content/60 mb-1">Select interfaces (from planned context):</p>
                                    <div className="flex flex-wrap gap-2 max-h-32 overflow-y-auto">
                                      {interfacesForSelection.devices.flatMap((d) =>
                                        (d.interfaces || []).map((i) => {
                                          const name = i.name;
                                          const checked = vlanSelectedInterfaces.has(name);
                                          return (
                                            <label key={`${d.hostname}-${name}`} className="label cursor-pointer gap-1.5 border border-base-300 rounded px-2 py-1 bg-base-100">
                                              <input
                                                type="checkbox"
                                                checked={checked}
                                                onChange={() => {
                                                  setVlanSelectedInterfaces((prev) => {
                                                    const next = new Set(prev);
                                                    if (next.has(name)) next.delete(name);
                                                    else next.add(name);
                                                    return next;
                                                  });
                                                }}
                                              />
                                              <span className="text-xs font-mono">{name}</span>
                                            </label>
                                          );
                                        })
                                      )}
                                    </div>
                                  </div>
                                )
                              : null}
                          </div>
                        ) : (
                          <div key={f.field} className="flex flex-wrap items-center gap-2">
                            <label className="text-sm font-medium text-base-content/80 w-32 shrink-0">
                              {f.label}
                              {f.required && <span className="text-error ml-0.5">*</span>}
                            </label>
                            <input
                              type={f.field === 'network_address' ? 'text' : f.type === 'number' ? 'number' : 'text'}
                              className="input input-bordered input-sm flex-1 min-w-0 max-w-xs"
                              value={schemaParamValues[f.field] ?? ''}
                              onChange={(e) =>
                                setSchemaParamValues((prev) => ({
                                  ...prev,
                                  [f.field]:
                                    f.field === 'network_address'
                                      ? sanitizeIPv4Input(e.target.value)
                                      : f.field === 'prefix_length'
                                        ? (() => {
                                            const n = parseInt(String(e.target.value || '').replace('/', '').trim(), 10);
                                            if (Number.isNaN(n)) return '';
                                            return Math.max(0, Math.min(32, n));
                                          })()
                                        : f.type === 'number'
                                          ? (e.target.value === '' ? '' : Number(e.target.value))
                                          : e.target.value,
                                }))
                              }
                              placeholder={f.required ? 'Required' : 'Optional'}
                            />
                          </div>
                        )
                      )}
                  </div>
                </div>
              )}
              <div className="mb-4">
                <p className="text-xs font-semibold text-base-content/70 uppercase tracking-wider mb-2">
                  Target inventory — devices
                </p>
                <p className="text-xs text-base-content/50 mb-2">Devices (hostnames) the playbook will target. Add or remove below; leave empty to use &quot;all&quot;.</p>
                <div className="flex flex-wrap gap-2 items-center">
                  {targetHosts.map((h) => (
                    <span key={h} className="badge badge-outline badge-sm gap-1 pr-1">
                      {h}
                      <button type="button" className="hover:bg-base-content/20 rounded-full p-0.5" onClick={() => setTargetHosts((prev) => prev.filter((x) => x !== h))} aria-label={`Remove ${h}`}><X className="w-3 h-3" /></button>
                    </span>
                  ))}
                  <input type="text" placeholder="Add device or host..." className="input input-bordered input-xs w-36" value={newTargetHost} onChange={(e) => setNewTargetHost(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), (() => { const v = newTargetHost.trim(); if (v && !targetHosts.includes(v)) { setTargetHosts((p) => [...p, v]); setNewTargetHost(''); } })())} />
                  <button type="button" className="btn btn-ghost btn-xs gap-0.5" onClick={() => { const v = newTargetHost.trim(); if (v && !targetHosts.includes(v)) { setTargetHosts((p) => [...p, v]); setNewTargetHost(''); } }}><Plus className="w-3 h-3" /> Add</button>
                  {targetHosts.length === 0 && !newTargetHost && <span className="text-sm text-base-content/50">No devices — playbook will target &quot;all&quot;</span>}
                </div>
              </div>
              {categories.map((c) => c.toLowerCase()).includes('interface') && (
                <div className="mb-4">
                  <p className="text-xs font-semibold text-base-content/70 uppercase tracking-wider mb-2">
                    Target interfaces
                  </p>
                  <p className="text-xs text-base-content/50 mb-2">Interfaces to apply the action to. Add or remove below.</p>
                  <div className="flex flex-wrap gap-2 items-center">
                    {targetInterfaces.map((iface) => (
                      <span key={iface} className="badge badge-outline badge-sm gap-1 pr-1 font-mono">
                        {iface}
                        <button
                          type="button"
                          className="hover:bg-base-content/20 rounded-full p-0.5"
                          onClick={() => setTargetInterfaces((prev) => prev.filter((x) => x !== iface))}
                          aria-label={`Remove ${iface}`}
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </span>
                    ))}
                    {plannedAllInterfacesPreview?.devices?.length ? (
                      <div className="w-full">
                        <div className="dropdown dropdown-end">
                          <label tabIndex={0} className="btn btn-ghost btn-xs gap-0.5 cursor-pointer">
                            <Plus className="w-3 h-3" /> Add
                          </label>
                          <div
                            tabIndex={0}
                            className="dropdown-content menu p-2 shadow bg-base-100 rounded-box max-h-48 overflow-y-auto w-72"
                          >
                            {Array.from(
                              new Set(
                                plannedAllInterfacesPreview.devices.flatMap((d) =>
                                  (d.interfaces || [])
                                    .map((i) => String(i.name || '').trim())
                                    .filter(Boolean)
                                )
                              )
                            )
                              .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
                              .map((name) => {
                                const checked = targetInterfaces.includes(name);
                                return (
                                  <li key={name} className="p-1.5">
                                    <label className="flex items-center gap-2 cursor-pointer">
                                      <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={() => {
                                          setTargetInterfaces((prev) => {
                                            const next = new Set(prev);
                                            if (next.has(name)) next.delete(name);
                                            else next.add(name);
                                            return Array.from(next);
                                          });
                                        }}
                                      />
                                      <span className="text-xs font-mono">{name}</span>
                                    </label>
                                  </li>
                                );
                              })}
                            {targetInterfaces.length === 0 ? (
                              <li className="text-xs opacity-70 p-1.5">No interfaces selected yet</li>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    ) : (
                      <>
                        <input
                          type="text"
                          placeholder="Add interface..."
                          className="input input-bordered input-xs w-36 font-mono"
                          value={newTargetInterface}
                          onChange={(e) => setNewTargetInterface(e.target.value)}
                          onKeyDown={(e) =>
                            e.key === 'Enter' &&
                            (e.preventDefault(),
                              (() => {
                                const v = newTargetInterface.trim();
                                if (v && !targetInterfaces.includes(v)) {
                                  setTargetInterfaces((p) => [...p, v]);
                                  setNewTargetInterface('');
                                }
                              })())
                          }
                        />
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs gap-0.5"
                          onClick={() => {
                            const v = newTargetInterface.trim();
                            if (v && !targetInterfaces.includes(v)) {
                              setTargetInterfaces((p) => [...p, v]);
                              setNewTargetInterface('');
                            }
                          }}
                        >
                          <Plus className="w-3 h-3" /> Add
                        </button>
                        {targetInterfaces.length === 0 && !newTargetInterface ? (
                          <span className="text-sm text-base-content/50">No interfaces selected</span>
                        ) : null}
                      </>
                    )}
                  </div>
                </div>
              )}
              <div className="flex justify-end gap-2">
                <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm gap-1"
                  onClick={handleConfirm}
                >
                  <Check className="w-3.5 h-3.5" /> Confirm & generate
                </button>
              </div>
            </>
          )}

          {step === 'generating' && !referenceContent && (
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <Loader2 className="w-8 h-8 text-primary animate-spin" />
              <p className="text-sm text-base-content/60">Loading reference template…</p>
            </div>
          )}
          {(step === 'result' || (step === 'generating' && referenceContent)) && (
            <>
              <div className="grid grid-cols-2 gap-3 h-[420px]">
                <div className="flex flex-col rounded-lg border border-base-300 overflow-hidden">
                  <div className="px-2 py-1.5 bg-base-300 text-xs font-semibold text-base-content/80 flex items-center gap-1">
                    <FileCode className="w-3 h-3" /> Reference template (structure only — red keys unchanged)
                  </div>
                  <div className="flex-1 min-h-0">
                    <Editor
                      height="100%"
                      defaultLanguage="yaml"
                      value={referenceContent}
                      theme="vs-dark"
                      options={{ readOnly: true, wordWrap: 'on', minimap: { enabled: false } }}
                    />
                  </div>
                </div>
                <div className="flex flex-col rounded-lg border border-base-300 overflow-hidden">
                  <div className="px-2 py-1.5 bg-primary/20 text-xs font-semibold text-primary flex items-center gap-1">
                    <ChevronRight className="w-3 h-3" /> Generated playbook (values filled — green)
                    {generatedJobId != null && (
                      <span className="ml-1 opacity-80">(Job #{generatedJobId})</span>
                    )}
                  </div>
                  <div className="flex-1 min-h-0 flex flex-col bg-base-200 relative">
                    {step === 'generating' ? (
                      <>
                        <Editor
                          height="100%"
                          defaultLanguage="yaml"
                          value="# Playbook will appear here when generation finishes."
                          theme="vs-dark"
                          options={{ readOnly: true, wordWrap: 'on', minimap: { enabled: false }, domReadOnly: true }}
                        />
                        <div className="absolute bottom-3 right-3 flex items-center gap-2 text-base-content/50 text-xs">
                          <Loader2 className="w-4 h-4 text-primary animate-spin shrink-0" />
                          <span>Composing…</span>
                        </div>
                      </>
                    ) : (
                      <Editor
                        height="100%"
                        defaultLanguage="yaml"
                        value={generatedContent}
                        theme="vs-dark"
                        options={{ readOnly: true, wordWrap: 'on', minimap: { enabled: false } }}
                      />
                    )}
                  </div>
                </div>
              </div>
              {(step === 'result' && resultPayload) && (
                <div className="flex justify-end gap-2 mt-3 pt-3 border-t border-base-300">
                  <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm gap-1"
                    onClick={() => {
                      onPlaybookReady(resultPayload);
                      setResultPayload(null);
                      onClose();
                    }}
                  >
                    <Check className="w-3.5 h-3.5" /> Confirm & use this playbook
                  </button>
                </div>
              )}
              {step === 'generating' && (
                <div className="flex justify-end gap-2 mt-3 pt-3 border-t border-base-300 opacity-50 pointer-events-none">
                  <button type="button" className="btn btn-ghost btn-sm" disabled>
                    Cancel
                  </button>
                  <button type="button" className="btn btn-primary btn-sm gap-1" disabled>
                    <Check className="w-3.5 h-3.5" /> Confirm & use this playbook
                  </button>
                </div>
              )}
            </>
          )}

          {step === 'error' && (
            <div className="py-6 flex flex-col items-center gap-3">
              <AlertCircle className="w-10 h-10 text-error" />
              <p className="text-sm text-error text-center max-w-md whitespace-pre-line">{errorMessage}</p>
              <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
