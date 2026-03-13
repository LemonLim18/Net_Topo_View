import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Folder,
  FolderOpen,
  FileCode2,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  FileJson,
  Plus,
  FilePlus2,
  FolderPlus,
  Upload,
  Trash2,
} from 'lucide-react';
import Swal from 'sweetalert2';
import { ansibleApi, configApi } from '../../src/services/api';
import type { AnsibleFileNode } from '../../typesLLM';
import ContextMenu, { type ContextMenuAction } from '../ContextMenu';
import { Confirm, Toast } from '../../src/utils/swal';

interface AnsibleFileTreeProps {
  onFileOpen: (node: AnsibleFileNode) => void;
  selectedPath: string | null;
  refreshTrigger?: number;
}

interface TreeNodeProps {
  node: AnsibleFileNode;
  depth: number;
  onFileOpen: (node: AnsibleFileNode) => void;
  selectedPath: string | null;
  defaultOpen?: boolean;
  onNodeContextMenu: (event: React.MouseEvent, node: AnsibleFileNode) => void;
}

const WRITABLE_ROOTS = new Set(['playbooks', 'inventory']);
const READONLY_ROOTS = new Set(['roles', 'awx']);
const VIRTUAL_ROOTS = new Set(['templates', 'schemas']);

const getRoot = (path: string) => path.split('/')[0] ?? '';
const getParentDir = (path: string) => {
  const idx = path.lastIndexOf('/');
  return idx > 0 ? path.slice(0, idx) : '';
};
const isWritablePath = (path: string) => WRITABLE_ROOTS.has(getRoot(path)) || path.startsWith('roles/generated/');
const isVirtualPath = (path: string) => VIRTUAL_ROOTS.has(getRoot(path));
const isReadOnlyRootPath = (path: string) => READONLY_ROOTS.has(getRoot(path));
const canMutatePath = (path: string) => isWritablePath(path) && !isVirtualPath(path) && !isReadOnlyRootPath(path);

const TreeNode: React.FC<TreeNodeProps> = ({
  node,
  depth,
  onFileOpen,
  selectedPath,
  defaultOpen = false,
  onNodeContextMenu,
}) => {
  const [open, setOpen] = useState(defaultOpen);

  if (node.type === 'dir') {
    return (
      <li>
        <button
          className="flex items-center gap-1.5 w-full text-left px-2 py-0.5 rounded hover:bg-base-300 text-sm"
          style={{ paddingLeft: `${8 + depth * 14}px` }}
          onClick={() => setOpen(o => !o)}
          onContextMenu={(e) => onNodeContextMenu(e, node)}
        >
          {open ? <ChevronDown className="h-3 w-3 opacity-50 shrink-0" /> : <ChevronRight className="h-3 w-3 opacity-50 shrink-0" />}
          {open ? <FolderOpen className="h-3.5 w-3.5 text-yellow-400 shrink-0" /> : <Folder className="h-3.5 w-3.5 text-yellow-400 shrink-0" />}
          <span className="truncate font-medium opacity-80">{node.name}</span>
        </button>
        {open && node.children && (
          <ul>
            {node.children.map(child => (
              <TreeNode
                key={child.path}
                node={child}
                depth={depth + 1}
                onFileOpen={onFileOpen}
                selectedPath={selectedPath}
                onNodeContextMenu={onNodeContextMenu}
              />
            ))}
          </ul>
        )}
      </li>
    );
  }

  const isYaml = node.name.endsWith('.yml') || node.name.endsWith('.yaml');
  const isJ2   = node.name.endsWith('.j2');
  const isJson = node.name.endsWith('.json');
  const isSel  = selectedPath === node.path;

  const iconColor = isSel
    ? ''
    : isYaml ? 'text-blue-400'
    : isJ2   ? 'text-orange-400'
    : isJson ? 'text-emerald-400'
    : 'text-base-content opacity-50';

  const Icon = isJson ? FileJson : FileCode2;

  return (
    <li>
      <button
        draggable
        onDragStart={e => {
          e.dataTransfer.effectAllowed = 'copy';
          e.dataTransfer.setData('application/ansible-path', node.path);
          e.dataTransfer.setData('text/plain', node.name);
        }}
        className={`flex items-center gap-1.5 w-full text-left px-2 py-0.5 rounded text-sm transition-colors cursor-grab active:cursor-grabbing ${
          isSel ? 'bg-primary text-primary-content' : 'hover:bg-base-300'
        }`}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => onFileOpen(node)}
        onContextMenu={(e) => onNodeContextMenu(e, node)}
        title={`${node.path} — drag to chat to attach`}
      >
        <Icon className={`h-3.5 w-3.5 shrink-0 ${iconColor}`} />
        <span className="truncate">{node.name}</span>
      </button>
    </li>
  );
};

const AnsibleFileTree: React.FC<AnsibleFileTreeProps> = ({ onFileOpen, selectedPath, refreshTrigger }) => {
  const [tree, setTree] = useState<AnsibleFileNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreateMenu, setShowCreateMenu] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; actions: ContextMenuAction[] } | null>(null);
  const createMenuRef = useRef<HTMLDivElement>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const uploadTargetDirRef = useRef<string>('playbooks');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [filesRes, templates] = await Promise.all([
        ansibleApi.listFiles(),
        configApi.listTemplates().catch(() => [] as { vendor: string; feature: string; has_undo: boolean; schema_exists: boolean }[]),
      ]);

      const ansibleTree: AnsibleFileNode[] = filesRes.tree || [];

      // Build virtual "templates/" and "schemas/" nodes from Config Engine data
      if (templates.length > 0) {
        const byVendor: Record<string, AnsibleFileNode[]> = {};
        const schemaChildren: AnsibleFileNode[] = [];
        const seenSchemas = new Set<string>();

        for (const t of templates) {
          // templates/{vendor}/{feature}.j2
          if (!byVendor[t.vendor]) byVendor[t.vendor] = [];
          byVendor[t.vendor].push({
            type: 'file',
            name: `${t.feature}.j2`,
            path: `templates/${t.vendor}/${t.feature}.j2`,
          });

          // schemas/{feature}.json  (deduplicated)
          if (t.schema_exists && !seenSchemas.has(t.feature)) {
            seenSchemas.add(t.feature);
            schemaChildren.push({
              type: 'file',
              name: `${t.feature}.json`,
              path: `schemas/${t.feature}.json`,
            });
          }
        }

        const vendorDirs: AnsibleFileNode[] = Object.entries(byVendor)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([vendor, files]) => ({
            type: 'dir' as const,
            name: vendor,
            path: `templates/${vendor}`,
            children: files.sort((a, b) => a.name.localeCompare(b.name)),
          }));

        ansibleTree.push({
          type: 'dir',
          name: 'templates',
          path: 'templates',
          children: vendorDirs,
        });

        if (schemaChildren.length > 0) {
          ansibleTree.push({
            type: 'dir',
            name: 'schemas',
            path: 'schemas',
            children: schemaChildren.sort((a, b) => a.name.localeCompare(b.name)),
          });
        }
      }

      setTree(ansibleTree);
    } catch (e: any) {
      setError(e.message || 'Failed to load files');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load, refreshTrigger]);

  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if (!createMenuRef.current) return;
      if (!createMenuRef.current.contains(e.target as Node)) {
        setShowCreateMenu(false);
      }
    };
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, []);

  const handleApiError = useCallback((e: any) => {
    const msg = e?.message || 'Operation failed';
    if (e?.status === 403) {
      setError(`Permission denied: ${msg}`);
      alert(`Permission denied: ${msg}`);
      return;
    }
    setError(msg);
  }, []);

  const askName = useCallback(async (title: string, placeholder: string) => {
    const res = await Swal.fire({
      title,
      input: 'text',
      inputPlaceholder: placeholder,
      showCancelButton: true,
      confirmButtonText: 'Create',
      cancelButtonText: 'Cancel',
      buttonsStyling: false,
      customClass: {
        popup: '!bg-base-100 !text-base-content !rounded-xl !shadow-2xl',
        title: '!text-base-content',
        htmlContainer: '!text-base-content/80',
        input: 'input input-bordered w-full !bg-base-200 !text-base-content',
        confirmButton: 'btn btn-primary btn-sm',
        cancelButton: 'btn btn-ghost btn-sm',
      },
      reverseButtons: true,
      didClose: () => {
        try { document.body.style.overflow = ''; } catch { /* ignore */ }
      },
    });

    const value = (res.value ?? '').toString().trim();
    if (!res.isConfirmed || !value) return null;
    return value;
  }, []);

  const preferredDir = useCallback(() => {
    if (!selectedPath) return 'playbooks';
    if (isWritablePath(selectedPath)) {
      return selectedPath.includes('.') ? getParentDir(selectedPath) || getRoot(selectedPath) : selectedPath;
    }
    return 'playbooks';
  }, [selectedPath]);

  const createFileAt = useCallback(async (baseDir: string) => {
    if (!canMutatePath(baseDir)) return;
    const name = await askName('New File', 'e.g. main.yml');
    if (!name) return;
    const filePath = `${baseDir}/${name}`.replace(/\/+/g, '/');
    try {
      await ansibleApi.createFile(filePath, '');
      await load();
      Toast.successEmphasis('File created', filePath);
    } catch (e: any) {
      handleApiError(e);
    }
  }, [askName, handleApiError, load]);

  const createFolderAt = useCallback(async (baseDir: string) => {
    if (!canMutatePath(baseDir)) return;
    const name = await askName('New Folder', 'e.g. site-west');
    if (!name) return;
    const folderPath = `${baseDir}/${name}`.replace(/\/+/g, '/');
    try {
      await ansibleApi.createFolder(folderPath);
      await load();
      Toast.successEmphasis('Folder created', folderPath);
    } catch (e: any) {
      handleApiError(e);
    }
  }, [askName, handleApiError, load]);

  const deletePath = useCallback(async (path: string, isDir: boolean) => {
    if (!canMutatePath(path)) return;
    const ok = await Confirm.danger(
      `Delete ${isDir ? 'folder' : 'file'}?`,
      path,
    );
    if (!ok) return;
    try {
      await ansibleApi.deletePath(path);
      await load();
      Toast.successEmphasis(isDir ? 'Folder deleted' : 'File deleted', path);
    } catch (e: any) {
      handleApiError(e);
    }
  }, [handleApiError, load]);

  const triggerUploadTo = useCallback((baseDir: string) => {
    if (!canMutatePath(baseDir)) return;
    uploadTargetDirRef.current = baseDir;
    uploadInputRef.current?.click();
  }, []);

  const handleUploadFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const baseDir = uploadTargetDirRef.current || 'playbooks';
    if (!canMutatePath(baseDir)) return;

    try {
      let uploaded = 0;
      for (const file of Array.from(files)) {
        const text = await file.text();
        const target = `${baseDir}/${file.name}`.replace(/\/+/g, '/');
        try {
          await ansibleApi.createFile(target, text);
          uploaded += 1;
        } catch (e: any) {
          if (e?.status === 409) {
            await ansibleApi.writeFile(target, text);
            uploaded += 1;
          } else {
            throw e;
          }
        }
      }
      await load();
      Toast.successEmphasis('Upload complete', `${uploaded} file(s) uploaded`);
    } catch (e: any) {
      handleApiError(e);
    }
  }, [handleApiError, load]);

  const openContextMenu = useCallback((event: React.MouseEvent, node: AnsibleFileNode) => {
    event.preventDefault();

    const baseDir = node.type === 'dir' ? node.path : (getParentDir(node.path) || getRoot(node.path));
    const editable = canMutatePath(baseDir);
    const nodeEditable = canMutatePath(node.path);

    const actions: ContextMenuAction[] = [
      {
        label: 'New File',
        icon: <FilePlus2 className="h-4 w-4" />,
        onClick: () => createFileAt(baseDir),
        disabled: !editable,
      },
      {
        label: 'New Folder',
        icon: <FolderPlus className="h-4 w-4" />,
        onClick: () => createFolderAt(baseDir),
        disabled: !editable,
      },
      {
        label: 'Upload File',
        icon: <Upload className="h-4 w-4" />,
        onClick: () => triggerUploadTo(baseDir),
        disabled: !editable,
      },
    ];

    const canDelete = node.type === 'file'
      ? nodeEditable
      : nodeEditable && node.path.includes('/');

    actions.push({
      label: node.type === 'dir' ? 'Delete Folder' : 'Delete File',
      icon: <Trash2 className="h-4 w-4" />,
      onClick: () => deletePath(node.path, node.type === 'dir'),
      disabled: !canDelete,
    });

    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      actions,
    });
  }, [createFileAt, createFolderAt, deletePath, triggerUploadTo]);

  return (
    <div className="flex flex-col h-full bg-base-100 border-r border-base-300">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-base-200 shrink-0">
        <span className="text-xs font-bold uppercase tracking-wider opacity-60">Ansible Files</span>
        <div className="flex items-center gap-1" ref={createMenuRef}>
          <div className="relative">
            <button
              className="btn btn-ghost btn-xs"
              onClick={() => setShowCreateMenu(v => !v)}
              title="Create"
            >
              <Plus className="h-3 w-3" />
            </button>
            {showCreateMenu && (
              <ul className="menu menu-xs absolute right-0 mt-1 bg-base-200 border border-base-300 rounded-box shadow-xl z-40 w-36">
                <li>
                  <button onClick={() => { setShowCreateMenu(false); createFileAt(preferredDir()); }}>
                    <FilePlus2 className="h-3.5 w-3.5" /> New File
                  </button>
                </li>
                <li>
                  <button onClick={() => { setShowCreateMenu(false); createFolderAt(preferredDir()); }}>
                    <FolderPlus className="h-3.5 w-3.5" /> New Folder
                  </button>
                </li>
                <li>
                  <button onClick={() => { setShowCreateMenu(false); triggerUploadTo(preferredDir()); }}>
                    <Upload className="h-3.5 w-3.5" /> Upload File
                  </button>
                </li>
              </ul>
            )}
          </div>
          <button
            className="btn btn-ghost btn-xs"
            onClick={load}
            disabled={loading}
            title="Refresh"
          >
            <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Tree */}
      <div className="grow overflow-y-auto py-1">
        {error && (
          <div className="px-3 py-2 text-xs text-error opacity-80">{error}</div>
        )}
        {!error && tree.length === 0 && !loading && (
          <div className="px-3 py-4 text-xs opacity-40 text-center">No files found</div>
        )}
        <ul className="menu menu-xs p-0 w-full">
          {tree.map(node => (
            <TreeNode
              key={node.path}
              node={node}
              depth={0}
              onFileOpen={onFileOpen}
              selectedPath={selectedPath}
              defaultOpen={true}
              onNodeContextMenu={openContextMenu}
            />
          ))}
        </ul>
      </div>

      <input
        ref={uploadInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          handleUploadFiles(e.target.files);
          e.target.value = '';
        }}
      />

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          actions={contextMenu.actions}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
};

export default AnsibleFileTree;
