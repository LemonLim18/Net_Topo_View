import React, { useState, useEffect, useCallback } from 'react';
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

export interface PlaybookStudioOverlayProps {
  open: boolean;
  onClose: () => void;
  intentDescription: string;
  onPlaybookReady: (payload: {
    jobId: number;
    content: string;
    name: string;
    entrypointPath?: string | null;
    files?: AnsibleGeneratedArtifactFile[];
  }) => void;
  ansibleModel: { provider: string; model: string; ollama_base_url?: string | null };
  parseIntent: (text: string) => { vendor: string; feature: string; target_hosts: string[] };
}

type Step = 'thinking' | 'confirm' | 'generating' | 'result' | 'error';

export function PlaybookStudioOverlay({
  open,
  onClose,
  intentDescription,
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
  const [resultPayload, setResultPayload] = useState<{
    jobId: number;
    content: string;
    name: string;
    entrypointPath?: string | null;
    files?: AnsibleGeneratedArtifactFile[];
  } | null>(null);

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
      setTargetHosts(
        res.target_hosts?.length ? res.target_hosts : target_hosts?.length ? target_hosts : []
      );
      setStep('confirm');
    } catch (e: unknown) {
      const err = e as { message?: string; detail?: { message?: string; issues?: string[] } };
      const msg =
        err?.message ||
        (typeof err?.detail === 'object' && err.detail?.message
          ? err.detail.message + (Array.isArray(err.detail.issues) && err.detail.issues.length ? '\n' + err.detail.issues.join('\n') : '')
          : String(e));
      setErrorMessage(msg);
      setStep('error');
    }
  }, [intentDescription, ansibleModel, parseIntent]);

  useEffect(() => {
    if (open && intentDescription.trim()) {
      runPreview();
    }
  }, [open, intentDescription, runPreview]);

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

  const handleConfirm = useCallback(async () => {
    setStep('generating');
    setErrorMessage(null);
    const { vendor, feature, params } = parseIntent(intentDescription);
    const firstRefPath = selectedRefs[0]?.path;
    if (firstRefPath) {
      loadReferenceContent(firstRefPath);
    } else {
      setReferenceContent('# No reference template selected');
    }
    const hostsToUse = targetHosts.length ? targetHosts : ['all'];
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
      });
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
      const err = e as { message?: string; detail?: { message?: string; issues?: string[] } };
      const msg =
        err?.message ||
        (typeof err?.detail === 'object' && err.detail?.message
          ? err.detail.message + (Array.isArray(err.detail.issues) && err.detail.issues.length ? '\n' + err.detail.issues.join('\n') : '')
          : String(e));
      setErrorMessage(msg);
      setStep('error');
      Toast.error('Generation failed', msg);
    }
  }, [intentDescription, selectedRefs, targetHosts, ansibleModel, parseIntent, loadReferenceContent, onPlaybookReady]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
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
              {step === 'generating' && 'Generating playbook…'}
              {step === 'result' && 'Generated'}
              {step === 'error' && 'Error'}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost btn-sm btn-square"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
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
              <div className="mb-4">
                <p className="text-xs font-semibold text-base-content/70 uppercase tracking-wider mb-2">
                  Target inventory — devices / interfaces
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

          {step === 'generating' && (
            <div className="flex flex-col items-center justify-center py-12 gap-4">
              <Loader2 className="w-10 h-10 text-primary animate-spin" />
              <p className="text-sm text-base-content/80">
                Generating playbook from templates — only values (green) are filled; structure (red) is preserved.
              </p>
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
                  <div className="flex-1 min-h-0 flex items-center justify-center bg-base-200">
                    {step === 'generating' ? (
                      <div className="flex flex-col items-center gap-2 text-base-content/60">
                        <Loader2 className="w-8 h-8 text-primary animate-spin" />
                        <span className="text-sm">Generating…</span>
                      </div>
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
              {step === 'result' && resultPayload && (
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
