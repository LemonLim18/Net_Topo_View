import React, { useState, useEffect, useRef } from 'react';
import {
  Loader2,
  CheckCircle,
  AlertCircle,
  FileText,
  Clock,
  HardDrive,
  Server,
  Sparkles,
  Save,
  X,
  RefreshCw,
} from 'lucide-react';
import {
  networkConfigBackupApi,
  type ConfigBackupDevice,
  type ConfigBackupCaptureResponse,
  type ConfigBackupSnapshot,
} from '../src/services/api';
import { Toast } from '../src/utils/swal';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CaptureSnapshotModalProps {
  device: ConfigBackupDevice;
  onClose: () => void;
  onComplete: () => void; // refresh parent snapshot list
  snapshot?: ConfigBackupSnapshot; // if passed, we are viewing/editing an existing snapshot
}

type Phase = 'capturing' | 'preview' | 'error';

// Outside the component to track active in-flight capture promises across mounts
const activeCaptures: Record<number, Promise<ConfigBackupCaptureResponse>> = {};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const CaptureSnapshotModal: React.FC<CaptureSnapshotModalProps> = ({
  device,
  onClose,
  onComplete,
  snapshot,
}) => {
  const [phase, setPhase] = useState<Phase>(snapshot ? 'preview' : 'capturing');
  const [captureResult, setCaptureResult] = useState<ConfigBackupCaptureResponse | null>(
    snapshot
      ? {
          snapshot_id: snapshot.id,
          device_id: snapshot.device_id,
          hostname: device.hostname,
          captured_at: snapshot.captured_at || '',
          message: 'Viewing stored snapshot',
        }
      : null
  );
  const [errorMsg, setErrorMsg] = useState<string>('');

  // Preview phase state
  const [configText, setConfigText] = useState<string | null>(null);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [description, setDescription] = useState(snapshot?.description || '');
  const [aiSuggesting, setAiSuggesting] = useState(false);
  const [savingDesc, setSavingDesc] = useState(false);
  const [descSaved, setDescSaved] = useState(false);
  const [viewMode, setViewMode] = useState<'config' | 'diff'>('config');
  const [diffData, setDiffData] = useState<{
    snapshot_id: number;
    has_previous: boolean;
    previous_snapshot_id: number | null;
    additions: number;
    deletions: number;
    diff_text: string;
  } | null>(null);
  const [loadingDiff, setLoadingDiff] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ── Phase 1: Capture immediately on mount (or load existing snapshot) ──────────────────────
  useEffect(() => {
    let cancelled = false;

    if (snapshot) {
      // Just load existing snapshot content and diff
      const loadExisting = async () => {
        setLoadingConfig(true);
        setLoadingDiff(true);
        try {
          const content = await networkConfigBackupApi.getSnapshotContent(snapshot.id);
          if (!cancelled) setConfigText(content.config_text);
        } catch {
          if (!cancelled) setConfigText(null);
        } finally {
          if (!cancelled) setLoadingConfig(false);
        }

        try {
          const diff = await networkConfigBackupApi.getSnapshotDiff(snapshot.id);
          if (!cancelled) setDiffData(diff);
        } catch (e) {
          console.error('Failed to load snapshot diff:', e);
        } finally {
          if (!cancelled) setLoadingDiff(false);
        }
      };

      loadExisting();
      return () => { cancelled = true; };
    }

    const doCapture = async () => {
      try {
        let promise = activeCaptures[device.id];
        if (!promise) {
          promise = networkConfigBackupApi.captureSnapshot(device.id).finally(() => {
            delete activeCaptures[device.id];
          });
          activeCaptures[device.id] = promise;
        }
        const res = await promise;
        if (cancelled) return;
        setCaptureResult(res);
        setPhase('preview');

        // Load full config for preview
        setLoadingConfig(true);
        setLoadingDiff(true);
        try {
          const content = await networkConfigBackupApi.getSnapshotContent(res.snapshot_id);
          if (!cancelled) setConfigText(content.config_text);
        } catch {
          if (!cancelled) setConfigText(null);
        } finally {
          if (!cancelled) setLoadingConfig(false);
        }

        try {
          const diff = await networkConfigBackupApi.getSnapshotDiff(res.snapshot_id);
          if (!cancelled) setDiffData(diff);
        } catch (e) {
          console.error('Failed to load snapshot diff:', e);
        } finally {
          if (!cancelled) setLoadingDiff(false);
        }

        // Signal parent to refresh snapshot list
        onComplete();
      } catch (e: any) {
        if (cancelled) return;
        setErrorMsg(e?.message ?? 'Capture failed — check device reachability and SSH credentials.');
        setPhase('error');
      }
    };

    doCapture();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  // ── Handlers ───────────────────────────────────────────────────
  const handleRetry = async () => {
    setPhase('capturing');
    setErrorMsg('');
    setCaptureResult(null);
    setConfigText(null);
    setDiffData(null);
    setViewMode('config');
    setDescription('');
    setDescSaved(false);

    try {
      let promise = activeCaptures[device.id];
      if (!promise) {
        promise = networkConfigBackupApi.captureSnapshot(device.id).finally(() => {
          delete activeCaptures[device.id];
        });
        activeCaptures[device.id] = promise;
      }
      const res = await promise;
      setCaptureResult(res);
      setPhase('preview');

      setLoadingConfig(true);
      setLoadingDiff(true);
      try {
        const content = await networkConfigBackupApi.getSnapshotContent(res.snapshot_id);
        setConfigText(content.config_text);
      } catch {
        setConfigText(null);
      } finally {
        setLoadingConfig(false);
      }

      try {
        const diff = await networkConfigBackupApi.getSnapshotDiff(res.snapshot_id);
        setDiffData(diff);
      } catch (e) {
        console.error('Failed to load snapshot diff:', e);
      } finally {
        setLoadingDiff(false);
      }
      onComplete();
    } catch (e: any) {
      setErrorMsg(e?.message ?? 'Capture failed');
      setPhase('error');
    }
  };

  const handleAiSuggest = async () => {
    if (!captureResult) return;
    setAiSuggesting(true);
    try {
      const res = await networkConfigBackupApi.suggestDescription(captureResult.snapshot_id);
      setDescription(res.description || '');
      // Focus and select so user can easily edit
      setTimeout(() => textareaRef.current?.focus(), 100);
    } catch (e: any) {
      Toast.error('AI suggestion failed', e?.message ?? 'Unknown error');
    } finally {
      setAiSuggesting(false);
    }
  };

  const handleSaveDescription = async () => {
    if (!captureResult || !description.trim()) return;
    setSavingDesc(true);
    try {
      await networkConfigBackupApi.updateSnapshotDescription(captureResult.snapshot_id, description.trim());
      setDescSaved(true);
      Toast.success('Description saved');
      onComplete(); // refresh list so description shows
    } catch (e: any) {
      Toast.error('Save failed', e?.message ?? 'Unknown error');
    } finally {
      setSavingDesc(false);
    }
  };

  const formatCapturedAt = (ts?: string) => {
    if (!ts) return '—';
    try {
      let normalized = ts;
      if (!ts.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(ts)) {
        normalized = ts + 'Z';
      }
      return new Date(normalized).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    } catch {
      return ts;
    }
  };

  // ── Render: Phase 1 — Capturing ───────────────────────────────
  if (phase === 'capturing') {
    return (
      <div className="modal modal-open" id="capture-snapshot-modal">
        <div className="modal-box max-w-lg bg-base-100 border border-base-300 shadow-2xl p-0 overflow-hidden">
          <div className="flex flex-col items-center justify-center py-16 px-8 gap-5">
            {/* Pulsing ring animation */}
            <div className="relative">
              <div className="absolute inset-0 rounded-full border-2 border-primary/30 animate-ping" />
              <div className="relative p-4 rounded-full bg-primary/10 border border-primary/20">
                <Loader2 className="h-8 w-8 text-primary animate-spin" />
              </div>
            </div>
            <div className="text-center space-y-2">
              <h3 className="text-base font-semibold text-base-content">
                Capturing Configuration
              </h3>
              <p className="text-xs text-base-content/60 leading-relaxed max-w-xs">
                Connecting to <strong className="text-primary">{device.hostname}</strong> via SSH
                and retrieving the running configuration…
              </p>
            </div>
            {/* Subtle progress dots */}
            <div className="flex gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        </div>
        <div className="modal-backdrop bg-black/60" />
      </div>
    );
  }

  // ── Render: Error ─────────────────────────────────────────────
  if (phase === 'error') {
    return (
      <div className="modal modal-open" id="capture-snapshot-modal">
        <div className="modal-box max-w-lg bg-base-100 border border-error/30 shadow-2xl p-0 overflow-hidden">
          <div className="flex flex-col items-center justify-center py-12 px-8 gap-5">
            <div className="p-4 rounded-full bg-error/10 border border-error/20">
              <AlertCircle className="h-8 w-8 text-error" />
            </div>
            <div className="text-center space-y-2">
              <h3 className="text-base font-semibold text-base-content">Capture Failed</h3>
              <p className="text-xs text-base-content/60 leading-relaxed max-w-sm">
                {errorMsg}
              </p>
            </div>
            <div className="flex gap-3 mt-2">
              <button className="btn btn-sm btn-outline gap-1.5" onClick={handleRetry}>
                <RefreshCw className="h-3.5 w-3.5" />
                Retry
              </button>
              <button className="btn btn-sm btn-ghost" onClick={onClose}>
                Close
              </button>
            </div>
          </div>
        </div>
        <div className="modal-backdrop bg-black/60" onClick={onClose} />
      </div>
    );
  }

  // ── Render: Phase 2 — Preview & Describe ──────────────────────
  return (
    <div className="modal modal-open" id="capture-snapshot-modal">
      <div className="modal-box max-w-4xl max-h-[88vh] flex flex-col p-0 overflow-hidden bg-base-100 border border-base-300 shadow-2xl">
        {/* ─── Header ─── */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-base-content/10 bg-base-200/60">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-1.5 rounded-lg bg-success/15">
              <CheckCircle className="h-4 w-4 text-success" />
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-bold text-base-content truncate">
                {snapshot ? `Snapshot #${snapshot.id} Details` : 'Snapshot Captured Successfully'}
              </h3>
              <p className="text-[11px] text-base-content/50 truncate">
                {device.hostname} — #{captureResult?.snapshot_id}
              </p>
            </div>
          </div>
          <button
            className="btn btn-sm btn-circle btn-ghost"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* View Mode Tabs */}
        <div className="flex border-b border-base-content/10 bg-base-200/40 px-4 shrink-0 select-none">
          <button
            className={`px-4 py-2 text-[11px] font-semibold uppercase tracking-wider border-b-2 transition-all ${
              viewMode === 'config'
                ? 'border-primary text-primary bg-primary/5'
                : 'border-transparent opacity-70 hover:opacity-100'
            }`}
            onClick={() => setViewMode('config')}
          >
            Full Configuration
          </button>
          <button
            className={`px-4 py-2 text-[11px] font-semibold uppercase tracking-wider border-b-2 transition-all ${
              viewMode === 'diff'
                ? 'border-primary text-primary bg-primary/5'
                : 'border-transparent opacity-70 hover:opacity-100'
            }`}
            onClick={() => setViewMode('diff')}
          >
            Changes (Diff)
          </button>
        </div>

        {viewMode === 'config' ? (
          <div className="flex-1 min-h-0 overflow-y-auto bg-neutral text-neutral-content custom-scrollbar">
            <div className="sticky top-0 z-10 flex items-center gap-2 px-4 py-2 bg-neutral/95 backdrop-blur-sm border-b border-white/5">
              <FileText className="h-3.5 w-3.5 text-primary/70" />
              <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-content/50">
                Running Configuration Preview
              </span>
            </div>
            <div className="px-4 py-3 font-mono text-xs leading-relaxed">
              {loadingConfig ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 opacity-50">
                  <Loader2 className="h-6 w-6 animate-spin" />
                  <p className="text-xs">Loading configuration…</p>
                </div>
              ) : configText ? (
                <pre className="whitespace-pre-wrap">{configText}</pre>
              ) : (
                <div className="text-center py-16 opacity-40 italic text-xs">
                  No configuration content available.
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 min-h-0 flex flex-col bg-neutral text-neutral-content font-mono">
            {loadingDiff ? (
              <div className="grow flex flex-col items-center justify-center py-16 gap-3 opacity-50">
                <Loader2 className="h-6 w-6 animate-spin" />
                <p className="text-xs">Calculating differences…</p>
              </div>
            ) : diffData ? (
              <div className="grow flex flex-col min-h-0">
                {/* Diff Summary Bar mimicking IDE file changed list */}
                <div className="flex items-center gap-3 px-4 py-2 bg-black/20 border-b border-white/5 shrink-0 select-none">
                  <div className="p-1 rounded bg-white/5 border border-white/10">
                    <FileText className="h-3.5 w-3.5 text-primary" />
                  </div>
                  <div className="flex items-center gap-2 text-[11px] font-mono">
                    {diffData.has_previous ? (
                      <>
                        <span className="text-success font-bold">+{diffData.additions}</span>
                        <span className="text-error font-bold">-{diffData.deletions}</span>
                        <span className="opacity-60 ml-2">
                          running-config diff (vs Snapshot #{diffData.previous_snapshot_id})
                        </span>
                      </>
                    ) : (
                      <span className="opacity-50">Baseline Snapshot (no previous version)</span>
                    )}
                  </div>
                </div>

                {/* Diff viewer scrollable lines */}
                <div className="grow overflow-y-auto p-4 custom-scrollbar">
                  {!diffData.has_previous ? (
                    <div className="text-center py-16 opacity-45 italic text-xs">
                      This is the initial baseline snapshot. No previous configuration exists to diff against.
                    </div>
                  ) : diffData.diff_text.trim() === '' ? (
                    <div className="text-center py-16 opacity-45 italic text-xs">
                      No configuration differences detected compared to the previous snapshot.
                    </div>
                  ) : (
                    <div className="flex flex-col text-xs leading-relaxed font-mono">
                      {diffData.diff_text.split('\n').map((line, idx) => {
                        const isAdded = line.startsWith('+');
                        const isRemoved = line.startsWith('-');
                        const isHunkHeader = line.startsWith('@@');
                        
                        let lineClass = 'opacity-80';
                        if (isAdded) lineClass = 'bg-success/10 text-success border-l-2 border-success pl-1';
                        if (isRemoved) lineClass = 'bg-error/10 text-error border-l-2 border-error pl-1';
                        if (isHunkHeader) lineClass = 'text-info/80 font-bold bg-info/5 opacity-80';

                        return (
                          <div 
                            key={idx} 
                            className={`py-0.5 px-2 whitespace-pre-wrap ${lineClass}`}
                          >
                            {line}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="grow flex items-center justify-center py-16 opacity-40 italic text-xs">
                Could not compute differences.
              </div>
            )}
          </div>
        )}

        {/* ─── Snapshot Metadata ─── */}
        <div className="px-5 py-3 border-t border-base-content/10 bg-base-200/40">
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-bold mb-2">
            Snapshot Details
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-base-content/70">
              <Server className="h-3 w-3 text-primary/60" />
              <span className="font-medium">{device.hostname}</span>
              {device.vendor && (
                <span className="text-base-content/40">({device.vendor})</span>
              )}
            </div>
            <div className="flex items-center gap-1.5 text-xs text-base-content/70">
              <Clock className="h-3 w-3 text-primary/60" />
              <span>{formatCapturedAt(captureResult?.captured_at)}</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-base-content/70">
              <HardDrive className="h-3 w-3 text-primary/60" />
              <span>
                {configText 
                  ? `${(configText.length / 1024).toFixed(1)} KB` 
                  : snapshot?.config_size 
                    ? `${(snapshot.config_size / 1024).toFixed(1)} KB` 
                    : '—'}
              </span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-base-content/70">
              <FileText className="h-3 w-3 text-primary/60" />
              <span>ID #{captureResult?.snapshot_id}</span>
            </div>
          </div>
        </div>

        {/* ─── Description Input ─── */}
        <div className="px-5 py-4 border-t border-base-content/10 bg-base-100">
          <div className="flex items-center justify-between mb-2">
            <label className="text-[11px] uppercase tracking-wider text-base-content/50 font-bold">
              Description
              <span className="normal-case tracking-normal font-normal ml-1.5 text-base-content/30">
                (optional)
              </span>
            </label>
            <button
              className="btn btn-xs gap-1.5 border-primary/30 bg-primary/5 hover:bg-primary/15 text-primary font-medium"
              onClick={handleAiSuggest}
              disabled={aiSuggesting || !captureResult}
              title="Let AI suggest a description based on the captured config"
            >
              {aiSuggesting ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="h-3 w-3" />
              )}
              {aiSuggesting ? 'Thinking…' : 'AI Suggest'}
            </button>
          </div>

          <textarea
            ref={textareaRef}
            className="textarea textarea-bordered w-full text-sm bg-base-200/50 focus:border-primary/40 resize-none"
            rows={2}
            maxLength={500}
            placeholder="e.g., Write the purpose of this snapshot, any notable config changes, or relevant context for future reference."
            value={description}
            onChange={(e) => { setDescription(e.target.value); setDescSaved(false); }}
          />

          <div className="flex items-center justify-between mt-3">
            <div className="text-[10px] text-base-content/30">
              {description.length > 0 && `${description.length}/500`}
            </div>
            <div className="flex gap-2">
              <button className="btn btn-sm btn-ghost" onClick={onClose}>
                {descSaved ? 'Done' : 'Skip'}
              </button>
              <button
                className="btn btn-sm btn-primary gap-1.5"
                onClick={handleSaveDescription}
                disabled={savingDesc || !description.trim() || descSaved}
              >
                {savingDesc ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : descSaved ? (
                  <CheckCircle className="h-3.5 w-3.5" />
                ) : (
                  <Save className="h-3.5 w-3.5" />
                )}
                {descSaved ? 'Saved' : 'Save Description'}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div className="modal-backdrop bg-black/60" onClick={onClose} />
    </div>
  );
};

export default CaptureSnapshotModal;
