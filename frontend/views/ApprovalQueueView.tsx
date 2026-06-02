import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  CheckCircle, Clock, Eye, ListChecks, RefreshCw, User, XCircle,
} from 'lucide-react';

import { ansibleApi, deployApi, llmApi, type PlaybookAnalysisResponse } from '../src/services/api';
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
  owner_username?: string | null;
}

type QueueTab = 'pending' | 'approved' | 'rejected';
type SortKey = 'id' | 'title' | 'requester' | 'status' | 'review' | 'created_at';
type SortDir = 'asc' | 'desc';

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

const MarkdownText: React.FC<{ children?: string }> = ({ children }) => (
  <div className="prose prose-sm prose-invert max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0 prose-strong:text-base-content">
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {children || ''}
    </ReactMarkdown>
  </div>
);

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

function parseCreatedAt(value?: string | null): number {
  const raw = String(value || '').trim();
  if (!raw) return 0;
  // If the string already has timezone info (+xx:xx or Z), parse as-is
  if (/[Zz]$/.test(raw) || /[+-]\d{2}:\d{2}$/.test(raw)) {
    const ts = Date.parse(raw);
    if (!Number.isNaN(ts)) return ts;
  }
  // Bare datetime string (no tz) — always treat as UTC by appending Z
  // Handles both "2026-06-02T11:31:19" and "2026-06-02 11:31:19" formats
  const normalized = raw.replace(' ', 'T').replace(/\.\d+$/, '');
  const asUtc = Date.parse(`${normalized}Z`);
  if (!Number.isNaN(asUtc)) return asUtc;
  return 0;
}

function formatCreatedAt(value?: string | null): string {
  const ts = parseCreatedAt(value);
  if (!ts) return '—';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  }).format(new Date(ts));
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

  // Playbook AI Review states
  const [aiTab, setAiTab] = useState<'audit' | 'chat'>('audit');
  const [analysis, setAnalysis] = useState<PlaybookAnalysisResponse | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [qaHistory, setQaHistory] = useState<{ role: 'user' | 'assistant'; content: string; quote?: string }[]>([]);
  const [qaInput, setQaInput] = useState('');
  const [qaSending, setQaSending] = useState(false);
  const [pendingQuote, setPendingQuote] = useState('');
  const [selectionCoords, setSelectionCoords] = useState<{ top: number; left: number } | null>(null);
  const [activeQuote, setActiveQuote] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('id');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

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

  const triggerAnalysis = useCallback(async (content: string, promptRequest?: string) => {
    setAnalysis(null);
    setQaHistory([]);
    setQaInput('');
    setAiTab('audit');

    const invalidContent = 
      !content || 
      content.startsWith('Loading playbook preview') || 
      content.startsWith('No Ansible playbook job') || 
      content.startsWith('Unable to load preview') ||
      content.startsWith('This job has no entrypoint');
      
    if (invalidContent) return;

    setAnalysisLoading(true);
    try {
      const res = await llmApi.analyzePlaybook(content, promptRequest);
      setAnalysis(res);
    } catch (e) {
      console.error("Failed to analyze playbook:", e);
    } finally {
      setAnalysisLoading(false);
    }
  }, []);

  const handleSendQa = async () => {
    if (!qaInput.trim() || qaSending || !previewContent) return;
    const userMsg = qaInput.trim();
    const quoteToSubmit = activeQuote;
    setQaInput('');
    setActiveQuote('');
    setQaHistory((prev) => [...prev, { role: 'user', content: userMsg, quote: quoteToSubmit }]);
    setQaSending(true);

    const promptText = quoteToSubmit
      ? `Quoted section from playbook/audit under review:\n"${quoteToSubmit}"\n\nQuestion about this quoted section:\n${userMsg}`
      : userMsg;

    const historyPayload = qaHistory.map((msg) => {
      if (msg.role === 'user' && msg.quote) {
        return {
          role: 'user',
          content: `Quoted section from playbook/audit under review:\n"${msg.quote}"\n\nQuestion about this quoted section:\n${msg.content}`
        };
      }
      return { role: msg.role, content: msg.content };
    });

    try {
      const res = await llmApi.qaPlaybook(previewContent, promptText, historyPayload);
      setQaHistory((prev) => [...prev, { role: 'assistant', content: res.answer }]);
    } catch (e: any) {
      Toast.error("Failed to get AI answer", e.message || String(e));
      setQaHistory((prev) => [...prev, { role: 'assistant', content: `❌ Error: ${e.message || 'Failed to communicate with AI review assistant.'}` }]);
    } finally {
      setQaSending(false);
    }
  };

  const handleTextSelection = () => {
    const selection = window.getSelection();
    if (!selection) {
      setPendingQuote('');
      return;
    }
    const text = selection.toString().trim();
    if (text.length > 0) {
      try {
        const range = selection.getRangeAt(0);
        const rect = range.getBoundingClientRect();
        setSelectionCoords({
          top: rect.bottom + 8,
          left: rect.left + (rect.width / 2) - 50,
        });
        setPendingQuote(text);
      } catch (err) {
        setPendingQuote('');
      }
    } else {
      setPendingQuote('');
    }
  };

  const handleApplyQuote = () => {
    setActiveQuote(pendingQuote);
    setPendingQuote('');
    setSelectionCoords(null);
    window.getSelection()?.removeAllRanges();
    setAiTab('chat');
  };

  const openPreview = useCallback(
    async (item: QueueItem) => {
      setPreviewOpen(true);
      setPreviewTitle(item.title);
      setPreviewContent('Loading playbook preview...');
      setPreviewLoading(true);
      setAnalysis(null);
      setAnalysisLoading(false);
      setQaHistory([]);
      setQaInput('');
      setPendingQuote('');
      setActiveQuote('');
      setSelectionCoords(null);
      setAiTab('audit');

      let loadedContent = '';
      try {
        if (item.type === 'ansible_job') {
          let path = (item.entrypoint_path || '').trim();
          if (!path) {
            const result = await ansibleApi.getResult(item.id);
            path = (result.entrypoint_path || '').trim();
          }
          if (!path) {
            loadedContent = 'This job has no entrypoint path recorded yet. Open Ansible Studio from the queue to inspect generated files.';
          } else {
            const file = await ansibleApi.readFile(path);
            loadedContent = file.content || '# Empty playbook';
          }
        } else {
          try {
            const link = await ansibleApi.getJobForRequest(item.request_id);
            let path = (link.entrypoint_path || '').trim();
            if (!path) {
              const result = await ansibleApi.getResult(link.job_id);
              path = (result.entrypoint_path || '').trim();
            }
            if (!path) {
              loadedContent = 'This job has no entrypoint path recorded yet.';
            } else {
              const file = await ansibleApi.readFile(path);
              loadedContent = file.content || '# Empty playbook';
            }
          } catch {
            loadedContent = 'No Ansible playbook job is linked to this change request, so there is nothing to preview. If this was submitted outside Ansible Studio, YAML preview may not be available.';
          }
        }
      } catch (err: any) {
        loadedContent = `Unable to load preview.\n\n${err.message || 'Unknown error'}`;
      } finally {
        setPreviewContent(loadedContent);
        setPreviewLoading(false);
        void triggerAnalysis(loadedContent, item.title);
      }
    },
    [triggerAnalysis]
  );

  const tabItems = items[activeTab];
  const sortedTabItems = useMemo(() => {
    const list = [...tabItems];
    list.sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'id') cmp = a.id - b.id;
      else if (sortKey === 'title') cmp = (a.title || '').localeCompare(b.title || '');
      else if (sortKey === 'requester') cmp = (a.requester || '').localeCompare(b.requester || '');
      else if (sortKey === 'status') cmp = statusDisplayLabel(a.status).localeCompare(statusDisplayLabel(b.status));
      else if (sortKey === 'review') cmp = (a.review_comment || '').localeCompare(b.review_comment || '');
      else cmp = parseCreatedAt(a.created_at) - parseCreatedAt(b.created_at);
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return list;
  }, [tabItems, sortDir, sortKey]);
  const tabLoading = loading[activeTab] && tabItems.length === 0;
  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortKey(key);
    setSortDir('asc');
  };
  const sortIndicator = (key: SortKey) => (sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '');

  return (
    <div className="h-full overflow-auto bg-base-200 p-4 md:p-6 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <ListChecks className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">Approval Queue</h1>
            
            <p className="text-slate-400 text-sm mt-1">
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
                <th><button type="button" className="font-semibold hover:underline" onClick={() => toggleSort('id')}>ID{sortIndicator('id')}</button></th>
                <th><button type="button" className="font-semibold hover:underline" onClick={() => toggleSort('title')}>Title{sortIndicator('title')}</button></th>
                <th><button type="button" className="font-semibold hover:underline" onClick={() => toggleSort('requester')}>Requester{sortIndicator('requester')}</button></th>
                <th><button type="button" className="font-semibold hover:underline" onClick={() => toggleSort('status')}>Status{sortIndicator('status')}</button></th>
                <th><button type="button" className="font-semibold hover:underline" onClick={() => toggleSort('review')}>Review{sortIndicator('review')}</button></th>
                <th><button type="button" className="font-semibold hover:underline" onClick={() => toggleSort('created_at')}>Created{sortIndicator('created_at')}</button></th>
                <th className="w-56">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedTabItems.map((item) => (
                <tr key={`${item.type}-${item.id}`} className="hover">
                  <td className="font-mono text-xs">#{item.id}</td>
                  <td className="max-w-xs truncate font-medium" title={item.title}>{item.title}</td>
                  <td>
                    <div className="flex flex-col">
                      <span className="flex items-center gap-1 text-xs">
                        <User className="w-3 h-3 opacity-50" /> {item.requester}
                      </span>
                      {item.owner_username && (
                        <span className="text-[10px] opacity-60 ml-4">
                          Owner: {item.owner_username}
                        </span>
                      )}
                    </div>
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
                  <td className="text-xs opacity-60">{formatCreatedAt(item.created_at)}</td>
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
          <div className="modal-box max-w-7xl w-11/12 p-5 flex flex-col overflow-hidden max-h-[90vh]" onMouseUp={handleTextSelection}>
            <div className="flex items-center justify-between border-b pb-3 mb-4 shrink-0">
              <div>
                <h3 className="font-bold text-lg text-base-content">Preview: {previewTitle}</h3>
                <p className="text-xs text-base-content/50">Review playbook YAML and consult AI safety insights before approving.</p>
              </div>
              <button className="btn btn-sm btn-circle btn-ghost" onClick={() => setPreviewOpen(false)}>✕</button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 h-[65vh] max-h-[65vh] min-h-0 overflow-hidden flex-1">
              {/* Left: Playbook Preview (YAML) */}
              <div className="lg:col-span-7 flex flex-col h-full min-h-0 border border-base-300 rounded-xl overflow-hidden bg-base-300/30">
                <div className="p-3 bg-base-200 border-b border-base-300 flex items-center justify-between font-mono text-xs shrink-0 select-none">
                  <span className="font-bold text-base-content/70">playbook_entrypoint.yml</span>
                  <span className="badge badge-sm badge-outline">YAML</span>
                </div>
                <div className="flex-1 overflow-auto p-4 custom-scrollbar bg-neutral text-neutral-content">
                  {previewLoading ? (
                    <div className="flex flex-col items-center justify-center h-full gap-2 py-24 opacity-60">
                      <span className="loading loading-spinner loading-md text-primary" />
                      <p className="text-xs">Loading playbook content…</p>
                    </div>
                  ) : (
                    <pre className="text-xs font-mono whitespace-pre-wrap leading-relaxed select-text">{previewContent}</pre>
                  )}
                </div>
              </div>

              {/* Right: AI Assistant (Audit / QA) */}
              <div className="lg:col-span-5 flex flex-col h-full min-h-0 border border-base-300 rounded-xl bg-base-100 overflow-hidden">
                {/* AI Assistant Tabs */}
                <div className="flex border-b border-base-300 bg-base-200/50 shrink-0 select-none">
                  <button
                    type="button"
                    onClick={() => setAiTab('audit')}
                    className={`flex-1 py-3 text-xs font-bold uppercase tracking-wider border-b-2 text-center transition-all ${
                      aiTab === 'audit'
                        ? 'border-primary text-primary bg-primary/5'
                        : 'border-transparent text-base-content/60 hover:text-base-content/90'
                    }`}
                  >
                    AI Summary
                  </button>
                  <button
                    type="button"
                    onClick={() => setAiTab('chat')}
                    className={`flex-1 py-3 text-xs font-bold uppercase tracking-wider border-b-2 text-center transition-all ${
                      aiTab === 'chat'
                        ? 'border-primary text-primary bg-primary/5'
                        : 'border-transparent text-base-content/60 hover:text-base-content/90'
                    }`}
                  >
                    AI Review Q&A
                  </button>
                </div>

                {/* Tab Contents */}
                <div className="flex-1 overflow-y-auto p-4 min-h-0 custom-scrollbar">
                  {analysisLoading ? (
                    <div className="flex flex-col items-center justify-center h-full gap-3 py-16 opacity-75 select-none">
                      <div className="relative w-16 h-16 flex items-center justify-center">
                        <div className="absolute inset-0 rounded-full border-2 border-cyan-500/30 border-t-cyan-500 animate-spin" />
                        <div className="absolute w-10 h-10 rounded-full border-2 border-violet-500/20 border-b-violet-500 animate-spin" style={{ animationDirection: 'reverse' }} />
                        <span className="loading loading-spinner loading-xs text-primary relative z-10" />
                      </div>
                      <p className="text-xs font-semibold text-base-content/70">AI is auditing & rating playbook…</p>
                    </div>
                  ) : aiTab === 'audit' ? (
                    /* AUDIT TAB */
                    <div className="space-y-4">
                      {/* Safety Rating Section */}
                      {analysis ? (
                        <>
                          <div className="p-4 rounded-xl border border-base-300 bg-base-200/40 flex items-center justify-between gap-4 select-none">
                            <div>
                              <span className="text-[10px] uppercase font-bold tracking-wider text-base-content/40">Playbook Reference Rating</span>
                              <div className="flex items-baseline gap-1 mt-0.5">
                                <span className="text-3xl font-extrabold tracking-tight text-base-content">{analysis.rating}</span>
                                <span className="text-xs opacity-50 font-medium">/ 10</span>
                              </div>
                            </div>
                            <div className={`badge py-3 px-4 font-bold border-none text-black/75 shadow-sm text-xs ${
                              analysis.safety_level === 'Safe' ? 'bg-success' : analysis.safety_level === 'Danger' ? 'bg-error' : 'bg-warning'
                            }`}>
                              {analysis.safety_level === 'Safe' ? '🟢 Safe' : analysis.safety_level === 'Danger' ? '🔴 Danger' : '🟡 Warning'}
                            </div>
                          </div>

                          {/* Playbook Operational Impact Rating */}
                          <div className="p-4 rounded-xl border border-base-300 bg-base-200/40 flex items-center justify-between gap-4 select-none mt-2">
                            <div>
                              <span className="text-[10px] uppercase font-bold tracking-wider text-base-content/40">Playbook Operational Rating</span>
                              <div className="flex items-baseline gap-1 mt-0.5">
                                <span className="text-3xl font-extrabold tracking-tight text-base-content">{analysis.operational_rating}</span>
                                <span className="text-xs opacity-50 font-medium">/ 10</span>
                              </div>
                            </div>
                            <div className={`badge py-3 px-4 font-bold border-none text-black/75 shadow-sm text-xs ${
                              analysis.operational_level === 'Normal' ? 'bg-success' : analysis.operational_level === 'Warning' ? 'bg-error' : 'bg-warning'
                            }`}>
                              {analysis.operational_level === 'Normal' ? '🟢 Normal' : analysis.operational_level === 'Warning' ? '🔴 Warning' : '🟡 Alert'}
                            </div>
                          </div>

                          {/* Explanation */}
                          <div className="space-y-1.5">
                            <h4 className="text-[10px] font-bold uppercase tracking-wider text-base-content/45 select-none">Playbook Explanation</h4>
                            <div className="text-xs leading-relaxed text-base-content/85 p-3 rounded-lg border bg-base-200/20 font-sans select-text">
                              <MarkdownText>{analysis.explanation}</MarkdownText>
                            </div>
                          </div>

                          {/* Operational Impact Explanation */}
                          <div className="space-y-1.5">
                            <h4 className="text-[10px] font-bold uppercase tracking-wider text-base-content/45 select-none">Operational Impact Details</h4>
                            <div className="text-xs leading-relaxed text-base-content/85 p-3 rounded-lg border bg-base-200/20 font-sans select-text">
                              <MarkdownText>{analysis.operational_explanation}</MarkdownText>
                            </div>
                          </div>

                          {/* Findings Checklist */}
                          <div className="space-y-2">
                            <h4 className="text-[10px] font-bold uppercase tracking-wider text-base-content/45 select-none">Overall Summary Findings</h4>
                            <div className="flex flex-col gap-2 select-text">
                              {analysis.findings.map((finding, idx) => {
                                let cleanFinding = finding;
                                let isWarning = false;

                                if (finding.startsWith('[⚠️ CRITICAL WARNING]')) {
                                  isWarning = true;
                                  cleanFinding = finding.replace('[⚠️ CRITICAL WARNING]', '').trim();
                                } else if (finding.startsWith('[✓]')) {
                                  cleanFinding = finding.replace('[✓]', '').trim();
                                } else {
                                  // Fallback keyword-based check
                                  const lower = finding.toLowerCase();
                                  const triggerKeywords = ['critical warning', 'danger', 'lockout', 'contradiction', 'vulnerability'];
                                  isWarning = triggerKeywords.some(w => lower.includes(w));
                                }

                                const indicator = isWarning ? (
                                  <span className="text-error font-bold shrink-0 mt-0.5">⚠</span>
                                ) : (
                                  <span className="text-success font-bold shrink-0 mt-0.5">✓</span>
                                );

                                return (
                                  <div key={idx} className="flex items-start gap-2.5 text-xs text-base-content/85 p-2.5 rounded-lg border border-base-200 bg-base-200/10">
                                    {indicator}
                                    <span className="leading-normal">{cleanFinding}</span>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="text-center py-16 opacity-50 italic text-xs select-none">
                          No playbook content loaded to audit. Ensure a valid Ansible playbook job is previewed.
                        </div>
                      )}
                    </div>
                  ) : (
                    /* Q&A CHAT TAB */
                    <div className="flex flex-col h-full min-h-0">
                      {/* Messages history */}
                      <div className="flex-1 space-y-3 overflow-y-auto mb-3 pr-1 text-xs custom-scrollbar">
                        {qaHistory.length === 0 ? (
                          <div className="text-center py-12 text-base-content/50 space-y-2 select-none">
                            <p className="italic">No questions asked yet.</p>
                            <p className="text-[11px] max-w-xs mx-auto leading-relaxed">
                              Ask the AI Review Assistant questions like "What configurations will change?", "Are there credentials in plain text?", or "Is rollback supported?"
                            </p>
                          </div>
                        ) : (
                          qaHistory.map((msg, idx) => (
                            <div key={idx} className={`chat ${msg.role === 'user' ? 'chat-end' : 'chat-start'}`}>
                              <div className={`chat-bubble py-2 px-3  leading-normal text-xs font-sans ${msg.role === 'user' ? 'chat-bubble-primary text-primary-content' : 'chat-bubble-neutral text-neutral-content'}`}>
                                {msg.quote && (
                                  <div className="border-l-2 border-current/35 pl-2 mb-1.5 italic opacity-85 text-[10px] bg-black/15 p-1 rounded-r max-w-xs truncate">
                                    "{msg.quote}"
                                  </div>
                                )}
                                <div className="whitespace-pre-wrap">{msg.content}</div>
                              </div>
                            </div>
                          ))
                        )}
                        {qaSending && (
                          <div className="chat chat-start">
                            <div className="chat-bubble py-2 px-3 rounded-xl bg-neutral/50 text-neutral-content text-xs flex items-center gap-1.5">
                              <span className="loading loading-dots loading-xs" />
                              Thinking…
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Active Quote preview */}
                      {activeQuote && (
                        <div className="bg-base-200 text-base-content border border-base-300 rounded-xl p-2 px-3 mb-2 flex items-center justify-between gap-3 text-[11px] shrink-0">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="bg-neutral text-neutral-content rounded-md w-5 h-5 flex items-center justify-center text-[10px] font-bold shrink-0">❝</span>
                            <span className="truncate italic opacity-95">
                              {activeQuote}
                            </span>
                          </div>
                          <button
                            type="button"
                            className="btn btn-xs btn-circle btn-ghost shrink-0"
                            onClick={() => setActiveQuote('')}
                          >
                            ✕
                          </button>
                        </div>
                      )}

                      {/* Input box */}
                      <div className="mt-auto border-t pt-3 flex gap-2 shrink-0 select-none">
                        <input
                          type="text"
                          className="input input-sm input-bordered flex-1 text-xs focus:border-primary/50"
                          placeholder="Ask about this playbook…"
                          value={qaInput}
                          onChange={(e) => setQaInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void handleSendQa();
                          }}
                          disabled={qaSending || !previewContent}
                        />
                        <button
                          type="button"
                          className="btn btn-sm btn-primary text-xs"
                          onClick={handleSendQa}
                          disabled={qaSending || !qaInput.trim() || !previewContent}
                        >
                          Send
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
            
            <div className="modal-action shrink-0 border-t pt-3 mt-4 flex justify-between select-none">
              <span className="text-[10px] uppercase font-bold tracking-wider text-base-content/40 flex items-center">
                AI Rating Reference Only — Reviews are Subject to Manual Approval Policy
              </span>
              <button className="btn btn-sm btn-outline" onClick={() => setPreviewOpen(false)}>Close Preview</button>
            </div>

            {pendingQuote && selectionCoords && (
  <>
    <style>{`
      @keyframes spin {
        0% { transform: translate(-50%, -50%) rotate(0deg); }
        100% { transform: translate(-50%, -50%) rotate(360deg); }
      }
      .rainbow-glow-wrapper {
        position: fixed;
        z-index: 9999;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
        padding: 2.5px; /* Slightly thicker for better visibility */
        background: transparent;
      }
      /* This layer creates the seamless, super bright spinning rainbow */
      .rainbow-glow-wrapper::before {
        content: '';
        position: absolute;
        top: 50%;
        left: 50%;
        width: 300%; /* Extra wide to guarantee 100% corner coverage */
        height: 300%;
        border-radius: 50%; /* Perfect circle prevents black edge cutoffs */
        background: conic-gradient(
          #ff0055, #00ffcc, #9900ff, #ffcc00, #ff0055
        );
        /* ADJUST DIMNESS HERE: 0.0 is completely invisible, 1.0 is max brightness */
        opacity: 0.50;
        animation: spin 15s linear infinite;
      }
      .rainbow-btn-inner {
        position: relative;
        z-index: 10;
        width: 100%;
        height: 100%;
      }
    `}</style>

    <div 
      className="rainbow-glow-wrapper rounded-xl shadow-[0_0_15px_rgba(0,255,204,0.15)]"
      style={{
        top: `${selectionCoords.top - 100}px`,
        left: `${selectionCoords.left - 100}px`,
      }}
    >
      <button
        type="button"
        className="rainbow-btn-inner btn btn-xs btn-neutral gap-1 rounded-lg px-2.5 py-2.5 text-[12.5px] font-sans hover:bg-neutral-focus border-0"
        onClick={handleApplyQuote}
      >
        <span className="text-primary font-bold">❝</span> Ask about
      </button>
    </div>
  </>
)}
          </div>
          <div className="modal-backdrop bg-black/60" onClick={() => setPreviewOpen(false)} />
        </dialog>
      )}
    </div>
  );
};

export default ApprovalQueueView;
