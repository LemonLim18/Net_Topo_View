import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircle, Clock, Eye, ListChecks, RefreshCw, User, XCircle,
} from 'lucide-react';

import { ansibleApi, deployApi } from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';
import type { ViewId } from '../types';

interface QueueItem {
  id: number;
  type: string;
  title: string;
  requester: string;
  status: string;
  created_at: string;
  request_id: number;
  entrypoint_path?: string | null;
  review_comment?: string | null;
  review_actor?: string | null;
  reviewed_at?: string | null;
}

type QueueTab = 'pending' | 'approved' | 'rejected';

interface Props {
  setView?: (v: ViewId) => void;
}

const TAB_LABELS: Record<QueueTab, string> = {
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
};

const APPROVAL_QUEUE_CACHE_KEY = 'approvalQueue.items.v1';

const EMPTY_QUEUE_ITEMS: Record<QueueTab, QueueItem[]> = {
  pending: [],
  approved: [],
  rejected: [],
};

function readCachedQueueItems(): Record<QueueTab, QueueItem[]> {
  try {
    const raw = sessionStorage.getItem(APPROVAL_QUEUE_CACHE_KEY);
    if (!raw) return EMPTY_QUEUE_ITEMS;
    const parsed = JSON.parse(raw) as Partial<Record<QueueTab, QueueItem[]>>;
    return {
      pending: Array.isArray(parsed.pending) ? parsed.pending : [],
      approved: Array.isArray(parsed.approved) ? parsed.approved : [],
      rejected: Array.isArray(parsed.rejected) ? parsed.rejected : [],
    };
  } catch {
    return EMPTY_QUEUE_ITEMS;
  }
}

function normalizeQueueItems(raw: QueueItem[]): QueueItem[] {
  const ordered = [...(raw || [])].sort((a, b) => {
    const aPlaybook = String(a.type || '').toLowerCase() === 'ansible_job';
    const bPlaybook = String(b.type || '').toLowerCase() === 'ansible_job';
    // Prefer playbook job rows when both request/job records point to same request.
    return Number(bPlaybook) - Number(aPlaybook);
  });
  const byRequest = new Map<number, QueueItem>();
  for (const item of ordered) {
    const key = Number(item.request_id || item.id);
    if (!Number.isFinite(key)) continue;
    if (byRequest.has(key)) continue;
    byRequest.set(key, { ...item });
  }
  return Array.from(byRequest.values());
}

const statusBadgeClass = (status: string) => {
  if (status === 'approved') return 'badge-success';
  if (status === 'rejected') return 'badge-error';
  return 'badge-warning';
};

const statusDisplayLabel = (status: string) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'pending_approval' || normalized === 'pending approval') return 'pending';
  return normalized.replace(/_/g, ' ') || 'pending';
};

const ApprovalQueueView: React.FC<Props> = ({ setView: _setView }) => {
  void _setView;
  const [activeTab, setActiveTab] = useState<QueueTab>('pending');
  const [items, setItems] = useState<Record<QueueTab, QueueItem[]>>(readCachedQueueItems);
  const [loading, setLoading] = useState<Record<QueueTab, boolean>>({
    pending: false,
    approved: false,
    rejected: false,
  });
  const [actionId, setActionId] = useState<number | null>(null);
  const [commentModal, setCommentModal] = useState<{ id: number } | null>(null);
  const [comment, setComment] = useState('');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewTitle, setPreviewTitle] = useState('');
  const [previewContent, setPreviewContent] = useState('Loading playbook preview...');
  const [previewLoading, setPreviewLoading] = useState(false);

  const loadTab = useCallback(async (tab: QueueTab) => {
    setLoading((prev) => ({ ...prev, [tab]: true }));
    try {
      const res = await deployApi.listApprovalQueue(tab, 100);
      setItems((prev) => ({ ...prev, [tab]: normalizeQueueItems(res.items || []) }));
    } catch (err: any) {
      Toast.error(`Failed to load ${TAB_LABELS[tab]}`, err.message);
    } finally {
      setLoading((prev) => ({ ...prev, [tab]: false }));
    }
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem(APPROVAL_QUEUE_CACHE_KEY, JSON.stringify(items));
    } catch {
      // ignore storage failures
    }
  }, [items]);

  const refreshAllTabs = useCallback(async () => {
    await Promise.all([
      loadTab('pending'),
      loadTab('approved'),
      loadTab('rejected'),
    ]);
  }, [loadTab]);

  useEffect(() => {
    refreshAllTabs();
  }, [refreshAllTabs]);

  const counts = useMemo(() => ({
    pending: items.pending.length,
    approved: items.approved.length,
    rejected: items.rejected.length,
  }), [items]);

  const handleAction = useCallback(
    async (
      item: QueueItem,
      action: 'approve' | 'reject',
      actionComment?: string,
      opts?: { skipConfirm?: boolean }
    ) => {
    const label = action === 'approve' ? 'Approve' : 'Reject';
    const confirmMessage =
      action === 'approve'
        ? item.type === 'ansible_job'
          ? 'This approves the Ansible job and the linked change request. You can run Execute from Ansible Studio after approval.'
          : 'This approves the change request. If a playbook job exists, prefer approving from the Playbook row so job state stays in sync.'
        : 'This will move the item to Rejected with your review note.';
    if (!opts?.skipConfirm) {
      const ok = await Confirm.ask(`${label} #${item.id}?`, confirmMessage);
      if (!ok) return;
    }

    setActionId(item.id);
    try {
      if (item.type === 'ansible_job') {
        if (action === 'approve') {
          await ansibleApi.approveJob(item.id);
        } else {
          await ansibleApi.rejectJob(item.id, actionComment);
        }
      } else if (action === 'approve') {
        await deployApi.approveRequest(item.request_id, 'admin', actionComment);
      } else {
        await deployApi.rejectRequest(item.request_id, 'admin', actionComment);
      }
      Toast.success(`${label}d #${item.id}`);
      setCommentModal(null);
      setComment('');
      await refreshAllTabs();
      if (activeTab !== 'pending') setActiveTab('pending');
    } catch (err: any) {
      Toast.error(`${label} failed`, err.message);
    } finally {
      setActionId(null);
    }
  }, [activeTab, refreshAllTabs]);

  const loadPlaybookPreviewForJob = useCallback(async (jobId: number, entryHint?: string | null) => {
    let path = (entryHint || '').trim();
    if (!path) {
      const result = await ansibleApi.getResult(jobId);
      path = (result.entrypoint_path || '').trim();
    }
    if (!path) {
      setPreviewContent(
        'This job has no entrypoint path recorded yet. Open **Ansible Studio** from the queue to inspect generated files.'
      );
      return;
    }
    const file = await ansibleApi.readFile(path);
    setPreviewContent(file.content || '# Empty playbook');
  }, []);

  const openPreview = useCallback(
    async (item: QueueItem) => {
      setPreviewOpen(true);
      setPreviewTitle(item.title);
      setPreviewContent('Loading playbook preview...');
      setPreviewLoading(true);
      try {
        if (item.type === 'ansible_job') {
          await loadPlaybookPreviewForJob(item.id, item.entrypoint_path);
        } else {
          try {
            const link = await ansibleApi.getJobForRequest(item.request_id);
            await loadPlaybookPreviewForJob(link.job_id, link.entrypoint_path);
          } catch {
            setPreviewContent(
              'No Ansible playbook job is linked to this change request, so there is nothing to preview. ' +
                'If this was submitted outside Ansible Studio, YAML preview may not be available.'
            );
          }
        }
      } catch (err: any) {
        setPreviewContent(`Unable to load preview.\n\n${err.message || 'Unknown error'}`);
      } finally {
        setPreviewLoading(false);
      }
    },
    [loadPlaybookPreviewForJob]
  );

  const tabItems = items[activeTab];
  const tabLoading = loading[activeTab] && tabItems.length === 0;

  return (
    <div className="h-full overflow-auto bg-base-200 p-4 md:p-6 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <ListChecks className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">Approval Queue</h1>
            <p className="text-sm opacity-60 max-w-xl">
              Unified request review list with live counts across Pending, Approved, and Rejected tabs.
            </p>
          </div>
        </div>
        <button
          className="btn btn-sm btn-outline gap-2"
          onClick={refreshAllTabs}
          disabled={loading.pending || loading.approved || loading.rejected}
        >
          <RefreshCw className={`w-4 h-4 ${(loading.pending || loading.approved || loading.rejected) ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {(['pending', 'approved', 'rejected'] as QueueTab[]).map((tab) => (
          <button
            key={tab}
            type="button"
            className={`stat rounded-xl border p-4 text-left transition ${activeTab === tab ? 'bg-base-100 border-primary shadow' : 'bg-base-100/60 border-base-300'}`}
            onClick={() => setActiveTab(tab)}
          >
            <div className="stat-title text-xs">{TAB_LABELS[tab]}</div>
            <div className={`stat-value text-2xl ${tab === 'pending' ? 'text-warning' : tab === 'approved' ? 'text-success' : 'text-error'}`}>
              {counts[tab]}
            </div>
            <div className="stat-desc">
              {tab === 'pending' ? 'Awaiting reviewer decision' : tab === 'approved' ? 'Ready for deploy' : 'Sent back with review'}
            </div>
          </button>
        ))}
      </div>

      <div className="tabs tabs-boxed w-fit">
        {(['pending', 'approved', 'rejected'] as QueueTab[]).map((tab) => (
          <button
            key={tab}
            type="button"
            className={`tab ${activeTab === tab ? 'tab-active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto bg-base-100 rounded-xl border border-base-300 shadow">
        {tabLoading ? (
          <div className="flex items-center justify-center py-16">
            <span className="loading loading-spinner loading-md" />
          </div>
        ) : tabItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 opacity-40">
            <CheckCircle className="w-12 h-12 text-success mb-3" />
            <p className="text-lg font-semibold">No {TAB_LABELS[activeTab].toLowerCase()} items</p>
            <p className="text-sm">This tab is clear right now.</p>
          </div>
        ) : (
          <table className="table table-sm w-full">
            <thead className="bg-base-200">
              <tr>
                <th>ID</th>
                <th>Title</th>
                <th>Requester</th>
                <th>Status</th>
                <th>Review</th>
                <th>Created</th>
                <th className="w-56">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tabItems.map((item) => (
                <tr key={`${item.type}-${item.id}`} className="hover">
                  <td className="font-mono text-xs">#{item.id}</td>
                  <td className="max-w-xs truncate font-medium" title={item.title}>{item.title}</td>
                  <td>
                    <span className="flex items-center gap-1 text-xs">
                      <User className="w-3 h-3 opacity-50" /> {item.requester}
                    </span>
                  </td>
                  <td>
                    <span className={`badge badge-sm gap-1 ${statusBadgeClass(item.status)}`}>
                      <Clock className="w-3 h-3" /> {statusDisplayLabel(item.status)}
                    </span>
                  </td>
                  <td className="text-xs">
                    {item.review_comment ? (
                        <div className="max-w-xs">
                        <div className="font-medium">{item.review_actor || 'Reviewer'}</div>
                        <div className="opacity-70 truncate" title={item.review_comment}>{item.review_comment}</div>
                      </div>
                    ) : (
                      <span className="opacity-40">No review note</span>
                    )}
                  </td>
                  <td className="text-xs opacity-60">
                    {item.created_at ? new Date(item.created_at).toLocaleString() : '—'}
                  </td>
                  <td>
                    <div className={`flex items-center gap-1 ${activeTab === 'pending' ? 'flex-nowrap whitespace-nowrap' : 'flex-wrap'}`}>
                      <button
                        className="btn btn-xs btn-ghost gap-1"
                        onClick={() => openPreview(item)}
                        title="Read-only preview"
                      >
                        <Eye className="w-3 h-3" /> Preview
                      </button>
                      {activeTab === 'pending' && (
                        <>
                          <button
                            className="btn btn-xs btn-success gap-1"
                            disabled={actionId === item.id}
                            onClick={() => handleAction(item, 'approve')}
                          >
                            <CheckCircle className="w-3 h-3" /> Accept
                          </button>
                          <button
                            className="btn btn-xs btn-error btn-outline gap-1"
                            disabled={actionId === item.id}
                            onClick={() => {
                              setCommentModal({ id: item.id });
                              setComment('');
                            }}
                          >
                            <XCircle className="w-3 h-3" /> Reject
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {commentModal && (
        <dialog className="modal modal-open">
          <div className="modal-box">
            <h3 className="font-bold text-lg">Reject #{commentModal.id}</h3>
            <p className="text-sm opacity-60 mt-1">
              Optional note for the engineer and audit trail.
            </p>
            <textarea
              className="textarea textarea-bordered h-28 w-full mt-4"
              placeholder="Explain why this was rejected..."
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            <div className="modal-action">
              <button
                className="btn btn-error gap-2"
                onClick={() => {
                  const item = items.pending.find((entry) => entry.id === commentModal.id);
                  if (item) handleAction(item, 'reject', comment || undefined, { skipConfirm: true });
                }}
                disabled={actionId !== null}
              >
                <XCircle className="w-4 h-4" />
                Submit rejection
              </button>
              <button className="btn btn-ghost" onClick={() => setCommentModal(null)}>
                Cancel
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setCommentModal(null)} />
        </dialog>
      )}

      {previewOpen && (
        <dialog className="modal modal-open">
          <div className="modal-box max-w-5xl">
            <h3 className="font-bold text-lg">Preview: {previewTitle}</h3>
            <p className="text-sm opacity-60 mt-1">
              Read-only YAML preview for this request.
            </p>
            <div className="mt-4 rounded-lg border border-base-300 bg-base-300/60 overflow-hidden">
              {previewLoading ? (
                <div className="flex items-center justify-center py-12">
                  <span className="loading loading-spinner loading-md" />
                </div>
              ) : (
                <pre className="p-4 text-xs overflow-auto max-h-[70vh] whitespace-pre-wrap">{previewContent}</pre>
              )}
            </div>
            <div className="modal-action">
              <button className="btn" onClick={() => setPreviewOpen(false)}>Close</button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setPreviewOpen(false)} />
        </dialog>
      )}
    </div>
  );
};

export default ApprovalQueueView;
