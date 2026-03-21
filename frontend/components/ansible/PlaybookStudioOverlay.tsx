import React, { useState, useEffect, useCallback, useRef } from 'react';
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

function pushInterfaceNamesFromList(arr: unknown, out: string[]): void {
  if (!Array.isArray(arr)) return;
  for (const x of arr) {
    if (typeof x === 'string' && x.trim()) out.push(x.trim());
    else if (x && typeof x === 'object' && 'name' in (x as object)) {
      const n = String((x as { name?: string }).name || '').trim();
      if (n) out.push(n);
    }
  }
}

/**
 * Names for "Target interfaces" chips: primary parameters and every `secondary_intents` slice
 * (composite create_vlan + configure_interface keeps interfaces on the secondary).
 */
function extractTargetInterfaceNamesFromParsedIntent(
  parsed: Record<string, unknown> | null | undefined,
): string[] {
  if (!parsed) return [];
  const out: string[] = [];
  const p = (parsed as { parameters?: Record<string, unknown> }).parameters ?? {};
  if (Array.isArray(p.interfaces_to_shutdown)) {
    pushInterfaceNamesFromList(p.interfaces_to_shutdown, out);
  }
  if (Array.isArray(p.interfaces)) {
    pushInterfaceNamesFromList(p.interfaces, out);
  }
  const secondaries = (parsed as { secondary_intents?: unknown }).secondary_intents;
  if (Array.isArray(secondaries)) {
    for (const sec of secondaries) {
      if (!sec || typeof sec !== 'object') continue;
      const sp = (sec as { parameters?: Record<string, unknown> }).parameters ?? {};
      if (Array.isArray(sp.interfaces_to_shutdown)) {
        pushInterfaceNamesFromList(sp.interfaces_to_shutdown, out);
      }
      if (Array.isArray(sp.interfaces)) {
        pushInterfaceNamesFromList(sp.interfaces, out);
      }
    }
  }
  const seen = new Set<string>();
  const dedup: string[] = [];
  for (const x of out) {
    if (!seen.has(x)) {
      seen.add(x);
      dedup.push(x);
    }
  }
  return dedup;
}

export interface SchemaEditableField {
  field: string;
  label: string;
  required: boolean;
  type: string;
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
  const [vlanNativeVlan, setVlanNativeVlan] = useState<number>(1);
  /** Last preview API parsed_intent — used so create-and-plan sends correct feature (avoid heuristic "general"). */
  const [previewParsedIntent, setPreviewParsedIntent] = useState<Record<string, unknown> | null>(null);
  const previewParsedIntentRef = useRef<Record<string, unknown> | null>(null);
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
        extractTargetInterfaceNamesFromParsedIntent(
          (res.parsed_intent as Record<string, unknown>) ?? null,
        ),
      );
      setPlannedAllInterfacesPreview(
        ((res.parsed_intent as any)?.planned_all_interfaces_preview as any) ?? null
      );
      setSchemaEditableFields(res.schema_editable_fields ?? []);
      const params = (res.parsed_intent as any)?.parameters ?? {};
      setSchemaParamValues(
        Object.fromEntries(
          (res.schema_editable_fields ?? []).map((f) => [f.field, params[f.field] ?? ''])
        )
      );
      setInterfacesForSelection((res as any).interfaces_for_selection ?? null);
      setPreviewParsedIntent((res.parsed_intent as Record<string, unknown>) ?? null);
      setInterfaceBindingMode('no');
      setVlanSelectedInterfaces(new Set());
      skipNextInventoryRefreshRef.current = true;
      setStep('confirm');
    } catch (e: unknown) {
      setErrorMessage(formatAnsibleApiError(e));
      setStep('error');
    }
  }, [intentDescription, ansibleModel, parseIntent]);

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
      setTargetHosts(initialPreviewResult.target_hosts?.length ? initialPreviewResult.target_hosts : []);
      setTargetInterfaces(
        extractTargetInterfaceNamesFromParsedIntent(
          (initialPreviewResult.parsed_intent as Record<string, unknown>) ?? null,
        ),
      );
      setPlannedAllInterfacesPreview(
        ((initialPreviewResult.parsed_intent as any)?.planned_all_interfaces_preview as any) ?? null
      );
      setSchemaEditableFields(initialPreviewResult.schema_editable_fields ?? []);
      const params = (initialPreviewResult.parsed_intent as any)?.parameters ?? {};
      setSchemaParamValues(
        Object.fromEntries(
          (initialPreviewResult.schema_editable_fields ?? []).map((f) => [f.field, params[f.field] ?? ''])
        )
      );
      setInterfacesForSelection(initialPreviewResult.interfaces_for_selection ?? null);
      setPreviewParsedIntent((initialPreviewResult.parsed_intent as Record<string, unknown>) ?? null);
      skipNextInventoryRefreshRef.current = true;
      setStep('confirm');
    } else {
      runPreview();
    }
  }, [open, intentDescription, runPreview, initialPreviewResult]);

  // When backend expanded "all interfaces" via planned network context, pre-fill the UI chips
  // so user can review/select from real interface names (no free-text typing).
  useEffect(() => {
    const opts = plannedAllInterfacesPreview;
    if (!opts || !opts.devices || opts.devices.length === 0) return;
    if (targetInterfaces.length !== 0) return; // don't clobber user edits

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
    if (targetHosts.length > 0) {
      params.target_hosts = targetHosts;
    }
    const parsedAction = String((pip?.action as string) || '')
      .toLowerCase()
      .replace(/-/g, '_');
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
      } else if (parsedAction === 'configure_interface') {
        params.interfaces = targetInterfaces.map((name) => ({
          name,
          mode: 'access' as const,
          ...(accessVlan !== undefined ? { access_vlan: accessVlan } : {}),
        }));
      } else if (parsedAction === 'create_vlan') {
        // Primary create_vlan must carry interfaces so backend inventory override + VLAN summary match the UI chips.
        params.interfaces = targetInterfaces.map((name) => ({
          name,
          mode: 'access' as const,
          ...(accessVlan !== undefined ? { access_vlan: accessVlan } : {}),
        }));
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
    if (interfaceBindingMode !== 'no' && vlanSelectedInterfaces.size > 0) {
      const names = Array.from(vlanSelectedInterfaces);
      if (interfaceBindingMode === 'access') {
        params.interfaces = names.map((name) => ({ name, mode: 'access', access_vlan: vlanAccessVlan }));
      } else {
        const allowed = vlanAllowedVlans.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n));
        params.interfaces = names.map((name) => ({ name, mode: 'trunk', allowed_vlans: allowed, native_vlan: vlanNativeVlan }));
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
    skipNextInventoryRefreshRef.current = true;
  }, [open, intentDescription]);

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
                                <option value="no">No</option>
                                <option value="access">Access</option>
                                <option value="trunk">Trunk</option>
                              </select>
                            </div>
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
                                  <label className="text-xs text-base-content/70">Native VLAN</label>
                                  <input
                                    type="number"
                                    min={1}
                                    max={4094}
                                    className="input input-bordered input-sm w-24"
                                    value={vlanNativeVlan}
                                    onChange={(e) => setVlanNativeVlan(parseInt(e.target.value, 10) || 1)}
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
                              type={f.type === 'number' ? 'number' : 'text'}
                              className="input input-bordered input-sm flex-1 min-w-0 max-w-xs"
                              value={schemaParamValues[f.field] ?? ''}
                              onChange={(e) =>
                                setSchemaParamValues((prev) => ({
                                  ...prev,
                                  [f.field]: f.type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value,
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
                    Target interfaces (e.g. to shutdown)
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
                        <p className="text-xs text-base-content/60 mb-2">
                          {plannedAllInterfacesPreview.note ||
                            'Select interface(s) from planned context (management-style names excluded).'}
                        </p>
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
