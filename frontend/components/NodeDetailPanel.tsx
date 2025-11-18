import React from 'react';
import type { D3Node } from '../types';
import { X } from 'lucide-react';

interface NodeDetailPanelProps {
  node: (D3Node & { id: string }) | null;
  onClose: () => void;
}

const NodeDetailPanel: React.FC<NodeDetailPanelProps> = ({ node, onClose }) => {
  if (!node) {
    return null;
  }

  // Use a transition for the panel visibility
  const panelClasses = `absolute top-0 right-0 h-full w-96 bg-base-100/90 backdrop-blur-sm shadow-2xl z-20 transform transition-transform duration-300 ease-in-out ${
    node ? 'translate-x-0' : 'translate-x-full'
  }`;

  return (
    <aside className={panelClasses}>
      <div className="p-4 h-full flex flex-col">
        <div className="flex justify-between items-center mb-4 pb-2 border-b border-base-300 shrink-0">
          <h2 className="text-xl font-bold">Node Details</h2>
          <button className="btn btn-ghost btn-sm btn-square" onClick={onClose}>
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="overflow-y-auto grow pr-2 space-y-4">
          <div>
            <p className="text-sm text-base-content/70">Node ID</p>
            <p className="font-mono text-lg">{node.id}</p>
          </div>
          <div>
            <p className="text-sm text-base-content/70">Management IP</p>
            <p className="font-mono">{node.mgmt_ip}</p>
          </div>
          
          {node.diffStatus && (
            <div>
              <p className="text-sm text-base-content/70">Status</p>
              <div className={`badge capitalize ${
                {
                  added: 'badge-warning',
                  removed: 'badge-error',
                  unchanged: 'badge-success'
                }[node.diffStatus]
              }`}>{
                {
                  added: 'Unplanned',
                  removed: 'Missing',
                  unchanged: 'OK'
                }[node.diffStatus]
              }</div>
            </div>
          )}

          <div>
            <p className="text-sm text-base-content/70 mb-1">Interfaces</p>
              {Array.isArray(node.interfaceList) && node.interfaceList.length > 0 ? (
                <ul className="list-none space-y-1 text-sm font-mono bg-base-200 p-2 rounded-box">
                  {node.interfaceList.map((ifaceObj, objIndex) => (
                    // Each ifaceObj may contain multiple interface entries; render them all
                    Object.entries(ifaceObj).map(([ifaceName, ipAddress]) => {
                      // collapse full names like 'Ethernet0/1' -> 'Et0/1', 'Loopback1' -> 'Lo1'
                      const collapse = (full: string) => {
                        if (!full) return full;
                        if (full.startsWith('Ethernet')) return 'Et' + full.replace('Ethernet', '');
                        if (full.startsWith('Loopback')) return 'Lo' + full.replace('Loopback', '');
                        return full;
                      };

                      const displayName = collapse(ifaceName);
                      return (
                        <li key={`${objIndex}-${ifaceName}`} className="flex justify-between">
                          <span>{displayName}</span>
                          <span>{ipAddress}</span>
                        </li>
                      );
                    })
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-base-content/50 italic">No interface information available.</p>
              )}
          </div>
          
          <div>
            <p className="text-sm text-base-content/70 mb-1">Connected Nodes</p>
            {node.connectedNode && node.connectedNode.length > 0 ? (
              <ul className="list-none space-y-1 text-sm font-mono bg-base-200 p-2 rounded-box">
                {node.connectedNode.map((connected, index) => <li key={index}>{connected}</li>)}
              </ul>
            ) : (
              <p className="text-sm text-base-content/50 italic">No direct connections.</p>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
};

export default NodeDetailPanel;
