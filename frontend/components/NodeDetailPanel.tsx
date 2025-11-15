
import React from 'react';
// FIX: Import EdgeLinkInfo for type casting.
import type { NodeData, EdgeLinkInfo } from '../types';

interface NodeDetailPanelProps {
  node: (NodeData & { id: string }) | null;
  onClose: () => void;
}

const NodeDetailPanel: React.FC<NodeDetailPanelProps> = ({ node, onClose }) => {
  const panelVisible = !!node;

  return (
    <div className={`
      absolute top-0 right-0 h-full bg-base-100/90 backdrop-blur-sm shadow-2xl 
      transition-transform duration-500 ease-in-out z-20 
      w-full sm:w-96 md:w-[450px]
      ${panelVisible ? 'translate-x-0' : 'translate-x-full'}
    `}>
      <div className="p-4 flex flex-col h-full">
        {node ? (
          <>
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-2xl font-bold text-primary">{node.id}</h2>
              <button onClick={onClose} className="btn btn-ghost btn-sm btn-circle">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>

            <div className="grow overflow-y-auto pr-2 space-y-6">
              <div>
                <h3 className="text-lg font-semibold text-base-content opacity-80 mb-2">Management IP</h3>
                <div className="bg-base-300 p-3 rounded-md font-mono text-base-content">{node.mgmt_ip}</div>
              </div>

              <div>
                <h3 className="text-lg font-semibold text-base-content opacity-80 mb-2">Interfaces</h3>
                <div className="space-y-2">
                  {node.interfaceList.map((ifaceGroup, idx) => (
                    <div key={idx} className="bg-base-300 p-3 rounded-md text-sm">
                      {Object.entries(ifaceGroup).map(([name, ip]) => (
                        <div key={name} className="flex justify-between items-center font-mono">
                          <span className="text-base-content/70">{name}</span>
                          <span className="text-base-content">{ip}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
              
              <div>
                <h3 className="text-lg font-semibold text-base-content opacity-80 mb-2">Edge Links</h3>
                 <div className="space-y-2">
                    {/* FIX: Cast the result of Object.entries to fix type inference for link. */}
                    {(Object.entries(node.edgeLink) as [string, EdgeLinkInfo][]).map(([localIface, link]) => (
                        <div key={localIface} className="card bg-base-300 compact">
                            <div className="card-body">
                                <p className="font-mono text-sm">
                                    <span className="font-semibold text-primary">{localIface}</span> &#8594; <span className="font-semibold text-secondary">{link.remote_node}</span>:<span className="font-semibold text-primary">{link.remote_interface}</span>
                                </p>
                                <p className="font-mono text-xs text-base-content/70">
                                    Remote IP: <span className="text-base-content">{link.remote_ip}</span>
                                </p>
                            </div>
                        </div>
                    ))}
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-neutral-content/60">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-12 w-12 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p>Select a node to see details</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default NodeDetailPanel;