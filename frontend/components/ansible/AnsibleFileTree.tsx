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
  Pencil,
} from 'lucide-react';
import Swal from 'sweetalert2';
import { ansibleApi } from '../../src/services/api';
import type { AnsibleFileNode } from '../../typesLLM';
import ContextMenu, { type ContextMenuAction } from '../ContextMenu';
import { Confirm, Toast } from '../../src/utils/swal';

interface AnsibleFileTreeProps {
  onFileOpen: (node: AnsibleFileNode) => void;
  selectedPath: string | null;
  refreshTrigger?: number;
  /** Called after the file list is reloaded (e.g. to reload active editor tab from disk) */
  onAfterRefresh?: () => void;
}

interface TreeNodeProps {
  node: AnsibleFileNode;
  depth: number;
  onFileOpen: (node: AnsibleFileNode) => void;
  selectedPath: string | null;
  defaultOpen?: boolean;
  onNodeContextMenu: (event: React.MouseEvent, node: AnsibleFileNode) => void;
  /** When set, this node shows an inline input for renaming */
  renamingPath?: string | null;
  renamingName?: string;
  onRenameCommit?: (path: string, newName: string) => void;
  onRenameCancel?: () => void;
}

const WRITABLE_ROOTS = new Set(['playbooks', 'inventory']);
const READONLY_ROOTS = new Set(['roles', 'awx', 'reference', 'Test_Reference']);
// generated/ is virtual (AI output); files under it are writable (backend maps to playbooks|roles/generated)
const VIRTUAL_ROOTS = new Set([] as string[]);
/** Roots grouped under "Project" (playbooks, roles, inventory); awx excluded — not used unless you run AWX/Tower */
const PROJECT_ROOT_NAMES = new Set(['playbooks', 'roles', 'inventory']);

const getRoot = (path: string) => path.split('/')[0] ?? '';
const getParentDir = (path: string) => {
  const idx = path.lastIndexOf('/');
  return idx > 0 ? path.slice(0, idx) : '';
};
const isWritablePath = (path: string) =>
  WRITABLE_ROOTS.has(getRoot(path)) || path.startsWith('roles/generated/') || path.startsWith('generated/');
const isVirtualPath = (path: string) => VIRTUAL_ROOTS.has(getRoot(path));
const isReadOnlyRootPath = (path: string) => READONLY_ROOTS.has(getRoot(path));
const canMutatePath = (path: string) => isWritablePath(path) && !isVirtualPath(path) && !isReadOnlyRootPath(path);
/** Create file/folder only under playbooks, inventory, or roles/generated (not under virtual generated/) */
const canCreateUnder = (path: string) => canMutatePath(path) && !path.startsWith('generated');

const TreeNode: React.FC<TreeNodeProps> = ({
  node,
  depth,
  onFileOpen,
  selectedPath,
  defaultOpen = false,
  onNodeContextMenu,
  renamingPath = null,
  renamingName = '',
  onRenameCommit,
  onRenameCancel,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  const [editValue, setEditValue] = useState(node.name);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const isRenaming = renamingPath === node.path;

  React.useEffect(() => {
    if (isRenaming) setEditValue(renamingName || node.name);
  }, [isRenaming, renamingName, node.name]);
  React.useEffect(() => {
    if (isRenaming && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isRenaming]);

  const paddingLeft = `${8 + depth * 14}px`;
  const handleRenameKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = (e.currentTarget.value || '').trim();
      if (v && v !== node.name && onRenameCommit) onRenameCommit(node.path, v);
      else if (onRenameCancel) onRenameCancel();
    } else if (e.key === 'Escape') {
      setEditValue(node.name);
      onRenameCancel?.();
    }
  };
  const handleRenameBlur = () => {
    const v = editValue.trim();
    if (v && v !== node.name && !v.includes('/') && !v.includes('\\') && onRenameCommit)
      onRenameCommit(node.path, v);
    else
      onRenameCancel?.();
  };

  if (node.type === 'dir') {
    return (
      <li>
        {isRenaming ? (
          <div
            className="flex items-center gap-1.5 w-full px-2 py-0.5 rounded text-sm bg-base-300 border border-primary"
            style={{ paddingLeft }}
          >
            {open ? <ChevronDown className="h-3 w-3 opacity-50 shrink-0" /> : <ChevronRight className="h-3 w-3 opacity-50 shrink-0" />}
            {open ? <FolderOpen className="h-3.5 w-3.5 text-yellow-400 shrink-0" /> : <Folder className="h-3.5 w-3.5 text-yellow-400 shrink-0" />}
            <input
              ref={inputRef}
              type="text"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={handleRenameKeyDown}
              onBlur={handleRenameBlur}
              onClick={(e) => e.stopPropagation()}
              className="flex-1 min-w-0 bg-transparent border-none outline-none text-sm font-medium opacity-80 py-0"
              aria-label="Rename"
            />
          </div>
        ) : (
          <button
            className="flex items-center gap-1.5 w-full text-left px-2 py-0.5 rounded hover:bg-base-300 text-sm"
            style={{ paddingLeft }}
            onClick={() => setOpen(o => !o)}
            onContextMenu={(e) => onNodeContextMenu(e, node)}
          >
            {open ? <ChevronDown className="h-3 w-3 opacity-50 shrink-0" /> : <ChevronRight className="h-3 w-3 opacity-50 shrink-0" />}
            {open ? <FolderOpen className="h-3.5 w-3.5 text-yellow-400 shrink-0" /> : <Folder className="h-3.5 w-3.5 text-yellow-400 shrink-0" />}
            <span className="truncate font-medium opacity-80">{node.name}</span>
          </button>
        )}
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
                renamingPath={renamingPath}
                renamingName={renamingName}
                onRenameCommit={onRenameCommit}
                onRenameCancel={onRenameCancel}
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

  if (isRenaming) {
    return (
      <li>
        <div
          className="flex items-center gap-1.5 w-full px-2 py-0.5 rounded text-sm bg-base-300 border border-primary"
          style={{ paddingLeft }}
        >
          <Icon className={`h-3.5 w-3.5 shrink-0 ${iconColor}`} />
          <input
            ref={inputRef}
            type="text"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onKeyDown={handleRenameKeyDown}
            onBlur={handleRenameBlur}
            onClick={(e) => e.stopPropagation()}
            className="flex-1 min-w-0 bg-transparent border-none outline-none text-sm py-0 truncate"
            aria-label="Rename"
          />
        </div>
      </li>
    );
  }

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

const AnsibleFileTree: React.FC<AnsibleFileTreeProps> = ({ onFileOpen, selectedPath, refreshTrigger, onAfterRefresh }) => {
  const [tree, setTree] = useState<AnsibleFileNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreateMenu, setShowCreateMenu] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; actions: ContextMenuAction[] } | null>(null);
  const createMenuRef = useRef<HTMLDivElement>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const uploadTargetDirRef = useRef<string>('playbooks');

  const load = useCallback(async (runAfterRefresh?: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const filesRes = await ansibleApi.listFiles();
      const rawTree: AnsibleFileNode[] = filesRes.tree || [];
      // Friendly names for main sections; group playbooks/roles/inventory/awx under "Project"
      const withNames = rawTree.map((root) => ({
        ...root,
        name:
          root.path === 'reference'
            ? 'Reference'
            : root.path === 'generated'
              ? 'AI Output'
              : root.path === 'Test_Reference'
                ? 'Test Reference'
                : root.name,
      }));
      const referenceNode = withNames.find((r) => r.path === 'reference');
      const generatedNode = withNames.find((r) => r.path === 'generated');
      const testRefNode = withNames.find((r) => r.path === 'Test_Reference');
      const projectChildren = withNames.filter((r) => PROJECT_ROOT_NAMES.has(r.path));
      const projectNode: AnsibleFileNode | null =
        projectChildren.length > 0
          ? {
              type: 'dir',
              name: 'Project',
              path: 'project',
              children: projectChildren,
            }
          : null;
      const ansibleTree: AnsibleFileNode[] = [
        referenceNode,
        generatedNode,
        testRefNode,
        projectNode,
      ].filter(Boolean) as AnsibleFileNode[];
      setTree(ansibleTree);
      if (runAfterRefresh) onAfterRefresh?.();
    } catch (e: any) {
      setError(e.message || 'Failed to load files');
    } finally {
      setLoading(false);
    }
  }, [onAfterRefresh]);

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

  const [renamingNode, setRenamingNode] = useState<{ path: string; name: string } | null>(null);

  const startRename = useCallback((node: AnsibleFileNode) => {
    setContextMenu(null);
    setRenamingNode({ path: node.path, name: node.name });
  }, []);

  const commitRename = useCallback(
    async (path: string, newName: string) => {
      const trimmed = newName.trim();
      if (!trimmed || trimmed.includes('/') || trimmed.includes('\\')) {
        setRenamingNode(null);
        return;
      }
      const parent = getParentDir(path) || getRoot(path);
      const currentName = renamingNode?.name ?? path.split('/').pop() ?? '';
      if (trimmed === currentName) {
        setRenamingNode(null);
        return;
      }
      const toPath = `${parent}/${trimmed}`.replace(/\/+/g, '/');
      try {
        await ansibleApi.renamePath(path, toPath);
        setRenamingNode(null);
        await load();
      } catch (e: any) {
        handleApiError(e);
      }
    },
    [renamingNode, load, handleApiError]
  );

  const cancelRename = useCallback(() => setRenamingNode(null), []);

  const openContextMenu = useCallback((event: React.MouseEvent, node: AnsibleFileNode) => {
    event.preventDefault();

    const baseDir = node.type === 'dir' ? node.path : (getParentDir(node.path) || getRoot(node.path));
    const canCreate = canCreateUnder(baseDir);
    const nodeEditable = canMutatePath(node.path);

    const actions: ContextMenuAction[] = [
      {
        label: 'New File',
        icon: <FilePlus2 className="h-4 w-4" />,
        onClick: () => createFileAt(baseDir),
        disabled: !canCreate,
      },
      {
        label: 'New Folder',
        icon: <FolderPlus className="h-4 w-4" />,
        onClick: () => createFolderAt(baseDir),
        disabled: !canCreate,
      },
      {
        label: 'Upload File',
        icon: <Upload className="h-4 w-4" />,
        onClick: () => triggerUploadTo(baseDir),
        disabled: !canCreate,
      },
    ];

    const canDelete = node.type === 'file'
      ? nodeEditable
      : nodeEditable && node.path.includes('/');

    const canRename = nodeEditable && node.path.includes('/');

    if (canRename) {
      actions.push({
        label: 'Rename',
        icon: <Pencil className="h-4 w-4" />,
        onClick: () => startRename(node),
        disabled: false,
      });
    }

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
  }, [createFileAt, createFolderAt, deletePath, triggerUploadTo, startRename]);

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
                  <button
                    disabled={!canCreateUnder(preferredDir())}
                    onClick={() => { setShowCreateMenu(false); createFileAt(preferredDir()); }}
                  >
                    <FilePlus2 className="h-3.5 w-3.5" /> New File
                  </button>
                </li>
                <li>
                  <button
                    disabled={!canCreateUnder(preferredDir())}
                    onClick={() => { setShowCreateMenu(false); createFolderAt(preferredDir()); }}
                  >
                    <FolderPlus className="h-3.5 w-3.5" /> New Folder
                  </button>
                </li>
                <li>
                  <button
                    disabled={!canCreateUnder(preferredDir())}
                    onClick={() => { setShowCreateMenu(false); triggerUploadTo(preferredDir()); }}
                  >
                    <Upload className="h-3.5 w-3.5" /> Upload File
                  </button>
                </li>
              </ul>
            )}
          </div>
          <button
            className="btn btn-ghost btn-xs"
            onClick={() => load(true)}
            disabled={loading}
            title="Refresh file list and reload open file from disk"
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
              renamingPath={renamingNode?.path ?? null}
              renamingName={renamingNode?.name ?? ''}
              onRenameCommit={commitRename}
              onRenameCancel={cancelRename}
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
