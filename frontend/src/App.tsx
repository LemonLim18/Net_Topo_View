
import React, { useState, useMemo, useCallback, useEffect } from 'react';
import type { D3Node, D3Link, NodeData, TopologyData, EdgeLinkInfo } from '../types';
import NetworkGraph from '../components/NetworkGraph';
import NodeDetailPanel from '../components/NodeDetailPanel';
import Header from '../components/Header';
import TopologyTable from '../components/TopologyTable';
import Legend from '../components/Legend';
import { CONTROLLER_NODE_ID, CONTROLLER_NODE_DATA, topologyData } from '../constants';
import { useWebSocket } from './hooks/useWebSocket';
import { API_CONFIG } from './config';

// Helper to convert abbreviated interface names to full names found in the data
const expandInterfaceName = (abbr: string): string => {
  if (abbr.startsWith('Et')) return abbr.replace('Et', 'Ethernet');
  if (abbr.startsWith('Lo')) return abbr.replace('Lo', 'Loopback');
  return abbr;
};

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected';

const App: React.FC = () => {
  // const [data, setData] = useState<TopologyData | null>(topologyData);
  // const [isLoading, setIsLoading] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connected');
  
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [panelHeight, setPanelHeight] = useState(window.innerHeight * 0.4);
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'night');
  const [isGridVisible, setIsGridVisible] = useState(true);

  useEffect(() => {
    localStorage.setItem('theme', theme);
  }, [theme]);

  const handleRefresh = useCallback(async () => {
    console.log("Refresh requested, but application is in static data mode.");
  }, []);

  const handleTopologyUpdate = useCallback((topology: TopologyData) => {
    console.log('Topology received:', topology.timestamp);
  }, []);

  const handleWebSocketError = useCallback((error: Event) => {
    console.error('WebSocket error:', error);
  }, []);

  const { status, topology: data, requestTopology } = useWebSocket({
    url: API_CONFIG.WS_URL,
    onTopologyUpdate: handleTopologyUpdate,
    onError: handleWebSocketError,
    reconnectInterval: 3000,
    maxReconnectAttempts: 10,
  });

  const isLoading = status === 'connecting' || (status === 'connected' && !data);

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

    // --- Add Server Controller Node and Link ---
    const seedIp = data.seeds && data.seeds.length > 0 ? data.seeds[0] : null;

    // Find the node that owns the seed IP address
    const entryNode = seedIp 
      ? nodes.find(node => 
          node.interfaceList.some(ifaceGroup => 
            Object.values(ifaceGroup).includes(seedIp)
          )
        ) 
      : null;

    if (entryNode) {
      // Add the controller node to the graph
      nodes.push({ ...CONTROLLER_NODE_DATA });

      // Create a link from the controller to the entry/seed node
      const controllerLink: D3Link = {
        source: CONTROLLER_NODE_ID,
        target: entryNode.id,
        sourceInterface: 'eth0',
        targetInterface: 'WAN', // Placeholder interface on the router
        sourceIp: CONTROLLER_NODE_DATA.mgmt_ip,
        targetIp: entryNode.mgmt_ip,
        info: { // Dummy info to satisfy the type
            remote_node: entryNode.id,
            remote_interface: 'WAN',
            remote_ip: entryNode.mgmt_ip,
        },
      };
      links.push(controllerLink);
      linkSet.add(`${CONTROLLER_NODE_ID}-${entryNode.id}`);
    }
    // --- End of modification ---

    return { nodes, links };
  }, [data]);
  
  const selectedNodeData = useMemo(() => {
    if (!selectedNodeId) return null;

    // Handle the controller node separately
    if (selectedNodeId === CONTROLLER_NODE_ID) {
      return CONTROLLER_NODE_DATA as NodeData & { id: string };
    }
    
    // Original logic for nodes from API data
    if (!data) return null;
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
      <Header theme={theme} setTheme={setTheme} onRefresh={handleRefresh} connectionStatus={connectionStatus} />
      <main className="flex-grow relative overflow-hidden">
        {isLoading && (
            <div className="absolute inset-0 bg-base-300/50 backdrop-blur-sm flex items-center justify-center z-30">
                <div className="text-center">
                    <span className="loading loading-spinner loading-lg text-primary"></span>
                    <p className="mt-2 text-base-content/80">Loading...</p>
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
