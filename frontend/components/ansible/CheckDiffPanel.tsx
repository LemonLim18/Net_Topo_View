import React, { useState } from 'react';
import { CheckCircle2, XCircle, Loader2, TerminalSquare, Columns2, AlignJustify } from 'lucide-react';
import type { AnsibleCheckResult } from '../../typesLLM';
import ConfigDiff from '../ConfigDiff';

interface CheckDiffPanelProps {
  result: AnsibleCheckResult | null;
  isLoading: boolean;
}

/** Extract before/after blocks from unified diff for side-by-side view */
function extractBeforeAfter(diff: string): { before: string; after: string } | null {
  const lines = diff.split('\n');
  const before: string[] = [];
  const after: string[] = [];
  let hasContent = false;

  for (const line of lines) {
    if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@')) continue;
    if (line.startsWith('-'))  { before.push(line.substring(1)); hasContent = true; }
    else if (line.startsWith('+')) { after.push(line.substring(1)); hasContent = true; }
    else { const ctx = line.startsWith(' ') ? line.substring(1) : line; before.push(ctx); after.push(ctx); }
  }

  return hasContent ? { before: before.join('\n'), after: after.join('\n') } : null;
}

const CheckDiffPanel: React.FC<CheckDiffPanelProps> = ({ result, isLoading }) => {
  const [sideBySide, setSideBySide] = useState(false);
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm opacity-70">
        <Loader2 className="h-4 w-4 animate-spin" />
        Running dry-run check…
      </div>
    );
  }

  if (!result) return null;

  const hasStderr = result.rc !== 0 && result.stderr && result.stderr.trim().length > 0;
  const diffContent = result.diff || '';
  const lines = diffContent.split('\n');
  const parsed = extractBeforeAfter(diffContent);

  return (
    <div className="flex flex-col border-t border-base-300 bg-base-200">
      {/* Summary bar */}
      <div className="flex items-center gap-3 px-4 py-2 text-xs border-b border-base-300 shrink-0">
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
        {/* Side-by-side toggle */}
        {parsed && (
          <button
            className={`btn btn-ghost btn-xs ml-2 ${sideBySide ? 'btn-active' : ''}`}
            onClick={() => setSideBySide(v => !v)}
            title={sideBySide ? 'Unified diff' : 'Side-by-side diff'}
          >
            {sideBySide ? <AlignJustify className="h-3 w-3" /> : <Columns2 className="h-3 w-3" />}
          </button>
        )}
      </div>

      {/* When failed: show stderr first so user sees connection/auth/task errors */}
      {hasStderr && (
        <div className="px-4 py-2 border-b border-base-300 bg-error/10">
          <div className="text-xs font-semibold text-error mb-1">Why it failed</div>
          <pre className="font-mono text-xs leading-5 whitespace-pre-wrap break-all text-error-content/90 max-h-48 overflow-auto">
            {result.stderr!.trim()}
          </pre>
        </div>
      )}

      {/* Diff output — unified or side-by-side */}
      {sideBySide && parsed ? (
        <div className="overflow-auto max-h-64 p-3">
          <ConfigDiff
            oldConfig={parsed.before}
            newConfig={parsed.after}
            fromLabel="Before"
            toLabel="After"
          />
        </div>
      ) : (
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
      )}
    </div>
  );
};

export default CheckDiffPanel;
