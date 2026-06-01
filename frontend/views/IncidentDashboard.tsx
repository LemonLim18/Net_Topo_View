import React, { useState, useEffect, useCallback } from 'react';
import {
  AlertTriangle,
  ShieldAlert,
  CheckCircle2,
  XCircle,
  Clock,
  Play,
  RefreshCw,
  Eye,
  BotMessageSquare,
  Loader2,
  Ban,
  FileCode,
} from 'lucide-react';
import { incidentApi } from '../src/services/api';
import { Confirm } from '../src/utils/swal';

/* ─── Types ─────────────────────────────────────────────────────── */

interface IncidentSummary {
  id: string;
  anomaly_type: string;
  severity: string;
  affected_devices: string[];
  detected_at: string;
  status: string;
  root_cause_summary: string;
  created_at: string;
}

interface IncidentReport {
  id: string;
  anomaly: {
    id: string;
    type: string;
    severity: string;
    affected_devices: string[];
    detected_at: string;
    metrics: { current: number; baseline: number; threshold: number };
  };
  collected_context: {
    logs: string[];
    configs: Record<string, string>;
    telemetry_snapshot: Record<string, any>;
  };
  root_cause_analysis: {
    summary: string;
    confidence: number;
    possible_causes: string[];
  };
  proposed_solution: {
    description: string;
    playbook: {
      name: string;
      filename: string;
      content: string;
      hosts: string[];
      tasks: any[];
    };
    estimated_impact: string;
    rollback_plan: string;
  };
  status: string;
  approver: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

/* ─── Helpers ───────────────────────────────────────────────────── */

const severityColor: Record<string, string> = {
  critical: 'badge-error',
  high: 'badge-warning',
  medium: 'badge-info',
  low: 'badge-ghost',
  info: 'badge-ghost',
};

const statusIcon: Record<string, React.ReactNode> = {
  open: <AlertTriangle className="w-4 h-4 text-warning" />,
  investigating: <BotMessageSquare className="w-4 h-4 text-info animate-pulse" />,
  approved: <CheckCircle2 className="w-4 h-4 text-success" />,
  dismissed: <Ban className="w-4 h-4 text-base-content/40" />,
  resolved: <CheckCircle2 className="w-4 h-4 text-success" />,
};

function timeAgo(iso: string): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/* ─── Component ─────────────────────────────────────────────────── */

const IncidentDashboard: React.FC = () => {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [selected, setSelected] = useState<IncidentReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'summary' | 'playbook' | 'context' | 'timeline'>('summary');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [executeResult, setExecuteResult] = useState<any>(null);

  // Fetch incident list
  const fetchIncidents = useCallback(async () => {
    try {
      const data = await incidentApi.listIncidents(statusFilter || undefined);
      setIncidents(data);
    } catch (err) {
      console.error('Failed to fetch incidents:', err);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchIncidents();
    const interval = setInterval(fetchIncidents, 30000);
    return () => clearInterval(interval);
  }, [fetchIncidents]);

  // Select an incident
  const handleSelect = async (id: string) => {
    setDetailLoading(true);
    setExecuteResult(null);
    try {
      const report = await incidentApi.getIncident(id);
      setSelected(report);
      setActiveTab('summary');
    } catch (err) {
      console.error('Failed to load incident:', err);
    } finally {
      setDetailLoading(false);
    }
  };

  // Actions
  const handleApprove = async () => {
    if (!selected) return;
    const ok = await Confirm.ask(
      `Approve incident ${selected.id}?`,
      'This marks the remediation as approved and allows execution.',
    );
    if (!ok) return;
    setActionLoading('approve');
    try {
      await incidentApi.approveIncident(selected.id);
      await handleSelect(selected.id);
      fetchIncidents();
    } finally {
      setActionLoading(null);
    }
  };

  const handleDismiss = async () => {
    if (!selected) return;
    const ok = await Confirm.ask(
      `Dismiss incident ${selected.id}?`,
      'This closes the incident without executing the proposed remediation.',
    );
    if (!ok) return;
    setActionLoading('dismiss');
    try {
      await incidentApi.dismissIncident(selected.id);
      await handleSelect(selected.id);
      fetchIncidents();
    } finally {
      setActionLoading(null);
    }
  };

  const handleResolve = async () => {
    if (!selected) return;
    const ok = await Confirm.ask(
      `Mark incident ${selected.id} as resolved?`,
      'This records the incident as resolved in the incident workflow.',
    );
    if (!ok) return;
    setActionLoading('resolve');
    try {
      await incidentApi.resolveIncident(selected.id);
      await handleSelect(selected.id);
      fetchIncidents();
    } finally {
      setActionLoading(null);
    }
  };

  const handleExecute = async () => {
    if (!selected) return;
    const ok = await Confirm.danger(
      `Execute remediation for ${selected.id}?`,
      'This will run the approved remediation playbook. Confirm the impact and rollback plan before proceeding.',
    );
    if (!ok) return;
    setActionLoading('execute');
    setExecuteResult(null);
    try {
      const res = await fetch(
        `${(window as any).__API_URL || `http://${window.location.hostname}:8000`}/api/v1/incidents/${encodeURIComponent(selected.id)}/execute`,
        { method: 'POST' }
      );
      const data = await res.json();
      setExecuteResult(data);
      if (data.result?.success) {
        await handleSelect(selected.id);
        fetchIncidents();
      }
    } catch (err: any) {
      setExecuteResult({ error: err.message });
    } finally {
      setActionLoading(null);
    }
  };

  const handleReanalyze = async () => {
    if (!selected) return;
    setActionLoading('reanalyze');
    try {
      await fetch(
        `${(window as any).__API_URL || `http://${window.location.hostname}:8000`}/api/v1/incidents/${encodeURIComponent(selected.id)}/reanalyze`,
        { method: 'POST' }
      );
      await handleSelect(selected.id);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="h-full flex overflow-hidden bg-base-200">
      {/* ─── Left: Incident List ───────────────────────────────── */}
      <div className="w-96 shrink-0 border-r border-base-300 flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-base-300 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-error" />
            <h2 className="font-bold text-lg">Incidents</h2>
            <span className="badge badge-sm">{incidents.length}</span>
          </div>
          <button className="btn btn-ghost btn-xs" onClick={fetchIncidents}>
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Filter */}
        <div className="p-2 border-b border-base-300">
          <select
            className="select select-bordered select-sm w-full"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="investigating">Investigating</option>
            <option value="approved">Approved</option>
            <option value="dismissed">Dismissed</option>
            <option value="resolved">Resolved</option>
          </select>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin opacity-50" />
            </div>
          ) : incidents.length === 0 ? (
            <div className="text-center py-12 opacity-50">
              <ShieldAlert className="w-10 h-10 mx-auto mb-2" />
              <p className="text-sm">No incidents found</p>
            </div>
          ) : (
            incidents.map((inc) => (
              <button
                key={inc.id}
                onClick={() => handleSelect(inc.id)}
                className={`w-full text-left px-4 py-3 border-b border-base-300 hover:bg-base-300/50 transition-colors ${
                  selected?.id === inc.id ? 'bg-base-300' : ''
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    {statusIcon[inc.status] || statusIcon.open}
                    <div className="min-w-0">
                      <p className="font-medium text-sm truncate">
                        {inc.anomaly_type.replace(/_/g, ' ')}
                      </p>
                      <p className="text-xs opacity-60 truncate">
                        {inc.affected_devices.join(', ') || 'Unknown device'}
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <span className={`badge badge-xs ${severityColor[inc.severity] || ''}`}>
                      {inc.severity}
                    </span>
                    <span className="text-xs opacity-50">{timeAgo(inc.created_at)}</span>
                  </div>
                </div>
                {inc.root_cause_summary && (
                  <p className="text-xs opacity-50 mt-1 line-clamp-2">{inc.root_cause_summary}</p>
                )}
              </button>
            ))
          )}
        </div>
      </div>

      {/* ─── Right: Detail Panel ───────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {!selected ? (
          <div className="flex-1 flex items-center justify-center opacity-40">
            <div className="text-center">
              <Eye className="w-12 h-12 mx-auto mb-3" />
              <p>Select an incident to view details</p>
            </div>
          </div>
        ) : detailLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="w-8 h-8 animate-spin opacity-50" />
          </div>
        ) : (
          <>
            {/* Detail Header */}
            <div className="p-4 border-b border-base-300 flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-lg">
                    {selected.anomaly.type.replace(/_/g, ' ')}
                  </h3>
                  <span className={`badge ${severityColor[selected.anomaly.severity]}`}>
                    {selected.anomaly.severity}
                  </span>
                  <span className="badge badge-outline">{selected.status}</span>
                </div>
                <p className="text-xs opacity-60 mt-1">
                  {selected.id} — {selected.anomaly.affected_devices.join(', ')}
                </p>
              </div>

              {/* Action buttons */}
              <div className="flex items-center gap-2">
                {selected.status === 'investigating' && (
                  <>
                    <button
                      className="btn btn-success btn-sm gap-1"
                      onClick={handleApprove}
                      disabled={actionLoading !== null}
                    >
                      {actionLoading === 'approve' ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <CheckCircle2 className="w-3.5 h-3.5" />
                      )}
                      Approve
                    </button>
                    <button
                      className="btn btn-ghost btn-sm gap-1"
                      onClick={handleReanalyze}
                      disabled={actionLoading !== null}
                    >
                      {actionLoading === 'reanalyze' ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="w-3.5 h-3.5" />
                      )}
                      Reanalyze
                    </button>
                  </>
                )}
                {selected.status === 'approved' && (
                  <button
                    className="btn btn-primary btn-sm gap-1"
                    onClick={handleExecute}
                    disabled={actionLoading !== null}
                  >
                    {actionLoading === 'execute' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Play className="w-3.5 h-3.5" />
                    )}
                    Deploy Playbook
                  </button>
                )}
                {['open', 'investigating'].includes(selected.status) && (
                  <button
                    className="btn btn-ghost btn-sm gap-1 text-error"
                    onClick={handleDismiss}
                    disabled={actionLoading !== null}
                  >
                    <XCircle className="w-3.5 h-3.5" />
                    Dismiss
                  </button>
                )}
                {selected.status === 'approved' && (
                  <button
                    className="btn btn-ghost btn-sm gap-1"
                    onClick={handleResolve}
                    disabled={actionLoading !== null}
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Resolve
                  </button>
                )}
              </div>
            </div>

            {/* Tabs */}
            <div className="tabs tabs-bordered px-4 pt-2">
              {(['summary', 'playbook', 'context', 'timeline'] as const).map((tab) => (
                <button
                  key={tab}
                  className={`tab ${activeTab === tab ? 'tab-active' : ''}`}
                  onClick={() => setActiveTab(tab)}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>

            {/* Tab Content */}
            <div className="flex-1 overflow-y-auto p-4">
              {activeTab === 'summary' && (
                <div className="space-y-4">
                  {/* RCA */}
                  <div className="card bg-base-100 shadow-sm">
                    <div className="card-body p-4">
                      <h4 className="font-semibold flex items-center gap-2">
                        <BotMessageSquare className="w-4 h-4 text-info" />
                        Root Cause Analysis
                        <span className="badge badge-xs badge-info">
                          {(selected.root_cause_analysis.confidence * 100).toFixed(0)}% confidence
                        </span>
                      </h4>
                      <p className="text-sm mt-2">
                        {selected.root_cause_analysis.summary || 'Awaiting AI analysis...'}
                      </p>
                      {selected.root_cause_analysis.possible_causes.length > 0 && (
                        <div className="mt-3">
                          <p className="text-xs font-medium opacity-70">Possible Causes:</p>
                          <ul className="list-disc list-inside text-sm mt-1">
                            {selected.root_cause_analysis.possible_causes.map((c, i) => (
                              <li key={i}>{c}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Proposed Solution */}
                  <div className="card bg-base-100 shadow-sm">
                    <div className="card-body p-4">
                      <h4 className="font-semibold">Proposed Remediation</h4>
                      <p className="text-sm mt-1">
                        {selected.proposed_solution.description || 'No remediation proposed yet.'}
                      </p>
                      {selected.proposed_solution.estimated_impact && (
                        <p className="text-xs mt-2 opacity-70">
                          <span className="font-medium">Impact:</span>{' '}
                          {selected.proposed_solution.estimated_impact}
                        </p>
                      )}
                      {selected.proposed_solution.rollback_plan && (
                        <p className="text-xs opacity-70">
                          <span className="font-medium">Rollback:</span>{' '}
                          {selected.proposed_solution.rollback_plan}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Metrics */}
                  {selected.anomaly.metrics && (
                    <div className="card bg-base-100 shadow-sm">
                      <div className="card-body p-4">
                        <h4 className="font-semibold">Detection Metrics</h4>
                        <div className="grid grid-cols-3 gap-4 mt-2">
                          <div>
                            <p className="text-xs opacity-60">Current</p>
                            <p className="font-mono font-bold">{selected.anomaly.metrics.current}</p>
                          </div>
                          <div>
                            <p className="text-xs opacity-60">Baseline</p>
                            <p className="font-mono">{selected.anomaly.metrics.baseline}</p>
                          </div>
                          <div>
                            <p className="text-xs opacity-60">Threshold</p>
                            <p className="font-mono">{selected.anomaly.metrics.threshold}</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Execution Result */}
                  {executeResult && (
                    <div className={`card shadow-sm ${executeResult.result?.success ? 'bg-success/10' : 'bg-error/10'}`}>
                      <div className="card-body p-4">
                        <h4 className="font-semibold">
                          {executeResult.result?.success ? 'Deployment Successful' : 'Deployment Result'}
                        </h4>
                        <pre className="text-xs whitespace-pre-wrap mt-2 max-h-60 overflow-auto font-mono bg-base-300 p-2 rounded">
                          {executeResult.result?.stdout || executeResult.error || JSON.stringify(executeResult, null, 2)}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'playbook' && (
                <div className="card bg-base-100 shadow-sm">
                  <div className="card-body p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="font-semibold flex items-center gap-2">
                        <FileCode className="w-4 h-4" />
                        {selected.proposed_solution.playbook.filename || 'Remediation Playbook'}
                      </h4>
                      {selected.proposed_solution.playbook.hosts.length > 0 && (
                        <span className="badge badge-sm badge-outline">
                          Hosts: {selected.proposed_solution.playbook.hosts.join(', ')}
                        </span>
                      )}
                    </div>
                    {selected.proposed_solution.playbook.content ? (
                      <pre className="text-sm whitespace-pre-wrap font-mono bg-base-300 p-4 rounded-lg overflow-auto max-h-[60vh] leading-relaxed">
                        {selected.proposed_solution.playbook.content}
                      </pre>
                    ) : (
                      <p className="text-sm opacity-50 py-8 text-center">
                        No playbook generated yet. Click "Reanalyze" to trigger AI generation.
                      </p>
                    )}
                  </div>
                </div>
              )}

              {activeTab === 'context' && (
                <div className="space-y-4">
                  {/* Logs */}
                  {selected.collected_context.logs.length > 0 && (
                    <div className="card bg-base-100 shadow-sm">
                      <div className="card-body p-4">
                        <h4 className="font-semibold">Collected Logs</h4>
                        <pre className="text-xs whitespace-pre-wrap mt-2 max-h-60 overflow-auto font-mono bg-base-300 p-2 rounded">
                          {selected.collected_context.logs.join('\n')}
                        </pre>
                      </div>
                    </div>
                  )}

                  {/* Configs */}
                  {Object.keys(selected.collected_context.configs).length > 0 && (
                    <div className="card bg-base-100 shadow-sm">
                      <div className="card-body p-4">
                        <h4 className="font-semibold">Device Configs</h4>
                        {Object.entries(selected.collected_context.configs).map(([host, cfg]) => (
                          <div key={host} className="mt-2">
                            <p className="text-xs font-medium opacity-70">{host}</p>
                            <pre className="text-xs whitespace-pre-wrap mt-1 max-h-40 overflow-auto font-mono bg-base-300 p-2 rounded">
                              {cfg}
                            </pre>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Telemetry Snapshot */}
                  {Object.keys(selected.collected_context.telemetry_snapshot).length > 0 && (
                    <div className="card bg-base-100 shadow-sm">
                      <div className="card-body p-4">
                        <h4 className="font-semibold">Telemetry Snapshot</h4>
                        <pre className="text-xs whitespace-pre-wrap mt-2 max-h-60 overflow-auto font-mono bg-base-300 p-2 rounded">
                          {JSON.stringify(selected.collected_context.telemetry_snapshot, null, 2)}
                        </pre>
                      </div>
                    </div>
                  )}

                  {Object.keys(selected.collected_context.configs).length === 0 &&
                    selected.collected_context.logs.length === 0 && (
                      <p className="text-sm opacity-50 text-center py-8">No context collected yet.</p>
                    )}
                </div>
              )}

              {activeTab === 'timeline' && (
                <div className="space-y-3">
                  <div className="flex items-start gap-3">
                    <Clock className="w-4 h-4 mt-0.5 text-info" />
                    <div>
                      <p className="text-sm font-medium">Incident Created</p>
                      <p className="text-xs opacity-60">{selected.created_at}</p>
                    </div>
                  </div>
                  {selected.root_cause_analysis.summary && (
                    <div className="flex items-start gap-3">
                      <BotMessageSquare className="w-4 h-4 mt-0.5 text-info" />
                      <div>
                        <p className="text-sm font-medium">AI Analysis Complete</p>
                        <p className="text-xs opacity-60">{selected.updated_at}</p>
                      </div>
                    </div>
                  )}
                  {selected.approver && (
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="w-4 h-4 mt-0.5 text-success" />
                      <div>
                        <p className="text-sm font-medium">Approved by {selected.approver}</p>
                      </div>
                    </div>
                  )}
                  {selected.resolved_at && (
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="w-4 h-4 mt-0.5 text-success" />
                      <div>
                        <p className="text-sm font-medium">Resolved</p>
                        <p className="text-xs opacity-60">{selected.resolved_at}</p>
                      </div>
                    </div>
                  )}
                  {selected.status === 'dismissed' && (
                    <div className="flex items-start gap-3">
                      <Ban className="w-4 h-4 mt-0.5 text-base-content/40" />
                      <div>
                        <p className="text-sm font-medium">Dismissed (false positive)</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default IncidentDashboard;
