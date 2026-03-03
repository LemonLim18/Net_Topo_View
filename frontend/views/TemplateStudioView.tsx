import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  FileCode2,
  FolderOpen,
  Save,
  Plus,
  Trash2,
  Upload,
  Eye,
  RefreshCw,
  Loader2,
  AlertTriangle,
  ChevronRight,
  ChevronDown,
  Play,
  X,
} from 'lucide-react';
import { configApi } from '../src/services/api';
import { Toast, Confirm } from '../src/utils/swal';

// ---------------------------------------------------------------------------
//  Types
// ---------------------------------------------------------------------------

interface TemplateInfo {
  vendor: string;
  feature: string;
  has_undo: boolean;
  schema_exists: boolean;
}

interface OpenTab {
  id: string; // "vendor/feature"
  vendor: string;
  feature: string;
  content: string;
  originalContent: string;
  isDirty: boolean;
}

// ---------------------------------------------------------------------------
//  Template Studio Component
// ---------------------------------------------------------------------------

const TemplateStudioView: React.FC = () => {
  // File tree
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loadingTree, setLoadingTree] = useState(false);
  const [expandedVendors, setExpandedVendors] = useState<Set<string>>(new Set());

  // Editor tabs
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);

  // Preview
  const [previewContent, setPreviewContent] = useState<string>('');
  const [isRendering, setIsRendering] = useState(false);

  // Variables panel
  const [variables, setVariables] = useState<Record<string, string>>({});

  // Modals
  const [showNewModal, setShowNewModal] = useState(false);
  const [newVendor, setNewVendor] = useState('cisco_ios');
  const [newFeature, setNewFeature] = useState('');
  const [newContent, setNewContent] = useState('');
  const [newError, setNewError] = useState<string | null>(null);

  const editorRef = useRef<HTMLTextAreaElement>(null);

  const activeTab = tabs.find(t => t.id === activeTabId) ?? null;

  // -----------------------------------------------------------------------
  //  Data fetching
  // -----------------------------------------------------------------------

  const fetchTemplates = useCallback(async () => {
    setLoadingTree(true);
    try {
      const res = await configApi.listTemplates();
      setTemplates(res);
      // Auto-expand all vendors
      const vendors = new Set(res.map(t => t.vendor));
      setExpandedVendors(vendors);
    } catch (e: any) {
      Toast.error('Failed to load templates', e.message);
    } finally {
      setLoadingTree(false);
    }
  }, []);

  useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

  // -----------------------------------------------------------------------
  //  Variable extraction
  // -----------------------------------------------------------------------

  const extractVariables = useCallback((content: string): string[] => {
    const regex = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;
    const vars = new Set<string>();
    let match;
    while ((match = regex.exec(content)) !== null) {
      vars.add(match[1]);
    }
    return Array.from(vars);
  }, []);

  // Update variables when active tab changes
  useEffect(() => {
    if (!activeTab) return;
    const vars = extractVariables(activeTab.content);
    setVariables(prev => {
      const next: Record<string, string> = {};
      for (const v of vars) {
        next[v] = prev[v] || '';
      }
      return next;
    });
  }, [activeTab?.content, extractVariables]);

  // -----------------------------------------------------------------------
  //  File tree actions
  // -----------------------------------------------------------------------

  const toggleVendor = (vendor: string) => {
    setExpandedVendors(prev => {
      const next = new Set(prev);
      if (next.has(vendor)) next.delete(vendor);
      else next.add(vendor);
      return next;
    });
  };

  const openTemplate = async (vendor: string, feature: string) => {
    const id = `${vendor}/${feature}`;

    // Already open?
    const existing = tabs.find(t => t.id === id);
    if (existing) {
      setActiveTabId(id);
      return;
    }

    try {
      const res = await configApi.getTemplateSource(vendor, feature);
      const tab: OpenTab = {
        id,
        vendor,
        feature,
        content: res.content,
        originalContent: res.content,
        isDirty: false,
      };
      setTabs(prev => [...prev, tab]);
      setActiveTabId(id);
    } catch (e: any) {
      Toast.error('Failed to open template', e.message);
    }
  };

  // -----------------------------------------------------------------------
  //  Editor actions
  // -----------------------------------------------------------------------

  const updateContent = (content: string) => {
    if (!activeTabId) return;
    setTabs(prev =>
      prev.map(t =>
        t.id === activeTabId
          ? { ...t, content, isDirty: content !== t.originalContent }
          : t
      )
    );
  };

  const saveTemplate = async () => {
    if (!activeTab || !activeTab.isDirty) return;
    try {
      await configApi.saveTemplateSource(activeTab.vendor, activeTab.feature, activeTab.content);
      setTabs(prev =>
        prev.map(t =>
          t.id === activeTab.id
            ? { ...t, originalContent: t.content, isDirty: false }
            : t
        )
      );
      Toast.success(`Template "${activeTab.feature}" saved`);
    } catch (e: any) {
      Toast.error('Save failed', e.message);
    }
  };

  const closeTab = (id: string) => {
    setTabs(prev => {
      const next = prev.filter(t => t.id !== id);
      if (activeTabId === id) {
        setActiveTabId(next.length > 0 ? next[next.length - 1].id : null);
      }
      return next;
    });
  };

  // Ctrl+S handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveTemplate();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [activeTab]);

  // -----------------------------------------------------------------------
  //  Preview
  // -----------------------------------------------------------------------

  const renderPreview = async () => {
    if (!activeTab) return;
    setIsRendering(true);
    try {
      const res = await configApi.render(activeTab.vendor, activeTab.feature, variables);
      setPreviewContent(res.config);
    } catch (e: any) {
      setPreviewContent(`Error: ${e.message}`);
    } finally {
      setIsRendering(false);
    }
  };

  // -----------------------------------------------------------------------
  //  Create / Delete
  // -----------------------------------------------------------------------

  const handleCreateTemplate = async () => {
    if (!newFeature.trim()) {
      setNewError('Feature name is required');
      return;
    }
    setNewError(null);
    try {
      await configApi.createTemplate(
        newVendor,
        newFeature.trim(),
        newContent || `{# ${newFeature.trim()} template for ${newVendor} #}\n\n`,
      );
      Toast.success(`Template "${newVendor}/${newFeature}" created`);
      setShowNewModal(false);
      setNewFeature('');
      setNewContent('');
      fetchTemplates();
    } catch (e: any) {
      setNewError(e.message || 'Failed to create template');
    }
  };

  const handleDeleteTemplate = async (vendor: string, feature: string) => {
    const ok = await Confirm.danger(
      `Delete ${vendor}/${feature}?`,
      'This permanently removes the template file.',
    );
    if (!ok) return;
    try {
      await configApi.deleteTemplate(vendor, feature);
      Toast.success(`Template "${feature}" deleted`);
      closeTab(`${vendor}/${feature}`);
      fetchTemplates();
    } catch (e: any) {
      Toast.error('Delete failed', e.message);
    }
  };

  const handleUpload = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.j2,.jinja2,.txt';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const content = await file.text();
      const featureName = file.name.replace(/\.(j2|jinja2|txt)$/, '');
      setNewContent(content);
      setNewFeature(featureName);
      setShowNewModal(true);
    };
    input.click();
  };

  // -----------------------------------------------------------------------
  //  Group templates by vendor
  // -----------------------------------------------------------------------

  const grouped = templates.reduce<Record<string, TemplateInfo[]>>((acc, t) => {
    if (!acc[t.vendor]) acc[t.vendor] = [];
    acc[t.vendor].push(t);
    return acc;
  }, {});

  // -----------------------------------------------------------------------
  //  Render
  // -----------------------------------------------------------------------

  return (
    <div className="flex flex-col h-full overflow-hidden select-none">
      <div className="flex grow min-h-0 overflow-hidden">

        {/* ─── Left: File Tree ─── */}
        <div
          className="shrink-0 overflow-y-auto border-r border-base-300 bg-base-100 flex flex-col"
          style={{ width: '220px' }}
        >
          <div className="p-2 border-b border-base-300 flex items-center justify-between">
            <span className="text-xs font-bold uppercase opacity-60">Templates</span>
            <div className="flex gap-1">
              <button
                className="btn btn-ghost btn-xs"
                title="Upload .j2 file"
                onClick={handleUpload}
              >
                <Upload className="w-3.5 h-3.5" />
              </button>
              <button
                className="btn btn-ghost btn-xs"
                title="New template"
                onClick={() => { setNewContent(''); setNewFeature(''); setShowNewModal(true); }}
              >
                <Plus className="w-3.5 h-3.5" />
              </button>
              <button
                className="btn btn-ghost btn-xs"
                title="Refresh"
                onClick={fetchTemplates}
                disabled={loadingTree}
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loadingTree ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>

          <div className="p-1 grow overflow-y-auto">
            {loadingTree ? (
              <div className="text-center py-8 opacity-50">
                <Loader2 className="w-5 h-5 animate-spin mx-auto mb-1" />
                <p className="text-xs">Loading…</p>
              </div>
            ) : Object.keys(grouped).length === 0 ? (
              <div className="text-center py-8 opacity-40">
                <FileCode2 className="w-6 h-6 mx-auto mb-1" />
                <p className="text-xs">No templates found</p>
              </div>
            ) : (
              Object.entries(grouped).sort().map(([vendor, items]) => (
                <div key={vendor} className="mb-1">
                  <button
                    className="flex items-center gap-1 w-full px-2 py-1 text-xs font-semibold hover:bg-base-200 rounded transition-colors"
                    onClick={() => toggleVendor(vendor)}
                  >
                    {expandedVendors.has(vendor)
                      ? <ChevronDown className="w-3 h-3" />
                      : <ChevronRight className="w-3 h-3" />
                    }
                    <FolderOpen className="w-3.5 h-3.5 text-warning" />
                    <span>{vendor}</span>
                    <span className="badge badge-xs badge-ghost ml-auto">{items.length}</span>
                  </button>
                  {expandedVendors.has(vendor) && (
                    <div className="ml-4">
                      {items.sort((a, b) => a.feature.localeCompare(b.feature)).map(t => (
                        <div
                          key={t.feature}
                          className={`flex items-center gap-1 px-2 py-0.5 text-xs cursor-pointer rounded group transition-colors
                            ${activeTabId === `${t.vendor}/${t.feature}` ? 'bg-primary/15 text-primary' : 'hover:bg-base-200'}
                          `}
                          onClick={() => openTemplate(t.vendor, t.feature)}
                        >
                          <FileCode2 className="w-3 h-3 opacity-50" />
                          <span className="truncate grow">{t.feature}.j2</span>
                          <button
                            className="btn btn-ghost btn-xs opacity-0 group-hover:opacity-100 text-error"
                            onClick={(e) => { e.stopPropagation(); handleDeleteTemplate(t.vendor, t.feature); }}
                          >
                            <Trash2 className="w-2.5 h-2.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* ─── Center: Editor ─── */}
        <div className="flex flex-col grow min-w-0 overflow-hidden">
          {/* Tab bar */}
          <div className="flex items-center border-b border-base-300 bg-base-200/50 shrink-0 overflow-x-auto">
            {tabs.map(t => (
              <div
                key={t.id}
                className={`flex items-center gap-1 px-3 py-2 text-xs cursor-pointer border-r border-base-300 shrink-0 transition-colors
                  ${activeTabId === t.id ? 'bg-base-100 font-semibold' : 'hover:bg-base-200 opacity-70'}
                `}
                onClick={() => setActiveTabId(t.id)}
              >
                <FileCode2 className="w-3 h-3" />
                <span>{t.feature}.j2</span>
                {t.isDirty && <span className="w-1.5 h-1.5 rounded-full bg-warning" />}
                <button
                  className="btn btn-ghost btn-xs p-0 ml-1 opacity-50 hover:opacity-100"
                  onClick={(e) => { e.stopPropagation(); closeTab(t.id); }}
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
            {tabs.length === 0 && (
              <div className="px-3 py-2 text-xs opacity-40">No files open</div>
            )}
            {activeTab && (
              <div className="ml-auto flex items-center gap-1 px-2 shrink-0">
                <button
                  className="btn btn-ghost btn-xs gap-1"
                  onClick={saveTemplate}
                  disabled={!activeTab.isDirty}
                >
                  <Save className="w-3.5 h-3.5" /> Save
                </button>
              </div>
            )}
          </div>

          {/* Editor area */}
          <div className="grow overflow-hidden relative">
            {activeTab ? (
              <textarea
                ref={editorRef}
                className="w-full h-full p-4 bg-base-100 font-mono text-sm resize-none outline-none border-0"
                value={activeTab.content}
                onChange={(e) => updateContent(e.target.value)}
                spellCheck={false}
                style={{ tabSize: 4 }}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-full opacity-40 gap-3">
                <FileCode2 className="w-12 h-12" />
                <p className="text-sm">Select a template from the file tree to start editing</p>
                <p className="text-xs">
                  Or{' '}
                  <button className="link link-primary" onClick={handleUpload}>upload a .j2 file</button>{' '}
                  or{' '}
                  <button className="link link-primary" onClick={() => setShowNewModal(true)}>create a new template</button>
                </p>
              </div>
            )}
          </div>
        </div>

        {/* ─── Right: Variables + Preview ─── */}
        <div
          className="shrink-0 overflow-hidden border-l border-base-300 bg-base-100 flex flex-col"
          style={{ width: '300px' }}
        >
          {activeTab ? (
            <>
              {/* Variables panel */}
              <div className="border-b border-base-300 p-3 shrink-0 overflow-y-auto" style={{ maxHeight: '45%' }}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase opacity-60">Template Variables</span>
                  <span className="badge badge-xs badge-ghost">{Object.keys(variables).length}</span>
                </div>
                {Object.keys(variables).length === 0 ? (
                  <p className="text-xs opacity-40">No {'{{ variables }}'} found in template</p>
                ) : (
                  <div className="space-y-2">
                    {Object.entries(variables).map(([key, val]) => (
                      <label key={key} className="form-control w-full">
                        <div className="label py-0">
                          <span className="label-text text-xs font-mono">{`{{ ${key} }}`}</span>
                        </div>
                        <input
                          type="text"
                          className="input input-bordered input-xs w-full font-mono"
                          placeholder={key}
                          value={val}
                          onChange={(e) => setVariables(prev => ({ ...prev, [key]: e.target.value }))}
                        />
                      </label>
                    ))}
                  </div>
                )}
              </div>

              {/* Preview */}
              <div className="flex flex-col grow min-h-0">
                <div className="flex items-center justify-between p-3 border-b border-base-300 shrink-0">
                  <span className="text-xs font-bold uppercase opacity-60">Live Preview</span>
                  <button
                    className="btn btn-xs btn-primary gap-1"
                    onClick={renderPreview}
                    disabled={isRendering}
                  >
                    {isRendering
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : <Play className="w-3 h-3" />
                    }
                    Render
                  </button>
                </div>
                <div className="grow overflow-y-auto p-3">
                  {previewContent ? (
                    <pre className="text-xs font-mono whitespace-pre-wrap opacity-90">{previewContent}</pre>
                  ) : (
                    <div className="text-center py-8 opacity-40">
                      <Eye className="w-6 h-6 mx-auto mb-1" />
                      <p className="text-xs">Fill in variables and click Render</p>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full opacity-40 gap-2 p-4">
              <Eye className="w-8 h-8" />
              <p className="text-xs">Preview panel</p>
            </div>
          )}
        </div>
      </div>

      {/* ─── New Template Modal ─── */}
      {showNewModal && (
        <div className="modal modal-open">
          <div className="modal-box max-w-md">
            <h3 className="font-bold text-lg mb-4">
              {newContent ? 'Upload Template' : 'New Template'}
            </h3>

            {newError && (
              <div className="alert alert-error alert-sm mb-3">
                <AlertTriangle className="w-4 h-4" />
                <span>{newError}</span>
              </div>
            )}

            <div className="space-y-3">
              <label className="form-control w-full">
                <div className="label"><span className="label-text">Vendor *</span></div>
                <select
                  className="select select-bordered w-full"
                  value={newVendor}
                  onChange={(e) => setNewVendor(e.target.value)}
                >
                  <option value="cisco_ios">Cisco IOS / IOS-XE</option>
                  <option value="juniper_junos">Juniper JunOS</option>
                  <option value="huawei_vrp">Huawei VRP</option>
                </select>
              </label>

              <label className="form-control w-full">
                <div className="label"><span className="label-text">Feature Name *</span></div>
                <input
                  type="text"
                  className="input input-bordered w-full"
                  placeholder="e.g. day0_bootstrap"
                  value={newFeature}
                  onChange={(e) => setNewFeature(e.target.value)}
                />
                <div className="label">
                  <span className="label-text-alt opacity-50">
                    Will be saved as {newVendor}/{newFeature || '…'}.j2
                  </span>
                </div>
              </label>

              {newContent && (
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Content Preview</span></div>
                  <textarea
                    className="textarea textarea-bordered w-full font-mono text-xs h-32"
                    value={newContent}
                    onChange={(e) => setNewContent(e.target.value)}
                  />
                </label>
              )}
            </div>

            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowNewModal(false)}>Cancel</button>
              <button className="btn btn-primary gap-1" onClick={handleCreateTemplate}>
                <Plus className="w-4 h-4" /> Create Template
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setShowNewModal(false)} />
        </div>
      )}
    </div>
  );
};

export default TemplateStudioView;
