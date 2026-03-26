import React, { useState, useCallback, useEffect, useRef } from 'react';
import AnsibleFileTree                              from '../components/ansible/AnsibleFileTree';
import PlaybookEditor                               from '../components/ansible/PlaybookEditor';
import JobStatusBar                                 from '../components/ansible/JobStatusBar';
import AnsibleChatPanel                             from '../components/ansible/AnsibleChatPanel';
import SSHTerminalDrawer, { type SSHTab }           from '../components/ansible/SSHTerminalDrawer';
import { ansibleApi, configApi }                    from '../src/services/api';
import type {
  AnsibleTab, AnsibleJob, AnsibleCheckResult, AnsibleGeneratedArtifactFile,
} from '../typesLLM';
import type { SSHConnectionState } from '../components/SSHTerminal';
import type { User }               from '../types';

const ANSIBLE_TABS_KEY = 'ansible.tabs';
const ANSIBLE_ACTIVE_TAB_KEY = 'ansible.activeTabId';
const ANSIBLE_TERMINAL_HEIGHT_KEY = 'ansible.terminalHeight';
const ANSIBLE_TERMINAL_MIN_KEY = 'ansible.terminalMinimized';
const ANSIBLE_FILE_TREE_WIDTH_KEY = 'ansible.fileTreeWidth';
const ANSIBLE_CHAT_PANEL_WIDTH_KEY = 'ansible.chatPanelWidth';
const ANSIBLE_STATUS_BAR_HEIGHT_KEY = 'ansible.statusBarHeight';
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
  orientation = 'vertical',
}: {
  onResize: (delta: number) => void;
  onDragStart?: () => void;
  onDragEnd?: () => void;
  orientation?: 'vertical' | 'horizontal';
}) {
  const startRef = useRef<{ x: number; y: number } | null>(null);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      startRef.current = { x: e.clientX, y: e.clientY };
      onDragStart?.();
      const onMouseMove = (move: MouseEvent) => {
        if (startRef.current === null) return;
        const delta = orientation === 'vertical' 
          ? move.clientX - startRef.current.x 
          : move.clientY - startRef.current.y;
        
        startRef.current = { x: move.clientX, y: move.clientY };
        onResize(delta);
      };
      const onMouseUp = () => {
        startRef.current = null;
        onDragEnd?.();
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };
      document.body.style.cursor = orientation === 'vertical' ? 'col-resize' : 'row-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    },
    [onResize, onDragStart, onDragEnd, orientation]
  );

  return (
    <div
      role="separator"
      aria-orientation={orientation}
      onMouseDown={onMouseDown}
      style={{
        width: orientation === 'vertical' ? 6 : '100%',
        height: orientation === 'vertical' ? '100%' : 6,
        minWidth: orientation === 'vertical' ? 6 : undefined,
        minHeight: orientation === 'vertical' ? undefined : 6,
        flexShrink: 0,
        cursor: orientation === 'vertical' ? 'col-resize' : 'row-resize',
        background: 'transparent',
      }}
      className="hover:bg-base-300/30 transition-colors"
    />
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
  const [checkResult, setCheckResult] = useState<AnsibleCheckResult | null>(null);
  const [isChecking, setIsChecking]   = useState(false);

  // ── SSE state ────────────────────────────────────────────────────────────
  const [sseLines, setSseLines]       = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const esRef = useRef<EventSource | null>(null);

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
  const [terminalHeight, setTerminalHeight] = useState(() => {
    const raw = Number(localStorage.getItem(ANSIBLE_TERMINAL_HEIGHT_KEY));
    return Number.isFinite(raw) && raw >= 140 ? raw : 220;
  });
  const [terminalMinimized, setTerminalMinimized] = useState(() => localStorage.getItem(ANSIBLE_TERMINAL_MIN_KEY) !== 'false');
  const [pendingHostname, setPendingHostname] = useState('');
  const [pendingHost, setPendingHost]         = useState('');
  const [modalOpen, setModalOpen]             = useState(false);
  const [statusBarHeight, setStatusBarHeight] = useState(() => {
    const raw = Number(localStorage.getItem(ANSIBLE_STATUS_BAR_HEIGHT_KEY));
    return Number.isFinite(raw) && raw >= 100 ? raw : 200;
  });

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
    localStorage.setItem(ANSIBLE_TABS_KEY, JSON.stringify(tabs));
  }, [tabs]);

  useEffect(() => {
    if (activeTabId) localStorage.setItem(ANSIBLE_ACTIVE_TAB_KEY, activeTabId);
    else localStorage.removeItem(ANSIBLE_ACTIVE_TAB_KEY);
  }, [activeTabId]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_TERMINAL_HEIGHT_KEY, String(terminalHeight));
  }, [terminalHeight]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_TERMINAL_MIN_KEY, String(terminalMinimized));
  }, [terminalMinimized]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_FILE_TREE_WIDTH_KEY, String(fileTreeWidth));
  }, [fileTreeWidth]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_CHAT_PANEL_WIDTH_KEY, String(chatPanelWidth));
  }, [chatPanelWidth]);

  useEffect(() => {
    localStorage.setItem(ANSIBLE_STATUS_BAR_HEIGHT_KEY, String(statusBarHeight));
  }, [statusBarHeight]);

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
    setCheckResult(null);
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

  // ── Check (dry-run) ──────────────────────────────────────────────────────
  const handleCheck = useCallback(async () => {
    if (!activeJobId) return;
    setIsChecking(true);
    setCheckResult(null);
    try {
      const r = await ansibleApi.checkPlaybook(activeJobId);
      setCheckResult({ rc: r.rc, diff: r.diff, changed: r.changed, summary: r.summary ?? '' });
      await refreshJob(activeJobId);
    } finally {
      setIsChecking(false);
    }
  }, [activeJobId, refreshJob]);

  // ── Approve ──────────────────────────────────────────────────────────────
  const handleApprove = useCallback(async () => {
    if (!activeJobId) return;
    await ansibleApi.approveJob(activeJobId);
    await refreshJob(activeJobId);
  }, [activeJobId, refreshJob]);

  // ── Deploy (SSE) ─────────────────────────────────────────────────────────
  const handleDeploy = useCallback(async () => {
    if (!activeJobId) return;
    setSseLines([]);
    setIsStreaming(true);

    const es = ansibleApi.streamJobOutput(activeJobId);
    esRef.current = es;

    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.done) {
          es.close();
          setIsStreaming(false);
          refreshJob(activeJobId);
          return;
        }
        setSseLines(prev => [...prev, `[${data.stage}] ${data.details ?? data.line ?? ''}`].slice(-200));
      } catch { /* ignore */ }
    };
    es.onerror = () => { es.close(); setIsStreaming(false); };

    try {
      await ansibleApi.executeJob(activeJobId);
    } catch (err) {
      es.close();
      setIsStreaming(false);
    }
  }, [activeJobId, refreshJob]);

  // ── Re-generate ──────────────────────────────────────────────────────────
  const handleRegenerate = useCallback(() => {
    // Close current job tab and let chat panel create a new one
    if (activeTab) closeTab(activeTab.id);
    setActiveJobId(null);
    setJob(null);
    setCheckResult(null);
    setSseLines([]);
  }, [activeTab, closeTab]);

  // ── SSH helpers ──────────────────────────────────────────────────────────
  const openSSHTerminal = useCallback((hostname: string, host: string) => {
    setPendingHostname(hostname);
    setPendingHost(host);
    setModalOpen(true);
  }, []);

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
          />
        </div>

        <ResizeHandle
          onResize={(deltaX) =>
            setFileTreeWidth((w) => Math.min(FILE_TREE_MAX, Math.max(FILE_TREE_MIN, w + deltaX)))
          }
        />

        {/* Centre: Editor + job status + terminal (terminal scoped here, not behind chat) */}
        <div className="flex flex-col grow min-w-0 overflow-hidden">
          <div className="grow min-h-0 overflow-hidden">
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
              checkResult={checkResult}
              isChecking={isChecking}
              userRole={userRole}
            />
          </div>

          <ResizeHandle
            orientation="horizontal"
            onResize={(deltaY) =>
              setStatusBarHeight((h) => Math.max(80, Math.min(800, h - deltaY)))
            }
          />

          <div
            className="shrink-0 overflow-hidden flex flex-col bg-base-100 border-t border-base-300"
            style={{ height: statusBarHeight }}
          >
            <JobStatusBar
              job={job}
              sseLines={sseLines}
              isStreaming={isStreaming}
            />
          </div>

          <SSHTerminalDrawer
            tabs={sshTabs}
            activeTabId={activeSshTab}
            tabStates={sshTabStates}
            height={terminalHeight}
            minimized={terminalMinimized}
            pendingHostname={pendingHostname}
            pendingHost={pendingHost}
            modalOpen={modalOpen}
            onSelectTab={setActiveSshTab}
            onCloseTab={(sid) => setSshTabs(prev => prev.filter(t => t.sessionId !== sid))}
            onConnectionChange={(sid, state) =>
              setSshTabStates(prev => ({ ...prev, [sid]: state }))
            }
            onToggleMinimize={() => setTerminalMinimized(v => !v)}
            onOpenModal={openSSHTerminal}
            onCloseModal={() => setModalOpen(false)}
            onTabAdded={(tab) => {
              setSshTabs(prev => [...prev, tab]);
              setActiveSshTab(tab.sessionId);
            }}
            onHeightChange={setTerminalHeight}
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
