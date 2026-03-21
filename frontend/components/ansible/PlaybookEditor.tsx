import React, { useCallback, useState } from 'react';
import Editor from '@monaco-editor/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  X, Save, Download, Play, ShieldCheck, RefreshCw, FlaskConical, Lock, FileText
} from 'lucide-react';
import type { AnsibleTab, AnsibleJob } from '../../typesLLM';

interface PlaybookEditorProps {
  tabs:            AnsibleTab[];
  activeTabId:     string | null;
  job:             AnsibleJob | null;
  isChecking?:     boolean;
  userRole:        string;
  onTabSelect:     (id: string) => void;
  onTabClose:      (id: string) => void;
  onChange:        (id: string, content: string) => void;
  onSave:          (id: string) => void;
  onCheck:         () => void;
  onApprove:       () => void;
  onDeploy:        () => void;
  onRegenerate:    () => void;
}

const PlaybookEditor: React.FC<PlaybookEditorProps> = ({
  tabs, activeTabId, job, isChecking = false,
  userRole,   onTabSelect, onTabClose, onChange, onSave,
  onCheck, onApprove, onDeploy, onRegenerate,
}) => {
  const activeTab = tabs.find(t => t.id === activeTabId) ?? null;
  const isAdmin   = userRole === 'admin';
  const isOp      = userRole === 'admin' || userRole === 'operator';

  // Only show markdown preview when user explicitly chooses Preview (right-click or toolbar)
  const [mdPreviewTabId, setMdPreviewTabId] = useState<string | null>(null);
  const isMdPreview = !!activeTab?.name.endsWith('.md') && mdPreviewTabId === activeTab.id;
  const toggleMdPreview = useCallback(() => {
    if (!activeTab?.name.endsWith('.md')) return;
    setMdPreviewTabId((id) => (id === activeTab.id ? null : activeTab.id));
  }, [activeTab?.id, activeTab?.name]);

  const [tabContextMenu, setTabContextMenu] = useState<{ x: number; y: number; tabId: string } | null>(null);
  const openTabContextMenu = useCallback((e: React.MouseEvent, tabId: string) => {
    e.preventDefault();
    e.stopPropagation();
    const tab = tabs.find((t) => t.id === tabId);
    if (tab?.name.endsWith('.md')) setTabContextMenu({ x: e.clientX, y: e.clientY, tabId });
  }, [tabs]);
  const closeTabContextMenu = useCallback(() => setTabContextMenu(null), []);

  const handleDownload = useCallback(() => {
    if (!activeTab) return;
    const blob = new Blob([activeTab.content], { type: 'text/yaml' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = activeTab.name;
    a.click();
    URL.revokeObjectURL(url);
  }, [activeTab]);

  // Derive action states
  const hasPlaybook = !!activeTab?.jobId;
  const isApproved  = job?.status === 'approved';
  const isDeploying = job?.status === 'running';
  const isDone      = job?.status === 'success' || job?.status === 'failed';
  // Allow re-deploy when a job was already approved at least once.
  // Backend enforces "must be approved" for execution, but historically jobs may
  // move to success/failed status after the first run; we still want Deploy to stay available.
  const canDeploy = isApproved || job?.status === 'success' || job?.status === 'failed';
  const approveTitle = !isAdmin
    ? 'Admin role required for device deployment approval'
    : 'Approve playbook for device deployment';
  const deployTitle = !isAdmin
    ? 'Admin role required for deployment to devices'
    : !canDeploy
      ? 'Job must be approved first'
      : 'Deploy to all target devices';

  return (
    <div className="flex flex-col h-full min-h-0 bg-base-200">

      {/* ── Tab bar ───────────────────────────────────────────────────── */}
      <div className="flex items-center gap-0 bg-base-100 border-b border-base-300 overflow-x-auto shrink-0 min-h-[38px]">
        {tabs.length === 0 && (
          <span className="px-4 text-xs opacity-40 italic">No files open — click a file or generate a playbook</span>
        )}
        {tabs.map(tab => (
          <div
            key={tab.id}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs border-r border-base-300 cursor-pointer shrink-0 transition-colors ${
              tab.id === activeTabId
                ? 'bg-base-200 text-base-content font-semibold'
                : 'hover:bg-base-200 opacity-60'
            }`}
            onClick={() => { closeTabContextMenu(); onTabSelect(tab.id); }}
            onContextMenu={(e) => openTabContextMenu(e, tab.id)}
          >
            {tab.isReadOnly && <Lock className="h-3 w-3 opacity-40" />}
            <span className="max-w-[160px] truncate">{tab.name}</span>
            {tab.isDirty && <span className="w-1.5 h-1.5 rounded-full bg-warning shrink-0" title="Unsaved changes" />}
            <button
              className="hover:text-error ml-0.5 opacity-50 hover:opacity-100"
              onClick={e => { e.stopPropagation(); onTabClose(tab.id); }}
              title="Close"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        ))}
        {tabContextMenu && (
          <>
            <div className="fixed inset-0 z-40" onClick={closeTabContextMenu} onContextMenu={closeTabContextMenu} aria-hidden="true" />
            <div
              className="fixed z-50 min-w-[140px] py-1 bg-base-200 border border-base-300 rounded-lg shadow-xl"
              style={{ left: tabContextMenu.x, top: tabContextMenu.y }}
            >
              <button
                type="button"
                className="w-full px-3 py-1.5 text-left text-xs hover:bg-base-300 flex items-center gap-2"
                onClick={() => {
                  if (tabContextMenu.tabId !== activeTabId) onTabSelect(tabContextMenu.tabId);
                  setMdPreviewTabId(tabContextMenu.tabId);
                  closeTabContextMenu();
                }}
              >
                <FileText className="h-3 w-3" /> Preview
              </button>
              {mdPreviewTabId === tabContextMenu.tabId && (
                <button
                  type="button"
                  className="w-full px-3 py-1.5 text-left text-xs hover:bg-base-300 flex items-center gap-2"
                  onClick={() => { setMdPreviewTabId(null); closeTabContextMenu(); }}
                >
                  Close Preview
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {/* ── Action bar ────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-base-100 border-b border-base-300 shrink-0 flex-wrap">
        <button
          className="btn btn-xs btn-ghost gap-1"
          onClick={onRegenerate}
          disabled={!hasPlaybook}
          title="Re-generate playbook via AI"
        >
          <RefreshCw className="h-3 w-3" /> Re-generate
        </button>

        <div className="divider divider-horizontal mx-0 my-0 h-5" />

        <button
          className="btn btn-xs btn-info gap-1"
          onClick={onCheck}
          disabled={!hasPlaybook || isChecking || isDeploying}
          title="Dry-run (--check --diff) — results appear in Output tab"
        >
          <FlaskConical className="h-3 w-3" />
          {isChecking ? 'Checking…' : 'Check'}
        </button>

        <button
          className="btn btn-xs btn-warning gap-1"
          onClick={onApprove}
          disabled={!isAdmin || !hasPlaybook || isApproved || isDeploying || isDone}
          title={approveTitle}
        >
          <ShieldCheck className="h-3 w-3" /> Approve
        </button>

        <button
          className={`btn btn-xs gap-1 ${isDeploying ? 'btn-disabled' : 'btn-success'}`}
          onClick={onDeploy}
          disabled={!isAdmin || !canDeploy || isDeploying}
          title={deployTitle}
        >
          <Play className="h-3 w-3" />
          {isDeploying ? 'Deploying…' : 'Deploy'}
        </button>

        <div className="ml-auto flex items-center gap-2">
          {activeTab?.name.endsWith('.md') && (
            <button
              type="button"
              className={`btn btn-xs gap-1 ${isMdPreview ? 'btn-ghost bg-base-300' : 'btn-ghost'}`}
              onClick={toggleMdPreview}
              title={isMdPreview ? 'Close preview' : 'Open preview'}
            >
              <FileText className="h-3 w-3" /> {isMdPreview ? 'Close Preview' : 'Preview'}
            </button>
          )}
          {activeTab && !activeTab.isReadOnly && isOp && (
            <button
              className={`btn btn-xs gap-1 ${activeTab.isDirty ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => onSave(activeTab.id)}
              disabled={!activeTab.isDirty}
              title="Save file to disk"
            >
              <Save className="h-3 w-3" /> Save
            </button>
          )}
          {activeTab && (
            <button
              className="btn btn-xs btn-ghost gap-1"
              onClick={handleDownload}
              title="Download file"
            >
              <Download className="h-3 w-3" /> Download
            </button>
          )}
        </div>
      </div>

      {/* ── Editor or Markdown preview (preview only when user chooses Preview) ── */}
      <div className="grow min-h-0 overflow-auto">
        {activeTab ? (
          activeTab.name.endsWith('.md') && isMdPreview ? (
            <div className="p-4 min-h-full bg-base-300 text-base-content overflow-auto">
              <article className="prose prose-sm prose-invert max-w-none prose-headings:text-base-content prose-p:text-base-content/90 prose-li:text-base-content/90 prose-strong:text-base-content prose-code:bg-base-200 prose-code:text-base-content prose-pre:bg-base-200 prose-pre:text-base-content prose-blockquote:border-base-content/30 prose-blockquote:text-base-content/80">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {activeTab.content}
                </ReactMarkdown>
              </article>
            </div>
          ) : (
            <Editor
              height="100%"
              language={
                activeTab.name.endsWith('.md') ? 'markdown' :
                activeTab.name.endsWith('.j2') ? 'jinja2' :
                activeTab.name.endsWith('.cfg') ? 'ini' :
                'yaml'
              }
              theme="vs-dark"
              value={activeTab.content}
              onChange={val => {
                if (!activeTab.isReadOnly) onChange(activeTab.id, val ?? '');
              }}
              options={{
                readOnly:         activeTab.isReadOnly,
                minimap:          { enabled: false },
                wordWrap:         'on',
                fontSize:         13,
                lineNumbers:      'on',
                scrollBeyondLastLine: false,
                automaticLayout:  true,
                tabSize:          2,
                renderWhitespace: 'boundary',
                folding:          true,
              }}
            />
          )
        ) : (
          <div className="flex flex-col items-center justify-center h-full opacity-30 gap-3">
            <FlaskConical className="h-10 w-10" />
            <span className="text-sm">Open a file from the explorer or generate a playbook via the AI chat</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default PlaybookEditor;
