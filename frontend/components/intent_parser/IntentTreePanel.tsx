import React, { useState, useEffect } from 'react';
import { Folder, Activity, Hash, Type } from 'lucide-react';
import type { IntentSchema } from '../../types/intentSchema';

interface Props {
  schema: IntentSchema;
  selectedNode: { type: string; category?: string; operation?: string; parameter?: string } | null;
  onSelectNode: (node: { type: string; category?: string; operation?: string; parameter?: string }) => void;
  onNavigateCategory?: (cat: string) => void;
  onNavigateOperation?: (cat: string, op: string) => void;
}

const IntentTreePanel: React.FC<Props> = ({ schema, selectedNode, onSelectNode, onNavigateCategory, onNavigateOperation }) => {
  const [contextMenu, setContextMenu] = useState<{ x: number, y: number, type: 'category' | 'operation', cat: string, op?: string } | null>(null);

  useEffect(() => {
    const closeMenu = () => setContextMenu(null);
    document.addEventListener('click', closeMenu);
    return () => document.removeEventListener('click', closeMenu);
  }, []);

  const handleContextMenu = (e: React.MouseEvent, type: 'category' | 'operation', cat: string, op?: string) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, type, cat, op });
  };

  return (
    <>
      <ul className="menu menu-xs bg-base-100 rounded-box w-full p-2 gap-1 text-sm font-medium overflow-x-hidden relative" onContextMenu={(e) => e.preventDefault()}>
        <li>
          <details open>
            <summary className="hover:bg-base-200 py-1">Target Devices <span className="text-xs text-error ml-2">*</span></summary>
            <ul>
              <li><a>device_list</a></li>
            </ul>
          </details>
        </li>
        {schema.categories.map((cat) => (
          <li key={cat.name}>
            <details open>
              <summary 
                className={`hover:bg-base-200 py-1 ${selectedNode?.category === cat.name && !selectedNode.operation ? 'bg-primary/10 text-primary' : ''}`}
                onContextMenu={(e) => handleContextMenu(e, 'category', cat.name)}
              >
                <div 
                  className="flex items-center gap-2 w-full"
                  onClick={(e) => {
                     e.preventDefault();
                     onSelectNode({ type: 'category', category: cat.name });
                  }}
                >
                  <Folder className="w-4 h-4 text-accent" />
                  {cat.name}
                </div>
              </summary>
              <ul>
                {cat.operations.map((op) => (
                  <li key={op.name}>
                    <details open>
                      <summary 
                        className={`hover:bg-base-200 py-1 ${selectedNode?.operation === op.name && !selectedNode.parameter ? 'bg-primary/10 text-primary' : ''}`}
                        onContextMenu={(e) => handleContextMenu(e, 'operation', cat.name, op.name)}
                      >
                        <div 
                          className="flex items-center gap-2 w-full"
                          onClick={(e) => {
                             e.preventDefault();
                             onSelectNode({ type: 'operation', category: cat.name, operation: op.name });
                          }}
                        >
                           <Activity className="w-3 h-3 text-secondary" />
                           {op.name}
                        </div>
                      </summary>
                      <ul>
                        {op.required_fields.map(f => (
                           <li key={f}>
                             <a 
                               className={`text-error text-xs ${selectedNode?.parameter === f && selectedNode.operation === op.name ? 'bg-primary/10 font-bold' : ''}`}
                               onClick={() => onSelectNode({ type: 'parameter', category: cat.name, operation: op.name, parameter: f })}
                             >
                               <Hash className="w-3 h-3" /> {f}
                             </a>
                           </li>
                        ))}
                        {op.optional_fields.map(f => (
                           <li key={f}>
                             <a 
                               className={`text-base-content/60 text-xs ${selectedNode?.parameter === f && selectedNode.operation === op.name ? 'bg-primary/10 font-bold' : ''}`}
                               onClick={() => onSelectNode({ type: 'parameter', category: cat.name, operation: op.name, parameter: f })}
                             >
                               <Type className="w-3 h-3" /> {f}
                             </a>
                           </li>
                        ))}
                      </ul>
                    </details>
                  </li>
                ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>

      {/* Context Menu Overlay rendering */}
      {contextMenu && (
        <ul 
          className="menu bg-base-100 w-56 rounded-box shadow-xl border border-secondary/50 fixed z-[100]"
          style={{ top: contextMenu.y, left: contextMenu.x }}
        >
          <li className="menu-title text-[10px] opacity-70 px-4 py-2 uppercase tracking-widest border-b border-base-200 mb-1">
             {contextMenu.type === 'category' ? `Category: ${contextMenu.cat}` : `Operation: ${contextMenu.op}`}
          </li>
          <li>
            <a onClick={() => {
              if (contextMenu.type === 'category' && onNavigateCategory) {
                onNavigateCategory(contextMenu.cat);
              } else if (contextMenu.type === 'operation' && onNavigateOperation && contextMenu.op) {
                onNavigateOperation(contextMenu.cat, contextMenu.op);
              }
            }}>
              Go To {contextMenu.type === 'category' ? 'Category Setup' : 'Operation Setup'}
            </a>
          </li>
        </ul>
      )}
    </>
  );
};

export default IntentTreePanel;
