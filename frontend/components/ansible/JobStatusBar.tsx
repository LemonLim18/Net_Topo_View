import React, { useEffect, useRef } from 'react';
import { CheckCircle2, Circle, Loader2, AlertCircle } from 'lucide-react';
import type { AnsibleJob } from '../../typesLLM';

interface JobStatusBarProps {
  job: AnsibleJob | null;
  sseLines: string[];
  isStreaming: boolean;
}

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

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [sseLines]);

  if (!job) return null;

  const currentStageIdx = STAGES.findIndex(s => s.key === job.stage) ?? -1;

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

      {/* Live log lines (SSE) */}
      {(isStreaming || sseLines.length > 0) && (
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
    </div>
  );
};

export default JobStatusBar;
