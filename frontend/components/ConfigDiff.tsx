import React from 'react';
import { Plus, Minus, Equal } from 'lucide-react';

interface ConfigDiffProps {
  oldConfig: string;
  newConfig: string;
  fromLabel?: string;
  toLabel?: string;
}

interface DiffLine {
  type: 'add' | 'remove' | 'context' | 'header';
  content: string;
}

function parseDiff(unified: string): DiffLine[] {
  if (!unified) return [];
  return unified.split('\n').map(line => {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) {
      return { type: 'header' as const, content: line };
    }
    if (line.startsWith('+')) {
      return { type: 'add' as const, content: line.substring(1) };
    }
    if (line.startsWith('-')) {
      return { type: 'remove' as const, content: line.substring(1) };
    }
    return { type: 'context' as const, content: line.startsWith(' ') ? line.substring(1) : line };
  });
}

const ConfigDiff: React.FC<ConfigDiffProps> = ({
  oldConfig,
  newConfig,
  fromLabel = 'Current',
  toLabel = 'Proposed',
}) => {
  // Generate a simple unified diff client-side for display
  const oldLines = oldConfig.split('\n');
  const newLines = newConfig.split('\n');

  // Build a simple line-by-line comparison
  const maxLen = Math.max(oldLines.length, newLines.length);
  const diffLines: DiffLine[] = [];

  // Simple diff: show removed old lines then added new lines
  // For a proper diff we rely on the backend, but for preview this is sufficient
  const oldSet = new Set(oldLines.filter(l => l.trim()));
  const newSet = new Set(newLines.filter(l => l.trim()));

  // Lines only in old (removed)
  const removed = oldLines.filter(l => l.trim() && !newSet.has(l));
  // Lines only in new (added)
  const added = newLines.filter(l => l.trim() && !oldSet.has(l));
  // Lines in both (context)
  const common = oldLines.filter(l => l.trim() && newSet.has(l));

  if (removed.length === 0 && added.length === 0) {
    return (
      <div className="alert alert-success shadow-sm">
        <Equal className="h-4 w-4" />
        <span>Configurations are identical. No changes detected.</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Summary Bar */}
      <div className="flex items-center gap-4 text-sm">
        <div className="badge badge-success gap-1">
          <Plus className="h-3 w-3" />
          {added.length} additions
        </div>
        <div className="badge badge-error gap-1">
          <Minus className="h-3 w-3" />
          {removed.length} deletions
        </div>
        <div className="badge badge-ghost">
          {common.length} unchanged
        </div>
      </div>

      {/* Diff Display */}
      <div className="bg-base-300 rounded-lg overflow-hidden font-mono text-sm">
        {/* Header */}
        <div className="flex border-b border-base-content/10">
          <div className="flex-1 px-4 py-2 text-error/80 font-semibold bg-error/5">
            ── {fromLabel}
          </div>
          <div className="flex-1 px-4 py-2 text-success/80 font-semibold bg-success/5">
            ── {toLabel}
          </div>
        </div>

        {/* Side-by-side content */}
        <div className="flex max-h-96 overflow-y-auto">
          {/* Old config side */}
          <div className="flex-1 border-r border-base-content/10">
            {oldLines.map((line, i) => {
              const isRemoved = line.trim() && !newSet.has(line);
              return (
                <div
                  key={`old-${i}`}
                  className={`px-4 py-0.5 text-xs leading-5 whitespace-pre ${
                    isRemoved 
                      ? 'bg-error/10 text-error border-l-2 border-error' 
                      : 'text-base-content/70'
                  }`}
                >
                  <span className="select-none opacity-40 mr-3 inline-block w-4 text-right">
                    {i + 1}
                  </span>
                  {line || '\u00A0'}
                </div>
              );
            })}
          </div>

          {/* New config side */}
          <div className="flex-1">
            {newLines.map((line, i) => {
              const isAdded = line.trim() && !oldSet.has(line);
              return (
                <div
                  key={`new-${i}`}
                  className={`px-4 py-0.5 text-xs leading-5 whitespace-pre ${
                    isAdded 
                      ? 'bg-success/10 text-success border-l-2 border-success' 
                      : 'text-base-content/70'
                  }`}
                >
                  <span className="select-none opacity-40 mr-3 inline-block w-4 text-right">
                    {i + 1}
                  </span>
                  {line || '\u00A0'}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ConfigDiff;
