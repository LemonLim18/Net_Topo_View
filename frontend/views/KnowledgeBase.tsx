/// <reference types="vite/client" />
import React, { useState, useEffect, useRef, useCallback } from 'react';
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
} from 'lucide-react';
import { ragApi, type RagDocument, type RagStatus } from '../src/services/api';
import { Toast } from '../src/utils/swal';

// ─── Constants ───────────────────────────────────────────────────────────────

const DOC_TYPES = [
  { value: 'vendor_doc',      label: 'Vendor Documentation' },
  { value: 'cli_reference',   label: 'CLI Reference' },
  { value: 'sop',             label: 'Standard Operating Procedure' },
  { value: 'troubleshooting', label: 'Troubleshooting Guide' },
  { value: 'best_practice',   label: 'Best Practice' },
];

const VENDORS = [
  { value: 'cisco',   label: 'Cisco' },
  { value: 'huawei',  label: 'Huawei' },
  { value: 'juniper', label: 'Juniper' },
  { value: 'aruba',   label: 'Aruba' },
  { value: 'general', label: 'General' },
];

const VENDOR_BADGE: Record<string, string> = {
  cisco:   'badge-primary',
  huawei:  'badge-secondary',
  juniper: 'badge-accent',
  aruba:   'badge-info',
  general: 'badge-ghost',
};

const DOCTYPE_BADGE: Record<string, string> = {
  vendor_doc:      'badge-warning',
  cli_reference:   'badge-info',
  sop:             'badge-success',
  troubleshooting: 'badge-error',
  best_practice:   'badge-secondary',
};

const ACCEPTED_EXTS = '.pdf,.txt,.md,.docx,.html,.htm';

// ─── Component ───────────────────────────────────────────────────────────────

const KnowledgeBase: React.FC = () => {
  // Status
  const [status, setStatus] = useState<RagStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  // Documents
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Upload form
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [docType, setDocType] = useState('vendor_doc');
  const [vendor, setVendor] = useState('general');
  const [docName, setDocName] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Rebuild
  const [rebuilding, setRebuilding] = useState(false);
  const [_rebuildResult, setRebuildResult] = useState<{ files_processed: number; total_chunks: number; errors: string[] } | null>(null);

  // Filter
  const [filterVendor, setFilterVendor] = useState('');
  const [filterType, setFilterType] = useState('');

  // ── Data loading ──────────────────────────────────────────────────────────

  const loadAll = useCallback(async () => {
    setStatusLoading(true);
    setDocsLoading(true);
    try {
      const [s, d] = await Promise.all([ragApi.getStatus(), ragApi.listDocuments()]);
      setStatus(s);
      setDocs(d);
    } catch (err: any) {
      Toast.error('Load Failed', err.message || 'Could not load knowledge base data');
    } finally {
      setStatusLoading(false);
      setDocsLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ── Upload ────────────────────────────────────────────────────────────────

  const handleUpload = async () => {
    if (!uploadFile) return;
    setUploading(true);
    try {
      const result = await ragApi.uploadDocument(
        uploadFile,
        docType,
        vendor,
        docName.trim() || undefined,
      );
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

  // ── Delete ────────────────────────────────────────────────────────────────

  const handleDelete = async (doc: RagDocument) => {
    setDeletingId(doc.doc_id);
    try {
      const result = await ragApi.deleteDocument(doc.doc_id);
      Toast.success('Deleted', result.message);
      setDocs(prev => prev.filter(d => d.doc_id !== doc.doc_id));
      loadAll();
    } catch (err: any) {
      Toast.error('Delete Failed', err.message);
    } finally {
      setDeletingId(null);
    }
  };

  // ── Rebuild ───────────────────────────────────────────────────────────────

  const handleRebuild = async () => {
    setRebuilding(true);
    setRebuildResult(null);
    try {
      const result = await ragApi.rebuild();
      setRebuildResult(result);
      Toast.success('Rebuild Started', 'Re-ingestion runs in the background. Refresh status below for chunk counts.');
      await loadAll();
    } catch (err: any) {
      Toast.error('Rebuild Failed', err.message);
    } finally {
      setRebuilding(false);
    }
  };

  // ── Filtered docs ─────────────────────────────────────────────────────────

  const filteredDocs = docs.filter(d => {
    if (filterVendor && d.vendor !== filterVendor) return false;
    if (filterType && d.doc_type !== filterType) return false;
    return true;
  });

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto space-y-6">

        {/* ── Header ── */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <BookOpen className="h-6 w-6 text-primary" />
              Knowledge Base
            </h1>
            <p className="text-base-content/60 text-sm mt-1">
              Upload vendor documentation, SOPs, and CLI references for the AI agent to consult.
            </p>
          </div>
          <div className="flex gap-2">
            {/* <button
              className={`btn btn-warning btn-sm gap-2 shrink-0 ${rebuilding ? 'loading' : ''}`}
              onClick={handleRebuild}
              disabled={rebuilding || status?.available === false}
            >
              {!rebuilding && <RefreshCw className="h-4 w-4" />}
              {rebuilding ? 'Rebuilding…' : 'Rebuild'}
            </button> */}
            <button
              className={`btn btn-ghost btn-sm gap-2 ${(statusLoading || docsLoading) ? 'loading' : ''}`}
              onClick={loadAll}
              disabled={statusLoading || docsLoading}
            >
              {!(statusLoading || docsLoading) && <RefreshCw className="h-4 w-4" />}
              Refresh
            </button>
            <button
              className={`btn btn-warning btn-sm gap-2 shrink-0 ${rebuilding ? 'loading' : ''}`}
              onClick={handleRebuild}
              disabled={rebuilding || status?.available === false}
            >
              {!rebuilding && <RefreshCw className="h-4 w-4" />}
              {rebuilding ? 'Rebuilding…' : 'Rebuild'}
            </button>
          </div>
        </div>

        {/* ── Info Banner ── */}
        {/* <div className="alert alert-info text-xs py-2 px-4">
          <Info className="h-4 w-4 shrink-0" />
          <span>
            Uploaded files are saved to <code className="bg-info/20 px-1 rounded">backend/knowledge_base/</code> and
            remain available for future rebuilds. Deleting a document only removes its vector chunks — the
            source file is preserved.
          </span>
        </div> */}

        {/* ── Status Cards ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard
            label="Status"
            loading={statusLoading}
            value={status?.available ? 'Online' : 'Offline'}
            icon={status?.available
              ? <CheckCircle className="h-5 w-5 text-success" />
              : <AlertTriangle className="h-5 w-5 text-error" />}
            sub={status?.available ? 'ChromaDB ready' : 'ChromaDB unavailable'}
          />
          <StatCard
            label="Documents"
            loading={statusLoading}
            value={status?.total_documents ?? '—'}
            icon={<FileText className="h-5 w-5 text-primary" />}
            sub="ingested files"
          />
          <StatCard
            label="Chunks"
            loading={statusLoading}
            value={status?.total_chunks ?? '—'}
            icon={<Database className="h-5 w-5 text-secondary" />}
            sub="vector embeddings"
          />
          <StatCard
            label="Store Path"
            loading={statusLoading}
            value="chroma_db/"
            icon={<Info className="h-5 w-5 text-base-content/40" />}
            sub={status?.chroma_path ? 'persistent SQLite' : '—'}
            mono
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* ── Upload Form ── */}
          <div className="card bg-base-100 shadow">
            <div className="card-body p-5 space-y-4">
              <h2 className="font-semibold text-sm flex items-center gap-2">
                <Upload className="h-4 w-4 text-primary" />
                Upload Document
              </h2>

              {/* File picker */}
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs">File</span></label>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED_EXTS}
                  className="file-input file-input-bordered file-input-sm w-full"
                  onChange={e => setUploadFile(e.target.files?.[0] ?? null)}
                />
                <label className="label py-0">
                  <span className="label-text-alt opacity-50">PDF · DOCX · HTML · TXT · MD</span>
                </label>
              </div>

              {/* Doc type */}
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs">Document Type</span></label>
                <select
                  className="select select-bordered select-sm"
                  value={docType}
                  onChange={e => setDocType(e.target.value)}
                >
                  {DOC_TYPES.map(t => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>

              {/* Vendor */}
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs">Vendor</span></label>
                <select
                  className="select select-bordered select-sm"
                  value={vendor}
                  onChange={e => setVendor(e.target.value)}
                >
                  {VENDORS.map(v => (
                    <option key={v.value} value={v.value}>{v.label}</option>
                  ))}
                </select>
              </div>

              {/* Optional name */}
              <div className="form-control">
                <label className="label py-1">
                  <span className="label-text text-xs">Display Name</span>
                  <span className="label-text-alt opacity-50">optional</span>
                </label>
                <input
                  type="text"
                  className="input input-bordered input-sm"
                  placeholder={uploadFile?.name ?? 'e.g. Cisco OSPF Guide'}
                  value={docName}
                  onChange={e => setDocName(e.target.value)}
                />
              </div>

              <button
                className={`btn btn-primary btn-sm w-full gap-2 ${uploading ? 'loading' : ''}`}
                onClick={handleUpload}
                disabled={!uploadFile || uploading || status?.available === false}
              >
                {!uploading && <Upload className="h-4 w-4" />}
                {uploading ? 'Ingesting…' : 'Upload & Ingest'}
              </button>

              {status?.available === false && (
                <p className="text-xs text-error text-center">
                  ChromaDB unavailable — check backend logs.
                </p>
              )}
            </div>
          </div>

          {/* ── Document Library ── */}
          <div className="lg:col-span-2 card bg-base-100 shadow">
            <div className="card-body p-5">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-semibold text-sm flex items-center gap-2">
                  <Database className="h-4 w-4 text-secondary" />
                  Document Library
                  {!docsLoading && (
                    <span className="badge badge-ghost badge-sm">{filteredDocs.length}</span>
                  )}
                </h2>

                {/* Filters */}
                <div className="flex gap-2">
                  <select
                    className="select select-bordered select-xs"
                    value={filterVendor}
                    onChange={e => setFilterVendor(e.target.value)}
                  >
                    <option value="">All vendors</option>
                    {VENDORS.map(v => (
                      <option key={v.value} value={v.value}>{v.label}</option>
                    ))}
                  </select>
                  <select
                    className="select select-bordered select-xs"
                    value={filterType}
                    onChange={e => setFilterType(e.target.value)}
                  >
                    <option value="">All types</option>
                    {DOC_TYPES.map(t => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              {docsLoading ? (
                <div className="flex justify-center py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-primary" />
                </div>
              ) : filteredDocs.length === 0 ? (
                <div className="text-center py-12">
                  <BookOpen className="h-10 w-10 mx-auto text-base-content/20 mb-3" />
                  <p className="text-base-content/50 text-sm">
                    {docs.length === 0
                      ? 'No documents ingested yet. Upload your first file.'
                      : 'No documents match the current filter.'}
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto max-h-[360px]">
                  <table className="table table-sm table-zebra">
                    <thead>
                      <tr>
                        <th>Document</th>
                        <th>Type</th>
                        <th>Vendor</th>
                        <th className="text-right">Chunks</th>
                        <th>Ingested</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredDocs.map(doc => (
                        <tr key={doc.doc_id}>
                          <td className="max-w-[180px]">
                            <div className="flex items-center gap-2">
                              <FileText className="h-3.5 w-3.5 text-base-content/40 shrink-0" />
                              <span className="truncate text-xs font-medium" title={doc.doc_name}>
                                {doc.doc_name}
                              </span>
                            </div>
                          </td>
                          <td>
                            <span className={`badge badge-xs ${DOCTYPE_BADGE[doc.doc_type] ?? 'badge-ghost'}`}>
                              {DOC_TYPES.find(t => t.value === doc.doc_type)?.label ?? doc.doc_type}
                            </span>
                          </td>
                          <td>
                            <span className={`badge badge-xs ${VENDOR_BADGE[doc.vendor] ?? 'badge-ghost'}`}>
                              {doc.vendor}
                            </span>
                          </td>
                          <td className="text-right font-mono text-xs">{doc.chunk_count}</td>
                          <td className="text-xs opacity-60 whitespace-nowrap">
                            {doc.ingested_at ? new Date(doc.ingested_at).toLocaleDateString() : '—'}
                          </td>
                          <td>
                            <button
                              className="btn btn-ghost btn-xs text-error"
                              onClick={() => handleDelete(doc)}
                              disabled={deletingId === doc.doc_id}
                              title="Delete all chunks for this document"
                            >
                              {deletingId === doc.doc_id
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Trash2 className="h-3.5 w-3.5" />}
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

        

      </div>
    </div>
  );
};

// ─── Helper: Stat Card ────────────────────────────────────────────────────────

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
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-base-content/50">{label}</span>
        {icon}
      </div>
      {loading ? (
        <div className="h-6 w-16 bg-base-300 animate-pulse rounded" />
      ) : (
        <div className={`text-lg font-bold ${mono ? 'font-mono text-sm' : ''}`}>{value}</div>
      )}
      <div className="text-xs text-base-content/40">{sub}</div>
    </div>
  </div>
);

export default KnowledgeBase;
