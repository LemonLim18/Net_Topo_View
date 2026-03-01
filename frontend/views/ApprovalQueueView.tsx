import React, { useState, useEffect, useCallback } from 'react';
import {
  ListChecks, CheckCircle, XCircle, Clock, RefreshCw,
  Workflow, GitPullRequestArrow, User, MessageSquare
} from 'lucide-react';
import { deployApi, ansibleApi } from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';
import type { ViewId } from '../types';

/* ── Types ─────────────────────────────────────────────────── */

interface PendingItem {
  id: number;
  type: string;        // 'change_request' | 'ansible_job'
  title: string;
  requester: string;
  status: string;
  created_at: string;
}

/* ── Type badge ────────────────────────────────────────────── */

const TypeBadge: React.FC<{ type: string }> = ({ type }) => {
  if (type === 'ansible_job') {
    return (
      <span className="badge badge-sm badge-accent gap-1">
        <Workflow className="w-3 h-3" /> Ansible
      </span>
    );
  }
  return (
    <span className="badge badge-sm badge-info gap-1">
      <GitPullRequestArrow className="w-3 h-3" /> Deploy
    </span>
  );
};

/* ── Main Component ────────────────────────────────────────── */

interface Props {
  setView?: (v: ViewId) => void;
}

const ApprovalQueueView: React.FC<Props> = ({ setView: _setView }) => {
  const [items, setItems] = useState<PendingItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<number | null>(null);
  const [commentModal, setCommentModal] = useState<{
    id: number;
    action: 'approve' | 'reject';
    type: string;
  } | null>(null);
  const [comment, setComment] = useState('');

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const res = await deployApi.listPendingApprovals(100);
      setItems(res.items);
      setTotal(res.total);
    } catch (err: any) {
      Toast.error('Failed to load approvals', err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  const handleAction = async (id: number, action: 'approve' | 'reject', type: string) => {
    const label = action === 'approve' ? 'Approve' : 'Reject';
    const ok = await Confirm.ask(
      `${label} Item #${id}?`,
      `This will ${action} the ${type === 'ansible_job' ? 'Ansible job' : 'change request'}.`
    );
    if (!ok) return;

    setActionId(id);
    try {
      if (type === 'ansible_job' && action === 'approve') {
        await ansibleApi.approveJob(id);
      } else if (action === 'approve') {
        await deployApi.approveRequest(id);
      } else {
        await deployApi.rejectRequest(id);
      }
      Toast.success(`${label}d #${id}`);
      loadQueue();
    } catch (err: any) {
      Toast.error(`${label} failed`, err.message);
    } finally {
      setActionId(null);
    }
  };

  const handleCommentAction = async () => {
    if (!commentModal) return;
    const { id, action, type } = commentModal;
    setActionId(id);
    try {
      if (action === 'approve') {
        if (type === 'ansible_job') {
          await ansibleApi.approveJob(id);
        } else {
          await deployApi.approveRequest(id, 'admin', comment || undefined);
        }
      } else {
        await deployApi.rejectRequest(id, 'admin', comment || undefined);
      }
      Toast.success(`${action === 'approve' ? 'Approved' : 'Rejected'} #${id}`);
      setCommentModal(null);
      setComment('');
      loadQueue();
    } catch (err: any) {
      Toast.error('Action failed', err.message);
    } finally {
      setActionId(null);
    }
  };

  const pendingCount = items.filter(i => i.status.includes('pending') || i.status.includes('awaiting')).length;

  return (
    <div className="h-full overflow-auto bg-base-200 p-4 md:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <ListChecks className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">Approval Queue</h1>
            <p className="text-sm opacity-60">
              Review and approve pending change requests and ansible jobs
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {pendingCount > 0 && (
            <span className="badge badge-warning badge-lg gap-1">
              <Clock className="w-4 h-4" />
              {pendingCount} pending
            </span>
          )}
          <button
            className="btn btn-sm btn-outline gap-2"
            onClick={loadQueue}
            disabled={loading}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="stat bg-base-100 rounded-xl border border-base-300 p-4">
          <div className="stat-title text-xs">Total Pending</div>
          <div className={`stat-value text-2xl ${total === 0 ? 'text-success' : 'text-warning'}`}>
            {total}
          </div>
          <div className="stat-desc">items awaiting review</div>
        </div>
        <div className="stat bg-base-100 rounded-xl border border-base-300 p-4">
          <div className="stat-title text-xs">Change Requests</div>
          <div className="stat-value text-2xl text-info">
            {items.filter(i => i.type === 'change_request').length}
          </div>
          <div className="stat-desc">deployment pipeline</div>
        </div>
        <div className="stat bg-base-100 rounded-xl border border-base-300 p-4">
          <div className="stat-title text-xs">Ansible Jobs</div>
          <div className="stat-value text-2xl text-accent">
            {items.filter(i => i.type === 'ansible_job').length}
          </div>
          <div className="stat-desc">playbook approvals</div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto bg-base-100 rounded-xl border border-base-300 shadow">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <span className="loading loading-spinner loading-md" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 opacity-40">
            <CheckCircle className="w-12 h-12 text-success mb-3" />
            <p className="text-lg font-semibold">All Clear</p>
            <p className="text-sm">No pending approvals at this time.</p>
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
                <th>Created</th>
                <th className="w-48">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={`${item.type}-${item.id}`} className="hover">
                  <td className="font-mono text-xs">#{item.id}</td>
                  <td><TypeBadge type={item.type} /></td>
                  <td className="max-w-xs truncate font-medium" title={item.title}>
                    {item.title}
                  </td>
                  <td>
                    <span className="flex items-center gap-1 text-xs">
                      <User className="w-3 h-3 opacity-50" /> {item.requester}
                    </span>
                  </td>
                  <td>
                    <span className="badge badge-sm badge-warning gap-1">
                      <Clock className="w-3 h-3" /> {item.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="text-xs opacity-60">
                    {item.created_at ? new Date(item.created_at).toLocaleString() : '—'}
                  </td>
                  <td>
                    <div className="flex gap-1">
                      <button
                        className="btn btn-xs btn-success gap-1"
                        disabled={actionId === item.id}
                        onClick={() => handleAction(item.id, 'approve', item.type)}
                      >
                        {actionId === item.id ? (
                          <span className="loading loading-spinner loading-xs" />
                        ) : (
                          <CheckCircle className="w-3 h-3" />
                        )}
                        Approve
                      </button>
                      <button
                        className="btn btn-xs btn-error btn-outline gap-1"
                        disabled={actionId === item.id}
                        onClick={() => handleAction(item.id, 'reject', item.type)}
                      >
                        <XCircle className="w-3 h-3" /> Reject
                      </button>
                      <button
                        className="btn btn-xs btn-ghost gap-1"
                        onClick={() => {
                          setCommentModal({ id: item.id, action: 'approve', type: item.type });
                          setComment('');
                        }}
                        title="Approve/Reject with comment"
                      >
                        <MessageSquare className="w-3 h-3" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Comment Modal */}
      {commentModal && (
        <dialog className="modal modal-open">
          <div className="modal-box">
            <h3 className="font-bold text-lg">
              {commentModal.action === 'approve' ? 'Approve' : 'Reject'} #{commentModal.id}
            </h3>
            <p className="text-sm opacity-60 mt-1">
              Add an optional comment for the audit trail.
            </p>
            <div className="form-control mt-4">
              <label className="label"><span className="label-text">Comment</span></label>
              <textarea
                className="textarea textarea-bordered h-24"
                placeholder="Optional comment..."
                value={comment}
                onChange={e => setComment(e.target.value)}
              />
            </div>
            <div className="flex gap-2 mt-4">
              <button
                className="btn btn-success btn-sm flex-1 gap-1"
                onClick={() => {
                  setCommentModal({ ...commentModal, action: 'approve' });
                  handleCommentAction();
                }}
                disabled={actionId !== null}
              >
                <CheckCircle className="w-4 h-4" /> Approve
              </button>
              <button
                className="btn btn-error btn-sm flex-1 gap-1"
                onClick={() => {
                  setCommentModal({ ...commentModal, action: 'reject' });
                  handleCommentAction();
                }}
                disabled={actionId !== null}
              >
                <XCircle className="w-4 h-4" /> Reject
              </button>
            </div>
            <div className="modal-action mt-2">
              <button className="btn btn-ghost btn-sm" onClick={() => setCommentModal(null)}>
                Cancel
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setCommentModal(null)} />
        </dialog>
      )}
    </div>
  );
};

export default ApprovalQueueView;
