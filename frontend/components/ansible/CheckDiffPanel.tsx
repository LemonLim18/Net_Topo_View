import React from 'react';
import { CheckCircle2, XCircle, Loader2, TerminalSquare } from 'lucide-react';
import type { AnsibleCheckResult } from '../../typesLLM';

interface CheckDiffPanelProps {
  result: AnsibleCheckResult | null;
  isLoading: boolean;
}

const CheckDiffPanel: React.FC<CheckDiffPanelProps> = ({ result, isLoading }) => {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm opacity-70">
        <Loader2 className="h-4 w-4 animate-spin" />
        Running dry-run check…
      </div>
    );
  }

  if (!result) return null;

  const lines = result.diff.split('\n');

  return (
    <div className="flex flex-col border-t border-base-300 bg-base-200">
      {/* Summary bar */}
      <div className="flex items-center gap-3 px-4 py-2 text-xs border-b border-base-300 flex-shrink-0">
        <TerminalSquare className="h-4 w-4 opacity-60" />
        <span className="font-semibold">Dry-Run Result</span>
        <span className={`badge badge-sm ${result.rc === 0 ? 'badge-success' : 'badge-error'}`}>
          rc={result.rc}
        </span>
        {result.changed
          ? <span className="badge badge-sm badge-warning">CHANGED</span>
          : <span className="badge badge-sm badge-ghost">NO CHANGE</span>
        }
        {result.rc === 0
          ? <CheckCircle2 className="h-4 w-4 text-success ml-auto" />
          : <XCircle className="h-4 w-4 text-error ml-auto" />
        }
        <span className="opacity-60 ml-1 truncate max-w-xs">{result.summary}</span>
      </div>

      {/* Diff output */}
      <div className="overflow-auto max-h-64 p-3 font-mono text-xs leading-5">
        {lines.map((line, i) => {
          let cls = 'text-base-content opacity-70';
          if (line.startsWith('+++') || line.startsWith('---')) cls = 'text-blue-400 font-semibold';
          else if (line.startsWith('+'))  cls = 'text-success';
          else if (line.startsWith('-'))  cls = 'text-error';
          else if (line.startsWith('@@')) cls = 'text-blue-400 opacity-80';
          return (
            <div key={i} className={cls}>
              <pre className="whitespace-pre-wrap break-all">{line || '\u00A0'}</pre>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default CheckDiffPanel;
