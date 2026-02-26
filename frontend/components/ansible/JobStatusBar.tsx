import React, { useEffect, useRef, useState, useCallback } from 'react';
import { CheckCircle2, Circle, Loader2, AlertCircle, History, ScrollText } from 'lucide-react';
import { deployApi } from '../../src/services/api';
import type { AnsibleJob } from '../../typesLLM';

interface JobStatusBarProps {
  job: AnsibleJob | null;
  sseLines: string[];
  isStreaming: boolean;
}

type BottomTab = 'output' | 'history' | 'audit';

const STAGES = [
  { key: 'plan',     label: 'Plan' },
  { key: 'approve',  label: 'Approve' },
  { key: 'apply',    label: 'Apply' },
  { key: 'commit',   label: 'Commit' },
];

const STATUS_COLOUR: Record<string, string> = {
  success:     'text-success',
  failed:      'text-error',
  rolled_back: 'text-warning',
  running:     'text-primary',
  approved:    'text-info',
  pending:     'text-base-content opacity-50',
};

const JobStatusBar: React.FC<JobStatusBarProps> = ({ job, sseLines, isStreaming }) => {
  const logRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<BottomTab>('output');
  const [jobs, setJobs] = useState<any[]>([]);
  const [audit, setAudit] = useState<any[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [loadingAudit, setLoadingAudit] = useState(false);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [sseLines]);

  const fetchJobs = useCallback(async () => {
    setLoadingJobs(true);
    try {
      const res = await deployApi.listJobs();
      setJobs(Array.isArray(res) ? res : []);
    } catch { setJobs([]); }
    finally { setLoadingJobs(false); }
  }, []);

  const fetchAudit = useCallback(async () => {
    setLoadingAudit(true);
    try {
      const res = await deployApi.getAuditLog(30);
      setAudit(res.entries ?? []);
    } catch { setAudit([]); }
    finally { setLoadingAudit(false); }
  }, []);

  // Load data when switching to a tab
  useEffect(() => {
    if (tab === 'history') fetchJobs();
    if (tab === 'audit') fetchAudit();
  }, [tab, fetchJobs, fetchAudit]);

  if (!job) return null;

  const currentStageIdx = STAGES.findIndex(s => s.key === job.stage) ?? -1;

  const tabBtn = (id: BottomTab, icon: React.ReactNode, label: string) => (
    <button
      className={`flex items-center gap-1 text-[11px] font-medium px-3 py-1.5 border-b-2 transition-colors ${
        tab === id
          ? 'border-primary text-primary'
          : 'border-transparent opacity-60 hover:opacity-100'
      }`}
      onClick={() => setTab(id)}
    >
      {icon}{label}
    </button>
  );

  return (
    <div className="border-t border-base-300 bg-base-100 flex-shrink-0">
      {/* Pipeline step indicator */}
      <div className="flex items-center gap-0 px-4 py-2 border-b border-base-200">
        {STAGES.map((s, idx) => {
          const done    = idx < currentStageIdx || job.status === 'success';
          const current = idx === currentStageIdx && job.status !== 'success';
          const failed  = job.status === 'failed' && idx === currentStageIdx;
          return (
            <React.Fragment key={s.key}>
              <div className={`flex items-center gap-1 text-xs font-medium px-2 py-1 rounded ${
                failed  ? 'text-error' :
                current ? 'text-primary' :
                done    ? 'text-success' :
                'opacity-30'
              }`}>
                {failed  ? <AlertCircle className="h-3.5 w-3.5" /> :
                 current && isStreaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> :
                 done    ? <CheckCircle2 className="h-3.5 w-3.5" /> :
                 <Circle className="h-3.5 w-3.5" />}
                {s.label}
              </div>
              {idx < STAGES.length - 1 && (
                <div className={`h-px w-5 ${done ? 'bg-success' : 'bg-base-300'}`} />
              )}
            </React.Fragment>
          );
        })}
        {job.status && (
          <span className={`ml-auto text-xs font-bold ${STATUS_COLOUR[job.status] ?? ''}`}>
            {job.status.toUpperCase()}
          </span>
        )}
      </div>

      {/* Bottom tabs */}
      <div className="flex border-b border-base-200 bg-base-100">
        {tabBtn('output', <Loader2 className="h-3 w-3" />, 'Output')}
        {tabBtn('history', <History className="h-3 w-3" />, 'History')}
        {tabBtn('audit', <ScrollText className="h-3 w-3" />, 'Audit')}
      </div>

      {/* Tab content */}
      {tab === 'output' && (isStreaming || sseLines.length > 0) && (
        <div
          ref={logRef}
          className="max-h-28 overflow-y-auto px-4 py-2 font-mono text-xs leading-5 bg-base-200"
        >
          {sseLines.slice(-50).map((line, i) => (
            <div key={i} className="opacity-80 whitespace-pre-wrap break-all">{line}</div>
          ))}
          {isStreaming && (
            <div className="flex items-center gap-1 opacity-50 mt-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>Running…</span>
            </div>
          )}
        </div>
      )}

      {tab === 'history' && (
        <div className="max-h-36 overflow-y-auto bg-base-200">
          {loadingJobs ? (
            <div className="flex items-center justify-center py-4 opacity-50 text-xs"><Loader2 className="h-3 w-3 animate-spin mr-1" /> Loading…</div>
          ) : jobs.length === 0 ? (
            <div className="text-xs text-center py-4 opacity-40">No deployment jobs yet</div>
          ) : (
            <table className="table table-xs table-pin-rows w-full">
              <thead><tr><th>ID</th><th>Status</th><th>Stage</th><th>Device</th><th>Created</th></tr></thead>
              <tbody>
                {jobs.slice(0, 20).map((j: any) => (
                  <tr key={j.id ?? j.job_id} className="hover">
                    <td className="font-mono text-[11px]">#{j.id ?? j.job_id}</td>
                    <td>
                      <span className={`badge badge-xs ${j.status === 'success' ? 'badge-success' : j.status === 'failed' ? 'badge-error' : j.status === 'running' ? 'badge-warning' : 'badge-ghost'}`}>
                        {j.status}
                      </span>
                    </td>
                    <td className="text-[11px] opacity-70">{j.stage ?? '—'}</td>
                    <td className="text-[11px] opacity-70">{j.device_id ?? '—'}</td>
                    <td className="text-[11px] opacity-50">{j.created_at ? new Date(j.created_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'audit' && (
        <div className="max-h-36 overflow-y-auto bg-base-200">
          {loadingAudit ? (
            <div className="flex items-center justify-center py-4 opacity-50 text-xs"><Loader2 className="h-3 w-3 animate-spin mr-1" /> Loading…</div>
          ) : audit.length === 0 ? (
            <div className="text-xs text-center py-4 opacity-40">No audit entries</div>
          ) : (
            <div className="space-y-0">
              {audit.slice(0, 25).map((e: any, i: number) => (
                <div key={e.id ?? i} className="flex items-start gap-2 px-4 py-1.5 text-xs hover:bg-base-300 border-b border-base-300/50">
                  <ScrollText className="h-3 w-3 mt-0.5 opacity-40 flex-shrink-0" />
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{e.action ?? e.event ?? '—'}</span>
                    {e.target && <span className="opacity-60 ml-1">→ {e.target}</span>}
                    {e.details && <p className="opacity-50 truncate">{e.details}</p>}
                  </div>
                  <span className="opacity-40 flex-shrink-0 text-[10px]">{e.created_at ?? e.timestamp ?? ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default JobStatusBar;
