import React, { useState, useEffect, useCallback } from 'react';
import {
  Search, Filter, Copy, XCircle, Edit3, ChevronDown, ChevronRight,
  Clock, AlertTriangle, CheckCircle2, Ban, FileText, RefreshCw,
  Zap, Eye, Shield, RotateCcw, Play, HelpCircle
} from 'lucide-react';
import { intentApi } from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';
import type { ViewId } from '../types';

// ─── Status / Risk Badges ────────────────────────────────────────────

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  draft:     { label: 'Draft',     color: 'badge-ghost',   icon: <Edit3 className="w-3 h-3" /> },
  parsed:    { label: 'Parsed',    color: 'badge-info',    icon: <Zap className="w-3 h-3" /> },
  validated: { label: 'Validated', color: 'badge-primary',  icon: <CheckCircle2 className="w-3 h-3" /> },
  planned:   { label: 'Planned',   color: 'badge-warning', icon: <Clock className="w-3 h-3" /> },
  approved:  { label: 'Approved',  color: 'badge-success', icon: <CheckCircle2 className="w-3 h-3" /> },
  executed:  { label: 'Executed',  color: 'badge-success', icon: <Play className="w-3 h-3" /> },
  verified:  { label: 'Verified',  color: 'badge-success', icon: <Shield className="w-3 h-3" /> },
  rejected:  { label: 'Rejected',  color: 'badge-error',   icon: <Ban className="w-3 h-3" /> },
  cancelled: { label: 'Cancelled', color: 'badge-neutral',  icon: <XCircle className="w-3 h-3" /> },
};

const RISK_CONFIG: Record<string, { label: string; color: string }> = {
  low:      { label: 'Low',      color: 'badge-success' },
  medium:   { label: 'Medium',   color: 'badge-warning' },
  high:     { label: 'High',     color: 'badge-error' },
  critical: { label: 'Critical', color: 'badge-error' },
};

const TYPE_CONFIG: Record<string, { label: string; icon: React.ReactNode }> = {
  configure:    { label: 'Configure',    icon: <Edit3 className="w-3.5 h-3.5" /> },
  troubleshoot: { label: 'Troubleshoot', icon: <HelpCircle className="w-3.5 h-3.5" /> },
  query:        { label: 'Query',        icon: <Eye className="w-3.5 h-3.5" /> },
  audit:        { label: 'Audit',        icon: <Shield className="w-3.5 h-3.5" /> },
  rollback:     { label: 'Rollback',     icon: <RotateCcw className="w-3.5 h-3.5" /> },
  deploy:       { label: 'Deploy',       icon: <Play className="w-3.5 h-3.5" /> },
};

// ─── Main Component ──────────────────────────────────────────────────

interface Props {
  setView?: (v: ViewId) => void;
}

interface IntentItem {
  id: string;
  user_prompt: string;
  intent_type: string;
  action?: string;
  feature?: string;
  vendor?: string;
  risk_level: string;
  status: string;
  blast_radius: number;
  resolved_devices?: string[];
  policy_approved?: boolean;
  created_at?: string;
}

const IntentHistoryView: React.FC<Props> = ({ setView }) => {
  const [intents, setIntents] = useState<IntentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);

  // Filters
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [riskFilter, setRiskFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  // Expanded row
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Edit modal
  const [editingIntent, setEditingIntent] = useState<IntentItem | null>(null);
  const [editPrompt, setEditPrompt] = useState('');

  const PAGE_SIZE = 15;

  const fetchIntents = useCallback(async () => {
    setLoading(true);
    try {
      const data = await intentApi.list({
        status: statusFilter || undefined,
        intent_type: typeFilter || undefined,
        risk_level: riskFilter || undefined,
        search: searchQuery || undefined,
        page,
        page_size: PAGE_SIZE,
      });
      setIntents(data.items);
      setTotal(data.total);
    } catch (e: any) {
      Toast.error('Failed to load intents', e.message);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, typeFilter, riskFilter, searchQuery, page]);

  useEffect(() => { fetchIntents(); }, [fetchIntents]);

  const fetchDetail = async (id: string) => {
    setDetailLoading(true);
    try {
      const d = await intentApi.get(id);
      setDetail(d);
    } catch {
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const toggleExpand = (id: string) => {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
    } else {
      setExpandedId(id);
      fetchDetail(id);
    }
  };

  // Actions
  const handleCancel = async (id: string) => {
    if (!(await Confirm.ask('Cancel Intent?', 'This intent will be marked as cancelled.'))) return;
    try {
      await intentApi.cancel(id);
      Toast.success('Intent cancelled');
      fetchIntents();
    } catch (e: any) {
      Toast.error('Cancel failed', e.message);
    }
  };

  const handleDuplicate = async (id: string) => {
    try {
      const dup = await intentApi.duplicate(id);
      Toast.success(`Duplicated → ${dup.id}`);
      fetchIntents();
    } catch (e: any) {
      Toast.error('Duplicate failed', e.message);
    }
  };

  const handleEditSave = async () => {
    if (!editingIntent) return;
    try {
      await intentApi.update(editingIntent.id, { user_prompt: editPrompt });
      Toast.success('Intent updated');
      setEditingIntent(null);
      fetchIntents();
    } catch (e: any) {
      Toast.error('Update failed', e.message);
    }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="h-full overflow-auto bg-base-200 p-4 md:p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Intent History</h1>
          <p className="text-sm opacity-60 mt-1">
            Track, edit, cancel, and duplicate all intent submissions ({total} total)
          </p>
        </div>
        <button className="btn btn-sm btn-outline gap-2" onClick={fetchIntents} disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 items-center bg-base-100 rounded-lg p-3">
        <div className="relative flex-grow max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 opacity-40" />
          <input
            type="text"
            placeholder="Search intents..."
            className="input input-sm input-bordered w-full pl-9"
            value={searchQuery}
            onChange={(e) => { setSearchQuery(e.target.value); setPage(1); }}
          />
        </div>

        <select className="select select-sm select-bordered" value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}>
          <option value="">All Status</option>
          {Object.entries(STATUS_CONFIG).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>

        <select className="select select-sm select-bordered" value={typeFilter}
                onChange={(e) => { setTypeFilter(e.target.value); setPage(1); }}>
          <option value="">All Types</option>
          {Object.entries(TYPE_CONFIG).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>

        <select className="select select-sm select-bordered" value={riskFilter}
                onChange={(e) => { setRiskFilter(e.target.value); setPage(1); }}>
          <option value="">All Risk</option>
          {Object.entries(RISK_CONFIG).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>

        {(statusFilter || typeFilter || riskFilter || searchQuery) && (
          <button className="btn btn-sm btn-ghost gap-1" onClick={() => {
            setStatusFilter(''); setTypeFilter(''); setRiskFilter(''); setSearchQuery(''); setPage(1);
          }}>
            <Filter className="w-3 h-3" /> Clear
          </button>
        )}
      </div>

      {/* Table */}
      <div className="overflow-x-auto bg-base-100 rounded-lg shadow">
        <table className="table table-sm w-full">
          <thead>
            <tr>
              <th className="w-8"></th>
              <th>ID</th>
              <th>Prompt</th>
              <th>Type</th>
              <th>Risk</th>
              <th>Status</th>
              <th>Devices</th>
              <th>Created</th>
              <th className="w-36">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading && intents.length === 0 ? (
              <tr><td colSpan={9} className="text-center py-8">
                <span className="loading loading-spinner loading-md"></span>
              </td></tr>
            ) : intents.length === 0 ? (
              <tr><td colSpan={9} className="text-center py-8 opacity-50">
                No intents found. Submit an intent via Network Copilot or Ansible Studio.
              </td></tr>
            ) : intents.map((intent) => {
              const statusCfg = STATUS_CONFIG[intent.status] ?? { label: intent.status, color: 'badge-ghost', icon: null };
              const riskCfg = RISK_CONFIG[intent.risk_level] ?? { label: intent.risk_level, color: 'badge-ghost' };
              const typeCfg = TYPE_CONFIG[intent.intent_type] ?? { label: intent.intent_type, icon: null };
              const isExpanded = expandedId === intent.id;
              const canEdit = ['parsed', 'draft', 'validated'].includes(intent.status);
              const canCancel = !['executed', 'verified', 'cancelled'].includes(intent.status);

              return (
                <React.Fragment key={intent.id}>
                  <tr className={`hover cursor-pointer ${isExpanded ? 'bg-base-200' : ''}`}
                      onClick={() => toggleExpand(intent.id)}>
                    <td>
                      {isExpanded
                        ? <ChevronDown className="w-4 h-4 text-primary" />
                        : <ChevronRight className="w-4 h-4 opacity-40" />}
                    </td>
                    <td className="font-mono text-xs">{intent.id}</td>
                    <td className="max-w-xs truncate" title={intent.user_prompt}>
                      {intent.user_prompt}
                    </td>
                    <td>
                      <span className="flex items-center gap-1 text-xs">
                        {typeCfg.icon} {typeCfg.label}
                      </span>
                    </td>
                    <td><span className={`badge badge-xs ${riskCfg.color}`}>{riskCfg.label}</span></td>
                    <td>
                      <span className={`badge badge-xs gap-1 ${statusCfg.color}`}>
                        {statusCfg.icon} {statusCfg.label}
                      </span>
                    </td>
                    <td className="text-xs">
                      {intent.resolved_devices?.length ?? intent.blast_radius ?? 0}
                    </td>
                    <td className="text-xs opacity-60">
                      {intent.created_at ? new Date(intent.created_at).toLocaleString() : '—'}
                    </td>
                    <td>
                      <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
                        {canEdit && (
                          <button className="btn btn-xs btn-ghost" title="Edit"
                                  onClick={() => { setEditingIntent(intent); setEditPrompt(intent.user_prompt); }}>
                            <Edit3 className="w-3 h-3" />
                          </button>
                        )}
                        {canCancel && (
                          <button className="btn btn-xs btn-ghost text-error" title="Cancel"
                                  onClick={() => handleCancel(intent.id)}>
                            <XCircle className="w-3 h-3" />
                          </button>
                        )}
                        <button className="btn btn-xs btn-ghost" title="Duplicate"
                                onClick={() => handleDuplicate(intent.id)}>
                          <Copy className="w-3 h-3" />
                        </button>
                      </div>
                    </td>
                  </tr>

                  {/* Expanded Detail Row */}
                  {isExpanded && (
                    <tr>
                      <td colSpan={9} className="bg-base-200 p-4">
                        {detailLoading ? (
                          <div className="flex justify-center py-4">
                            <span className="loading loading-spinner loading-sm"></span>
                          </div>
                        ) : detail ? (
                          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                            {/* Column 1: Intent Details */}
                            <div className="space-y-3">
                              <h4 className="font-semibold text-sm flex items-center gap-1">
                                <FileText className="w-4 h-4" /> Intent Details
                              </h4>
                              <div className="bg-base-100 rounded p-3 text-xs space-y-2">
                                <div><span className="font-semibold">Action:</span> {detail.action || '—'}</div>
                                <div><span className="font-semibold">Feature:</span> {detail.feature || '—'}</div>
                                <div><span className="font-semibold">Vendor:</span> {detail.vendor || 'any'}</div>
                                <div><span className="font-semibold">Policy Approved:</span>{' '}
                                  {detail.policy_approved === true ? '✅ Yes' : detail.policy_approved === false ? '❌ No' : '—'}
                                </div>
                                {detail.resolved_devices && detail.resolved_devices.length > 0 && (
                                  <div>
                                    <span className="font-semibold">Target Devices:</span>
                                    <div className="flex flex-wrap gap-1 mt-1">
                                      {detail.resolved_devices.map((d: string) => (
                                        <span key={d} className="badge badge-xs badge-outline">{d}</span>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>

                              {detail.parameters && Object.keys(detail.parameters).length > 0 && (
                                <div>
                                  <h5 className="font-semibold text-xs mb-1">Parameters</h5>
                                  <pre className="bg-base-100 rounded p-2 text-xs overflow-auto max-h-40">
                                    {JSON.stringify(detail.parameters, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </div>

                            {/* Column 2: Scope & Policy */}
                            <div className="space-y-3">
                              <h4 className="font-semibold text-sm flex items-center gap-1">
                                <AlertTriangle className="w-4 h-4" /> Scope & Policy
                              </h4>
                              {detail.scope && (
                                <pre className="bg-base-100 rounded p-3 text-xs overflow-auto max-h-48">
                                  {JSON.stringify(detail.scope, null, 2)}
                                </pre>
                              )}
                              {detail.policy_result && (
                                <div>
                                  <h5 className="font-semibold text-xs mb-1">Policy Result</h5>
                                  <pre className="bg-base-100 rounded p-2 text-xs overflow-auto max-h-40">
                                    {JSON.stringify(detail.policy_result, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </div>

                            {/* Column 3: Linked Playbooks */}
                            <div className="space-y-3">
                              <h4 className="font-semibold text-sm flex items-center gap-1">
                                <FileText className="w-4 h-4" /> Linked Playbooks
                              </h4>
                              {detail.linked_proposals?.length > 0 ? (
                                <div className="space-y-2">
                                  {detail.linked_proposals.map((p: any) => (
                                    <div key={p.id} className="bg-base-100 rounded p-2 text-xs">
                                      <div className="flex justify-between">
                                        <span className="font-mono">{p.id?.slice(0, 8)}</span>
                                        <span className={`badge badge-xs ${
                                          p.status === 'deployed' ? 'badge-success' :
                                          p.status === 'approved' ? 'badge-primary' : 'badge-ghost'
                                        }`}>{p.status}</span>
                                      </div>
                                      <div className="mt-1 opacity-60">{p.feature} / {p.vendor}</div>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <p className="text-xs opacity-50">No linked proposals</p>
                              )}
                              {detail.linked_plans?.length > 0 && (
                                <div className="space-y-2">
                                  <h5 className="font-semibold text-xs">Agent Plans</h5>
                                  {detail.linked_plans.map((p: any) => (
                                    <div key={p.id} className="bg-base-100 rounded p-2 text-xs">
                                      <div className="flex justify-between">
                                        <span className="font-mono">{p.id?.slice(0, 8)}</span>
                                        <span className={`badge badge-xs ${
                                          p.status === 'completed' ? 'badge-success' : 'badge-warning'
                                        }`}>{p.status}</span>
                                      </div>
                                      <div className="mt-1 opacity-60">{p.risk_level} risk</div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        ) : (
                          <p className="text-xs opacity-50">No details available</p>
                        )}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center">
          <div className="join">
            <button className="join-item btn btn-sm" disabled={page <= 1}
                    onClick={() => setPage(p => Math.max(1, p - 1))}>«</button>
            <button className="join-item btn btn-sm">Page {page} of {totalPages}</button>
            <button className="join-item btn btn-sm" disabled={page >= totalPages}
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}>»</button>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editingIntent && (
        <dialog className="modal modal-open">
          <div className="modal-box">
            <h3 className="font-bold text-lg">Edit Intent — {editingIntent.id}</h3>
            <p className="text-sm opacity-60 mt-1">Modify the user prompt for this intent.</p>
            <div className="form-control mt-4">
              <label className="label"><span className="label-text">User Prompt</span></label>
              <textarea
                className="textarea textarea-bordered h-32"
                value={editPrompt}
                onChange={(e) => setEditPrompt(e.target.value)}
              />
            </div>
            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setEditingIntent(null)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleEditSave}
                      disabled={editPrompt === editingIntent.user_prompt}>
                Save Changes
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setEditingIntent(null)}></div>
        </dialog>
      )}
    </div>
  );
};

export default IntentHistoryView;
