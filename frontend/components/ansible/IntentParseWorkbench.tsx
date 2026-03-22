import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  FlaskConical,
  GitBranch,
  Layers,
  ListTree,
  Minus,
  Play,
  Plus,
  RotateCcw,
  Save,
  Trash2,
} from 'lucide-react';
import { ansibleApi } from '../../src/services/api';
import { Toast } from '../../src/utils/swal';

type ViewMode = 'tree' | 'category' | 'operation' | 'preview';

type CatalogCategory = {
  key: string;
  display_name: string;
  category_description?: string;
  legacy_feature?: string;
  shared_fields?: Array<{ name: string; data_type?: string; required?: boolean }>;
  operations: Array<{
    name: string;
    action?: string;
    feature?: string;
    required_parameters: string[];
    rules: string[];
  }>;
};

type IntentCategoryUiModel = {
  category_key: string;
  display_name: string;
  category_description: string;
  legacy_feature: string;
  shared_fields: Array<{ name: string; data_type: string; required: boolean }>;
  operations: Array<{
    name: string;
    action: string;
    feature: string;
    required_parameters: string[];
    rules: string[];
  }>;
};

const DATA_TYPES = ['string', 'integer', 'enum', 'array', 'object', 'boolean'] as const;

const VIEW_TABS: { id: ViewMode; label: string; icon: React.ReactNode }[] = [
  { id: 'tree', label: 'Parse tree', icon: <ListTree className="h-3.5 w-3.5" /> },
  { id: 'category', label: 'Category schema', icon: <Layers className="h-3.5 w-3.5" /> },
  { id: 'operation', label: 'Operation schema', icon: <GitBranch className="h-3.5 w-3.5" /> },
  { id: 'preview', label: 'Preview / Test', icon: <FlaskConical className="h-3.5 w-3.5" /> },
];

const LEFT_MIN = 160;
const LEFT_MAX = 520;
const DEFAULT_LEFT = 260;

function usePanelResize(
  width: number,
  setWidth: (w: number) => void
): { onMouseDown: (e: React.MouseEvent) => void } {
  const dragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const dx = e.clientX - startX.current;
      const next = Math.min(LEFT_MAX, Math.max(LEFT_MIN, startW.current + dx));
      setWidth(next);
    };
    const onUp = () => {
      dragging.current = false;
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [setWidth]);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      startX.current = e.clientX;
      startW.current = width;
    },
    [width]
  );

  return { onMouseDown };
}

const IntentParseWorkbench: React.FC<{ userRole?: string }> = ({ userRole = 'viewer' }) => {
  const isAdmin = userRole === 'admin';
  const [view, setView] = useState<ViewMode>('preview');
  const [leftWidth, setLeftWidth] = useState(DEFAULT_LEFT);
  const resizeHandle = usePanelResize(leftWidth, setLeftWidth);

  const [catalog, setCatalog] = useState<{
    categories: CatalogCategory[];
    canonical_adapter?: { mappings: string[] };
    global_fields?: Record<string, unknown>;
  } | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedOperation, setSelectedOperation] = useState<string | null>(null);
  const [treeZoom, setTreeZoom] = useState(1);

  const [draft, setDraft] = useState<IntentCategoryUiModel | null>(null);
  const [draftCategoryKey, setDraftCategoryKey] = useState<string | null>(null);
  const snapshotRef = useRef<string>('');
  const [uiLoading, setUiLoading] = useState(false);
  const [uiErr, setUiErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [newParam, setNewParam] = useState('');
  const [prompt, setPrompt] = useState(
    'Create VLAN 67 named ShuYing on access switch 1, 2, and 3'
  );
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [parseErr, setParseErr] = useState<string | null>(null);

  const reloadCatalog = useCallback(async () => {
    try {
      const c = await ansibleApi.getIntentCatalog();
      setCatalog({
        categories: c.categories as CatalogCategory[],
        canonical_adapter: c.canonical_adapter,
        global_fields: c.global_fields,
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      Toast.error('Reload catalog failed', msg);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const c = await ansibleApi.getIntentCatalog();
        if (cancelled) return;
        setCatalog({
          categories: c.categories as CatalogCategory[],
          canonical_adapter: c.canonical_adapter,
          global_fields: c.global_fields,
        });
        if (c.categories?.length) setSelectedCategory(String(c.categories[0].key));
      } catch (e: unknown) {
        if (!cancelled) setLoadErr(e instanceof Error ? e.message : 'Failed to load intent catalog');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const isDirty = useMemo(() => {
    if (!draft) return false;
    try {
      return JSON.stringify(draft) !== snapshotRef.current;
    } catch {
      return true;
    }
  }, [draft]);

  useEffect(() => {
    if (!selectedCategory) return;
    if (view !== 'category' && view !== 'operation') return;
    if (draftCategoryKey === selectedCategory) return;

    let cancelled = false;
    (async () => {
      setUiErr(null);
      setUiLoading(true);
      try {
        const m = await ansibleApi.getIntentCategoryUiModel(selectedCategory);
        if (cancelled) return;
        setDraft(m);
        setDraftCategoryKey(selectedCategory);
        snapshotRef.current = JSON.stringify(m);
        if (m.operations.length) {
          setSelectedOperation((prev) =>
            prev && m.operations.some((o) => o.name === prev) ? prev : m.operations[0].name
          );
        } else {
          setSelectedOperation(null);
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setUiErr(e instanceof Error ? e.message : String(e));
          setDraft(null);
          setDraftCategoryKey(null);
        }
      } finally {
        if (!cancelled) setUiLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedCategory, view, draftCategoryKey]);

  const trySelectCategory = useCallback(
    (key: string) => {
      if (key === selectedCategory) return;
      if (isDirty) {
        const ok = window.confirm('Discard unsaved schema changes for the current category?');
        if (!ok) return;
      }
      setSelectedCategory(key);
      setDraftCategoryKey(null);
      setDraft(null);
      snapshotRef.current = '';
      setSelectedOperation(null);
    },
    [isDirty, selectedCategory]
  );

  const saveDraft = useCallback(async () => {
    if (!isAdmin || !draft || !selectedCategory) return;
    setSaving(true);
    try {
      const body = {
        display_name: draft.display_name,
        category_description: draft.category_description,
        legacy_feature: draft.legacy_feature,
        shared_fields: draft.shared_fields,
        operations: draft.operations,
      };
      await ansibleApi.putIntentCategoryUiModel(selectedCategory, body);
      snapshotRef.current = JSON.stringify(draft);
      Toast.success('Saved', `categories/${selectedCategory}.yaml — catalog reloaded.`);
      await reloadCatalog();
    } catch (e: unknown) {
      Toast.error('Save failed', e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [isAdmin, draft, selectedCategory, reloadCatalog]);

  const currentOp = useMemo(() => {
    if (!draft?.operations.length) return null;
    const bySel = selectedOperation
      ? draft.operations.find((o) => o.name === selectedOperation)
      : null;
    return bySel ?? draft.operations[0];
  }, [draft, selectedOperation]);

  useEffect(() => {
    if (!draft?.operations.length) return;
    if (selectedOperation && draft.operations.some((o) => o.name === selectedOperation)) return;
    setSelectedOperation(draft.operations[0].name);
  }, [draft, selectedOperation]);

  const runPreview = useCallback(async () => {
    setRunning(true);
    setParseErr(null);
    setResult(null);
    try {
      const r = await ansibleApi.intentParseWorkbench({ prompt });
      setResult(r as unknown as Record<string, unknown>);
    } catch (e: unknown) {
      setParseErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [prompt]);

  const sharedNames = useMemo(
    () => draft?.shared_fields.map((s) => s.name).filter(Boolean) ?? [],
    [draft]
  );

  const updateOperation = useCallback(
    (opName: string, patch: Partial<IntentCategoryUiModel['operations'][0]>) => {
      setDraft((prev) => {
        if (!prev) return prev;
        const next = structuredClone(prev);
        const idx = next.operations.findIndex((o) => o.name === opName);
        if (idx < 0) return prev;
        next.operations[idx] = { ...next.operations[idx], ...patch };
        return next;
      });
    },
    []
  );

  const pendingSelectOp = useRef<string | null>(null);

  const addOperation = useCallback(() => {
    setDraft((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev);
      let n = next.operations.length + 1;
      let name = `new_operation_${n}`;
      while (next.operations.some((o) => o.name === name)) {
        n += 1;
        name = `new_operation_${n}`;
      }
      next.operations.push({
        name,
        action: name,
        feature: prev.legacy_feature || prev.category_key,
        required_parameters: [],
        rules: [],
      });
      pendingSelectOp.current = name;
      return next;
    });
  }, []);

  useEffect(() => {
    const p = pendingSelectOp.current;
    if (!p || !draft?.operations.some((o) => o.name === p)) return;
    setSelectedOperation(p);
    pendingSelectOp.current = null;
  }, [draft]);

  const removeOperation = useCallback((opName: string) => {
    if (!window.confirm(`Delete operation "${opName}" from this category file?`)) return;
    setDraft((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev);
      next.operations = next.operations.filter((o) => o.name !== opName);
      return next;
    });
    setSelectedOperation(null);
  }, []);

  return (
    <div className="flex flex-col h-full min-h-0 bg-base-200 text-xs">
      <div className="flex flex-wrap gap-1 px-2 py-1.5 border-b border-base-300 bg-base-100 shrink-0 items-center">
        {VIEW_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`btn btn-xs gap-1 ${view === t.id ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setView(t.id)}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
        {isAdmin && (view === 'category' || view === 'operation') && (
          <button
            type="button"
            className="btn btn-xs btn-success gap-1 ml-auto"
            disabled={!draft || !isDirty || saving || uiLoading}
            onClick={() => void saveDraft()}
          >
            <Save className="h-3 w-3" />
            {saving ? 'Saving…' : 'Save to disk'}
          </button>
        )}
      </div>

      <div className="flex grow min-h-0 overflow-hidden">
        {/* Left navigation — width resizable */}
        <div
          className="shrink-0 flex min-h-0 border-r border-base-300 bg-base-100"
          style={{ width: leftWidth }}
        >
          <div className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden p-2">
            {loadErr && <p className="text-error mb-2 break-words">{loadErr}</p>}
            {!catalog && !loadErr && <p className="opacity-50">Loading catalog…</p>}
            {catalog && (
              <ul className="space-y-0.5">
                {catalog.categories.map((c) => (
                  <li key={c.key}>
                    <button
                      type="button"
                      className={`w-full text-left px-1 py-0.5 rounded break-words ${
                        selectedCategory === c.key ? 'bg-primary/20 font-semibold' : 'hover:bg-base-200'
                      }`}
                      onClick={() => trySelectCategory(c.key)}
                    >
                      {c.display_name}
                    </button>
                    <ul className="ml-2 mt-0.5 border-l border-base-300 pl-1 space-y-0.5">
                      {c.operations.map((o) => (
                        <li key={o.name}>
                          <button
                            type="button"
                            className={`w-full text-left break-words hyphens-auto ${
                              selectedOperation === o.name && selectedCategory === c.key
                                ? 'text-primary font-medium'
                                : 'opacity-80 hover:opacity-100'
                            }`}
                            onClick={() => {
                              trySelectCategory(c.key);
                              setSelectedOperation(o.name);
                              setView('operation');
                            }}
                          >
                            {o.name}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
            <p className="opacity-50 text-[10px] mt-3 leading-snug">
              Schemas live in <code className="text-[9px]">intent_catalog/categories/*.yaml</code> on the
              server. Non-required parameters are optional by default.
            </p>
          </div>
          <button
            type="button"
            aria-label="Resize sidebar"
            className="w-1.5 shrink-0 cursor-col-resize hover:bg-primary/30 bg-base-300 border-0 p-0"
            onMouseDown={resizeHandle.onMouseDown}
          />
        </div>

        {/* Main */}
        <div className="flex flex-col grow min-w-0 min-h-0">
          {view === 'tree' && (
            <div className="flex flex-col grow min-h-0 p-2 gap-2">
              <div className="flex flex-wrap items-center gap-2 shrink-0">
                <span className="font-semibold text-sm">Intent schema parse tree</span>
                <div className="flex items-center gap-1 ml-auto">
                  <span className="opacity-60 text-[10px]">Zoom</span>
                  <button
                    type="button"
                    className="btn btn-xs btn-square"
                    title="Zoom out"
                    onClick={() => setTreeZoom((z) => Math.max(0.4, Math.round((z - 0.1) * 10) / 10))}
                  >
                    <Minus className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    className="btn btn-xs btn-square"
                    title="Zoom in"
                    onClick={() => setTreeZoom((z) => Math.min(2, Math.round((z + 0.1) * 10) / 10))}
                  >
                    <Plus className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    className="btn btn-xs gap-0.5"
                    title="Reset zoom"
                    onClick={() => setTreeZoom(1)}
                  >
                    <RotateCcw className="h-3 w-3" />
                    100%
                  </button>
                </div>
              </div>
              <p className="opacity-70 text-[10px] shrink-0">
                Scroll horizontally to see all categories. Canonical fine-grained ops map via{' '}
                <code className="bg-base-300 px-1 rounded">operation_adapter.yaml</code>
                {catalog?.canonical_adapter?.mappings?.length
                  ? ` (${catalog.canonical_adapter.mappings.length} mappings).`
                  : '.'}
              </p>
              <div className="flex-1 min-h-0 overflow-auto rounded border border-base-300 bg-base-300/10">
                <div
                  style={{
                    transform: `scale(${treeZoom})`,
                    transformOrigin: 'top left',
                  }}
                  className="inline-block align-top"
                >
                  <div className="p-4 min-w-max flex flex-nowrap gap-4 items-start">
                    <div className="flex flex-col items-center gap-2 shrink-0">
                      <span className="badge badge-primary badge-lg">Root</span>
                      <div className="w-px h-4 bg-primary/40" />
                    </div>
                    {catalog?.categories.map((c) => (
                      <div
                        key={c.key}
                        className="border border-base-300 rounded-xl bg-base-100 shadow-sm min-w-[220px] max-w-[300px] shrink-0"
                      >
                        <div className="px-3 py-2 border-b border-base-300 bg-base-200/80">
                          <div className="font-bold text-sm text-success">{c.display_name}</div>
                          <div className="text-[10px] opacity-60 font-mono">{c.key}</div>
                          {c.category_description && (
                            <p className="text-[10px] opacity-80 mt-1 leading-snug">{c.category_description}</p>
                          )}
                        </div>
                        <ul className="p-2 space-y-2 max-h-[70vh] overflow-y-auto">
                          {c.operations.map((o) => (
                            <li
                              key={o.name}
                              className="border border-base-300 rounded-lg p-2 bg-base-200/40"
                            >
                              <div className="font-semibold text-primary text-[11px] break-all">{o.name}</div>
                              <div className="text-[9px] opacity-50 mt-0.5">
                                {o.action} / {o.feature}
                              </div>
                              <div className="flex flex-wrap gap-1 mt-1.5">
                                {(o.required_parameters || []).map((p) => (
                                  <span key={p} className="badge badge-error badge-xs whitespace-nowrap">
                                    {p}
                                  </span>
                                ))}
                                {!(o.required_parameters || []).length && (
                                  <span className="opacity-40 text-[10px]">(no required params)</span>
                                )}
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {view === 'category' && (
            <div className="p-3 overflow-y-auto grow space-y-3 min-h-0">
              {!selectedCategory && <p className="opacity-50">Select a category in the left panel.</p>}
              {selectedCategory && uiErr && <p className="text-error">{uiErr}</p>}
              {selectedCategory && uiLoading && <p className="opacity-50">Loading schema…</p>}
              {selectedCategory && draft && !uiLoading && (
                <>
                  <h2 className="text-sm font-bold">Category-specific intent schema</h2>
                  <p className="opacity-60 text-[10px]">
                    Shared fields define names and types for this category. Operation required lists can reference
                    these names; any other parameter name is optional unless a rule states otherwise.
                  </p>
                  <label className="form-control w-full max-w-lg">
                    <span className="label-text text-[10px]">Display name</span>
                    <input
                      className="input input-bordered input-sm"
                      value={draft.display_name}
                      onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
                      disabled={!isAdmin}
                    />
                  </label>
                  <label className="form-control w-full max-w-lg">
                    <span className="label-text text-[10px]">Category description</span>
                    <input
                      className="input input-bordered input-sm"
                      value={draft.category_description}
                      onChange={(e) => setDraft({ ...draft, category_description: e.target.value })}
                      disabled={!isAdmin}
                    />
                  </label>
                  <label className="form-control w-full max-w-md">
                    <span className="label-text text-[10px]">Legacy feature key</span>
                    <input
                      className="input input-bordered input-sm font-mono"
                      value={draft.legacy_feature}
                      onChange={(e) => setDraft({ ...draft, legacy_feature: e.target.value })}
                      disabled={!isAdmin}
                    />
                  </label>

                  <div className="overflow-x-auto">
                    <table className="table table-xs table-zebra w-full max-w-4xl">
                      <thead>
                        <tr>
                          <th>Field name</th>
                          <th>Data type</th>
                          <th>Required</th>
                          {isAdmin && <th />}
                        </tr>
                      </thead>
                      <tbody>
                        {draft.shared_fields.map((row, idx) => (
                          <tr key={`${row.name}-${idx}`}>
                            <td>
                              <input
                                className="input input-bordered input-xs w-full max-w-[200px] font-mono"
                                value={row.name}
                                disabled={!isAdmin}
                                onChange={(e) => {
                                  const next = structuredClone(draft);
                                  next.shared_fields[idx].name = e.target.value;
                                  setDraft(next);
                                }}
                              />
                            </td>
                            <td>
                              <select
                                className="select select-bordered select-xs"
                                value={row.data_type}
                                disabled={!isAdmin}
                                onChange={(e) => {
                                  const next = structuredClone(draft);
                                  next.shared_fields[idx].data_type = e.target.value;
                                  setDraft(next);
                                }}
                              >
                                {DATA_TYPES.map((dt) => (
                                  <option key={dt} value={dt}>
                                    {dt}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td>
                              <input
                                type="checkbox"
                                className="checkbox checkbox-xs"
                                checked={row.required}
                                disabled={!isAdmin}
                                onChange={(e) => {
                                  const next = structuredClone(draft);
                                  next.shared_fields[idx].required = e.target.checked;
                                  setDraft(next);
                                }}
                              />
                            </td>
                            {isAdmin && (
                              <td>
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-xs text-error"
                                  title="Remove row"
                                  onClick={() => {
                                    const next = structuredClone(draft);
                                    next.shared_fields.splice(idx, 1);
                                    setDraft(next);
                                  }}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {isAdmin && (
                    <button
                      type="button"
                      className="btn btn-xs btn-outline gap-1"
                      onClick={() => {
                        setDraft({
                          ...draft,
                          shared_fields: [
                            ...draft.shared_fields,
                            { name: 'new_field', data_type: 'string', required: false },
                          ],
                        });
                      }}
                    >
                      <Plus className="h-3 w-3" />
                      Add field
                    </button>
                  )}
                </>
              )}
            </div>
          )}

          {view === 'operation' && (
            <div className="p-3 overflow-y-auto grow space-y-3 min-h-0">
              {!selectedCategory && <p className="opacity-50">Select a category in the left panel.</p>}
              {selectedCategory && uiErr && <p className="text-error">{uiErr}</p>}
              {selectedCategory && uiLoading && <p className="opacity-50">Loading schema…</p>}
              {selectedCategory && draft && !uiLoading && (
                <>
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="form-control">
                      <span className="label-text text-[10px]">Operation</span>
                      <select
                        className="select select-bordered select-sm min-w-[200px]"
                        value={currentOp?.name ?? ''}
                        onChange={(e) => setSelectedOperation(e.target.value || null)}
                      >
                        {draft.operations.map((o) => (
                          <option key={o.name} value={o.name}>
                            {o.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    {isAdmin && (
                      <>
                        <button type="button" className="btn btn-sm btn-primary gap-1" onClick={addOperation}>
                          <Plus className="h-3.5 w-3.5" />
                          Add operation
                        </button>
                        {currentOp && (
                          <button
                            type="button"
                            className="btn btn-sm btn-outline btn-error gap-1"
                            onClick={() => currentOp && removeOperation(currentOp.name)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Delete
                          </button>
                        )}
                      </>
                    )}
                  </div>

                  {currentOp && (
                    <>
                      <label className="form-control w-full max-w-lg">
                        <span className="label-text text-[10px]">Identifier (YAML key)</span>
                        <input
                          className="input input-bordered input-sm font-mono"
                          value={currentOp.name}
                          disabled={!isAdmin}
                          onChange={(e) => {
                            const old = currentOp.name;
                            const name = e.target.value;
                            setDraft((prev) => {
                              if (!prev) return prev;
                              const next = structuredClone(prev);
                              const i = next.operations.findIndex((o) => o.name === old);
                              if (i >= 0) next.operations[i].name = name;
                              return next;
                            });
                            setSelectedOperation(name);
                          }}
                        />
                      </label>
                      <label className="form-control w-full max-w-lg">
                        <span className="label-text text-[10px]">Action</span>
                        <input
                          className="input input-bordered input-sm font-mono"
                          value={currentOp.action}
                          disabled={!isAdmin}
                          onChange={(e) => updateOperation(currentOp.name, { action: e.target.value })}
                        />
                      </label>
                      <label className="form-control w-full max-w-lg">
                        <span className="label-text text-[10px]">Feature</span>
                        <input
                          className="input input-bordered input-sm font-mono"
                          value={currentOp.feature}
                          disabled={!isAdmin}
                          onChange={(e) => updateOperation(currentOp.name, { feature: e.target.value })}
                        />
                      </label>

                      <div>
                        <div className="text-[10px] font-semibold mb-1">Required parameters</div>
                        <p className="opacity-50 text-[10px] mb-2">
                          Everything else is optional by default. Add names that must be present for this operation.
                        </p>
                        <div className="flex flex-wrap gap-1 mb-2">
                          {currentOp.required_parameters.map((p) => (
                            <span key={p} className="badge badge-error gap-1 pr-0">
                              {p}
                              {isAdmin && (
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-xs btn-square min-h-0 h-5 w-5"
                                  title="Remove"
                                  onClick={() =>
                                    updateOperation(currentOp.name, {
                                      required_parameters: currentOp.required_parameters.filter((x) => x !== p),
                                    })
                                  }
                                >
                                  ×
                                </button>
                              )}
                            </span>
                          ))}
                        </div>
                        {isAdmin && (
                          <div className="flex flex-wrap gap-2 items-center">
                            <select
                              className="select select-bordered select-xs"
                              defaultValue=""
                              onChange={(e) => {
                                const v = e.target.value;
                                if (!v || currentOp.required_parameters.includes(v)) return;
                                updateOperation(currentOp.name, {
                                  required_parameters: [...currentOp.required_parameters, v],
                                });
                                e.target.selectedIndex = 0;
                              }}
                            >
                              <option value="">+ From shared field…</option>
                              {sharedNames
                                .filter((n) => !currentOp.required_parameters.includes(n))
                                .map((n) => (
                                  <option key={n} value={n}>
                                    {n}
                                  </option>
                                ))}
                            </select>
                            <input
                              className="input input-bordered input-xs w-40 font-mono"
                              placeholder="custom name"
                              value={newParam}
                              onChange={(e) => setNewParam(e.target.value)}
                            />
                            <button
                              type="button"
                              className="btn btn-xs btn-outline"
                              onClick={() => {
                                const v = newParam.trim();
                                if (!v || currentOp.required_parameters.includes(v)) return;
                                updateOperation(currentOp.name, {
                                  required_parameters: [...currentOp.required_parameters, v],
                                });
                                setNewParam('');
                              }}
                            >
                              Add
                            </button>
                          </div>
                        )}
                      </div>

                      <label className="form-control w-full">
                        <span className="label-text text-[10px]">
                          Dependency / cross-check rules (one per line)
                        </span>
                        <textarea
                          className="textarea textarea-bordered textarea-sm font-mono min-h-[100px]"
                          readOnly={!isAdmin}
                          value={currentOp.rules.join('\n')}
                          onChange={(e) =>
                            updateOperation(currentOp.name, {
                              rules: e.target.value.split('\n'),
                            })
                          }
                        />
                      </label>

                      {isAdmin && (
                        <button
                          type="button"
                          className="btn btn-sm btn-primary gap-1"
                          disabled={!isDirty || saving}
                          onClick={() => void saveDraft()}
                        >
                          <Save className="h-3.5 w-3.5" />
                          Commit operation (save file)
                        </button>
                      )}
                    </>
                  )}

                  {!currentOp && (
                    <p className="opacity-60">No operations in this category. Add one with “Add operation”.</p>
                  )}
                </>
              )}
            </div>
          )}

          {view === 'preview' && (
            <div className="flex flex-col grow min-h-0 p-2 gap-2">
              <textarea
                className="textarea textarea-bordered textarea-sm w-full font-mono min-h-[72px] grow max-h-40"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Paste a network automation prompt…"
              />
              <button
                type="button"
                className="btn btn-sm btn-primary gap-1 w-fit"
                disabled={running || !prompt.trim()}
                onClick={() => void runPreview()}
              >
                <Play className="h-3.5 w-3.5" />
                {running ? 'Parsing…' : 'Run parser'}
              </button>
              {parseErr && <p className="text-error">{parseErr}</p>}
              {result && (
                <div className="flex flex-col gap-2 grow min-h-0 overflow-hidden">
                  <div className="shrink-0 space-y-1">
                    <h3 className="font-semibold">Pipeline trace</h3>
                    <pre className="bg-base-300/30 p-2 rounded overflow-x-auto max-h-32 text-[10px] select-text">
                      {JSON.stringify(result.pipeline, null, 2)}
                    </pre>
                  </div>
                  <div className="grow min-h-0 flex flex-col">
                    <h3 className="font-semibold shrink-0">Parsed intent</h3>
                    <pre className="bg-base-300/30 p-2 rounded overflow-auto grow text-[10px] select-text">
                      {JSON.stringify(result.parsed_intent, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default IntentParseWorkbench;
