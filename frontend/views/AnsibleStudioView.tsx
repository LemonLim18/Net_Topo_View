import React, { useState, useCallback, useEffect, useRef } from 'react';
import AnsibleFileTree                              from '../components/ansible/AnsibleFileTree';
import PlaybookEditor                               from '../components/ansible/PlaybookEditor';
import JobStatusBar                                 from '../components/ansible/JobStatusBar';
import AnsibleChatPanel                             from '../components/ansible/AnsibleChatPanel';
import SSHTerminalDrawer, { type SSHTab }           from '../components/ansible/SSHTerminalDrawer';
import { ansibleApi, configApi }                    from '../src/services/api';
import type {
  AnsibleTab, AnsibleJob, AnsibleCheckResult,
} from '../typesLLM';
import type { SSHConnectionState } from '../components/SSHTerminal';
import type { User }               from '../types';

interface AnsibleStudioViewProps {
  user: User | null;
}

const AnsibleStudioView: React.FC<AnsibleStudioViewProps> = ({ user }) => {
  const userRole = (user as any)?.role ?? 'viewer';

  // ── Playbook / editor state ──────────────────────────────────────────────
  const [tabs, setTabs]               = useState<AnsibleTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
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

  // ── SSH terminal drawer ──────────────────────────────────────────────────
  const [sshTabs, setSshTabs]             = useState<SSHTab[]>([]);
  const [activeSshTab, setActiveSshTab]   = useState<string | null>(null);
  const [sshTabStates, setSshTabStates]   = useState<Record<string, SSHConnectionState>>({});
  const [terminalHeight, setTerminalHeight] = useState(220);
  const [terminalMinimized, setTerminalMinimized] = useState(true);
  const [pendingHostname, setPendingHostname] = useState('');
  const [pendingHost, setPendingHost]         = useState('');
  const [modalOpen, setModalOpen]             = useState(false);

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
        steps:       r.steps ?? [],
      });
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (activeJobId) refreshJob(activeJobId);
  }, [activeJobId, refreshJob]);

  // ── Tab management ───────────────────────────────────────────────────────
  const openTab = useCallback((id: string, name: string, content: string, isDirty = false, isReadOnly = false, jobId?: number) => {
    setTabs(prev => {
      const existing = prev.find(t => t.id === id);
      if (existing) return prev;
      return [...prev, { id, path: id, name, content, isDirty, isReadOnly, jobId }];
    });
    setActiveTabId(id);
  }, []);

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
  const handlePlaybookReady = useCallback((jobId: number, content: string, name: string) => {
    setActiveJobId(jobId);
    setCheckResult(null);
    setSseLines([]);
    openTab(`job_${jobId}`, name, content, false, false, jobId);
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
      openTab(node.path, node.name, r.content, false, false);
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

        {/* Left: File tree */}
        <div
          className="shrink-0 overflow-y-auto border-r border-base-300 bg-base-100"
          style={{ width: '200px' }}
        >
          <AnsibleFileTree
            onFileOpen={handleFileOpen}
            selectedPath={activeTab?.id ?? null}
            refreshTrigger={fileTreeRefresh}
          />
        </div>

        {/* Centre: Editor + job status + terminal (terminal scoped here, not behind chat) */}
        <div className="flex flex-col grow min-w-0 overflow-hidden">
          <div className="grow min-h-0 overflow-hidden">
            <PlaybookEditor
              tabs={tabs}
              activeTabId={activeTabId}
              onTabSelect={setActiveTabId}
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
          <JobStatusBar
            job={job}
            sseLines={sseLines}
            isStreaming={isStreaming}
          />
          {/* SSH terminal — bottom of editor column only */}
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

        {/* Right: AI chat — VS Code Copilot style */}
        <div
          className="shrink-0 overflow-hidden"
          style={{ width: '320px' }}
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
