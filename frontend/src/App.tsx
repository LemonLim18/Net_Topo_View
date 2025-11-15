import React, { useState, useMemo, useCallback, useEffect } from 'react';
import { topologyData } from '../constants';
import type { D3Node, D3Link, NodeData, TopologyData, EdgeLinkInfo } from '../types';
import NetworkGraph from '../components/NetworkGraph';
import NodeDetailPanel from '../components/NodeDetailPanel';
import Header from '../components/Header';
import TopologyTable from '../components/TopologyTable';
import Legend from '../components/Legend';

// Helper to convert abbreviated interface names to full names found in the data
const expandInterfaceName = (abbr: string): string => {
  if (abbr.startsWith('Et')) return abbr.replace('Et', 'Ethernet');
  if (abbr.startsWith('Lo')) return abbr.replace('Lo', 'Loopback');
  return abbr;
};

const App: React.FC = () => {
  const [data, setData] = useState<TopologyData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [panelHeight, setPanelHeight] = useState(window.innerHeight * 0.4);
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'night');
  const [isGridVisible, setIsGridVisible] = useState(true);

  useEffect(() => {
    localStorage.setItem('theme', theme);
  }, [theme]);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    // Simulate an API call to fetch data
    await new Promise(resolve => setTimeout(resolve, 800));
    setData(topologyData); // In a real app, this would come from a fetch() call
    setIsLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const graphData = useMemo(() => {
    if (!data) {
      return { nodes: [], links: [] };
    }
    
    const nodes: D3Node[] = Object.keys(data.nodes).map(nodeId => ({
      id: nodeId,
      ...data.nodes[nodeId],
    }));

    const links: D3Link[] = [];
    const linkSet = new Set<string>();

    (Object.entries(data.nodes) as [string, NodeData][]).forEach(([sourceId, sourceDetails]) => {
      if (sourceDetails.edgeLink) {
        (Object.entries(sourceDetails.edgeLink) as [string, EdgeLinkInfo][]).forEach(([localIfaceAbbr, linkInfo]) => {
          const targetId = linkInfo.remote_node;
          const targetDetails = data.nodes[targetId];
          if (!targetDetails) return; // Skip if target node doesn't exist

          const linkKey1 = `${sourceId}-${targetId}`;
          const linkKey2 = `${targetId}-${sourceId}`;

          if (!linkSet.has(linkKey1) && !linkSet.has(linkKey2)) {
            const localIfaceFull = expandInterfaceName(localIfaceAbbr);
            const remoteIfaceFull = expandInterfaceName(linkInfo.remote_interface);
            
            // Safely access IPs from the interface list
            const sourceIp = sourceDetails.interfaceList[0]?.[localIfaceFull] ?? 'N/A';
            const targetIp = targetDetails.interfaceList[0]?.[remoteIfaceFull] ?? 'N/A';

            links.push({
              source: sourceId,
              target: targetId,
              sourceInterface: localIfaceAbbr,
              targetInterface: linkInfo.remote_interface,
              sourceIp,
              targetIp,
              info: linkInfo,
            });
            linkSet.add(linkKey1);
          }
        });
      }
    });

    return { nodes, links };
  }, [data]);
  
  const selectedNodeData = useMemo(() => {
    if (!selectedNodeId || !data) return null;
    const node = graphData.nodes.find(n => n.id === selectedNodeId);
    return node ? { id: node.id, ...data.nodes[selectedNodeId] } as NodeData & { id: string } : null;
  }, [selectedNodeId, data, graphData.nodes]);

  const handleNodeClick = useCallback((nodeId: string) => {
    setSelectedNodeId(prevId => (prevId === nodeId ? null : nodeId));
  }, []);
  
  const handlePanelClose = useCallback(() => {
      setSelectedNodeId(null);
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();

    const startY = e.clientY;
    const startHeight = panelHeight;

    const handleMouseMove = (moveEvent: MouseEvent) => {
        const deltaY = moveEvent.clientY - startY;
        const newHeight = startHeight - deltaY;

        const minHeight = 100;
        const maxHeight = window.innerHeight - 150;
        
        setPanelHeight(Math.max(minHeight, Math.min(newHeight, maxHeight)));
    };

    const handleMouseUp = () => {
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }, [panelHeight]);


  return (
    <div data-theme={theme} className="w-screen h-screen flex flex-col font-sans">
      <Header theme={theme} setTheme={setTheme} onRefresh={fetchData} />
      <main className="grow relative overflow-hidden">
        {isLoading && (
            <div className="absolute inset-0 bg-base-300/50 backdrop-blur-sm flex items-center justify-center z-30">
                <div className="text-center">
                    <span className="loading loading-spinner loading-lg text-primary"></span>
                    <p className="mt-2 text-base-content/80">Loading Topology...</p>
                </div>
            </div>
        )}
        <div className="w-full h-full">
            <NetworkGraph 
                nodes={graphData.nodes} 
                links={graphData.links}
                onNodeClick={handleNodeClick}
                selectedNodeId={selectedNodeId}
                isGridVisible={isGridVisible}
                setIsGridVisible={setIsGridVisible}
                theme={theme}
            />
            <Legend />
        </div>
        
        <div 
          className="absolute bottom-0 left-0 right-0 flex flex-col bg-base-100/90 backdrop-blur-sm rounded-t-lg shadow-[0_-8px_20px_rgba(0,0,0,0.25)]"
          style={{ height: `${panelHeight}px` }}
        >
            <div 
                className="w-full h-5 flex-shrink-0 cursor-row-resize flex items-center justify-center"
                onMouseDown={handleMouseDown}
            >
                <div className="w-12 h-1.5 bg-base-300/70 rounded-full"></div>
            </div>
            <TopologyTable nodes={graphData.nodes} links={graphData.links} onNodeClick={handleNodeClick} />
        </div>

        <NodeDetailPanel 
            node={selectedNodeData} 
            onClose={handlePanelClose} 
        />
      </main>
    </div>
  );
};

export default App;
