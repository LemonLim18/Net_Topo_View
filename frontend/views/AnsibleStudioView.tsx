import React, { useState, useCallback, useEffect, useRef } from 'react';
import AnsibleFileTree                              from '../components/ansible/AnsibleFileTree';
import PlaybookEditor                               from '../components/ansible/PlaybookEditor';
import JobStatusBar                                 from '../components/ansible/JobStatusBar';
import AnsibleChatPanel                             from '../components/ansible/AnsibleChatPanel';
import IntentParseWorkbench                         from '../components/ansible/IntentParseWorkbench';
import SSHConnectModal                             from '../components/SSHConnectModal';
import type { SSHTab }                             from '../components/SSHTerminalPanel';
import { ansibleApi, configApi, sshApi }          from '../src/services/api';
import type {
  AnsibleTab, AnsibleJob, AnsibleGeneratedArtifactFile,
  AnsibleOutputSession,
} from '../typesLLM';
import type { SSHConnectionState } from '../components/SSHTerminal';
import type { User }               from '../types';

const ANSIBLE_TABS_KEY = 'ansible.tabs';
const ANSIBLE_ACTIVE_TAB_KEY = 'ansible.activeTabId';
const ANSIBLE_STATUS_BAR_HEIGHT_KEY = 'ansible.statusBarHeight';
const STATUS_BAR_HEIGHT_MIN = 100;
const STATUS_BAR_HEIGHT_MAX = 600;
const STATUS_BAR_HEIGHT_DEFAULT = 220;
const ANSIBLE_FILE_TREE_WIDTH_KEY = 'ansible.fileTreeWidth';
const ANSIBLE_CHAT_PANEL_WIDTH_KEY = 'ansible.chatPanelWidth';
const FILE_TREE_MIN = 160;
const FILE_TREE_MAX = 480;
const FILE_TREE_DEFAULT = 200;
const CHAT_PANEL_MIN = 280;
const CHAT_PANEL_MAX = 560;
const CHAT_PANEL_DEFAULT = 320;

interface AnsibleStudioViewProps {
  user: User | null;
}

/** Vertical resize handle between panels; drag to change the width of the panel on the left of the handle. */
function ResizeHandle({
  onResize,
  onDragStart,
  onDragEnd,
}: {
  onResize: (deltaX: number) => void;
  onDragStart?: () => void;
  onDragEnd?: () => void;
}) {
  const startRef = useRef<{ x: number } | null>(null);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      startRef.current = { x: e.clientX };
      onDragStart?.();
      const onMouseMove = (move: MouseEvent) => {
        if (startRef.current === null) return;
        const deltaX = move.clientX - startRef.current.x;
        startRef.current.x = move.clientX;
        onResize(deltaX);
      };
      const onMouseUp = () => {
        startRef.current = null;
        onDragEnd?.();
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    },
    [onResize, onDragStart, onDragEnd]
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onMouseDown={onMouseDown}
      style={{
        width: 6,
        minWidth: 6,
        flexShrink: 0,
        cursor: 'col-resize',
        background: 'transparent',
      }}
      className="hover:bg-base-300/30 transition-colors"
    />
  );
}

/** Horizontal resize handle; drag to change the height of the panel below the handle. */
function VerticalResizeHandle({ onResize }: { onResize: (deltaY: number) => void }) {
  const startRef = useRef<{ y: number } | null>(null);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      startRef.current = { y: e.clientY };
      const onMouseMove = (move: MouseEvent) => {
        if (startRef.current === null) return;
        const deltaY = move.clientY - startRef.current.y;
        startRef.current.y = move.clientY;
        onResize(deltaY);
      };
      const onMouseUp = () => {
        startRef.current = null;
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };
      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    },
    [onResize]
  );

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      onMouseDown={onMouseDown}
      className="h-1.5 shrink-0 cursor-row-resize hover:bg-primary/30 transition-colors flex items-center justify-center"
      title="Drag to resize output panel"
    >
      <div className="w-12 h-0.5 rounded-full bg-base-300 opacity-60" />
    </div>
  );
}

const AnsibleStudioView: React.FC<AnsibleStudioViewProps> = ({ user }) => {
  const userRole = (user as any)?.role ?? 'viewer';

  const getStoredTabs = (): AnsibleTab[] => {
    try {
      const raw = localStorage.getItem(ANSIBLE_TABS_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((t: any) =>
        typeof t?.id === 'string' &&
        typeof t?.path === 'string' &&
        typeof t?.name === 'string' &&
        typeof t?.content === 'string'
      ).map((t: any) => ({
        id: t.id,
        path: t.path,
        name: t.name,
        content: t.content,
        isDirty: Boolean(t.isDirty),
        isReadOnly: Boolean(t.isReadOnly),
        jobId: typeof t.jobId === 'number' ? t.jobId : undefined,
      }));
    } catch {
      return [];
    }
  };

  // ── Playbook / editor state ──────────────────────────────────────────────
  const [tabs, setTabs]               = useState<AnsibleTab[]>(() => getStoredTabs());
  const [activeTabId, setActiveTabId] = useState<string | null>(() => localStorage.getItem(ANSIBLE_ACTIVE_TAB_KEY));
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [job, setJob]                 = useState<AnsibleJob | null>(null);
  const [isChecking, setIsChecking]   = useState(false);

  // ── SSE state ────────────────────────────────────────────────────────────
  const [sseLines, setSseLines]       = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // ── Output tab (IDE-style): sessions for Check / Deploy / Plan logs ──────
  const [outputSessions, setOutputSessions]   = useState<AnsibleOutputSession[]>([]);
  const [activeOutputId, setActiveOutputId]   = useState<string | null>(null);
  const [splitOutputId, setSplitOutputId]     = useState<string | null>(null);
  const deploySessionIdRef = useRef<string | null>(null);
  const [streamingSessionId, setStreamingSessionId] = useState<string | null>(null);
  const deployPollTimerRef = useRef<number | null>(null);

  // ── File tree ────────────────────────────────────────────────────────────
  const [fileTreeRefresh, setFileTreeRefresh] = useState(0);

  // ── Slidable panel widths (file tree left, AI chat right) ───────────────
  const [fileTreeWidth, setFileTreeWidth] = useState(() => {
    const raw = Number(localStorage.getItem(ANSIBLE_FILE_TREE_WIDTH_KEY));
    return Number.isFinite(raw) && raw >= FILE_TREE_MIN && raw <= FILE_TREE_MAX ? raw : FILE_TREE_DEFAULT;
  });
  const [chatPanelWidth, setChatPanelWidth] = useState(() => {
    const raw = Number(localStorage.getItem(ANSIBLE_CHAT_PANEL_WIDTH_KEY));
    return Number.isFinite(raw) && raw >= CHAT_PANEL_MIN && raw <= CHAT_PANEL_MAX ? raw : CHAT_PANEL_DEFAULT;
  });

  // ── SSH terminal drawer ──────────────────────────────────────────────────
  const [sshTabs, setSshTabs]             = useState<SSHTab[]>([]);
  const [activeSshTab, setActiveSshTab]   = useState<string | null>(null);
  const [sshTabStates, setSshTabStates]   = useState<Record<string, SSHConnectionState>>({});
  const [statusBarHeight, setStatusBarHeight] = useState(() => {
    const raw = Number(localStorage.getItem(ANSIBLE_STATUS_BAR_HEIGHT_KEY));
    return Number.isFinite(raw) && raw >= STATUS_BAR_HEIGHT_MIN && raw <= STATUS_BAR_HEIGHT_MAX
      ? raw
      : STATUS_BAR_HEIGHT_DEFAULT;
  });
  const [pendingHostname, setPendingHostname] = useState('');
  const [pendingHost, setPendingHost]         = useState('');
  const [modalOpen, setModalOpen]             = useState(false);

  /** Center column: playbook editor vs Intent Parse workbench (REFACTOR_FRONTEND.md). */
  const [centerStudioMode, setCenterStudioMode] = useState<'playbook' | 'intent'>('playbook');

  const handleStatusBarResize = useCallback((deltaY: number) => {
    setStatusBarHeight((h) =>
      Math.min(STATUS_BAR_HEIGHT_MAX, Math.max(STATUS_BAR_HEIGHT_MIN, h - deltaY))
    );
  }, []);

  // ── Helpers ──────────────────────────────────────────────────────────────
  const activeTab = tabs.find(t => t.id === activeTabId) ?? null;

  const refreshJob = useCallback(async (id: number) => {
    try {
      const r = await ansibleApi.getResult(id);
      setJob({
        jobId:       r.job_id,
        requestId:   0,
        status:      r.status,
        stage:       r.stage,
        hasPlaybook: r.has_playbook,
        artifactType: r.artifact_type,
        entrypointPath: r.entrypoint_path,
        steps:       r.steps ?? [],
      });
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (activeJobId) refreshJob(activeJobId);
  }, [activeJobId, refreshJob]);
  useEffect(() => {
    if (!activeJobId) return;
    const t = window.setInterval(() => {
      refreshJob(activeJobId);
    }, 2000);
    return () => window.clearInterval(t);
  }, [activeJobId, refreshJob]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_TABS_KEY, JSON.stringify(tabs));
  }, [tabs]);

  useEffect(() => {
    if (activeTabId) localStorage.setItem(ANSIBLE_ACTIVE_TAB_KEY, activeTabId);
    else localStorage.removeItem(ANSIBLE_ACTIVE_TAB_KEY);
  }, [activeTabId]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_STATUS_BAR_HEIGHT_KEY, String(statusBarHeight));
  }, [statusBarHeight]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_FILE_TREE_WIDTH_KEY, String(fileTreeWidth));
  }, [fileTreeWidth]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_CHAT_PANEL_WIDTH_KEY, String(chatPanelWidth));
  }, [chatPanelWidth]);

  useEffect(() => {
    if (!tabs.length) {
      if (activeTabId !== null) setActiveTabId(null);
      return;
    }
    if (!activeTabId || !tabs.some(t => t.id === activeTabId)) {
      setActiveTabId(tabs[tabs.length - 1].id);
    }
  }, [tabs, activeTabId]);

  // When the active tab is a generated playbook (by jobId or path), set active job so the status bar and Inventory tab appear
  useEffect(() => {
    if (!activeTabId || !tabs.length) return;
    const activeTab = tabs.find(t => t.id === activeTabId);
    if (!activeTab) return;
    if (activeTab.jobId != null) {
      setActiveJobId(activeTab.jobId);
      return;
    }
    const pathNorm = (activeTab.path || activeTab.id || '').replace(/\\/g, '/');
    const jobMatch = pathNorm.match(/job_(\d+)/);
    const jobIdFromPath = jobMatch && Number.isFinite(Number(jobMatch[1])) ? parseInt(jobMatch[1], 10) : null;
    if (jobIdFromPath != null) {
      setActiveJobId(jobIdFromPath);
      setTabs(prev => prev.map(t => t.id === activeTabId ? { ...t, jobId: jobIdFromPath } : t));
    } else {
      setActiveJobId(null);
    }
  }, [activeTabId, tabs]);

  // ── Tab management ───────────────────────────────────────────────────────
  const openTab = useCallback((id: string, name: string, content: string, isDirty = false, isReadOnly = false, jobId?: number) => {
    setTabs(prev => {
      const existing = prev.find(t => t.id === id);
      if (existing) return prev;
      return [...prev, { id, path: id, name, content, isDirty, isReadOnly, jobId }];
    });
    setActiveTabId(id);
    setActiveJobId(jobId ?? null);
  }, []);

  const handleTabSelect = useCallback((id: string) => {
    setActiveTabId(id);
    const tab = tabs.find(t => t.id === id);
    let jobId: number | null = tab?.jobId ?? null;
    if (jobId == null && tab) {
      const pathNorm = (tab.path || tab.id || '').replace(/\\/g, '/');
      const jobMatch = pathNorm.match(/job_(\d+)/);
      if (jobMatch && Number.isFinite(Number(jobMatch[1]))) {
        jobId = parseInt(jobMatch[1], 10);
        setTabs(prev => prev.map(t => t.id === id ? { ...t, jobId: jobId! } : t));
      }
    }
    setActiveJobId(jobId);
  }, [tabs]);

  const updateTabContent = useCallback((id: string, content: string) => {
    setTabs(prev => prev.map(t => t.id === id ? { ...t, content, isDirty: true } : t));
  }, []);

  const closeTab = useCallback((id: string) => {
    setTabs(prev => {
      const next = prev.filter(t => t.id !== id);
      if (activeTabId === id) setActiveTabId(next[next.length - 1]?.id ?? null);
      return next;
    });
  }, [activeTabId]);

  // ── Chat → new playbook ──────────────────────────────────────────────────
  const handlePlaybookReady = useCallback((payload: {
    jobId: number;
    content: string;
    name: string;
    entrypointPath?: string | null;
    files?: AnsibleGeneratedArtifactFile[];
  }) => {
    setActiveJobId(payload.jobId);
    setSseLines([]);
    if (payload.entrypointPath) {
      openTab(payload.entrypointPath, payload.name, payload.content, false, false, payload.jobId);
      setFileTreeRefresh(n => n + 1);
    } else {
      openTab(`job_${payload.jobId}`, payload.name, payload.content, false, false, payload.jobId);
    }
  }, [openTab]);

  // ── File tree → open file ────────────────────────────────────────────────
  const handleFileOpen = useCallback(async (node: any) => {
    if (tabs.find(t => t.id === node.path)) { setActiveTabId(node.path); return; }

    // Virtual template file from Config Engine
    const tplMatch = node.path.match(/^templates\/([^/]+)\/(.+)\.j2$/);
    if (tplMatch) {
      try {
        const res = await configApi.getTemplateSource(tplMatch[1], tplMatch[2]);
        openTab(node.path, node.name, res.content, false, false);
      } catch { /* ignore */ }
      return;
    }

    // Virtual schema file from Config Engine
    const schemaMatch = node.path.match(/^schemas\/(.+)\.json$/);
    if (schemaMatch) {
      try {
        const res = await configApi.getSchemaSource(schemaMatch[1]);
        openTab(node.path, node.name, res.content, false, false);
      } catch { /* ignore */ }
      return;
    }

    try {
      const r = await ansibleApi.readFile(node.path);
      const pathNorm = (node.path || '').replace(/\\/g, '/');
      const jobMatch = pathNorm.match(/(?:^|\/)generated\/job_(\d+)(?:\/|$)/);
      const jobIdFromPath = jobMatch && Number.isFinite(Number(jobMatch[1])) ? parseInt(jobMatch[1], 10) : undefined;
      openTab(node.path, node.name, r.content, false, false, jobIdFromPath);
    } catch { /* ignore */ }
  }, [tabs, openTab]);

  // ── Save ─────────────────────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!activeTab || activeTab.isReadOnly) return;

    // Virtual template file → Config Engine API
    const tplMatch = activeTab.id.match(/^templates\/([^/]+)\/(.+)\.j2$/);
    if (tplMatch) {
      await configApi.saveTemplateSource(tplMatch[1], tplMatch[2], activeTab.content);
      setTabs(prev => prev.map(t => t.id === activeTab.id ? { ...t, isDirty: false } : t));
      return;
    }

    // Virtual schema file → Config Engine API
    const schemaMatch = activeTab.id.match(/^schemas\/(.+)\.json$/);
    if (schemaMatch) {
      await configApi.saveSchemaSource(schemaMatch[1], activeTab.content);
      setTabs(prev => prev.map(t => t.id === activeTab.id ? { ...t, isDirty: false } : t));
      return;
    }

    await ansibleApi.writeFile(activeTab.id, activeTab.content);
    setTabs(prev => prev.map(t => t.id === activeTab.id ? { ...t, isDirty: false } : t));
    setFileTreeRefresh(n => n + 1);
  }, [activeTab]);

  // Reload active tab content from disk (so edits made outside the UI or accepted file updates are shown)
  const handleReload = useCallback(async () => {
    if (!activeTab) return;
    let path = (activeTab.path || activeTab.id || '').replace(/\\/g, '/');
    if (/^templates\//.test(path) || /^schemas\//.test(path)) return;
    // Tab opened from chat may have id "job_13" with no path; backend expects playbooks/generated/job_13/playbook.yml
    if (/^job_\d+$/.test(path)) {
      const jobNum = path.replace('job_', '');
      path = `playbooks/generated/job_${jobNum}/playbook.yml`;
    }
    try {
      const r = await ansibleApi.readFile(path);
      updateTabContent(activeTab.id, r.content);
    } catch { /* ignore */ }
  }, [activeTab, updateTabContent]);

  // ── Check (dry-run) ──────────────────────────────────────────────────────
  const handleCheck = useCallback(async () => {
    if (!activeJobId) return;
    setIsChecking(true);
    try {
      const r = await ansibleApi.checkPlaybook(activeJobId);
      // Append to Output tab (IDE-style)
      const ts = new Date().toLocaleTimeString();
      const lines: string[] = [
        `[${ts}] Check (dry-run) — Job ${activeJobId}`,
        `Dry-Run Result  rc=${r.rc}  changed=${r.changed}  ${r.summary ?? ''}  ${r.rc !== 0 ? 'FAILED' : 'OK'}`,
        '',
      ];
      if (r.rc !== 0 && r.stderr?.trim()) {
        lines.push('Why it failed:', r.stderr.trim(), '');
      } else if (r.stderr?.trim()) {
        lines.push('--- stderr ---', r.stderr.trim(), '');
      }
      if (r.diff?.trim()) {
        lines.push('--- output ---', r.diff.trim());
      }
      const session: AnsibleOutputSession = {
        id: `check-${activeJobId}-${Date.now()}`,
        label: `Check — Job ${activeJobId}`,
        type: 'check',
        createdAt: Date.now(),
        lines,
      };
      setOutputSessions(prev => [session, ...prev].slice(0, 50));
      setActiveOutputId(session.id);
      await refreshJob(activeJobId);
    } finally {
      setIsChecking(false);
    }
  }, [activeJobId, refreshJob]);

  // ── Approve ──────────────────────────────────────────────────────────────
  const handleApprove = useCallback(async () => {
    if (!activeJobId) return;
    const ts = new Date().toLocaleTimeString();
    const session: AnsibleOutputSession = {
      id: `approve-${activeJobId}-${Date.now()}`,
      label: `Approve — Job ${activeJobId}`,
      type: 'plan',
      createdAt: Date.now(),
      lines: [`[${ts}] Approve — Job ${activeJobId}`, 'Approval request sent.'],
    };
    setOutputSessions(prev => [session, ...prev].slice(0, 50));
    setActiveOutputId(session.id);
    await ansibleApi.approveJob(activeJobId);
    await refreshJob(activeJobId);
    const ts2 = new Date().toLocaleTimeString();
    setOutputSessions(prev =>
      prev.map(s =>
        s.id === session.id
          ? { ...s, lines: [...s.lines, `[${ts2}] Approval complete.`] }
          : s
      )
    );
  }, [activeJobId, refreshJob]);

  // ── Deploy (SSE) ─────────────────────────────────────────────────────────
  const handleDeploy = useCallback(async () => {
    if (!activeJobId) return;
    setSseLines([]);
    setIsStreaming(true);

    const sessionId = `deploy-${activeJobId}-${Date.now()}`;
    const deploySession: AnsibleOutputSession = {
      id: sessionId,
      label: `Deploy — Job ${activeJobId}`,
      type: 'deploy',
      createdAt: Date.now(),
      lines: [
        `[${new Date().toLocaleTimeString()}] Deploy requested — Job ${activeJobId}`,
        'Streaming execution logs…',
      ],
    };
    deploySessionIdRef.current = sessionId;
    setStreamingSessionId(sessionId);
    setOutputSessions(prev => [deploySession, ...prev].slice(0, 50));
    setActiveOutputId(sessionId);

    const es = ansibleApi.streamJobOutput(activeJobId);
    esRef.current = es;

    // Poll job state during deploy so the UI reflects Apply/Commit stage
    // and the Deploy button shows its loading indicator while running.
    if (deployPollTimerRef.current) window.clearInterval(deployPollTimerRef.current);
    deployPollTimerRef.current = window.setInterval(() => {
      refreshJob(activeJobId);
    }, 1500);

    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.done) {
          es.close();
          setIsStreaming(false);
          if (deployPollTimerRef.current) {
            window.clearInterval(deployPollTimerRef.current);
            deployPollTimerRef.current = null;
          }
          deploySessionIdRef.current = null;
          setStreamingSessionId(null);
          refreshJob(activeJobId);
          const tsDone = new Date().toLocaleTimeString();
          setOutputSessions(prev =>
            prev.map(s =>
              s.id === sessionId
                ? { ...s, lines: [...s.lines, `[${tsDone}] Deploy stream finished.`] }
                : s
            )
          );
          return;
        }
        const line = `[${data.stage}] ${data.details ?? data.line ?? ''}`;
        setSseLines(prev => [...prev, line].slice(-200));
        setOutputSessions(prev =>
          prev.map(s =>
            s.id === deploySessionIdRef.current
              ? { ...s, lines: [...s.lines, line].slice(-500) }
              : s
          )
        );
      } catch { /* ignore */ }
    };
    es.onerror = () => {
      es.close();
      setIsStreaming(false);
      if (deployPollTimerRef.current) {
        window.clearInterval(deployPollTimerRef.current);
        deployPollTimerRef.current = null;
      }
      deploySessionIdRef.current = null;
      setStreamingSessionId(null);
      const tsErr = new Date().toLocaleTimeString();
      setOutputSessions(prev =>
        prev.map(s =>
          s.id === sessionId
            ? { ...s, lines: [...s.lines, `[${tsErr}] Stream disconnected or completed unexpectedly.`] }
            : s
        )
      );
    };

    try {
      await ansibleApi.executeJob(activeJobId);
    } catch (err) {
      es.close();
      setIsStreaming(false);
      if (deployPollTimerRef.current) {
        window.clearInterval(deployPollTimerRef.current);
        deployPollTimerRef.current = null;
      }
      deploySessionIdRef.current = null;
      setStreamingSessionId(null);
      const tsFail = new Date().toLocaleTimeString();
      setOutputSessions(prev =>
        prev.map(s =>
          s.id === sessionId
            ? { ...s, lines: [...s.lines, `[${tsFail}] Deploy request failed before execution stream completed.`] }
            : s
        )
      );
    }
  }, [activeJobId, refreshJob]);

  const handleDeleteOutputSession = useCallback((id: string) => {
    setOutputSessions(prev => prev.filter(s => s.id !== id));
    setActiveOutputId(prev => (prev === id ? null : prev));
    setSplitOutputId(prev => (prev === id ? null : prev));
  }, []);

  // ── Re-generate ──────────────────────────────────────────────────────────
  const handleRegenerate = useCallback(() => {
    // Close current job tab and let chat panel create a new one
    if (activeTab) closeTab(activeTab.id);
    setActiveJobId(null);
    setJob(null);
    setSseLines([]);
    setOutputSessions([]);
    setActiveOutputId(null);
    setSplitOutputId(null);
  }, [activeTab, closeTab]);

  // ── SSH helpers ──────────────────────────────────────────────────────────
  const openSSHTerminal = useCallback((hostname: string, host: string) => {
    setPendingHostname(hostname);
    setPendingHost(host);
    setModalOpen(true);
  }, []);

  const handleConnectSsh = useCallback(
    async (hostname: string, _host: string, username: string, password: string, port: number, saveToVault: boolean) => {
      const { session_id, host: resolvedHost } = await sshApi.connectWithCreds(
        hostname,
        username,
        password,
        port,
        saveToVault,
      );
      setSshTabs(prev => [
        ...prev,
        { sessionId: session_id, hostname, host: resolvedHost, wsUrl: sshApi.getWsUrl(session_id) },
      ]);
      setActiveSshTab(session_id);
    },
    [],
  );

  const handleCloseSshTab = useCallback(
    async (sessionId: string) => {
      try {
        await sshApi.closeSession(sessionId);
      } catch {
        // Session may already be closed
      }

      setSshTabs(prev => {
        const remaining = prev.filter(t => t.sessionId !== sessionId);
        setActiveSshTab(prevActive => (prevActive === sessionId ? (remaining[0]?.sessionId ?? null) : prevActive));
        return remaining;
      });
      setSshTabStates(prev => {
        const next = { ...prev };
        delete next[sessionId];
        return next;
      });
    },
    [],
  );

  return (
    <div className="flex flex-col h-full overflow-hidden select-none">
      {/* ── Main three-column area ── */}
      <div className="flex grow min-h-0 overflow-hidden">

        {/* Left: File tree (slidable width) */}
        <div
          className="shrink-0 overflow-y-auto border-r border-base-300 bg-base-100"
          style={{ width: fileTreeWidth, minWidth: FILE_TREE_MIN, maxWidth: FILE_TREE_MAX }}
        >
          <AnsibleFileTree
            onFileOpen={handleFileOpen}
            selectedPath={activeTab?.id ?? null}
            refreshTrigger={fileTreeRefresh}
            onAfterRefresh={handleReload}
          />
        </div>

        <ResizeHandle
          onResize={(deltaX) =>
            setFileTreeWidth((w) => Math.min(FILE_TREE_MAX, Math.max(FILE_TREE_MIN, w + deltaX)))
          }
        />

        {/* Centre: Editor + job status + terminal (terminal scoped here, not behind chat) */}
        <div className="flex flex-col grow min-w-0 overflow-hidden">
          <div className="flex gap-1 px-2 py-1 border-b border-base-300 bg-base-100 shrink-0 text-xs">
            <button
              type="button"
              className={`btn btn-xs ${centerStudioMode === 'playbook' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setCenterStudioMode('playbook')}
            >
              Playbook
            </button>
            <button
              type="button"
              className={`btn btn-xs ${centerStudioMode === 'intent' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setCenterStudioMode('intent')}
            >
              Intent parse
            </button>
          </div>
          <div className="grow min-h-0 overflow-hidden">
            {centerStudioMode === 'playbook' ? (
              <PlaybookEditor
                tabs={tabs}
                activeTabId={activeTabId}
                onTabSelect={handleTabSelect}
                onTabClose={closeTab}
                onChange={updateTabContent}
                onSave={() => handleSave()}
                onCheck={handleCheck}
                onApprove={handleApprove}
                onDeploy={handleDeploy}
                onRegenerate={handleRegenerate}
                job={job}
                isChecking={isChecking}
                userRole={userRole}
              />
            ) : (
              <IntentParseWorkbench userRole={userRole} />
            )}
          </div>
          <VerticalResizeHandle onResize={handleStatusBarResize} />
          <div
            className="shrink-0 overflow-hidden flex flex-col border-t border-base-300 bg-base-100"
            style={{ height: statusBarHeight, minHeight: STATUS_BAR_HEIGHT_MIN, maxHeight: STATUS_BAR_HEIGHT_MAX }}
          >
            <JobStatusBar
            job={job}
            sseLines={sseLines}
            isStreaming={isStreaming}
            outputSessions={outputSessions}
            activeOutputId={activeOutputId}
            onSelectOutputSession={setActiveOutputId}
            onDeleteOutputSession={handleDeleteOutputSession}
            splitOutputId={splitOutputId}
            onSetSplitOutputId={setSplitOutputId}
            streamingSessionId={streamingSessionId}

              sshTabs={sshTabs}
              activeSshTabId={activeSshTab}
              sshTabStates={sshTabStates}
              onSelectSshTab={setActiveSshTab}
              onCloseSshTab={handleCloseSshTab}
              onOpenSshModal={openSSHTerminal}
              onConnectionChangeSsh={(sid, state) => setSshTabStates(prev => ({ ...prev, [sid]: state }))}
          />
          </div>

          <SSHConnectModal
            hostname={pendingHostname}
            host={pendingHost}
            isOpen={modalOpen}
            onConnect={handleConnectSsh}
            onClose={() => setModalOpen(false)}
          />
        </div>

        <ResizeHandle
          onResize={(deltaX) =>
            setChatPanelWidth((w) =>
              Math.min(CHAT_PANEL_MAX, Math.max(CHAT_PANEL_MIN, w - deltaX))
            )
          }
        />

        {/* Right: AI chat — VS Code Copilot style (slidable width) */}
        <div
          className="shrink-0 overflow-hidden"
          style={{ width: chatPanelWidth, minWidth: CHAT_PANEL_MIN, maxWidth: CHAT_PANEL_MAX }}
        >
          <AnsibleChatPanel
            onPlaybookReady={handlePlaybookReady}
            onOpenFile={(path) => handleFileOpen({ path, name: path.split('/').pop() ?? path })}
            activeJob={job}
          />
        </div>
      </div>
    </div>
  );
};

export default AnsibleStudioView;
