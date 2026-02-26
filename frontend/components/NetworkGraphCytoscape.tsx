import React, { useRef } from 'react';
import CytoscapeComponent from 'react-cytoscapejs';
import cytoscape from 'cytoscape';
import type { D3Node, D3Link } from '../types'; 
import { CONTROLLER_NODE_ID } from '../constants';

// Register extensions if needed (e.g. fcose, dagre) - for now using built-in 'cose' or 'grid'

interface NetworkGraphCytoscapeProps {
  nodes: D3Node[];
  links: D3Link[];
  onNodeClick: (nodeId: string) => void;
  onNodeContextMenu?: (nodeId: string, x: number, y: number) => void;
  selectedNodeId: string | null;
  theme: string; // 'bumblebee' or 'night' usually
  topologyView?: 'actual' | 'planned' | 'diff';
}

const NetworkGraphCytoscape: React.FC<NetworkGraphCytoscapeProps> = ({ 
  nodes, 
  links, 
  onNodeClick,
  onNodeContextMenu,
  selectedNodeId, 
  theme,
  topologyView = 'actual'
}) => {
  const cyRef = useRef<cytoscape.Core | null>(null);

  // Convert D3Node/D3Link to Cytoscape Elements
  const elements = React.useMemo(() => {
    const cyNodes = nodes.map(n => {
        let borderWidth = 0;
        let borderColor = 'transparent';

        if (topologyView === 'diff') {
            const status = (n as any).diffStatus || 'unchanged';
            if (status === 'added') {
                borderColor = '#fbbd23'; // warning (Unplanned/Added)
                borderWidth = 3;
            } else if (status === 'removed') {
                borderColor = '#f87272'; // error (Missing)
                borderWidth = 3;
            }
        }
        
        if (n.id === selectedNodeId) {
            borderColor = '#661AE6'; // primary
            borderWidth = 4;
        }

        return {
            data: { 
                id: n.id, 
                label: n.id,
                mgmt_ip: n.mgmt_ip,
                type: n.id === CONTROLLER_NODE_ID ? 'server' : 'router',
                diffStatus: (n as any).diffStatus,
                obj: n 
            },
            classes: [
                n.id === selectedNodeId ? 'selected' : '',
                (n as any).diffStatus === 'added' ? 'diff-added' : '',
                (n as any).diffStatus === 'removed' ? 'diff-removed' : ''
            ].join(' '),
            style: {
                'border-width': borderWidth,
                'border-color': borderColor,
                'background-color': '#1f2937' // dark grey base
            }
        };
    });

    const cyEdges = links.map((l, i) => {
        const sourceId = typeof l.source === 'object' ? (l.source as any).id : l.source;
        const targetId = typeof l.target === 'object' ? (l.target as any).id : l.target;
        
        let lineColor = '#a6adbb';
        let lineStyle = 'solid';
        let opacity = 0.5;

        // Ensure we differentiate Unplanned vs Missing links too
        if (topologyView === 'diff') {
             const status = (l as any).diffStatus || 'unchanged';
             if (status === 'added') { // Unplanned
                 lineColor = '#fbbd23'; 
                 opacity = 1;
             } else if (status === 'removed') { // Missing
                 lineColor = '#f87272'; 
                 opacity = 1;
                 lineStyle = 'dashed';
             }
        }

        return {
            data: { 
                id: `${sourceId}-${targetId}-${i}`, 
                source: sourceId, 
                target: targetId,
                label: `${l.sourceInterface} ↔ ${l.targetInterface}`
            },
            style: {
                'line-color': lineColor,
                'line-style': lineStyle,
                'opacity': opacity
            }
        };
    });

    return [...cyNodes, ...cyEdges];
  }, [nodes, links, selectedNodeId, topologyView]);

  // Handle Layout
  const layout: cytoscape.LayoutOptions = {
    name: 'cose', // Built-in force-directed layout
    animate: true,
    animationDuration: 500,
    idealEdgeLength: 100,
    nodeOverlap: 20,
    refresh: 20,
    fit: true,
    padding: 30,
    randomize: false,
    componentSpacing: 100,
    nodeRepulsion: 400000,
    edgeElasticity: 100,
    nestingFactor: 5,
    gravity: 80,
    numIter: 1000,
    initialTemp: 200,
    coolingFactor: 0.95,
    minTemp: 1.0
  };

  const stylesheet: cytoscape.StylesheetJson = [
      {
          selector: 'node',
          style: {
              'label': 'data(label)',
              'text-valign': 'bottom',
              'text-halign': 'center',
              'text-margin-y': 8,
              'color': theme === 'night' ? '#a6adbb' : '#1f2937',
              'background-fit': 'cover',
              'width': 50,
              'height': 50,
          }
      },
      {
          selector: 'edge',
          style: {
              'width': 2,
              'curve-style': 'bezier',
              'target-arrow-shape': 'none',
              'label': 'data(label)',
              'font-size': 10,
              'text-rotation': 'autorotate',
              'text-background-opacity': 1,
              'text-background-color': theme === 'night' ? '#1d232a' : '#ffffff',
              'text-background-padding': '3px',
              'color': theme === 'night' ? '#a6adbb' : '#1f2937'
          }
      },
      {
          selector: ':selected',
          style: {
              'border-width': 4,
              'border-color': '#661AE6'
          }
      }
  ];

  return (
    <div className="w-full h-full bg-base-200 shadow-inner overflow-hidden relative">
      <CytoscapeComponent
        elements={CytoscapeComponent.normalizeElements(elements)}
        layout={layout}
        stylesheet={stylesheet}
        cy={(cy) => {
            cyRef.current = cy;
            cy.on('tap', 'node', (evt) => {
                const node = evt.target;
                onNodeClick(node.id());
            });
            cy.on('tap', (evt) => {
                if(evt.target === cy){
                    onNodeClick("");
                }
            });
            cy.on('cxttap', 'node', (evt) => {
                const node = evt.target;
                const renderedPos = node.renderedPosition();
                const container = cy.container();
                if (container && onNodeContextMenu) {
                    const rect = container.getBoundingClientRect();
                    onNodeContextMenu(
                        node.id(),
                        rect.left + renderedPos.x,
                        rect.top + renderedPos.y
                    );
                }
            });
        }}
        className="w-full h-full"
        wheelSensitivity={0.3}
      />
      
      {/* Zoom Controls Overlay (reused style from D3) */}
      <div className="absolute top-4 left-4 z-10 btn-group btn-group-vertical shadow-md">
         <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)} className="btn btn-sm" aria-label="Zoom in">+</button>
         <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 0.8)} className="btn btn-sm" aria-label="Zoom out">-</button>
         <button onClick={() => cyRef.current?.fit()} className="btn btn-sm" aria-label="Fit">Fit</button>
      </div>
    </div>
  );
};

export default NetworkGraphCytoscape;
