/// <reference types="vite/client" />
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  BookOpen,
  Upload,
  Trash2,
  RefreshCw,
  Database,
  FileText,
  CheckCircle,
  AlertTriangle,
  Loader2,
  Info,
  Eye,
  ArrowUpDown,
} from 'lucide-react';
import Swal from 'sweetalert2';
import { ragApi, type RagDocument, type RagStatus } from '../src/services/api';
import { Toast, Confirm } from '../src/utils/swal';

const DOC_TYPES = [
  { value: 'ansible_reference', label: 'Ansible Reference' },
  { value: 'live_ops_state', label: 'Live Operational State' },
  { value: 'planned_inventory', label: 'Planned Inventory' },
];

const VENDORS = [
  { value: 'cisco', label: 'Cisco' },
  { value: 'huawei', label: 'Huawei' },
  { value: 'juniper', label: 'Juniper' },
  { value: 'aruba', label: 'Aruba' },
  { value: 'general', label: 'General' },
  { value: 'multi_vendor', label: 'Multi Vendor' },
  { value: 'cisco_ios', label: 'Cisco IOS' },
  { value: 'juniper_junos', label: 'Juniper Junos' },
  { value: 'huawei_vrp', label: 'Huawei VRP' },
];

const VENDOR_BADGE: Record<string, string> = {
  cisco: 'badge-primary',
  huawei: 'badge-secondary',
  juniper: 'badge-accent',
  aruba: 'badge-info',
  general: 'badge-ghost',
  multi_vendor: 'badge-ghost',
  cisco_ios: 'badge-primary',
  juniper_junos: 'badge-accent',
  huawei_vrp: 'badge-secondary',
};

const DOCTYPE_BADGE: Record<string, string> = {
  ansible_reference: 'badge-primary badge-outline',
  live_ops_state: 'badge-accent',
  planned_inventory: 'badge-info',
};

const ACCEPTED_EXTS = '.pdf,.txt,.md,.docx,.html,.htm';
const ANSIBLE_DELETE_PASSWORD = (import.meta as any).env?.VITE_ANSIBLE_REFERENCE_DELETE_PASSWORD || 'ansible-ref-lock';

type SortField = 'doc_name' | 'doc_type' | 'vendor' | 'chunk_count' | 'ingested_at';
type SortDir = 'asc' | 'desc';

const KnowledgeBase: React.FC = () => {
  const [status, setStatus] = useState<RagStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [docType, setDocType] = useState('ansible_reference');
  const [vendor, setVendor] = useState('general');
  const [docName, setDocName] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [rebuilding, setRebuilding] = useState(false);

  const [filterVendor, setFilterVendor] = useState('');
  const [filterType, setFilterType] = useState('');

  const [sortField, setSortField] = useState<SortField>('ingested_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<{ name: string; type: string; vendor: string; text: string } | null>(null);

  // Operational Integration States
  const [showOpDashboard, setShowOpDashboard] = useState<boolean>(() => {
    return localStorage.getItem('kb_show_op_dashboard') === 'true';
  });

  const [redisBypassing, setRedisBypassing] = useState(false);
  const [redisTesting, setRedisTesting] = useState(false);
  
  // Redis configuration overrides
  const [configModalOpen, setConfigModalOpen] = useState(false);
  const [configHost, setConfigHost] = useState('');
  const [configPort, setConfigPort] = useState('');
  const [configPassword, setConfigPassword] = useState('');
  const [configSaving, setConfigSaving] = useState(false);

  const [contextPreviewLoading, setContextPreviewLoading] = useState(false);
  const [contextPreviewText, setContextPreviewText] = useState<string>('');
  
  const [stateEmbeddingLoading, setStateEmbeddingLoading] = useState(false);
  const [stateEmbeddingState, setStateEmbeddingState] = useState<{ available: boolean; last_embedded_at: string | null } | null>(null);

  const fetchStateEmbeddingStatus = useCallback(async () => {
    try {
      const res = await ragApi.stateEmbeddingStatus();
      setStateEmbeddingState(res);
    } catch (err: any) {
      console.error('Failed to load embedding status', err);
    }
  }, []);

  const handleToggleOpDashboard = (val: boolean) => {
    setShowOpDashboard(val);
    localStorage.setItem('kb_show_op_dashboard', val ? 'true' : 'false');
  };

  const handleToggleRedisBypass = async () => {
    if (!status) return;
    const currentBypassed = !!status.redis_bypassed;
    setRedisBypassing(true);
    try {
      await ragApi.setRedisBypass(!currentBypassed);
      Toast.success(
        !currentBypassed ? 'Redis Bypassed' : 'Redis Reconnected',
        !currentBypassed ? 'Forced local in-memory operational cache fallback.' : 'Operational data syncing restored.'
      );
      const s = await ragApi.getStatus();
      setStatus(s);
    } catch (err: any) {
      Toast.error('Bypass Toggle Failed', err.message || 'Could not change Redis bypass state.');
    } finally {
      setRedisBypassing(false);
    }
  };

  const handleOpenConfigModal = () => {
    setConfigHost(status?.redis_host || '192.168.1.250');
    setConfigPort(String(status?.redis_port || 6379));
    setConfigPassword('');
    setConfigModalOpen(true);
  };

  const handleSaveRedisConfig = async () => {
    if (!configHost) {
      Toast.error('Validation Error', 'Host is required');
      return;
    }
    const portNum = Number(configPort);
    if (isNaN(portNum) || portNum <= 0 || portNum > 65535) {
      Toast.error('Validation Error', 'Port must be a valid number between 1 and 65535');
      return;
    }
    
    setConfigSaving(true);
    try {
      await ragApi.updateRedisConfig(configHost, portNum, configPassword || undefined);
      Toast.success('Saved', 'Redis configuration updated and connection verified.');
      setConfigModalOpen(false);
      const s = await ragApi.getStatus();
      setStatus(s);
    } catch (err: any) {
      Toast.error('Configuration Failed', err.message || 'Could not verify Redis connection.');
    } finally {
      setConfigSaving(false);
    }
  };

  const handleTestConnection = async () => {
    setRedisTesting(true);
    try {
      const res = await ragApi.testRedisActiveConnection();
      Toast.success('Connected', res.message || 'Successfully connected to Redis cache.');
      await loadAll();
    } catch (err: any) {
      Toast.error('Connection Failed', err.message || 'Could not reach Redis server.');
      await loadAll();
    } finally {
      setRedisTesting(false);
    }
  };

  const handleFetchContextPreview = async () => {
    setContextPreviewLoading(true);
    try {
      const res = await ragApi.previewBundle({ include_telemetry: true, include_sdn: true });
      setContextPreviewText(res.prompt_text);
      Toast.success('Context Loaded', 'Retrieved latest fused LLM context bundle.');
    } catch (err: any) {
      Toast.error('Preview Failed', err.message || 'Could not build context preview.');
    } finally {
      setContextPreviewLoading(false);
    }
  };

  const handleTriggerStateEmbedding = async () => {
    setStateEmbeddingLoading(true);
    try {
      const res = await ragApi.triggerStateEmbedding();
      Toast.success('Embedding Triggered', `Re-embedded ${res.total_chunks || 0} chunks successfully.`);
      await fetchStateEmbeddingStatus();
    } catch (err: any) {
      Toast.error('Embedding Failed', err.message || 'Could not trigger re-embedding.');
    } finally {
      setStateEmbeddingLoading(false);
    }
  };

  const loadAll = useCallback(async () => {
    setStatusLoading(true);
    setDocsLoading(true);
    try {
      const [s, d] = await Promise.all([ragApi.getStatus(), ragApi.listDocuments()]);
      setStatus(s);
      setDocs(d);
      await fetchStateEmbeddingStatus();
    } catch (err: any) {
      Toast.error('Load Failed', err.message || 'Could not load knowledge base data');
    } finally {
      setStatusLoading(false);
      setDocsLoading(false);
    }
  }, [fetchStateEmbeddingStatus]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleUpload = async () => {
    if (!uploadFile) return;
    setUploading(true);
    try {
      const result = await ragApi.uploadDocument(uploadFile, docType, vendor, docName.trim() || undefined);
      Toast.success('Ingested', `${result.chunks_stored} chunks stored for "${result.doc_name}"`);
      setUploadFile(null);
      setDocName('');
      if (fileInputRef.current) fileInputRef.current.value = '';
      loadAll();
    } catch (err: any) {
      Toast.error('Upload Failed', err.message || 'Ingestion failed');
    } finally {
      setUploading(false);
    }
  };

  const promptAnsibleDeletePassword = async (): Promise<boolean> => {
    const result = await Swal.fire({
      icon: 'warning',
      title: 'Protected Deletion',
      text: 'Ansible Reference deletion requires password confirmation.',
      input: 'password',
      inputLabel: 'Enter deletion password',
      inputPlaceholder: 'Password',
      showCancelButton: true,
      confirmButtonText: 'Verify',
      cancelButtonText: 'Cancel',
      inputValidator: (value) => (!value ? 'Password is required' : undefined),
    });
    if (!result.isConfirmed) return false;
    if (result.value !== ANSIBLE_DELETE_PASSWORD) {
      Toast.error('Denied', 'Incorrect password for Ansible Reference deletion.');
      return false;
    }
    return true;
  };

  const handleDelete = async (doc: RagDocument) => {
    if (doc.doc_type === 'ansible_reference') {
      const ok = await promptAnsibleDeletePassword();
      if (!ok) return;
    }

    setDeletingId(doc.doc_id);
    try {
      const result = await ragApi.deleteDocument(doc.doc_id);
      Toast.success('Deleted', result.message);
      setDocs((prev) => prev.filter((d) => d.doc_id !== doc.doc_id));
      loadAll();
    } catch (err: any) {
      Toast.error('Delete Failed', err.message);
    } finally {
      setDeletingId(null);
    }
  };

  const handleDeleteAllNonAnsible = async () => {
    const targets = docs.filter((d) => d.doc_type !== 'ansible_reference');
    if (targets.length === 0) {
      Toast.info('Nothing to Remove', 'Only ansible_reference documents remain.');
      return;
    }

    const confirmed = await Confirm.danger(
      'Remove all non-Ansible documents?',
      `This will permanently delete all ${targets.length} non-Ansible document(s) from vector chunks and source files.`
    );
    if (!confirmed) return;

    setBulkDeleting(true);
    try {
      const res = await ragApi.deleteNonAnsibleDocuments();
      Toast.success('Cleanup Complete', res.message || 'Deleted non-Ansible documents.');
      await loadAll();
    } catch (err: any) {
      Toast.error('Cleanup Failed', err.message || 'Could not delete non-Ansible documents.');
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleRebuild = async () => {
    setRebuilding(true);
    try {
      await ragApi.rebuild();
      Toast.success('Rebuild Started', 'Re-ingestion runs in the background. Refresh status below for chunk counts.');
      await loadAll();
    } catch (err: any) {
      Toast.error('Rebuild Failed', err.message);
    } finally {
      setRebuilding(false);
    }
  };

  const openPreview = async (doc: RagDocument) => {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewDoc(null);
    try {
      const res = await ragApi.getDocumentPreview(doc.doc_id, 140);
      setPreviewDoc({
        name: res.doc_name,
        type: res.doc_type,
        vendor: res.vendor,
        text: res.preview_text || '(No preview text available)',
      });
    } catch (err: any) {
      setPreviewDoc({ name: doc.doc_name, type: doc.doc_type, vendor: doc.vendor, text: `Preview failed: ${err.message || 'Unknown error'}` });
    } finally {
      setPreviewLoading(false);
    }
  };

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortField(field);
    setSortDir('asc');
  };

  const filteredDocs = useMemo(() => {
    const base = docs.filter((d) => {
      if (filterVendor && d.vendor !== filterVendor) return false;
      if (filterType && d.doc_type !== filterType) return false;
      return true;
    });

    const dir = sortDir === 'asc' ? 1 : -1;
    return [...base].sort((a, b) => {
      if (sortField === 'chunk_count') return (a.chunk_count - b.chunk_count) * dir;
      if (sortField === 'ingested_at') return (new Date(a.ingested_at).getTime() - new Date(b.ingested_at).getTime()) * dir;
      const av = String(a[sortField] || '').toLowerCase();
      const bv = String(b[sortField] || '').toLowerCase();
      return av.localeCompare(bv) * dir;
    });
  }, [docs, filterVendor, filterType, sortField, sortDir]);

  return (
    <div className="p-6 h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <BookOpen className="h-6 w-6 text-primary" />
              Knowledge Base
            </h1>
            <p className="text-base-content/60 text-sm mt-1">Minimal, effective curation controls for LLM quality.</p>
          </div>
          <div className="flex gap-2 items-center">
            <label className="label cursor-pointer gap-2 mr-2">
              <span className="label-text text-xs font-semibold">Show Operational Integration</span>
              <input 
                type="checkbox" 
                className="toggle toggle-primary toggle-sm" 
                checked={showOpDashboard} 
                onChange={(e) => handleToggleOpDashboard(e.target.checked)} 
              />
            </label>
            <button className="btn btn-ghost btn-sm gap-2" onClick={loadAll} disabled={statusLoading || docsLoading}>
              {(statusLoading || docsLoading) ? (
                <span className="loading loading-spinner loading-xs shrink-0"></span>
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Refresh
            </button>
            <button className="btn btn-warning btn-sm gap-2 shrink-0" onClick={handleRebuild} disabled={rebuilding || status?.available === false}>
              {rebuilding ? (
                <span className="loading loading-spinner loading-xs shrink-0"></span>
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              {rebuilding ? 'Rebuilding...' : 'Rebuild'}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="Status" loading={statusLoading} value={status?.available ? 'Online' : 'Offline'} icon={status?.available ? <CheckCircle className="h-5 w-5 text-success" /> : <AlertTriangle className="h-5 w-5 text-error" />} sub={status?.available ? 'ChromaDB ready' : 'ChromaDB unavailable'} />
          <StatCard label="Documents" loading={statusLoading} value={status?.total_documents ?? '-'} icon={<FileText className="h-5 w-5 text-primary" />} sub="ingested files" />
          <StatCard label="Chunks" loading={statusLoading} value={status?.total_chunks ?? '-'} icon={<Database className="h-5 w-5 text-secondary" />} sub="vector embeddings" />
          <StatCard label="Store Path" loading={statusLoading} value="chroma_db/" icon={<Info className="h-5 w-5 text-base-content/40" />} sub={status?.chroma_path ? 'persistent SQLite' : '-'} mono />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="card bg-base-100 shadow">
            <div className="card-body p-5 space-y-4">
              <h2 className="font-semibold text-sm flex items-center gap-2"><Upload className="h-4 w-4 text-primary" /> Upload Document</h2>

              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs">File</span></label>
                <input ref={fileInputRef} type="file" accept={ACCEPTED_EXTS} className="file-input file-input-bordered file-input-sm w-full" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} />
              </div>

              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs">Document Type</span></label>
                <select className="select select-bordered select-sm" value={docType} onChange={(e) => setDocType(e.target.value)}>
                  {DOC_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>

              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs">Vendor</span></label>
                <select className="select select-bordered select-sm" value={vendor} onChange={(e) => setVendor(e.target.value)}>
                  {VENDORS.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                </select>
              </div>

              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs">Display Name</span><span className="label-text-alt opacity-50">optional</span></label>
                <input type="text" className="input input-bordered input-sm" placeholder={uploadFile?.name ?? 'e.g. reference.md'} value={docName} onChange={(e) => setDocName(e.target.value)} />
              </div>

              <button className="btn btn-primary btn-sm w-full gap-2" onClick={handleUpload} disabled={!uploadFile || uploading || status?.available === false}>
                {uploading ? (
                  <span className="loading loading-spinner loading-xs shrink-0"></span>
                ) : (
                  <Upload className="h-4 w-4" />
                )}
                {uploading ? 'Ingesting...' : 'Upload & Ingest'}
              </button>
            </div>
          </div>

          <div className="lg:col-span-2 card bg-base-100 shadow">
            <div className="card-body p-5">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-semibold text-sm flex items-center gap-2"><Database className="h-4 w-4 text-secondary" /> Document Library {!docsLoading && (<span className="badge badge-ghost badge-sm">{filteredDocs.length}</span>)}</h2>

                <div className="flex gap-2 items-center">
                  <select className="select select-bordered select-xs" value={filterVendor} onChange={(e) => setFilterVendor(e.target.value)}>
                    <option value="">All vendors</option>
                    {VENDORS.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                  </select>
                  <button 
                    className="btn btn-xs btn-outline btn-error gap-1"
                    onClick={handleDeleteAllNonAnsible}
                    disabled={bulkDeleting || docsLoading}
                    title="Purge all non-Ansible reference documents from library and source files"
                  >
                    {bulkDeleting ? (
                      <span className="loading loading-spinner loading-xs shrink-0"></span>
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                    Purge Non-Ansible
                  </button>
                </div>
              </div>

              {/* Tabbed view for Document Type filters */}
              <div className="tabs tabs-boxed flex-nowrap overflow-x-auto py-1 mb-4 gap-1 select-none no-scrollbar">
                <button 
                  className={`tab tab-xs flex-1 ${filterType === '' ? 'tab-active font-semibold' : ''}`}
                  onClick={() => setFilterType('')}
                >
                  All Documents
                </button>
                {DOC_TYPES.map((t) => (
                  <button
                    key={t.value}
                    className={`tab tab-xs whitespace-nowrap ${filterType === t.value ? 'tab-active font-semibold' : ''}`}
                    onClick={() => setFilterType(t.value)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {docsLoading ? (
                <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-primary" /></div>
              ) : filteredDocs.length === 0 ? (
                <div className="text-center py-12"><BookOpen className="h-10 w-10 mx-auto text-base-content/20 mb-3" /><p className="text-base-content/50 text-sm">No documents match the current filter.</p></div>
              ) : (
                <div className="overflow-x-auto max-h-[300px]">
                  <table className="table table-sm table-zebra">
                    <thead>
                      <tr>
                        <th><button className="btn btn-ghost btn-xs" onClick={() => toggleSort('doc_name')}>Document <ArrowUpDown className="h-3 w-3" /></button></th>
                        <th><button className="btn btn-ghost btn-xs" onClick={() => toggleSort('doc_type')}>Type <ArrowUpDown className="h-3 w-3" /></button></th>
                        <th><button className="btn btn-ghost btn-xs" onClick={() => toggleSort('vendor')}>Vendor <ArrowUpDown className="h-3 w-3" /></button></th>
                        <th className="text-right"><button className="btn btn-ghost btn-xs" onClick={() => toggleSort('chunk_count')}>Chunks <ArrowUpDown className="h-3 w-3" /></button></th>
                        <th><button className="btn btn-ghost btn-xs" onClick={() => toggleSort('ingested_at')}>Ingested <ArrowUpDown className="h-3 w-3" /></button></th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredDocs.map((doc) => (
                        <tr key={doc.doc_id}>
                          <td className="max-w-[220px]">
                            <button className="flex items-center gap-2 text-left hover:underline" onClick={() => openPreview(doc)} title="Preview document">
                              <Eye className="h-3.5 w-3.5 text-base-content/40 shrink-0" />
                              <span className="truncate text-xs font-medium" title={doc.doc_name}>{doc.doc_name}</span>
                            </button>
                          </td>
                          <td><span className={`badge badge-xs ${DOCTYPE_BADGE[doc.doc_type] ?? 'badge-ghost'}`}>{DOC_TYPES.find((t) => t.value === doc.doc_type)?.label ?? doc.doc_type}</span></td>
                          <td><span className={`badge badge-xs ${VENDOR_BADGE[doc.vendor] ?? 'badge-ghost'}`}>{doc.vendor}</span></td>
                          <td className="text-right font-mono text-xs">{doc.chunk_count}</td>
                          <td className="text-xs opacity-60 whitespace-nowrap">{doc.ingested_at ? new Date(doc.ingested_at).toLocaleString() : '-'}</td>
                          <td>
                            <button className="btn btn-ghost btn-xs text-error" onClick={() => handleDelete(doc)} disabled={deletingId === doc.doc_id} title={doc.doc_type === 'ansible_reference' ? 'Protected delete (password required)' : 'Delete document chunks'}>
                              {deletingId === doc.doc_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>

        {showOpDashboard && (
          <div className="card bg-base-100 shadow border border-primary/20 transition-all duration-300 animate-fadeIn">
            <div className="card-body p-6 space-y-6">
              <div className="flex items-center justify-between border-b border-base-200 pb-3">
                <div className="flex items-center gap-2">
                  <Database className="h-5 w-5 text-primary" />
                  <h2 className="text-lg font-bold">Operational AI & Grounding Integration</h2>
                </div>
                <div className="badge badge-accent badge-outline">Live Telemetry & Topology Grounding</div>
              </div>

              {/* Sub-grid: Redis Connection Info & Explanation, Network State Embedding Status */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                
                {/* Card 1: Redis Connection Status */}
                <div className="flex flex-col space-y-3 bg-base-200/50 p-4 rounded-xl border border-base-300">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold flex items-center gap-1.5">
                      <Database className="h-4 w-4 text-secondary" /> Redis Connection
                    </span>
                    <span className={`badge badge-sm ${status?.redis_bypassed ? 'badge-warning' : (status?.redis_online ? 'badge-success' : 'badge-error')}`}>
                      {status?.redis_bypassed ? 'Bypassed (Fallback)' : (status?.redis_online ? 'Connected' : 'Offline')}
                    </span>
                  </div>
                  
                  <div className="text-xs space-y-1">
                    <div className="flex justify-between">
                      <span className="opacity-60">Configured Host:</span>
                      <span className="font-mono font-medium">{status?.redis_host || '192.168.1.250'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="opacity-60">Configured Port:</span>
                      <span className="font-mono font-medium">{status?.redis_port || 6379}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="opacity-60">Authentication:</span>
                      <span className="font-mono font-medium">{status?.redis_has_password ? 'Password Enabled' : 'No Password'}</span>
                    </div>
                  </div>

                  {/* Connection Controls */}
                  <div className="flex gap-2 mt-1">
                    <button 
                      className="btn btn-sm btn-outline btn-primary flex-1 text-xs"
                      onClick={handleOpenConfigModal}
                    >
                      Configure Redis
                    </button>
                    <button 
                      className="btn btn-sm btn-outline flex-1 text-xs gap-1"
                      onClick={handleTestConnection}
                      disabled={redisTesting || statusLoading}
                    >
                      {redisTesting ? (
                        <span className="loading loading-spinner loading-xs shrink-0"></span>
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                      Test / Connect
                    </button>
                  </div>

                  {/* Bypass toggle button */}
                  <button 
                    className={`btn btn-sm w-full gap-2 mt-1 ${status?.redis_bypassed ? 'btn-success' : 'btn-outline btn-warning'}`}
                    onClick={handleToggleRedisBypass}
                    disabled={redisBypassing}
                  >
                    {redisBypassing ? (
                      <span className="loading loading-spinner loading-xs shrink-0"></span>
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    {status?.redis_bypassed ? 'Reconnect Redis Cache' : 'Bypass Redis (Force Fallback)'}
                  </button>

                  <div className="alert alert-info py-2 px-3 text-[11px] leading-relaxed shadow-sm bg-info/10 text-info border-info/20 mt-2">
                    <Info className="h-4 w-4 shrink-0" />
                    <span>
                      Redis serves as the short-lived operational memory configured in the backend <code>.env</code> (currently pointing to <strong>192.168.1.250</strong>). It is not prompted in the GUI because it is system-level infrastructure. If unreachable, the application falls back automatically to local in-memory storage.
                    </span>
                  </div>
                </div>

                {/* Card 2: Network State Auto-Embedding Status */}
                <div className="flex flex-col space-y-3 bg-base-200/50 p-4 rounded-xl border border-base-300">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold flex items-center gap-1.5">
                      <Database className="h-4 w-4 text-primary" /> State Embedding Status
                    </span>
                    <span className={`badge badge-sm ${stateEmbeddingState?.available ? 'badge-primary' : 'badge-ghost'}`}>
                      {stateEmbeddingState?.available ? 'Ready' : 'Not Loaded'}
                    </span>
                  </div>

                  <div className="text-xs space-y-1">
                    <div className="flex justify-between">
                      <span className="opacity-60">Last Embedded:</span>
                      <span className="font-mono font-medium">
                        {stateEmbeddingState?.last_embedded_at ? new Date(stateEmbeddingState.last_embedded_at).toLocaleString() : 'Never'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="opacity-60">ChromaDB Partition:</span>
                      <span className="font-mono font-medium">network_state</span>
                    </div>
                  </div>

                  <button
                    className="btn btn-sm btn-primary w-full gap-2 mt-2"
                    onClick={handleTriggerStateEmbedding}
                    disabled={stateEmbeddingLoading || !stateEmbeddingState?.available}
                  >
                    {stateEmbeddingLoading ? (
                      <span className="loading loading-spinner loading-xs shrink-0"></span>
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    {stateEmbeddingLoading ? 'Embedding State...' : 'Trigger Re-Embedding'}
                  </button>

                  <div className="alert alert-info py-2 px-3 text-[11px] leading-relaxed shadow-sm bg-info/10 text-info border-info/20 mt-2">
                    <Info className="h-4 w-4 shrink-0" />
                    <span>
                      RAG automatically embeds live configuration, loopbacks, interfaces, and logical routing tables into ChromaDB to provide real-time grounding context to the LLM agent.
                    </span>
                  </div>
                </div>

              </div>

              {/* Card 3: Live LLM Grounding Context Bundle Preview */}
              <div className="flex flex-col space-y-3 bg-base-200/50 p-4 rounded-xl border border-base-300">
                <div className="flex items-center justify-between border-b border-base-300 pb-2">
                  <span className="text-sm font-semibold flex items-center gap-1.5">
                    <FileText className="h-4 w-4 text-accent" /> Live Grounding Context Bundle
                  </span>
                  <button 
                    className="btn btn-xs btn-outline btn-accent gap-1"
                    onClick={handleFetchContextPreview}
                    disabled={contextPreviewLoading}
                  >
                    {contextPreviewLoading ? (
                      <span className="loading loading-spinner loading-xs shrink-0"></span>
                    ) : (
                      <RefreshCw className="h-3 w-3" />
                    )}
                    Fetch Live Bundle
                  </button>
                </div>

                <p className="text-xs text-base-content/60">
                  Inspect the physical topology, InfluxDB telemetry (CPU/memory), and live syslog logs exactly as they are formatted, weighted by freshness, and sent to the LLM for agentic reasoning:
                </p>

                <div className="relative bg-neutral text-neutral-content rounded-lg p-4 overflow-hidden border border-neutral-focus">
                  <div className="max-h-[300px] overflow-y-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
                    {contextPreviewText || (
                      <span className="opacity-50 italic">No bundle loaded. Click "Fetch Live Bundle" to generate and inspect.</span>
                    )}
                  </div>
                </div>
              </div>

            </div>
          </div>
        )}

        {previewOpen && (
          <dialog className="modal modal-open">
            <div className="modal-box max-w-5xl h-[80vh] flex flex-col">
              <h3 className="font-bold text-lg">Document Preview</h3>
              {previewLoading ? (
                <div className="flex-1 flex items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div>
              ) : (
                <>
                  <div className="text-xs opacity-70 mt-2 mb-3">{previewDoc?.name} • {previewDoc?.type} • {previewDoc?.vendor}</div>
                  <pre className="flex-1 overflow-auto p-3 bg-base-200 rounded text-xs whitespace-pre-wrap">{previewDoc?.text}</pre>
                </>
              )}
              <div className="modal-action"><button className="btn btn-sm" onClick={() => setPreviewOpen(false)}>Close</button></div>
            </div>
          </dialog>
        )}

        {configModalOpen && (
          <dialog className="modal modal-open">
            <div className="modal-box max-w-md flex flex-col space-y-4">
              <h3 className="font-bold text-lg flex items-center gap-2">
                <Database className="h-5 w-5 text-primary" />
                Configure Redis Cache
              </h3>
              <p className="text-xs text-base-content/60">
                Update connection details for the operational grounding memory layer. Changes are tested instantly and written to the backend <code>.env</code> file.
              </p>

              <div className="form-control w-full">
                <label className="label py-1">
                  <span className="label-text text-xs font-semibold">Redis Host</span>
                </label>
                <input 
                  type="text" 
                  className="input input-bordered input-sm font-mono" 
                  value={configHost} 
                  onChange={(e) => setConfigHost(e.target.value)} 
                  placeholder="e.g. 192.168.1.250"
                  disabled={configSaving}
                />
              </div>

              <div className="form-control w-full">
                <label className="label py-1">
                  <span className="label-text text-xs font-semibold">Redis Port</span>
                </label>
                <input 
                  type="number" 
                  className="input input-bordered input-sm font-mono" 
                  value={configPort} 
                  onChange={(e) => setConfigPort(e.target.value)} 
                  placeholder="e.g. 6379"
                  disabled={configSaving}
                />
              </div>

              <div className="form-control w-full">
                <label className="label py-1">
                  <span className="label-text text-xs font-semibold">Password (Optional)</span>
                </label>
                <input 
                  type="password" 
                  className="input input-bordered input-sm font-mono" 
                  value={configPassword} 
                  onChange={(e) => setConfigPassword(e.target.value)} 
                  placeholder="Leave empty if none"
                  disabled={configSaving}
                />
              </div>

              <div className="modal-action flex gap-2">
                <button 
                  className="btn btn-sm btn-ghost flex-1" 
                  onClick={() => setConfigModalOpen(false)}
                  disabled={configSaving}
                >
                  Cancel
                </button>
                <button 
                  className="btn btn-sm btn-primary flex-1"
                  onClick={handleSaveRedisConfig}
                  disabled={configSaving}
                >
                  {configSaving ? (
                    <>
                      <span className="loading loading-spinner loading-xs shrink-0 mr-1"></span>
                      Testing...
                    </>
                  ) : (
                    'Test & Save'
                  )}
                </button>
              </div>
            </div>
          </dialog>
        )}
      </div>
    </div>
  );
};

interface StatCardProps {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  sub: string;
  loading?: boolean;
  mono?: boolean;
}

const StatCard: React.FC<StatCardProps> = ({ label, value, icon, sub, loading, mono }) => (
  <div className="card bg-base-100 shadow">
    <div className="card-body p-4">
      <div className="flex items-center justify-between mb-1"><span className="text-xs text-base-content/50">{label}</span>{icon}</div>
      {loading ? <div className="h-6 w-16 bg-base-300 animate-pulse rounded" /> : <div className={`text-lg font-bold ${mono ? 'font-mono text-sm' : ''}`}>{value}</div>}
      <div className="text-xs text-base-content/40">{sub}</div>
    </div>
  </div>
);

export default KnowledgeBase;
