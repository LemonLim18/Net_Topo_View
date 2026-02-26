import React, { useCallback } from 'react';
import Editor from '@monaco-editor/react';
import {
  X, Save, Download, Play, ShieldCheck, RefreshCw, FlaskConical, Lock
} from 'lucide-react';
import type { AnsibleTab, AnsibleJob, AnsibleCheckResult } from '../../typesLLM';
import CheckDiffPanel from './CheckDiffPanel';

interface PlaybookEditorProps {
  tabs:            AnsibleTab[];
  activeTabId:     string | null;
  job:             AnsibleJob | null;
  checkResult:     AnsibleCheckResult | null;
  isChecking:      boolean;
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
  tabs, activeTabId, job, checkResult, isChecking,
  userRole, onTabSelect, onTabClose, onChange, onSave,
  onCheck, onApprove, onDeploy, onRegenerate,
}) => {
  const activeTab = tabs.find(t => t.id === activeTabId) ?? null;
  const isAdmin   = userRole === 'admin';
  const isOp      = userRole === 'admin' || userRole === 'operator';

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
  const approveTitle = !isAdmin
    ? 'Admin role required for device deployment approval'
    : 'Approve playbook for device deployment';
  const deployTitle = !isAdmin
    ? 'Admin role required for deployment to devices'
    : !isApproved
      ? 'Job must be approved first'
      : 'Deploy to all target devices';

  return (
    <div className="flex flex-col h-full min-h-0 bg-base-200">

      {/* ── Tab bar ───────────────────────────────────────────────────── */}
      <div className="flex items-center gap-0 bg-base-100 border-b border-base-300 overflow-x-auto flex-shrink-0 min-h-[38px]">
        {tabs.length === 0 && (
          <span className="px-4 text-xs opacity-40 italic">No files open — click a file or generate a playbook</span>
        )}
        {tabs.map(tab => (
          <div
            key={tab.id}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs border-r border-base-300 cursor-pointer flex-shrink-0 transition-colors ${
              tab.id === activeTabId
                ? 'bg-base-200 text-base-content font-semibold'
                : 'hover:bg-base-200 opacity-60'
            }`}
            onClick={() => onTabSelect(tab.id)}
          >
            {tab.isReadOnly && <Lock className="h-3 w-3 opacity-40" />}
            <span className="max-w-[160px] truncate">{tab.name}</span>
            {tab.isDirty && <span className="w-1.5 h-1.5 rounded-full bg-warning flex-shrink-0" title="Unsaved changes" />}
            <button
              className="hover:text-error ml-0.5 opacity-50 hover:opacity-100"
              onClick={e => { e.stopPropagation(); onTabClose(tab.id); }}
              title="Close"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        ))}
      </div>

      {/* ── Action bar ────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-base-100 border-b border-base-300 flex-shrink-0 flex-wrap">
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
          title="Dry-run (--check --diff) — no changes applied"
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
          disabled={!isAdmin || !isApproved || isDeploying}
          title={deployTitle}
        >
          <Play className="h-3 w-3" />
          {isDeploying ? 'Deploying…' : 'Deploy'}
        </button>

        <div className="ml-auto flex items-center gap-2">
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

      {/* ── Monaco editor ─────────────────────────────────────────────── */}
      <div className="flex-grow min-h-0">
        {activeTab ? (
          <Editor
            height="100%"
            language={
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
        ) : (
          <div className="flex flex-col items-center justify-center h-full opacity-30 gap-3">
            <FlaskConical className="h-10 w-10" />
            <span className="text-sm">Open a file from the explorer or generate a playbook via the AI chat</span>
          </div>
        )}
      </div>

      {/* ── Dry-run diff panel ──────────────────────────────────────── */}
      <CheckDiffPanel result={checkResult} isLoading={isChecking} />
    </div>
  );
};

export default PlaybookEditor;
