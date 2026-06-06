import React, { useState, useCallback } from 'react';
import {
  GitCompareArrows, CheckCircle, XCircle, AlertTriangle, Play,
  Shield, Zap, ChevronDown, ChevronRight, RefreshCw
} from 'lucide-react';
import { ansibleApi } from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';
import type { ViewId } from '../types';

/* ── Types ─────────────────────────────────────────────────── */

interface Candidate {
  index: number;
  yaml: string;
  approach: string;
  valid: boolean;
  errors: string[];
}

/* ── Approach config ───────────────────────────────────────── */

const APPROACH_CONFIG: Record<string, {
  label: string;
  description: string;
  color: string;
  icon: React.ReactNode;
}> = {
  conservative: {
    label: 'Conservative',
    description: 'Safest, single-step changes. Minimal impact.',
    color: 'border-success text-success',
    icon: <Shield className="w-5 h-5 text-success" />,
  },
  balanced: {
    label: 'Balanced',
    description: 'Best-practice approach balancing safety and completeness.',
    color: 'border-info text-info',
    icon: <Zap className="w-5 h-5 text-info" />,
  },
  aggressive: {
    label: 'Aggressive',
    description: 'Comprehensive with pre/post validation tasks.',
    color: 'border-warning text-warning',
    icon: <AlertTriangle className="w-5 h-5 text-warning" />,
  },
};

/* ── Diff highlighter (simple line-level) ──────────────────── */

const YamlViewer: React.FC<{ yaml: string; maxHeight?: string }> = ({ yaml, maxHeight = '450px' }) => (
  <pre
    className="text-xs font-mono bg-neutral-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre"
    style={{ maxHeight }}
  >
    {yaml || '(empty)'}
  </pre>
);

/* ── Candidate Card ────────────────────────────────────────── */

const CandidateCard: React.FC<{
  candidate: Candidate;
  selected: boolean;
  onSelect: () => void;
  disabled: boolean;
}> = ({ candidate, selected, onSelect, disabled }) => {
  const [expanded, setExpanded] = useState(true);
  const cfg = APPROACH_CONFIG[candidate.approach] ?? {
    label: candidate.approach,
    description: '',
    color: 'border-base-300',
    icon: null,
  };

  const lineCount = candidate.yaml.split('\n').length;
  const taskCount = (candidate.yaml.match(/- name:/g) || []).length;

  return (
    <div
      className={`card bg-base-100 border-2 transition-all duration-200 ${
        selected ? 'border-primary ring-2 ring-primary/30 shadow-lg' : 'border-base-300 hover:border-base-content/20'
      }`}
    >
      <div className="card-body p-4 space-y-3">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {cfg.icon}
            <div>
              <h3 className="font-bold text-sm">{cfg.label}</h3>
              <p className="text-xs opacity-60">{cfg.description}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {candidate.valid ? (
              <span className="badge badge-sm badge-success gap-1">
                <CheckCircle className="w-3 h-3" /> Valid
              </span>
            ) : (
              <span className="badge badge-sm badge-error gap-1">
                <XCircle className="w-3 h-3" /> Invalid
              </span>
            )}
          </div>
        </div>

        {/* Stats */}
        <div className="flex gap-4 text-xs opacity-70">
          <span>{lineCount} lines</span>
          <span>{taskCount} task{taskCount !== 1 ? 's' : ''}</span>
          <span className="capitalize">{candidate.approach}</span>
        </div>

        {/* Errors */}
        {candidate.errors.length > 0 && (
          <div className="bg-error/10 rounded p-2 space-y-1">
            {candidate.errors.map((e, i) => (
              <p key={i} className="text-xs text-error flex items-start gap-1">
                <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
                {e}
              </p>
            ))}
          </div>
        )}

        {/* YAML toggle */}
        <button
          className="btn btn-xs btn-ghost gap-1 w-fit"
          onClick={() => setExpanded(o => !o)}
        >
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          {expanded ? 'Collapse' : 'Expand'} Playbook
        </button>

        {expanded && <YamlViewer yaml={candidate.yaml} />}

        {/* Select button */}
        <button
          className={`btn btn-sm w-full gap-2 ${selected ? 'btn-primary' : 'btn-outline btn-primary'}`}
          onClick={onSelect}
          disabled={disabled || !candidate.valid}
        >
          {selected ? (
            <><CheckCircle className="w-4 h-4" /> Selected</>
          ) : (
            <><Play className="w-4 h-4" /> Select This Candidate</>
          )}
        </button>
      </div>
    </div>
  );
};

/* ── Side-by-Side Comparison View ──────────────────────────── */

const SideBySideView: React.FC<{ candidates: Candidate[] }> = ({ candidates }) => {
  if (candidates.length < 2) return null;

  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-sm flex items-center gap-2">
        <GitCompareArrows className="w-4 h-4" /> Side-by-Side Comparison
      </h3>
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
        {candidates.map((c) => {
          const cfg = APPROACH_CONFIG[c.approach] ?? { label: c.approach, color: '', icon: null, description: '' };
          return (
            <div key={c.index} className={`border-2 rounded-lg p-3 ${cfg.color.split(' ')[0]}`}>
              <div className="flex items-center gap-2 mb-2">
                {cfg.icon}
                <span className="font-semibold text-sm">{cfg.label}</span>
                {c.valid ? (
                  <CheckCircle className="w-3 h-3 text-success ml-auto" />
                ) : (
                  <XCircle className="w-3 h-3 text-error ml-auto" />
                )}
              </div>
              <YamlViewer yaml={c.yaml} maxHeight="350px" />
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ── Main Component ────────────────────────────────────────── */

interface Props {
  setView?: (v: ViewId) => void;
  jobId?: number;
  intentDescription?: string;
  targetHosts?: string[];
  vendor?: string;
  feature?: string;
  params?: Record<string, any>;
}

const PlaybookComparisonView: React.FC<Props> = ({
  setView,
  jobId: propJobId,
  intentDescription: propDesc,
  targetHosts: propHosts,
  vendor: propVendor,
  feature: propFeature,
  params: propParams,
}) => {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [viewMode, setViewMode] = useState<'cards' | 'side-by-side'>('cards');

  // Form for manual generation
  const [jobId, setJobId] = useState(propJobId ?? 0);
  const [desc, setDesc] = useState(propDesc ?? '');
  const [hosts, setHosts] = useState(propHosts?.join(', ') ?? '');
  const [vendor, setVendor] = useState(propVendor ?? 'cisco_ios');
  const [feature, setFeature] = useState(propFeature ?? '');

  const generateCandidates = useCallback(async () => {
    if (!jobId || !desc) {
      Toast.warning('Enter a Job ID and intent description');
      return;
    }
    setLoading(true);
    setCandidates([]);
    setSelectedIndex(null);
    try {
      const res = await ansibleApi.planCandidates(jobId, {
        intent_description: desc,
        target_hosts: hosts.split(',').map(h => h.trim()).filter(Boolean),
        vendor,
        feature,
        params: propParams ?? {},
      }, 3);
      setCandidates(res.candidates);
      Toast.success(`${res.candidates.length} candidates generated`);
    } catch (err: any) {
      Toast.error('Generation failed', err.message);
    } finally {
      setLoading(false);
    }
  }, [jobId, desc, hosts, vendor, feature, propParams]);

  const confirmSelection = async () => {
    if (selectedIndex === null) return;
    const c = candidates[selectedIndex];
    if (!c) return;

    const ok = await Confirm.ask(
      'Confirm Playbook Selection',
      `Select the "${APPROACH_CONFIG[c.approach]?.label ?? c.approach}" candidate for Job #${jobId}?`
    );
    if (!ok) return;

    setConfirming(true);
    try {
      await ansibleApi.selectCandidate(jobId, c.index, c.yaml);
      Toast.success(`Candidate "${c.approach}" selected for Job #${jobId}`);
      if (setView) setView('ansible_studio');
    } catch (err: any) {
      Toast.error('Selection failed', err.message);
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="h-full overflow-auto bg-base-200 p-4 md:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <GitCompareArrows className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">Playbook Candidates</h1>
            <p className="text-sm opacity-60">
              Compare AI-generated playbook approaches side-by-side (UC14)
            </p>
          </div>
        </div>
        {candidates.length > 0 && (
          <div className="flex items-center gap-2">
            <div className="btn-group">
              <button
                className={`btn btn-sm ${viewMode === 'cards' ? 'btn-active' : ''}`}
                onClick={() => setViewMode('cards')}
              >Cards</button>
              <button
                className={`btn btn-sm ${viewMode === 'side-by-side' ? 'btn-active' : ''}`}
                onClick={() => setViewMode('side-by-side')}
              >Side-by-Side</button>
            </div>
          </div>
        )}
      </div>

      {/* Generation Form */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 space-y-3">
        <h3 className="font-semibold text-sm">Generate Candidates</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs">Job ID</span></label>
            <input
              type="number"
              className="input input-sm input-bordered"
              value={jobId || ''}
              onChange={e => setJobId(Number(e.target.value))}
            />
          </div>
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs">Vendor</span></label>
            <select className="select select-sm select-bordered" value={vendor} onChange={e => setVendor(e.target.value)}>
              <option value="cisco_ios">Cisco IOS</option>
              <option value="cisco_xe">Cisco IOS-XE</option>
              <option value="juniper_junos">Juniper JunOS</option>
              <option value="huawei_vrp">Huawei VRP</option>
              <option value="arista_eos">Arista EOS</option>
            </select>
          </div>
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs">Feature</span></label>
            <input
              type="text"
              className="input input-sm input-bordered"
              placeholder="e.g. ospf, bgp, acl"
              value={feature}
              onChange={e => setFeature(e.target.value)}
            />
          </div>
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs">Target Hosts</span></label>
            <input
              type="text"
              className="input input-sm input-bordered"
              placeholder="host1, host2"
              value={hosts}
              onChange={e => setHosts(e.target.value)}
            />
          </div>
        </div>
        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs">Intent Description</span></label>
          <textarea
            className="textarea textarea-bordered textarea-sm h-20"
            placeholder="Describe the configuration change..."
            value={desc}
            onChange={e => setDesc(e.target.value)}
          />
        </div>
        <button
          className="btn btn-primary btn-sm gap-2"
          onClick={generateCandidates}
          disabled={loading}
        >
          {loading ? (
            <><span className="loading loading-spinner loading-xs" /> Generating…</>
          ) : (
            <><RefreshCw className="w-4 h-4" /> Generate 3 Candidates</>
          )}
        </button>
      </div>

      {/* Candidates Display */}
      {candidates.length > 0 && (
        <>
          {viewMode === 'cards' ? (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              {candidates.map(c => (
                <CandidateCard
                  key={c.index}
                  candidate={c}
                  selected={selectedIndex === c.index}
                  onSelect={() => setSelectedIndex(c.index)}
                  disabled={confirming}
                />
              ))}
            </div>
          ) : (
            <SideBySideView candidates={candidates} />
          )}

          {/* Confirm bar */}
          {selectedIndex !== null && (
            <div className="sticky bottom-4 bg-base-100 rounded-xl border-2 border-primary p-4 shadow-lg flex items-center justify-between">
              <div className="flex items-center gap-3">
                <CheckCircle className="w-5 h-5 text-primary" />
                <div>
                  <p className="font-semibold text-sm">
                    Selected: {APPROACH_CONFIG[candidates[selectedIndex]?.approach]?.label ?? candidates[selectedIndex]?.approach}
                  </p>
                  <p className="text-xs opacity-60">
                    {candidates[selectedIndex]?.yaml.split('\n').length} lines •{' '}
                    {(candidates[selectedIndex]?.yaml.match(/- name:/g) || []).length} tasks
                  </p>
                </div>
              </div>
              <button
                className="btn btn-primary btn-sm gap-2"
                onClick={confirmSelection}
                disabled={confirming}
              >
                {confirming ? (
                  <><span className="loading loading-spinner loading-xs" /> Confirming…</>
                ) : (
                  <><Play className="w-4 h-4" /> Confirm &amp; Proceed</>
                )}
              </button>
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {!loading && candidates.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 opacity-40">
          <GitCompareArrows className="w-16 h-16 mb-4" />
          <p className="text-lg font-semibold">No Candidates Yet</p>
          <p className="text-sm">Fill in the form above and generate playbook candidates to compare.</p>
        </div>
      )}
    </div>
  );
};

export default PlaybookComparisonView;
