import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { 
  PanelLeftClose, 
  PanelLeftOpen,
  CheckCircle,
  AlertTriangle,
  X,
  FileCode,
  Loader2,
  Copy,
  Check,
  Edit3,
  RotateCcw,
  ClipboardList,
  Zap,
  Bot,
  Shield,
  Activity,
  Terminal
} from 'lucide-react';

import { Role } from '../typesLLM';
import type { Message, ChatSession, ConfigProposal, DeployResult, IntentMeta } from '../typesLLM';
import { SidebarLLM } from '../components/SidebarLLM';
import { ChatInput } from '../components/ChatInput';
import { MessageBubble } from '../components/MessageBubble';
import { llmApi, proposalApi } from '../src/services/api';
import { Toast } from '../src/utils/swal';

import type { AppDomain } from '../types';

// Mock ID generator
const generateId = () => Math.random().toString(36).substring(2, 15);

interface ProposalReviewInput {
  mergedConfig: string;
  acceptedLineIndices: number[];
  rejectedLineIndices: number[];
}

interface ReviewableDiffLine {
  index: number;
  lineType: 'add' | 'remove';
  content: string;
}

type HunkLineKind = 'context' | 'add' | 'remove';

interface DiffHunkLine {
  kind: HunkLineKind;
  content: string;
  diffIndex: number;
}

interface DiffHunk {
  newStart: number;
  lines: DiffHunkLine[];
}

function parseReviewableDiffLines(diffText: string): ReviewableDiffLine[] {
  if (!diffText) return [];
  return diffText
    .split('\n')
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => !line.startsWith('+++') && !line.startsWith('---') && !line.startsWith('@@'))
    .filter(({ line }) => line.startsWith('+') || line.startsWith('-'))
    .map(({ line, index }) => ({
      index,
      lineType: line.startsWith('+') ? 'add' : 'remove',
      content: line.slice(1),
    }));
}

function buildMergedConfig(
  diffText: string,
  generatedConfig: string,
  decisions: Record<number, boolean>,
): string {
  const parseUnifiedDiffHunks = (unified: string): DiffHunk[] => {
    const diffLines = unified.split('\n');
    const hunks: DiffHunk[] = [];
    let current: DiffHunk | null = null;

    for (let index = 0; index < diffLines.length; index += 1) {
      const line = diffLines[index] ?? '';
      const hunkHeader = /^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/.exec(line);
      if (hunkHeader) {
        current = {
          newStart: Number(hunkHeader[1] ?? 1),
          lines: [],
        };
        hunks.push(current);
        continue;
      }

      if (!current || line.startsWith('\\')) continue;

      const prefix = line[0] ?? '';
      if (prefix === ' ' || prefix === '+' || prefix === '-') {
        const kind: HunkLineKind = prefix === ' ' ? 'context' : prefix === '+' ? 'add' : 'remove';
        current.lines.push({
          kind,
          content: line.slice(1),
          diffIndex: index,
        });
      }
    }

    return hunks;
  };

  const buildReviewedHunkChunk = (hunk: DiffHunk): string[] => {
    const chunk: string[] = [];
    for (const line of hunk.lines) {
      if (line.kind === 'context') {
        chunk.push(line.content);
        continue;
      }
      if (line.kind === 'add') {
        if (decisions[line.diffIndex] !== false) chunk.push(line.content);
        continue;
      }
      if (decisions[line.diffIndex] === false) {
        chunk.push(line.content);
      }
    }
    return chunk;
  };

  const buildAppliedHunkChunk = (hunk: DiffHunk): string[] => {
    return hunk.lines
      .filter((line) => line.kind === 'context' || line.kind === 'add')
      .map((line) => line.content);
  };

  const findSubsequenceNear = (
    source: string[],
    pattern: string[],
    expected: number,
  ): number => {
    if (pattern.length === 0) return Math.max(0, Math.min(expected, source.length));
    if (pattern.length > source.length) return -1;

    const candidates: number[] = [];
    for (let start = 0; start <= source.length - pattern.length; start += 1) {
      let matched = true;
      for (let i = 0; i < pattern.length; i += 1) {
        if (source[start + i] !== pattern[i]) {
          matched = false;
          break;
        }
      }
      if (matched) candidates.push(start);
    }

    if (candidates.length === 0) return -1;

    let best = candidates[0];
    let bestDistance = Math.abs(best - expected);
    for (const candidate of candidates.slice(1)) {
      const distance = Math.abs(candidate - expected);
      if (distance < bestDistance) {
        best = candidate;
        bestDistance = distance;
      }
    }
    return best;
  };

  const hunks = parseUnifiedDiffHunks(diffText);
  if (hunks.length === 0) return generatedConfig;

  const mergedLines = generatedConfig.split('\n');

  for (let h = hunks.length - 1; h >= 0; h -= 1) {
    const hunk = hunks[h];
    const appliedChunk = buildAppliedHunkChunk(hunk);
    const reviewedChunk = buildReviewedHunkChunk(hunk);
    const expectedStart = Math.max(0, hunk.newStart - 1);

    let removeLen = appliedChunk.length;
    let start = findSubsequenceNear(mergedLines, appliedChunk, expectedStart);

    if (start < 0) {
      const contextChunk = hunk.lines
        .filter((line) => line.kind === 'context')
        .map((line) => line.content);
      const contextStart = findSubsequenceNear(mergedLines, contextChunk, expectedStart);
      if (contextStart >= 0) {
        start = contextStart;
        removeLen = contextChunk.length;
      }
    }

    if (start < 0) {
      start = Math.max(0, Math.min(expectedStart, mergedLines.length));
      removeLen = 0;
    }

    mergedLines.splice(start, removeLen, ...reviewedChunk);
  }

  return mergedLines.join('\n');
}

function summarizeMergeNotes(reviewableLines: ReviewableDiffLine[], decisions: Record<number, boolean>): string {
  const rejectedAdds = reviewableLines.filter(
    (line) => line.lineType === 'add' && decisions[line.index] === false,
  ).length;
  const rejectedRemoves = reviewableLines.filter(
    (line) => line.lineType === 'remove' && decisions[line.index] === false,
  ).length;

  if (rejectedAdds === 0 && rejectedRemoves === 0) {
    return 'No rejected lines';
  }
  return `Rejected +: ${rejectedAdds} • Rejected - restored: ${rejectedRemoves}`;
}

// ==================== M19 SUB-COMPONENTS ====================

const RiskBadge: React.FC<{ level: 'low' | 'medium' | 'high' }> = ({ level }) => {
  const config = {
    low:    { color: 'badge-success', icon: '🟢', label: 'Low Risk' },
    medium: { color: 'badge-warning', icon: '🟡', label: 'Medium Risk' },
    high:   { color: 'badge-error',   icon: '🔴', label: 'High Risk' },
  };
  const { color, icon, label } = config[level];
  return <span className={`badge ${color} gap-1`}>{icon} {label}</span>;
};

/** ProposalCard — renders config, rollback, diff, and Accept/Revise/Reject buttons */
const ProposalCard: React.FC<{
  proposal: ConfigProposal;
  onAccept: (review: ProposalReviewInput) => void;
  onRevise: () => void;
  onReject: () => void;
  isDeploying: boolean;
}> = ({ proposal, onAccept, onRevise, onReject, isDeploying }) => {
  const [showRollback, setShowRollback] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [copied, setCopied] = useState(false);
  const reviewableLines = useMemo(
    () => parseReviewableDiffLines(proposal.diff_text),
    [proposal.diff_text],
  );
  const [lineDecisions, setLineDecisions] = useState<Record<number, boolean>>({});

  useEffect(() => {
    const initial: Record<number, boolean> = {};
    for (const line of reviewableLines) initial[line.index] = true;
    setLineDecisions(initial);
  }, [proposal.id, proposal.diff_text, reviewableLines]);

  const acceptedLineIndices = reviewableLines
    .filter(line => lineDecisions[line.index] !== false)
    .map(line => line.index);
  const rejectedLineIndices = reviewableLines
    .filter(line => lineDecisions[line.index] === false)
    .map(line => line.index);
  const mergedConfig = buildMergedConfig(
    proposal.diff_text,
    proposal.generated_config,
    lineDecisions,
  );
  const mergeNotes = summarizeMergeNotes(reviewableLines, lineDecisions);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-3 mt-3 border border-base-300 rounded-xl p-4 bg-base-200/50">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-primary" />
          <span className="font-semibold text-sm">Configuration Proposal</span>
        </div>
        <RiskBadge level={proposal.risk_level} />
      </div>

      {/* Target Info */}
      <div className="bg-base-300 rounded-lg p-3 text-sm space-y-1">
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-[60px]">Target:</span>
          <span className="font-mono">{proposal.target_device} ({proposal.vendor})</span>
        </div>
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-[60px]">Feature:</span>
          <span>{proposal.feature}</span>
        </div>
      </div>

      {/* AI Explanation */}
      {proposal.ai_explanation && (
        <div className="bg-base-300 rounded-lg p-3">
          <h4 className="font-semibold text-xs flex items-center gap-1 mb-2">
            <Zap className="w-3 h-3 text-warning" /> AI Explanation
          </h4>
          <p className="text-xs leading-relaxed whitespace-pre-wrap max-h-32 overflow-y-auto">
            {proposal.ai_explanation}
          </p>
        </div>
      )}

      {/* Proposed Configuration */}
      <div className="bg-base-300 rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 bg-base-200 border-b border-base-300">
          <span className="text-xs font-semibold flex items-center gap-1">
            <FileCode className="w-3 h-3 text-secondary" /> Proposed Config
          </span>
          <button
            onClick={() => handleCopy(proposal.generated_config)}
            className="btn btn-ghost btn-xs"
          >
            {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
          </button>
        </div>
        <pre className="p-3 text-xs overflow-x-auto max-h-48 overflow-y-auto font-mono bg-neutral-800 text-green-400">
          <code>{proposal.generated_config}</code>
        </pre>
      </div>

      {/* Rollback Commands (Collapsible) */}
      {proposal.rollback_config && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowRollback(!showRollback)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <RotateCcw className="w-3 h-3 text-error" /> Rollback Commands
            </span>
            <span className="text-xs">{showRollback ? '▲' : '▼'}</span>
          </button>
          {showRollback && (
            <pre className="p-3 text-xs overflow-x-auto max-h-32 overflow-y-auto font-mono bg-neutral-800 text-yellow-400">
              <code>{proposal.rollback_config}</code>
            </pre>
          )}
        </div>
      )}

      {/* Config Diff (Collapsible) */}
      {proposal.diff_text && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowDiff(!showDiff)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <ClipboardList className="w-3 h-3 text-info" /> Configuration Diff
            </span>
            <span className="text-xs">{showDiff ? '▲' : '▼'}</span>
          </button>
          {showDiff && (
            <div className="p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[11px] text-base-content/70">
                  Engineer line review: choose <span className="font-semibold">Keep</span> or <span className="font-semibold">Undo</span> per line before approval.
                </div>
                <div className="flex gap-1">
                  <button
                    type="button"
                    className="btn btn-xs btn-success btn-outline gap-1"
                    onClick={() => {
                      const all: Record<number, boolean> = {};
                      for (const l of reviewableLines) all[l.index] = true;
                      setLineDecisions(all);
                    }}
                  >
                    <CheckCircle className="w-3 h-3" /> Keep All
                  </button>
                  <button
                    type="button"
                    className="btn btn-xs btn-warning btn-outline gap-1"
                    onClick={() => {
                      const all: Record<number, boolean> = {};
                      for (const l of reviewableLines) all[l.index] = false;
                      setLineDecisions(all);
                    }}
                  >
                    <X className="w-3 h-3" /> Undo All
                  </button>
                </div>
              </div>
              <div className="max-h-48 overflow-y-auto border border-base-300 rounded-md bg-base-100">
                {reviewableLines.map((line) => (
                  <div
                    key={line.index}
                    className={`flex items-start gap-2 px-2 py-1 border-b border-base-300/50 text-xs font-mono ${
                      line.lineType === 'add' ? 'text-success' : 'text-error'
                    }`}
                  >
                    <div className="join join-horizontal mt-0.5">
                      <button
                        type="button"
                        className={`join-item btn btn-xs ${lineDecisions[line.index] !== false ? 'btn-success' : 'btn-ghost'}`}
                        onClick={() => setLineDecisions(prev => ({ ...prev, [line.index]: true }))}
                      >
                        Keep
                      </button>
                      <button
                        type="button"
                        className={`join-item btn btn-xs ${lineDecisions[line.index] === false ? 'btn-warning' : 'btn-ghost'}`}
                        onClick={() => setLineDecisions(prev => ({ ...prev, [line.index]: false }))}
                      >
                        Undo
                      </button>
                    </div>
                    <span className="opacity-60">{line.lineType === 'add' ? '+' : '-'}</span>
                    <span className="break-all">{line.content || ' '}</span>
                  </div>
                ))}
              </div>
              <div className="text-[11px] text-base-content/70">
                Accepted: {acceptedLineIndices.length} • Rejected: {rejectedLineIndices.length}
              </div>
              <div className="text-[11px] text-base-content/60">{mergeNotes}</div>
              <div className="rounded-md border border-base-300 overflow-hidden bg-base-300">
                <div className="px-2 py-1 text-[11px] font-semibold bg-base-200 border-b border-base-300">
                  Reviewed Preview (applies Keep/Undo)
                </div>
                <pre className="p-2 text-xs max-h-40 overflow-auto font-mono bg-neutral-800 text-green-400">
                  <code>{mergedConfig}</code>
                </pre>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Action Buttons */}
      {proposal.status === 'pending' && (
        <div className="border-t border-base-300 pt-3">
          <p className="text-xs text-center mb-2 text-base-content/70">
            Engineer approval saves reviewed lines first; device deployment requires admin approval.
          </p>
          <div className="flex gap-2 justify-center flex-wrap">
            <button
              onClick={() => onAccept({
                mergedConfig,
                acceptedLineIndices,
                rejectedLineIndices,
              })}
              className="btn btn-success btn-sm gap-1"
              disabled={isDeploying}
            >
              {isDeploying ? (
                <><Loader2 className="w-3 h-3 animate-spin" /> Approving...</>
              ) : (
                <><CheckCircle className="w-3 h-3" /> Approve Proposal</>
              )}
            </button>
            <button onClick={onRevise} className="btn btn-ghost btn-sm gap-1" disabled={isDeploying}>
              <Edit3 className="w-3 h-3" /> Revise
            </button>
            <button onClick={onReject} className="btn btn-ghost btn-sm gap-1 text-error" disabled={isDeploying}>
              <X className="w-3 h-3" /> Reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// ==================== M24: INTENT PIPELINE CARD ====================

const BlastRadiusBadge: React.FC<{ level: string; count: number }> = ({ level, count }) => {
  const cfg: Record<string, { color: string; label: string }> = {
    low:      { color: 'badge-success', label: 'Low' },
    medium:   { color: 'badge-warning', label: 'Medium' },
    high:     { color: 'badge-error',   label: 'High' },
    critical: { color: 'badge-error',   label: 'Critical' },
  };
  const { color, label } = cfg[level] || cfg.medium;
  return (
    <span className={`badge ${color} gap-1 text-xs`}>
      <Activity className="w-3 h-3" /> {label} blast — {count} device{count !== 1 ? 's' : ''}
    </span>
  );
};

/** IntentReviewCard — rich card for 3-stage intent pipeline results */
const IntentReviewCard: React.FC<{
  proposal: ConfigProposal;
  intentMeta: IntentMeta;
  onAccept: (review: ProposalReviewInput) => void;
  onRevise: () => void;
  onReject: () => void;
  isDeploying: boolean;
}> = ({ proposal, intentMeta, onAccept, onRevise, onReject, isDeploying }) => {
  const [showConfig, setShowConfig] = useState(false);
  const [showRollback, setShowRollback] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [showVerify, setShowVerify] = useState(false);
  const [copied, setCopied] = useState(false);
  const reviewableLines = useMemo(
    () => parseReviewableDiffLines(proposal.diff_text),
    [proposal.diff_text],
  );
  const [lineDecisions, setLineDecisions] = useState<Record<number, boolean>>({});

  useEffect(() => {
    const initial: Record<number, boolean> = {};
    for (const line of reviewableLines) initial[line.index] = true;
    setLineDecisions(initial);
  }, [proposal.id, proposal.diff_text, reviewableLines]);

  const acceptedLineIndices = reviewableLines
    .filter(line => lineDecisions[line.index] !== false)
    .map(line => line.index);
  const rejectedLineIndices = reviewableLines
    .filter(line => lineDecisions[line.index] === false)
    .map(line => line.index);
  const mergedConfig = buildMergedConfig(
    proposal.diff_text,
    proposal.generated_config,
    lineDecisions,
  );
  const mergeNotes = summarizeMergeNotes(reviewableLines, lineDecisions);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-3 mt-3 border-2 border-primary/30 rounded-xl p-4 bg-base-200/50">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-primary" />
          <span className="font-semibold text-sm">Intent-Based Change Plan</span>
        </div>
        <div className="flex gap-2 flex-wrap">
          <RiskBadge level={proposal.risk_level} />
          {intentMeta.blast_radius && (
            <BlastRadiusBadge
              level={intentMeta.blast_radius.level}
              count={intentMeta.blast_radius.device_count}
            />
          )}
        </div>
      </div>

      {/* Intent Summary */}
      <div className="bg-base-300 rounded-lg p-3 text-sm space-y-1">
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-20">Action:</span>
          <span className="font-semibold capitalize">{intentMeta.intent_type || proposal.feature}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-20">Feature:</span>
          <span>{proposal.feature}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-20">Vendor:</span>
          <span className="font-mono">{proposal.vendor}</span>
        </div>
        <div className="flex gap-2">
          <span className="text-base-content/60 min-w-20">Devices:</span>
          <span className="font-mono">
            {intentMeta.resolved_devices?.join(', ') || proposal.target_device}
            {intentMeta.device_count > 1 && ` (${intentMeta.device_count} total)`}
          </span>
        </div>
        {intentMeta.execution_engine && (
          <div className="flex gap-2">
            <span className="text-base-content/60 min-w-20">Engine:</span>
            <span className="badge badge-outline badge-xs">{intentMeta.execution_engine}</span>
          </div>
        )}
      </div>

      {/* Policy Checks */}
      <div className="bg-base-300 rounded-lg p-3">
        <h4 className="font-semibold text-xs flex items-center gap-1 mb-2">
          <Shield className="w-3 h-3 text-info" /> Policy Validation
        </h4>
        <div className="space-y-1">
          {intentMeta.policy_approved ? (
            <div className="flex items-center gap-1 text-xs text-success">
              <CheckCircle className="w-3 h-3" /> All policy checks passed
            </div>
          ) : (
            intentMeta.policy_violations.map((v, i) => (
              <div key={i} className="flex items-center gap-1 text-xs text-error">
                <AlertTriangle className="w-3 h-3" /> [{v.rule_id}] {v.message}
              </div>
            ))
          )}
          {intentMeta.policy_warnings.map((w, i) => (
            <div key={`w-${i}`} className="flex items-center gap-1 text-xs text-warning">
              <AlertTriangle className="w-3 h-3" /> {w.message}
            </div>
          ))}
        </div>
      </div>

      {/* AI Explanation */}
      {proposal.ai_explanation && (
        <div className="bg-base-300 rounded-lg p-3">
          <h4 className="font-semibold text-xs flex items-center gap-1 mb-2">
            <Zap className="w-3 h-3 text-warning" /> AI Summary
          </h4>
          <p className="text-xs leading-relaxed whitespace-pre-wrap max-h-32 overflow-y-auto">
            {proposal.ai_explanation}
          </p>
        </div>
      )}

      {/* Proposed Configuration (Collapsible) */}
      {proposal.generated_config && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowConfig(!showConfig)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <FileCode className="w-3 h-3 text-secondary" /> Generated Config ({intentMeta.device_count} device{intentMeta.device_count !== 1 ? 's' : ''})
            </span>
            <div className="flex items-center gap-1">
              {showConfig && (
                <button onClick={(e) => { e.stopPropagation(); handleCopy(proposal.generated_config); }} className="btn btn-ghost btn-xs">
                  {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
                </button>
              )}
              <span className="text-xs">{showConfig ? '▲' : '▼'}</span>
            </div>
          </button>
          {showConfig && (
            <pre className="p-3 text-xs overflow-x-auto max-h-64 overflow-y-auto font-mono bg-neutral-800 text-green-400">
              <code>{proposal.generated_config}</code>
            </pre>
          )}
        </div>
      )}

      {/* Rollback Commands (Collapsible) */}
      {proposal.rollback_config && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowRollback(!showRollback)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <RotateCcw className="w-3 h-3 text-error" /> Rollback Commands
            </span>
            <span className="text-xs">{showRollback ? '▲' : '▼'}</span>
          </button>
          {showRollback && (
            <pre className="p-3 text-xs overflow-x-auto max-h-32 overflow-y-auto font-mono bg-neutral-800 text-yellow-400">
              <code>{proposal.rollback_config}</code>
            </pre>
          )}
        </div>
      )}

      {/* Config Diff (Collapsible) */}
      {proposal.diff_text && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowDiff(!showDiff)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <ClipboardList className="w-3 h-3 text-info" /> Configuration Diff
            </span>
            <span className="text-xs">{showDiff ? '▲' : '▼'}</span>
          </button>
          {showDiff && (
            <div className="p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[11px] text-base-content/70">
                  Engineer line review: choose <span className="font-semibold">Keep</span> or <span className="font-semibold">Undo</span> per line before approval.
                </div>
                <div className="flex gap-1">
                  <button
                    type="button"
                    className="btn btn-xs btn-success btn-outline gap-1"
                    onClick={() => {
                      const all: Record<number, boolean> = {};
                      for (const l of reviewableLines) all[l.index] = true;
                      setLineDecisions(all);
                    }}
                  >
                    <CheckCircle className="w-3 h-3" /> Keep All
                  </button>
                  <button
                    type="button"
                    className="btn btn-xs btn-warning btn-outline gap-1"
                    onClick={() => {
                      const all: Record<number, boolean> = {};
                      for (const l of reviewableLines) all[l.index] = false;
                      setLineDecisions(all);
                    }}
                  >
                    <X className="w-3 h-3" /> Undo All
                  </button>
                </div>
              </div>
              <div className="max-h-48 overflow-y-auto border border-base-300 rounded-md bg-base-100">
                {reviewableLines.map((line) => (
                  <div
                    key={line.index}
                    className={`flex items-start gap-2 px-2 py-1 border-b border-base-300/50 text-xs font-mono ${
                      line.lineType === 'add' ? 'text-success' : 'text-error'
                    }`}
                  >
                    <div className="join join-horizontal mt-0.5">
                      <button
                        type="button"
                        className={`join-item btn btn-xs ${lineDecisions[line.index] !== false ? 'btn-success' : 'btn-ghost'}`}
                        onClick={() => setLineDecisions(prev => ({ ...prev, [line.index]: true }))}
                      >
                        Keep
                      </button>
                      <button
                        type="button"
                        className={`join-item btn btn-xs ${lineDecisions[line.index] === false ? 'btn-warning' : 'btn-ghost'}`}
                        onClick={() => setLineDecisions(prev => ({ ...prev, [line.index]: false }))}
                      >
                        Undo
                      </button>
                    </div>
                    <span className="opacity-60">{line.lineType === 'add' ? '+' : '-'}</span>
                    <span className="break-all">{line.content || ' '}</span>
                  </div>
                ))}
              </div>
              <div className="text-[11px] text-base-content/70">
                Accepted: {acceptedLineIndices.length} • Rejected: {rejectedLineIndices.length}
              </div>
              <div className="text-[11px] text-base-content/60">{mergeNotes}</div>
              <div className="rounded-md border border-base-300 overflow-hidden bg-base-300">
                <div className="px-2 py-1 text-[11px] font-semibold bg-base-200 border-b border-base-300">
                  Reviewed Preview (applies Keep/Undo)
                </div>
                <pre className="p-2 text-xs max-h-40 overflow-auto font-mono bg-neutral-800 text-green-400">
                  <code>{mergedConfig}</code>
                </pre>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Auto-Verification Checks (Collapsible) */}
      {intentMeta.verification_checks?.length > 0 && (
        <div className="bg-base-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowVerify(!showVerify)}
            className="flex items-center justify-between w-full px-3 py-2 bg-base-200 hover:bg-base-300 transition-colors"
          >
            <span className="text-xs font-semibold flex items-center gap-1">
              <Terminal className="w-3 h-3 text-accent" /> Auto-Verification ({intentMeta.verification_checks.length} checks)
            </span>
            <span className="text-xs">{showVerify ? '▲' : '▼'}</span>
          </button>
          {showVerify && (
            <div className="p-3 space-y-2">
              {intentMeta.verification_checks.map((vc, i) => (
                <div key={i} className="bg-base-200 rounded p-2 text-xs">
                  <div className="font-semibold text-accent">{vc.description}</div>
                  <div className="font-mono text-base-content/70 mt-1">$ {vc.command}</div>
                  <div className="font-mono text-success/70">expect: {vc.expected}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Action Buttons */}
      {proposal.status === 'pending' && (
        <div className="border-t border-base-300 pt-3">
          <p className="text-xs text-center mb-2 text-base-content/70">
            Engineer approval saves reviewed lines first; device deployment requires admin approval.
            {!intentMeta.policy_approved && (
              <span className="text-error font-semibold ml-1">(Policy violated — deploy blocked)</span>
            )}
          </p>
          <div className="flex gap-2 justify-center flex-wrap">
            <button
              onClick={() => onAccept({
                mergedConfig,
                acceptedLineIndices,
                rejectedLineIndices,
              })}
              className="btn btn-success btn-sm gap-1"
              disabled={isDeploying || !intentMeta.policy_approved}
            >
              {isDeploying ? (
                <><Loader2 className="w-3 h-3 animate-spin" /> Approving...</>
              ) : (
                <><CheckCircle className="w-3 h-3" /> Approve Proposal</>
              )}
            </button>
            <button onClick={onRevise} className="btn btn-ghost btn-sm gap-1" disabled={isDeploying}>
              <Edit3 className="w-3 h-3" /> Revise
            </button>
            <button onClick={onReject} className="btn btn-ghost btn-sm gap-1 text-error" disabled={isDeploying}>
              <X className="w-3 h-3" /> Reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

/** DeploymentStatusCard — shown after approval or failure */
const DeploymentStatusCard: React.FC<{
  result: DeployResult;
  proposal: ConfigProposal;
  onRollback: () => void;
  isRollingBack: boolean;
}> = ({ result, proposal, onRollback, isRollingBack }) => {
  const isSuccess = result.status === 'deployed';

  return (
    <div className="space-y-3 mt-3 border border-base-300 rounded-xl p-4 bg-base-200/50">
      <div className="flex items-center gap-2">
        {isSuccess ? (
          <CheckCircle className="w-5 h-5 text-success" />
        ) : (
          <AlertTriangle className="w-5 h-5 text-error" />
        )}
        <span className="font-semibold text-sm">
          {isSuccess ? 'Deployment Successful' : 'Deployment Failed'}
        </span>
      </div>

      <div className="bg-base-300 rounded-lg p-3 text-sm">
        <p>{result.message}</p>
        {result.job_id != null && result.job_id > 0 && (
          <p className="text-xs text-base-content/60 mt-1">Job ID: #{result.job_id}</p>
        )}
      </div>

      {isSuccess && proposal.rollback_config && (
        <div className="flex gap-2 justify-center">
          <button
            onClick={onRollback}
            className="btn btn-warning btn-sm gap-1"
            disabled={isRollingBack}
          >
            {isRollingBack ? (
              <><Loader2 className="w-3 h-3 animate-spin" /> Rolling back...</>
            ) : (
              <><RotateCcw className="w-3 h-3" /> Rollback</>
            )}
          </button>
        </div>
      )}
    </div>
  );
};

interface AIChatProps {
    domain: AppDomain;
}

const GeminiChat: React.FC<AIChatProps> = ({ domain }) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);

  // M19 — agentic workflow state
  const [currentProposal, setCurrentProposal] = useState<ConfigProposal | null>(null);
  const [isDeploying, setIsDeploying] = useState(false);
  const [isRollingBack, setIsRollingBack] = useState(false);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Initialize first chat on load if none exists
  useEffect(() => {
    if (!currentSessionId) {
        startNewChat();
    }
  }, []);

  const startNewChat = useCallback(() => {
    const newSessionId = generateId();
    // Clear backend history when starting new chat (if that's desired behavior)
    // llmApi.clearHistory(); 
    
    setMessages([{
        id: generateId(),
        role: Role.Model,
        content: `I am your ${domain === 'network' ? 'Network' : 'Security'} Operations Assistant. How can I help you today?`,
        timestamp: Date.now()
    }]);
    setCurrentSessionId(newSessionId);
    setIsSidebarOpen(window.innerWidth > 768);
  }, [domain]);

  // Reset chat when domain changes
  useEffect(() => {
     startNewChat();
  }, [domain, startNewChat]);

  const handleSendMessage = async (text: string) => {
    if (!text.trim() || !currentSessionId) return;

    const userMsgId = generateId();
    const userMessage: Message = {
      id: userMsgId,
      role: Role.User,
      content: text,
      timestamp: Date.now()
    };

    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    // Create placeholder for AI Message
    const aiMsgId = generateId();
    const aiPlaceholder: Message = {
      id: aiMsgId,
      role: Role.Model,
      content: "",
      isStreaming: true,
      timestamp: Date.now()
    };
    setMessages(prev => [...prev, aiPlaceholder]);

    // Handle Session Title (Client side only for now)
    if (currentSessionId) {
      setSessions(prevSessions => {
        const exists = prevSessions.some(s => s.id === currentSessionId);
        if (exists) return prevSessions;
        return [{
          id: currentSessionId,
          title: text.substring(0, 30) + (text.length > 30 ? "..." : ""),
          updatedAt: Date.now()
        }, ...prevSessions];
      });
    }

    try {
      // Call Backend API
      const response = await llmApi.chat(text);

      if ((response.type === 'proposal' || response.type === 'intent_plan') && response.proposal) {
        // M19/M24: AI generated a config proposal → show ProposalCard or IntentReviewCard
        const proposal = response.proposal as ConfigProposal;
        const intentMeta = response.type === 'intent_plan' && response.intent_meta
          ? response.intent_meta as IntentMeta
          : undefined;
        setCurrentProposal(proposal);

        setMessages(prev => prev.map(msg =>
          msg.id === aiMsgId
            ? {
                ...msg,
                isStreaming: false,
                content: response.content,
                proposal: proposal,
                intentMeta: intentMeta,
              }
            : msg
        ));
      } else {
        // Normal text response
        setMessages(prev => prev.map(msg =>
          msg.id === aiMsgId
            ? { ...msg, isStreaming: false, content: response.content }
            : msg
        ));
      }

    } catch (error: any) {
      console.error("Chat error:", error);
      Toast.error('Chat Error', error.message || 'Failed to get response');
      setMessages(prev => prev.map(msg => 
        msg.id === aiMsgId 
          ? { ...msg, isStreaming: false, isError: true, content: `Error: ${error.message || "Failed to get response"}` } 
          : msg
      ));
    } finally {
      setIsLoading(false);
    }
  };

  // ------------------------------------------------------------------
  // M19: Proposal lifecycle handlers
  // ------------------------------------------------------------------

  const handleAcceptProposal = async (review: ProposalReviewInput) => {
    if (!currentProposal) return;
    setIsDeploying(true);

    try {
      await proposalApi.review(currentProposal.id, {
        merged_config: review.mergedConfig,
        accepted_line_indices: review.acceptedLineIndices,
        rejected_line_indices: review.rejectedLineIndices,
      });
      const result = await proposalApi.approve(currentProposal.id);
      const updatedProposal = result.proposal as ConfigProposal;

      if (result.status === 'deployed' || updatedProposal.status === 'deployed') {
        const deployMsg: Message = {
          id: generateId(),
          role: Role.Model,
          content: '',
          timestamp: Date.now(),
          deployResult: {
            status: result.status,
            message: result.message,
            job_id: result.job_id,
          },
          proposal: updatedProposal,
        };
        setMessages(prev => [...prev, deployMsg]);
      } else {
        const approvalMsg: Message = {
          id: generateId(),
          role: Role.Model,
          content: `✅ ${result.message}`,
          timestamp: Date.now(),
          proposal: updatedProposal,
        };
        setMessages(prev => [...prev, approvalMsg]);
      }

      setCurrentProposal(updatedProposal);
    } catch (error: any) {
      const errorMsg: Message = {
        id: generateId(),
        role: Role.Model,
        content: `❌ Approval failed: ${error.message}`,
        isError: true,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, errorMsg]);
      Toast.error('Approval Failed', error.message);
    } finally {
      setIsDeploying(false);
    }
  };

  const handleReviseProposal = () => {
    const reviseMsg: Message = {
      id: generateId(),
      role: Role.Model,
      content: "What changes would you like to make? I'll regenerate the configuration.",
      timestamp: Date.now(),
    };
    setMessages(prev => [...prev, reviseMsg]);
    setCurrentProposal(null);
  };

  const handleRejectProposal = async () => {
    if (!currentProposal) return;

    try {
      await proposalApi.reject(currentProposal.id);
    } catch {
      Toast.warning('Could not notify server', 'Rejection recorded locally');
    }

    const rejectMsg: Message = {
      id: generateId(),
      role: Role.Model,
      content: 'Proposal rejected. Let me know if you need anything else.',
      timestamp: Date.now(),
    };
    setMessages(prev => [...prev, rejectMsg]);
    setCurrentProposal(null);
  };

  const handleRollbackProposal = async () => {
    if (!currentProposal) return;
    setIsRollingBack(true);

    try {
      const result = await proposalApi.rollback(currentProposal.id);

      const rollbackMsg: Message = {
        id: generateId(),
        role: Role.Model,
        content: `↩️ ${result.message}`,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, rollbackMsg]);
      setCurrentProposal(result.proposal as ConfigProposal);
    } catch (error: any) {
      const errorMsg: Message = {
        id: generateId(),
        role: Role.Model,
        content: `❌ Rollback failed: ${error.message}`,
        isError: true,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, errorMsg]);
      Toast.error('Rollback Failed', error.message);
    } finally {
      setIsRollingBack(false);
    }
  };

  const handleStopGeneration = () => {
    setIsLoading(false);
  };

  const handleDeleteChat = (id: string) => {
    setSessions(prev => prev.filter(s => s.id !== id));
    if (currentSessionId === id) {
        startNewChat();
    }
    // Ideally call backend to clear specific session history if supported
    llmApi.clearHistory();
  };

  const handleSelectChat = (id: string) => {
      if (id !== currentSessionId) {
          setCurrentSessionId(id);
          setMessages([]); 
          llmApi.clearHistory(); // Clear backend context for "new" session feel
          // detailed history fetching would go here
      }
  };

  return (
    <div className="flex h-full bg-base-100 relative overflow-hidden">
      
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-20 md:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div className={`fixed inset-y-0 left-0 z-30 transform transition-transform duration-300 ease-in-out md:relative md:translate-x-0 ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'} flex`}>
        <SidebarLLM 
          isOpen={isSidebarOpen}
          toggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)}
          sessions={sessions}
          currentSessionId={currentSessionId}
          onNewChat={startNewChat}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
        />
      </div>

      <div className="flex-1 flex flex-col h-full relative min-w-0">
        <header className="h-14 border-b border-base-300 flex items-center justify-between px-4 gap-3 shrink-0 bg-base-100/50 backdrop-blur">
          <div className="flex items-center gap-2">
             <button 
              onClick={() => setIsSidebarOpen(!isSidebarOpen)}
              className="btn btn-ghost btn-sm btn-square"
            >
              {isSidebarOpen ? <PanelLeftClose size={20} /> : <PanelLeftOpen size={20} />}
            </button>
            <span className="font-medium truncate">
                {sessions.find(s => s.id === currentSessionId)?.title || 'NetTopo Assistant'}
            </span>
          </div>
          <div className="text-xs text-gray-500 font-mono">Backend Agent (M5)</div>
        </header>

        <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-800 p-4 sm:p-6 md:p-10 scroll-smooth">
           {messages.length === 0 ? (
             <div className="flex flex-col items-center text-base-content justify-center h-full text-center opacity-50 select-none"> 
                <div className="w-20 h-20 bg-base-200 rounded-full flex items-center justify-center mb-4">
                  <RoleIcon size={48} />
                </div>
                <h2 className="text-lg font-semibold mb-2">How can I help you today?</h2>
                <p className="max-w-md text-sm text-gray-400">
                    I have access to your network topology and tools.
                </p>
             </div>
           ) : (
             <div className="max-w-3xl mx-auto pb-4">
               {messages.map(msg => (
                 <div key={msg.id}>
                   {/* Deployment result card (no text bubble needed) */}
                   {msg.deployResult && msg.proposal ? (
                     <div className="chat chat-start mb-4">
                       <div className="chat-bubble chat-bubble-neutral max-w-[85%] sm:max-w-[75%]">
                         <DeploymentStatusCard
                           result={msg.deployResult}
                           proposal={msg.proposal}
                           onRollback={handleRollbackProposal}
                           isRollingBack={isRollingBack}
                         />
                       </div>
                     </div>
                   ) : (
                     <>
                       {/* Normal text bubble */}
                       <MessageBubble message={msg} />
                       {/* M24: Intent pipeline card (3-stage) */}
                       {msg.proposal && msg.intentMeta && !msg.deployResult && (
                         <div className="chat chat-start mb-4 -mt-2">
                           <div className="chat-image avatar hidden sm:block">
                             <div className="w-10" />
                           </div>
                           <div className="chat-bubble chat-bubble-neutral max-w-[85%] sm:max-w-[75%] p-0 overflow-visible bg-transparent shadow-none">
                             <IntentReviewCard
                               proposal={msg.proposal}
                               intentMeta={msg.intentMeta}
                               onAccept={handleAcceptProposal}
                               onRevise={handleReviseProposal}
                               onReject={handleRejectProposal}
                               isDeploying={isDeploying}
                             />
                           </div>
                         </div>
                       )}
                       {/* M19: Legacy proposal card (direct generate_config) */}
                       {msg.proposal && !msg.intentMeta && !msg.deployResult && (
                         <div className="chat chat-start mb-4 -mt-2">
                           <div className="chat-image avatar hidden sm:block">
                             <div className="w-10" />
                           </div>
                           <div className="chat-bubble chat-bubble-neutral max-w-[85%] sm:max-w-[75%] p-0 overflow-visible bg-transparent shadow-none">
                             <ProposalCard
                               proposal={msg.proposal}
                               onAccept={handleAcceptProposal}
                               onRevise={handleReviseProposal}
                               onReject={handleRejectProposal}
                               isDeploying={isDeploying}
                             />
                           </div>
                         </div>
                       )}
                     </>
                   )}
                 </div>
               ))}
               <div ref={messagesEndRef} className="h-4" />
             </div>
           )}
        </div>

        <div className="p-4 bg-base-100 border-t border-base-300 shrink-0">
           <ChatInput 
              onSend={handleSendMessage} 
              disabled={isLoading} 
              isGenerating={isLoading}
              onStop={handleStopGeneration}
           />
        </div>

      </div>
    </div>
  );
};

const RoleIcon = ({ size }: { size: number }) => {
  return <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="gray" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2v-2a2 2 0 0 1-2-2 2 2 0 0 1 2-2Z"/><path d="m9 22 3-8 3 8"/><path d="M8 22h8"/><path d="M12 10v2c0 1.5-2 2-2 3.5V16"/><path d="M12 22h.01"/></svg>
}

export default GeminiChat;