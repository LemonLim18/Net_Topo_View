import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircle, Clock, Eye, GitPullRequestArrow, ListChecks,
  MessageSquare, RefreshCw, User, Workflow, XCircle,
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

const TypeBadge: React.FC<{ type: string }> = ({ type }) => (
  type === 'ansible_job' ? (
    <span className="badge badge-sm badge-accent gap-1">
      <Workflow className="w-3 h-3" /> Playbook
    </span>
  ) : (
    <span className="badge badge-sm badge-info gap-1">
      <GitPullRequestArrow className="w-3 h-3" /> Request
    </span>
  )
);

const statusBadgeClass = (status: string) => {
  if (status === 'approved') return 'badge-success';
  if (status === 'rejected') return 'badge-error';
  return 'badge-warning';
};

const ApprovalQueueView: React.FC<Props> = () => {
  const [activeTab, setActiveTab] = useState<QueueTab>('pending');
  const [items, setItems] = useState<Record<QueueTab, QueueItem[]>>({
    pending: [],
    approved: [],
    rejected: [],
  });
  const [loading, setLoading] = useState<Record<QueueTab, boolean>>({
    pending: true,
    approved: false,
    rejected: false,
  });
  const [loaded, setLoaded] = useState<Record<QueueTab, boolean>>({
    pending: false,
    approved: false,
    rejected: false,
  });
  const [actionId, setActionId] = useState<number | null>(null);
  const [commentModal, setCommentModal] = useState<{
    id: number;
    type: string;
    action: 'approve' | 'reject';
  } | null>(null);
  const [comment, setComment] = useState('');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewTitle, setPreviewTitle] = useState('');
  const [previewContent, setPreviewContent] = useState('Loading playbook preview...');
  const [previewLoading, setPreviewLoading] = useState(false);

  const loadTab = useCallback(async (tab: QueueTab) => {
    setLoading((prev) => ({ ...prev, [tab]: true }));
    try {
      const res = await deployApi.listApprovalQueue(tab, 100);
      setItems((prev) => ({ ...prev, [tab]: res.items }));
      setLoaded((prev) => ({ ...prev, [tab]: true }));
    } catch (err: any) {
      Toast.error(`Failed to load ${TAB_LABELS[tab]}`, err.message);
    } finally {
      setLoading((prev) => ({ ...prev, [tab]: false }));
    }
  }, []);

  useEffect(() => {
    loadTab('pending');
  }, [loadTab]);

  useEffect(() => {
    if (!loaded[activeTab] && !loading[activeTab]) {
      loadTab(activeTab);
    }
  }, [activeTab, loaded, loading, loadTab]);

  const refreshAllTabs = useCallback(async () => {
    await Promise.all([
      loadTab('pending'),
      loadTab('approved'),
      loadTab('rejected'),
    ]);
  }, [loadTab]);

  const counts = useMemo(() => ({
    pending: items.pending.length,
    approved: items.approved.length,
    rejected: items.rejected.length,
  }), [items]);

  const handleAction = useCallback(async (item: QueueItem, action: 'approve' | 'reject', actionComment?: string) => {
    const label = action === 'approve' ? 'Approve' : 'Reject';
    const confirmMessage = action === 'approve'
      ? 'This playbook will move to the Approved tab and can then be deployed.'
      : 'This playbook will move to the Rejected tab with your review note.';
    const ok = await Confirm.ask(`${label} #${item.id}?`, confirmMessage);
    if (!ok) return;

    setActionId(item.id);
    try {
      if (item.type === 'ansible_job') {
        if (action === 'approve') await ansibleApi.approveJob(item.id);
        else await ansibleApi.rejectJob(item.id, actionComment);
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

  const openPreview = useCallback(async (item: QueueItem) => {
    setPreviewOpen(true);
    setPreviewTitle(item.title);
    setPreviewContent('Loading playbook preview...');
    setPreviewLoading(true);
    try {
      if (item.type !== 'ansible_job') {
        setPreviewContent('No playbook artifact is attached to this queue item.');
        return;
      }
      let path = item.entrypoint_path || '';
      if (!path) {
        const result = await ansibleApi.getResult(item.id);
        path = result.entrypoint_path || '';
      }
      if (!path) {
        setPreviewContent('This playbook has no entrypoint path recorded yet.');
        return;
      }
      const file = await ansibleApi.readFile(path);
      setPreviewContent(file.content || '# Empty playbook');
    } catch (err: any) {
      setPreviewContent(`Unable to load preview.\n\n${err.message || 'Unknown error'}`);
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  const tabItems = items[activeTab];
  const tabLoading = loading[activeTab];

  return (
    <div className="h-full overflow-auto bg-base-200 p-4 md:p-6 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <ListChecks className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">Approval Queue</h1>
            <p className="text-sm opacity-60">
              Admin-only review queue for engineer-submitted playbooks and deployment requests
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
              {tab === 'pending' ? 'Awaiting admin review' : tab === 'approved' ? 'Ready for deploy' : 'Sent back with review'}
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
                <th>Type</th>
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
                  <td><TypeBadge type={item.type} /></td>
                  <td className="max-w-xs truncate font-medium" title={item.title}>{item.title}</td>
                  <td>
                    <span className="flex items-center gap-1 text-xs">
                      <User className="w-3 h-3 opacity-50" /> {item.requester}
                    </span>
                  </td>
                  <td>
                    <span className={`badge badge-sm gap-1 ${statusBadgeClass(item.status)}`}>
                      <Clock className="w-3 h-3" /> {item.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="text-xs">
                    {item.review_comment ? (
                      <div className="max-w-xs">
                        <div className="font-medium">{item.review_actor || 'Admin'}</div>
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
                    <div className="flex flex-wrap gap-1">
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
                            <CheckCircle className="w-3 h-3" /> Approve
                          </button>
                          <button
                            className="btn btn-xs btn-error btn-outline gap-1"
                            disabled={actionId === item.id}
                            onClick={() => {
                              setCommentModal({ id: item.id, type: item.type, action: 'reject' });
                              setComment('');
                            }}
                          >
                            <XCircle className="w-3 h-3" /> Reject
                          </button>
                          <button
                            className="btn btn-xs btn-ghost gap-1"
                            onClick={() => {
                              setCommentModal({ id: item.id, type: item.type, action: 'approve' });
                              setComment('');
                            }}
                            title="Approve or reject with comment"
                          >
                            <MessageSquare className="w-3 h-3" />
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
            <h3 className="font-bold text-lg">
              {commentModal.action === 'approve' ? 'Approve' : 'Reject'} #{commentModal.id}
            </h3>
            <p className="text-sm opacity-60 mt-1">
              This note will be shown in the queue history and audit trail.
            </p>
            <textarea
              className="textarea textarea-bordered h-28 w-full mt-4"
              placeholder={commentModal.action === 'approve' ? 'Optional approval note...' : 'Explain why this was rejected...'}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            <div className="modal-action">
              <button
                className={`btn gap-2 ${commentModal.action === 'approve' ? 'btn-success' : 'btn-error'}`}
                onClick={() => {
                  const item = items.pending.find((entry) => entry.id === commentModal.id && entry.type === commentModal.type);
                  if (item) handleAction(item, commentModal.action, comment || undefined);
                }}
                disabled={actionId !== null}
              >
                {commentModal.action === 'approve' ? <CheckCircle className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                {commentModal.action === 'approve' ? 'Submit Approval' : 'Submit Rejection'}
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
              Read-only preview. Admin review cannot modify the engineer's stored playbook from here.
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
