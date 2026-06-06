import React, { useEffect, useRef, useState, useCallback } from 'react';
import { CheckCircle2, Circle, Loader2, AlertCircle, History, ScrollText, Server, Terminal, Play, ChevronDown, Trash2, PanelRightOpen, X, Plus } from 'lucide-react';
import { deployApi, ansibleApi } from '../../src/services/api';
import SSHTerminal from '../SSHTerminal';
import type { SSHTab } from '../SSHTerminalPanel';
import type { SSHConnectionState } from '../SSHTerminal';
import type { AnsibleJob, AnsibleOutputSession } from '../../typesLLM';

interface JobStatusBarProps {
  job: AnsibleJob | null;
  sseLines: string[];
  isStreaming: boolean;
  outputSessions?: AnsibleOutputSession[];
  activeOutputId?: string | null;
  onSelectOutputSession?: (id: string | null) => void;
  onDeleteOutputSession?: (id: string) => void;
  splitOutputId?: string | null;
  onSetSplitOutputId?: (id: string | null) => void;
  streamingSessionId?: string | null;

  // SSH Terminal tab (used by AnsibleStudioView)
  sshTabs?: SSHTab[];
  activeSshTabId?: string | null;
  sshTabStates?: Record<string, SSHConnectionState>;
  onSelectSshTab?: (sessionId: string) => void;
  onCloseSshTab?: (sessionId: string) => void | Promise<void>;
  onOpenSshModal?: (hostname: string, host: string) => void;
  onConnectionChangeSsh?: (sessionId: string, state: SSHConnectionState) => void;
}

type BottomTab = 'output' | 'history' | 'audit' | 'inventory' | 'ssh';

const STAGES = [
  { key: 'plan',     label: 'Plan' },
  { key: 'approve',  label: 'Approve' },
  { key: 'apply',    label: 'Apply' },
  { key: 'commit',   label: 'Commit' },
];

const STATUS_COLOUR: Record<string, string> = {
  success:     'text-success',
  failed:      'text-error',
  rolled_back: 'text-warning',
  running:     'text-primary',
  approved:    'text-info',
  pending:     'text-base-content opacity-50',
};

const JobStatusBar: React.FC<JobStatusBarProps> = ({
  job,
  sseLines,
  isStreaming,
  outputSessions = [],
  activeOutputId = null,
  onSelectOutputSession,
  onDeleteOutputSession,
  splitOutputId = null,
  onSetSplitOutputId,
  streamingSessionId = null,

  // SSH Terminal
  sshTabs = [],
  activeSshTabId = null,
  sshTabStates = {},
  onSelectSshTab,
  onCloseSshTab,
  onOpenSshModal,
  onConnectionChangeSsh,
}) => {
  const logRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<BottomTab>('output');
  const [outputPanelCollapsed, setOutputPanelCollapsed] = useState(false);
  const [jobs, setJobs] = useState<any[]>([]);
  const [audit, setAudit] = useState<any[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [loadingAudit, setLoadingAudit] = useState(false);
  type InventoryData = {
    hosts: {
      hostname: string;
      ansible_host: string;
      vendor: string;
      role: string;
      credentials_source: string;
      ssh_user?: string;
      ssh_password?: string;
    }[];
    credentials_note: string;
  };
  const [inventoryCache, setInventoryCache] = useState<Record<number, InventoryData>>({});
  const [loadingInventory, setLoadingInventory] = useState(false);
  const inventory = job?.jobId != null ? inventoryCache[job.jobId] ?? null : null;

  // Remember which session was streaming so we can force the UI back to "Output"
  // once deploy completes for easy operator review.
  const lastStreamingSessionRef = useRef<string | null>(null);
  useEffect(() => {
    if (isStreaming && streamingSessionId) {
      lastStreamingSessionRef.current = streamingSessionId;
    }
  }, [isStreaming, streamingSessionId]);
  useEffect(() => {
    if (!isStreaming && lastStreamingSessionRef.current) {
      setTab('output');
      lastStreamingSessionRef.current = null;
    }
  }, [isStreaming]);

  const STATE_DOT: Record<SSHConnectionState, string> = {
    connecting: 'bg-warning animate-pulse',
    connected: 'bg-success',
    disconnected: 'bg-base-content/40',
    error: 'bg-error',
  };

  const activeSession = outputSessions.find(s => s.id === activeOutputId) ?? outputSessions[0];

  const prevActiveSshTabRef = useRef<string | null>(null);
  useEffect(() => {
    if (activeSshTabId && activeSshTabId !== prevActiveSshTabRef.current) {
      setTab('ssh');
    }
    prevActiveSshTabRef.current = activeSshTabId;
  }, [activeSshTabId]);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [sseLines, activeSession?.lines.length]);

  const fetchJobs = useCallback(async () => {
    setLoadingJobs(true);
    try {
      const res = await deployApi.listJobs();
      setJobs(Array.isArray(res) ? res : []);
    } catch { setJobs([]); }
    finally { setLoadingJobs(false); }
  }, []);

  const fetchAudit = useCallback(async () => {
    setLoadingAudit(true);
    try {
      const res = await deployApi.getAuditLog(30);
      setAudit(res.entries ?? []);
    } catch { setAudit([]); }
    finally { setLoadingAudit(false); }
  }, []);

  const fetchInventory = useCallback(async (jobId: number) => {
    setLoadingInventory(true);
    try {
      const res = await ansibleApi.getJobInventory(jobId);
      setInventoryCache(prev => ({ ...prev, [jobId]: { hosts: res.hosts ?? [], credentials_note: res.credentials_note ?? '' } }));
    } catch {
      setInventoryCache(prev => ({ ...prev, [jobId]: { hosts: [], credentials_note: 'Failed to load.' } }));
    }
    finally { setLoadingInventory(false); }
  }, []);

  // Prefetch inventory as soon as we have a job (so Inventory tab is instant when clicked)
  useEffect(() => {
    if (job?.jobId == null) return;
    if (inventoryCache[job.jobId]) return;
    fetchInventory(job.jobId);
  }, [job?.jobId, inventoryCache, fetchInventory]);

  // Load data when switching to a tab (history/audit only; inventory is prefetched above)
  useEffect(() => {
    if (tab === 'history') fetchJobs();
    if (tab === 'audit') fetchAudit();
  }, [tab, fetchJobs, fetchAudit]);

  let currentStageIdx = job ? STAGES.findIndex(s => s.key === job.stage) : -1;
  const jobStatus = job?.status;
  if (jobStatus === 'approved' && currentStageIdx === 1) {
    currentStageIdx = 2; // Move current stage to 'apply' since approval is done
  }
  const stageStatusMap: Record<string, string> = {};
  for (const st of job?.steps || []) {
    const key = String(st.stage || '').trim().toLowerCase();
    if (key) stageStatusMap[key] = String(st.status || '').trim().toLowerCase();
  }
  const stageDone = (key: string, idx: number): boolean => {
    const st = stageStatusMap[key];
    if (st === 'success' || st === 'approved' || st === 'completed' || st === 'done') return true;
    if (jobStatus === 'success') return true;
    return Boolean(job && idx < currentStageIdx);
  };
  const stageCurrent = (key: string, idx: number): boolean => {
    const st = stageStatusMap[key];
    if (st === 'running' || st === 'in_progress') return true;
    return Boolean(job && idx === currentStageIdx && jobStatus !== 'success');
  };
  const stageFailed = (key: string, idx: number): boolean => {
    const st = stageStatusMap[key];
    if (st === 'failed' || st === 'error') return true;
    return Boolean(job && jobStatus === 'failed' && idx === currentStageIdx);
  };

  const tabBtn = (id: BottomTab, icon: React.ReactNode, label: string) => (
    <button
      className={`flex items-center gap-1 text-[11px] font-medium px-3 py-1.5 border-b-2 transition-colors ${
        tab === id
          ? 'border-primary text-primary'
          : 'border-transparent opacity-60 hover:opacity-100'
      }`}
      onClick={() => setTab(id)}
    >
      {icon}{label}
    </button>
  );

  return (
    <div className="flex-1 min-h-0 flex flex-col border-t border-base-300 bg-base-100">
      {/* Pipeline step indicator */}
      <div className="flex items-center gap-0 px-4 py-2 border-b border-base-200">
        {STAGES.map((s, idx) => {
          const done    = stageDone(s.key, idx);
          const current = stageCurrent(s.key, idx);
          const failed  = stageFailed(s.key, idx);
          const inactive = !job;
          
          return (
            <React.Fragment key={s.key}>
              <div className={`flex items-center gap-1 text-xs font-medium px-2 py-1 rounded transition-all duration-300 ${
                failed   ? 'text-error' :
                current  ? 'text-primary' :
                done     ? 'text-success' :
                inactive ? 'opacity-20' :
                'opacity-30'
              }`}>
                {failed  ? <AlertCircle className="h-3.5 w-3.5" /> :
                 current && isStreaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> :
                 done    ? <CheckCircle2 className="h-3.5 w-3.5" /> :
                 <Circle className="h-3.5 w-3.5" />}
                {s.label}
              </div>
              {idx < STAGES.length - 1 && (
                <div className={`h-px w-5 transition-colors duration-500 ${done ? 'bg-success' : 'bg-base-300'} ${inactive ? 'opacity-20' : ''}`} />
              )}
            </React.Fragment>
          );
        })}
        {jobStatus ? (
          <span className={`ml-auto text-xs font-bold ${STATUS_COLOUR[jobStatus] ?? ''}`}>
            {jobStatus.toUpperCase()}
          </span>
        ) : (
          <span className="ml-auto text-xs font-medium opacity-20 italic">
            IDLE
          </span>
        )}
      </div>

      {/* Bottom tabs */}
      <div className="flex border-b border-base-200 bg-base-100">
        {tabBtn('output', <Loader2 className="h-3 w-3" />, 'Output')}
        {tabBtn('inventory', <Server className="h-3 w-3" />, 'Inventory')}
        {tabBtn('history', <History className="h-3 w-3" />, 'History')}
        {tabBtn('audit', <ScrollText className="h-3 w-3" />, 'Audit')}
        {tabBtn('ssh', <Terminal className="h-3 w-3" />, 'SSH Terminal')}
      </div>

      {/* Tab content — Output: log content (left) + session list side panel (right) */}
      {tab === 'output' && (
        <div className="flex flex-1 min-h-0 bg-base-200">
          {/* Output content: single pane or split (left + right) */}
          <div className={`flex flex-1 min-w-0 ${splitOutputId ? 'flex gap-0 border-r border-base-300' : 'flex flex-col'}`}>
            {outputSessions.length === 0 ? (
              isStreaming ? (
                <div className="flex items-center gap-2 px-4 py-4 text-xs opacity-70">
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                  <span>Starting terminal output stream. Logs will appear here shortly…</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 px-4 py-4 text-xs opacity-50">
                  <Terminal className="h-4 w-4 shrink-0" />
                  <span>No output yet. Run <strong>Check</strong> or <strong>Deploy</strong> to see logs here.</span>
                </div>
              )
            ) : (
              <>
                {/* Left pane (primary) */}
                <div className={`flex flex-col min-h-0 ${splitOutputId ? 'flex-1 min-w-0' : 'flex-1'}`}>
                  <div
                    ref={logRef}
                    className="flex-1 overflow-y-auto overflow-x-auto px-4 py-2 font-mono text-xs leading-5 min-h-0 select-text"
                    style={{ userSelect: 'text' }}
                  >
                    {(() => {
                      const session = outputSessions.find(s => s.id === activeOutputId) ?? outputSessions[0];
                      if (!session) return null;
                      const isSessionStreaming = streamingSessionId === session.id && isStreaming;
                      const hasLines = session.lines.length > 0;
                      return (
                        <>
                          {!hasLines && isSessionStreaming && (
                            <div className="flex items-center gap-2 opacity-70 mb-2">
                              <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                              <span>Loading terminal output…</span>
                            </div>
                          )}
                          {!hasLines && !isSessionStreaming && (
                            <div className="text-xs opacity-50 italic">No output captured for this run.</div>
                          )}
                          {session.lines.map((line, i) => (
                            <div key={i} className="whitespace-pre-wrap break-all opacity-90">{line || '\u00A0'}</div>
                          ))}
                          {isSessionStreaming && hasLines && (
                            <div className="flex items-center gap-1 opacity-60 mt-1">
                              <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                              <span>Running…</span>
                            </div>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>
                {/* Right pane (split) */}
                {splitOutputId && (() => {
                  const splitSession = outputSessions.find(s => s.id === splitOutputId);
                  if (!splitSession) return null;
                  return (
                    <div className="flex-1 min-w-0 flex flex-col border-l border-base-300">
                      <div className="flex items-center justify-between px-2 py-1 border-b border-base-300 bg-base-100 text-[10px] font-semibold">
                        <span className="truncate opacity-80">{splitSession.label}</span>
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs p-0 min-h-0 h-5"
                          onClick={() => onSetSplitOutputId?.(null)}
                          title="Close split"
                          aria-label="Close split"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                      <div
                        className="flex-1 overflow-y-auto overflow-x-auto px-4 py-2 font-mono text-xs leading-5 min-h-0 select-text"
                        style={{ userSelect: 'text' }}
                      >
                        {splitSession.lines.length === 0 && streamingSessionId === splitSession.id && isStreaming && (
                          <div className="flex items-center gap-2 opacity-70 mb-2">
                            <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                            <span>Loading terminal output…</span>
                          </div>
                        )}
                        {splitSession.lines.length === 0 && !(streamingSessionId === splitSession.id && isStreaming) && (
                          <div className="text-xs opacity-50 italic">No output captured for this run.</div>
                        )}
                        {splitSession.lines.map((line, i) => (
                          <div key={i} className="whitespace-pre-wrap break-all opacity-90">{line || '\u00A0'}</div>
                        ))}
                        {streamingSessionId === splitSession.id && isStreaming && splitSession.lines.length > 0 && (
                          <div className="flex items-center gap-1 opacity-60 mt-1">
                            <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                            <span>Running…</span>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })()}
              </>
            )}
          </div>
          {/* Side panel (right): output session list */}
          {outputSessions.length > 0 && (
            <div
              className={`shrink-0 border-l border-base-300 bg-base-100 flex flex-col ${outputPanelCollapsed ? 'w-8' : 'w-40'}`}
              title={outputPanelCollapsed ? 'Show output list' : 'Hide output list'}
            >
              {!outputPanelCollapsed && (
                <>
                  <div className="flex items-center justify-between px-2 py-1 border-b border-base-300 text-[10px] font-semibold uppercase tracking-wider opacity-70">
                    <span>Logs</span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-xs p-0 min-h-0 h-5"
                      onClick={() => setOutputPanelCollapsed(true)}
                      aria-label="Collapse"
                    >
                      <ChevronDown className="h-3 w-3 rotate-90" />
                    </button>
                  </div>
                  <div className="overflow-y-auto flex-1 min-h-0 py-0.5">
                    {outputSessions.map((s) => (
                      <div
                        key={s.id}
                        className={`group flex items-center gap-0.5 w-full text-left px-2 py-1.5 text-[11px] font-mono border-r-2 transition-colors ${
                          activeOutputId === s.id
                            ? 'border-primary bg-primary/10 text-primary'
                            : splitOutputId === s.id
                              ? 'border-info bg-info/10 text-info'
                              : 'border-transparent hover:bg-base-200 opacity-80'
                        }`}
                      >
                        <button
                          type="button"
                          className="flex-1 min-w-0 flex items-center gap-1 truncate"
                          onClick={() => onSelectOutputSession?.(s.id)}
                        >
                          {streamingSessionId === s.id && (
                            <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
                          )}
                          {s.type === 'check' && <Terminal className="h-3 w-3 shrink-0 opacity-60" />}
                          {s.type === 'deploy' && <Play className="h-3 w-3 shrink-0 opacity-60" />}
                          <span className="truncate">{s.label}</span>
                        </button>
                        <div className="flex items-center shrink-0 opacity-50 group-hover:opacity-100">
                          <button
                            type="button"
                            className="p-0.5 hover:bg-base-300 rounded"
                            onClick={(e) => { e.stopPropagation(); onSetSplitOutputId?.(splitOutputId === s.id ? null : s.id); }}
                            title={splitOutputId === s.id ? 'Close split' : 'Split (show in second pane)'}
                            aria-label={splitOutputId === s.id ? 'Close split' : 'Split'}
                          >
                            <PanelRightOpen className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            className="p-0.5 hover:bg-error/20 hover:text-error rounded"
                            onClick={(e) => { e.stopPropagation(); onDeleteOutputSession?.(s.id); }}
                            title="Delete log"
                            aria-label="Delete"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
              {outputPanelCollapsed && (
                <button
                  type="button"
                  className="flex flex-col items-center justify-center py-2 w-full hover:bg-base-200"
                  onClick={() => setOutputPanelCollapsed(false)}
                  aria-label="Expand output list"
                >
                  <Terminal className="h-4 w-4 opacity-60" />
                  <span className="text-[9px] mt-0.5">Logs</span>
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {tab === 'history' && (
        <div className="flex-1 min-h-0 overflow-y-auto bg-base-200">
          {loadingJobs ? (
            <div className="flex items-center justify-center py-4 opacity-50 text-xs"><Loader2 className="h-3 w-3 animate-spin mr-1" /> Loading…</div>
          ) : jobs.length === 0 ? (
            <div className="text-xs text-center py-4 opacity-40">No deployment jobs yet</div>
          ) : (
            <table className="table table-xs table-pin-rows w-full">
              <thead><tr><th>ID</th><th>Status</th><th>Stage</th><th>Device</th><th>Created</th></tr></thead>
              <tbody>
                {jobs.slice(0, 20).map((j: any) => (
                  <tr key={j.id ?? j.job_id} className="hover">
                    <td className="font-mono text-[11px]">#{j.id ?? j.job_id}</td>
                    <td>
                      <span className={`badge badge-xs ${j.status === 'success' ? 'badge-success' : j.status === 'failed' ? 'badge-error' : j.status === 'running' ? 'badge-warning' : 'badge-ghost'}`}>
                        {j.status}
                      </span>
                    </td>
                    <td className="text-[11px] opacity-70">{j.stage ?? '—'}</td>
                    <td className="text-[11px] opacity-70">{j.device_id ?? '—'}</td>
                    <td className="text-[11px] opacity-50">{j.created_at ? new Date(j.created_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'inventory' && (
        <div className="flex-1 min-h-0 overflow-y-auto bg-base-200">
          {loadingInventory ? (
            <div className="flex items-center justify-center py-4 opacity-50 text-xs"><Loader2 className="h-3 w-3 animate-spin mr-1" /> Loading…</div>
          ) : !inventory ? (
            <div className="text-xs text-center py-4 opacity-40">No inventory data</div>
          ) : (
            <div className="p-3 space-y-2 select-text" style={{ userSelect: 'text' }}>
              {/* Credentials info hidden temporarily
              <div className="rounded border border-base-300 bg-base-100/80 px-3 py-2 space-y-1">
                <div className="flex items-center gap-1.5 text-xs font-medium opacity-90">
                  <Key className="h-3.5 w-3.5" />
                  Credentials
                </div>
                <p className="text-[11px] opacity-85 leading-relaxed">{inventory.credentials_note}</p>
                <p className="text-[11px] opacity-75 leading-relaxed mt-1">
                  To view or edit SSH username/password: use <code className="px-1 py-0.5 rounded bg-base-300 text-[10px]">ansible-vault edit backend/ansible/vault/net_creds.yml</code> (vault password required). Per-vendor vars: <code className="px-1 py-0.5 rounded bg-base-300 text-[10px]">vault_cisco_ios_user</code>, <code className="px-1 py-0.5 rounded bg-base-300 text-[10px]">vault_juniper_junos_user</code>, etc.
                </p>
              </div>
              */}
              {inventory.hosts.length === 0 ? (
                <div className="text-xs opacity-40">No hosts in this job</div>
              ) : (
                <table className="table table-xs w-full">
                  <thead>
                    <tr>
                      <th>Host</th><th>IP</th><th>Vendor</th><th>Role</th>
                      {/* Credentials column hidden temporarily <th>Credentials</th> */}
                      {inventory.hosts.some((h) => h.ssh_user != null || h.ssh_password != null || h.ssh_user === '' || h.ssh_password === '') && (
                        <>
                          <th>SSH User</th><th>SSH Password</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {inventory.hosts.map((h) => (
                      <tr key={h.hostname} className="hover">
                        <td className="font-mono text-[11px]">{h.hostname}</td>
                        <td className="font-mono text-[11px]">{h.ansible_host}</td>
                        <td className="text-[11px] opacity-80">{h.vendor || '—'}</td>
                        <td className="text-[11px] opacity-80">{h.role || '—'}</td>
                        {/* <td className="text-[11px] opacity-60">{h.credentials_source}</td> */}
                        {inventory.hosts.some((x) => x.ssh_user != null || x.ssh_password != null || x.ssh_user === '' || x.ssh_password === '') && (
                          <>
                            <td className="font-mono text-[11px]">{h.ssh_user ?? '—'}</td>
                            <td className="font-mono text-[11px]">{h.ssh_password ?? '—'}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      )}

      {tab === 'audit' && (
        <div className="flex-1 min-h-0 overflow-y-auto bg-base-200">
          {loadingAudit ? (
            <div className="flex items-center justify-center py-4 opacity-50 text-xs"><Loader2 className="h-3 w-3 animate-spin mr-1" /> Loading…</div>
          ) : audit.length === 0 ? (
            <div className="text-xs text-center py-4 opacity-40">No audit entries</div>
          ) : (
            <div className="space-y-0">
              {audit.slice(0, 25).map((e: any, i: number) => (
                <div key={e.id ?? i} className="flex items-start gap-2 px-4 py-1.5 text-xs hover:bg-base-300 border-b border-base-300/50">
                  <ScrollText className="h-3 w-3 mt-0.5 opacity-40 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{e.action ?? e.event ?? '—'}</span>
                    {e.target && <span className="opacity-60 ml-1">→ {e.target}</span>}
                    {e.details && <p className="opacity-50 truncate">{e.details}</p>}
                  </div>
                  <span className="opacity-40 shrink-0 text-[10px]">{e.created_at ?? e.timestamp ?? ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* SSH Terminal tab (kept mounted; hidden when not active) */}
      <div
        className="flex-1 min-h-0 bg-base-200 overflow-hidden"
        style={{ display: tab === 'ssh' ? 'flex' : 'none' }}
      >
        {sshTabs.length === 0 ? (
          <div className="flex-1 flex items-center justify-center px-4">
            <div className="text-xs opacity-60 text-center space-y-3">
              <div className="flex items-center justify-center gap-2">
                <Terminal className="h-4 w-4 shrink-0 opacity-80" />
                <span>No SSH terminal sessions yet</span>
              </div>
              <button
                type="button"
                className="btn btn-xs btn-ghost outline-1 hover:btn-primary rounded-full"
                onClick={() => onOpenSshModal?.('', '')}
              >
                <Plus className="h-3.5 w-3.5" /> New terminal
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Left: terminal content */}
            <div className="flex-1 min-w-0 relative">
              {sshTabs.map((t) => {
                const visible = (activeSshTabId ?? sshTabs[0]?.sessionId) === t.sessionId;
                return (
                  <div
                    key={t.sessionId}
                    className="absolute inset-0"
                    style={{ display: visible ? 'block' : 'none' }}
                  >
                    <SSHTerminal
                      wsUrl={t.wsUrl}
                      isActive={tab === 'ssh' && visible}
                      onConnectionChange={(state) => onConnectionChangeSsh?.(t.sessionId, state)}
                    />
                  </div>
                );
              })}
            </div>

            {/* Right: session list */}
            <div className="shrink-0 border-l border-base-300 bg-base-100 w-44 flex flex-col">
              <div className="flex items-center justify-between px-2 py-1 border-b border-base-300 bg-base-100 text-[10px] font-semibold uppercase tracking-wider opacity-70">
                <span>Devices</span>
                <button
                  type="button"
                  className="btn btn-ghost btn-xs p-0 min-h-0 h-5"
                  onClick={() => onOpenSshModal?.('', '')}
                  title="Open new SSH terminal"
                >
                  <Plus className="h-3 w-3" />
                </button>
              </div>
              <div className="overflow-y-auto flex-1 min-h-0 py-0.5">
                {sshTabs.map((s) => (
                  <div
                    key={s.sessionId}
                    className={`group flex items-center gap-0.5 w-full text-left px-2 py-1.5 text-[11px] font-mono border-r-2 transition-colors ${
                      (activeSshTabId ?? sshTabs[0]?.sessionId) === s.sessionId
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-transparent hover:bg-base-200 opacity-80'
                    }`}
                  >
                    <button
                      type="button"
                      className="flex-1 min-w-0 flex items-center gap-1 truncate"
                      onClick={() => onSelectSshTab?.(s.sessionId)}
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                          sshTabStates[s.sessionId] ? STATE_DOT[sshTabStates[s.sessionId]] : STATE_DOT.connecting
                        }`}
                      />
                      <span className="truncate">{s.hostname}</span>
                    </button>
                    <button
                      type="button"
                      className="p-0.5 hover:bg-error/20 hover:text-error rounded"
                      onClick={(e) => {
                        e.stopPropagation();
                        onCloseSshTab?.(s.sessionId);
                      }}
                      title="Close SSH session"
                      aria-label="Close"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default JobStatusBar;
