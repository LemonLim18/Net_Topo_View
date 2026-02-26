import React, { useEffect, useState, useCallback } from 'react';
import { Folder, FolderOpen, FileCode2, RefreshCw, ChevronRight, ChevronDown, FileJson } from 'lucide-react';
import { ansibleApi, configApi } from '../../src/services/api';
import type { AnsibleFileNode } from '../../typesLLM';

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
}

const TreeNode: React.FC<TreeNodeProps> = ({ node, depth, onFileOpen, selectedPath, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);

  if (node.type === 'dir') {
    return (
      <li>
        <button
          className="flex items-center gap-1.5 w-full text-left px-2 py-0.5 rounded hover:bg-base-300 text-sm"
          style={{ paddingLeft: `${8 + depth * 14}px` }}
          onClick={() => setOpen(o => !o)}
        >
          {open ? <ChevronDown className="h-3 w-3 opacity-50 flex-shrink-0" /> : <ChevronRight className="h-3 w-3 opacity-50 flex-shrink-0" />}
          {open ? <FolderOpen className="h-3.5 w-3.5 text-yellow-400 flex-shrink-0" /> : <Folder className="h-3.5 w-3.5 text-yellow-400 flex-shrink-0" />}
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
        title={`${node.path} — drag to chat to attach`}
      >
        <Icon className={`h-3.5 w-3.5 flex-shrink-0 ${iconColor}`} />
        <span className="truncate">{node.name}</span>
      </button>
    </li>
  );
};

const AnsibleFileTree: React.FC<AnsibleFileTreeProps> = ({ onFileOpen, selectedPath, refreshTrigger }) => {
  const [tree, setTree] = useState<AnsibleFileNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="flex flex-col h-full bg-base-100 border-r border-base-300">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-base-200 flex-shrink-0">
        <span className="text-xs font-bold uppercase tracking-wider opacity-60">Ansible Files</span>
        <button
          className="btn btn-ghost btn-xs"
          onClick={load}
          disabled={loading}
          title="Refresh"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Tree */}
      <div className="flex-grow overflow-y-auto py-1">
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
            />
          ))}
        </ul>
      </div>
    </div>
  );
};

export default AnsibleFileTree;
