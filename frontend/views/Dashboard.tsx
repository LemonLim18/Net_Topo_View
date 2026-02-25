import React, { useState, useMemo, useCallback } from 'react';
import type { D3Node, D3Link, TopologyData } from '../types';
import NetworkGraph from '../components/NetworkGraph';
import NetworkGraphCytoscape from '../components/NetworkGraphCytoscape';
import NodeDetailPanel from '../components/NodeDetailPanel';
import TopologyTable from '../components/TopologyTable';
import Legend from '../components/Legend';
import ContextMenu from '../components/ContextMenu';
import type { ContextMenuAction } from '../components/ContextMenu';
import SSHTerminalPanel from '../components/SSHTerminalPanel';
import type { SSHTab } from '../components/SSHTerminalPanel';
import type { SSHConnectionState } from '../components/SSHTerminal';
import SSHConnectModal from '../components/SSHConnectModal';
import { CONTROLLER_NODE_ID, CONTROLLER_NODE_DATA, mockTopologyData as PLANNED_TOPOLOGY_DATA } from '../constants';
import { useWebSocket } from '../src/hooks/useWebSocket';
import { API_CONFIG } from '../src/config';
import { RefreshCw, Terminal, Info } from 'lucide-react';
import { Toast } from '../src/utils/swal';
import { sshApi } from '../src/services/api';

type TopologyView = 'actual' | 'planned' | 'diff';

// Helper to convert abbreviated interface names to full names
const expandInterfaceName = (abbr: string): string => {
  if (abbr.startsWith('Et')) return abbr.replace('Et', 'Ethernet');
  if (abbr.startsWith('Lo')) return abbr.replace('Lo', 'Loopback');
  return abbr;
};

const processTopologyData = (data: TopologyData | null) => {
    if (!data) return { nodes: [], links: [] };

    const nodes: D3Node[] = Object.keys(data.nodes).map(nodeId => ({
        id: nodeId,
        ...data.nodes[nodeId],
    }));

    const links: D3Link[] = [];
    const linkSet = new Set<string>();

    Object.entries(data.nodes).forEach(([sourceId, sourceDetails]) => {
        if (sourceDetails.edgeLink) {
            Object.entries(sourceDetails.edgeLink).forEach(([localIfaceAbbr, linkInfo]) => {
                const targetId = linkInfo.remote_node;
                const targetDetails = data.nodes[targetId];
                if (!targetDetails) return;

                const linkKey1 = `${sourceId}-${targetId}`;
                const linkKey2 = `${targetId}-${sourceId}`;

                if (!linkSet.has(linkKey1) && !linkSet.has(linkKey2)) {
                    const localIfaceFull = expandInterfaceName(localIfaceAbbr);
                    const remoteIfaceFull = expandInterfaceName(linkInfo.remote_interface);
                    
                    // interfaceList is an array of objects where each object may contain
                    // multiple interface entries. Previously the code compared only the
                    // first key of each object which failed to find the correct entry
                    // unless it happened to be first. Use hasOwnProperty to check for
                    // the presence of the exact interface key instead.
                    const sourceEntry = sourceDetails.interfaceList.find(iface => Object.prototype.hasOwnProperty.call(iface, localIfaceFull));
                    const sourceIp = sourceEntry ? sourceEntry[localIfaceFull] : 'N/A';

                    const targetEntry = targetDetails.interfaceList.find(iface => Object.prototype.hasOwnProperty.call(iface, remoteIfaceFull));
                    const targetIp = targetEntry ? targetEntry[remoteIfaceFull] : 'N/A';

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
    
        // Add Controller Node and Link using seed IP and its interface from the
        // backend topology. We locate which interface on the entry node matches
        // the seed IP (from `data.seeds`) and use that interface name + IP for
        // the controller link instead of hard-coding 'WAN'.
        const seedIp = data.seeds?.[0] ?? null;
        let seedInterfaceName: string | null = null;
        const entryNode = seedIp
            ? nodes.find(node => {
                    // find the interface object that contains the seed IP
                    const match = node.interfaceList.find(ifaceObj => Object.values(ifaceObj).includes(seedIp));
                                        if (match) {
                                                // find the exact interface key whose value equals the seedIp
                                                const key = Object.keys(match).find(k => (match as any)[k] === seedIp) || null;
                                                // Collapse full name ('Ethernet0/1') to abbreviated form ('Et0/1')
                                                if (key) {
                                                    if (key.startsWith('Ethernet')) seedInterfaceName = 'Et' + key.replace('Ethernet', '');
                                                    else if (key.startsWith('Loopback')) seedInterfaceName = 'Lo' + key.replace('Loopback', '');
                                                    else seedInterfaceName = key;
                                                } else {
                                                    seedInterfaceName = null;
                                                }
                                                return true;
                                        }
                    return false;
                })
            : null;

        if (entryNode) {
                nodes.push({ ...CONTROLLER_NODE_DATA });
                links.push({
                        source: CONTROLLER_NODE_ID,
                        target: entryNode.id,
                        sourceInterface: 'Ens18',
                        // use the exact interface name from the node's interfaceList when available
                        targetInterface: seedInterfaceName ?? 'WAN',
                        sourceIp: CONTROLLER_NODE_DATA.mgmt_ip,
                        // prefer the seed IP (the interface IP) as the target IP
                        targetIp: seedIp ?? entryNode.mgmt_ip,
                        info: { remote_node: entryNode.id, remote_interface: seedInterfaceName ?? 'WAN', remote_ip: seedIp ?? entryNode.mgmt_ip },
                });
        }

    return { nodes, links };
};

const Dashboard: React.FC = () => {
    const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
    const [panelHeight, setPanelHeight] = useState(window.innerHeight * 0.4);
    const [isGridVisible, setIsGridVisible] = useState(true);
    const [topologyView, setTopologyView] = useState<TopologyView>('diff');
    const [vizEngine, setVizEngine] = useState<'d3' | 'cytoscape'>('d3');
    const theme = localStorage.getItem('theme') || 'night';

    // --- Context Menu state ---
    const [contextMenu, setContextMenu] = useState<{
      nodeId: string;
      x: number;
      y: number;
    } | null>(null);

    // --- SSH Terminal state ---
    const [sshTabs, setSshTabs] = useState<SSHTab[]>([]);
    const [activeSSHTab, setActiveSSHTab] = useState<string | null>(null);
    const [sshPanelHeight, setSshPanelHeight] = useState(300);
    const [sshMinimized, setSshMinimized] = useState(false);
    const [tabStates, setTabStates] = useState<Record<string, SSHConnectionState>>({});

    // --- SSH Connect Modal state ---
    const [sshConnectTarget, setSshConnectTarget] = useState<{
      hostname: string;
      host: string;
      sessionId?: string;
    } | null>(null);

    const { status, topology: realTimeTopology, requestTopology } = useWebSocket({
        url: API_CONFIG.WS_URL,
    });
    
    const isLoading = status === 'connecting' || (status === 'connected' && !realTimeTopology);

    const graphData = useMemo(() => {
        const planned = processTopologyData(PLANNED_TOPOLOGY_DATA);
        const actual = processTopologyData(realTimeTopology);

        if (topologyView === 'planned') return planned;
        if (topologyView === 'actual') return actual;

        // Diff view logic
        if (!realTimeTopology) return { nodes: [], links: [] };

        const nodeMap = new Map<string, D3Node>();
        planned.nodes.forEach(n => nodeMap.set(n.id, { ...n, diffStatus: 'removed' }));
        actual.nodes.forEach(n => {
            if (nodeMap.has(n.id)) {
                // Node exists in both, update data from actual and mark as unchanged
                const existingNode = nodeMap.get(n.id)!;
                nodeMap.set(n.id, { ...existingNode, ...n, diffStatus: 'unchanged' });
            } else {
                nodeMap.set(n.id, { ...n, diffStatus: 'added' });
            }
        });

        const getLinkKey = (l: D3Link) => {
            const s = l.source;
            const t = l.target;
            const sourceId = typeof s === 'object' && s !== null ? (s as D3Node).id : s;
            const targetId = typeof t === 'object' && t !== null ? (t as D3Node).id : t;
            return [String(sourceId), String(targetId)].sort().join('-');
        }

        const linkMap = new Map<string, D3Link>();
        planned.links.forEach(l => linkMap.set(getLinkKey(l), { ...l, diffStatus: 'removed' }));
        actual.links.forEach(l => {
            const key = getLinkKey(l);
            if (linkMap.has(key)) {
                linkMap.get(key)!.diffStatus = 'unchanged';
            } else {
                linkMap.set(key, { ...l, diffStatus: 'added' });
            }
        });

        return { nodes: Array.from(nodeMap.values()), links: Array.from(linkMap.values()) };

    }, [topologyView, realTimeTopology]);

    const selectedNodeData = useMemo(() => {
        if (!selectedNodeId) return null;
        
        const node = graphData.nodes.find(n => n.id === selectedNodeId);
        return node ? { ...node } : null; // Return the full node data with diffStatus
    }, [selectedNodeId, graphData.nodes]);

    const handleNodeClick = useCallback((nodeId: string) => {
        setSelectedNodeId(prevId => (prevId === nodeId ? null : nodeId));
    }, []);
    
    const handlePanelClose = useCallback(() => setSelectedNodeId(null), []);

    const handleMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        const startY = e.clientY;
        const startHeight = panelHeight;

        const handleMouseMove = (moveEvent: MouseEvent) => {
            const newHeight = startHeight - (moveEvent.clientY - startY);
            const minHeight = 100, maxHeight = window.innerHeight - 150;
            setPanelHeight(Math.max(minHeight, Math.min(newHeight, maxHeight)));
        };

        const handleMouseUp = () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
    }, [panelHeight]);
    
    const handleManualCrawl = useCallback(async () => {
        try {
            const response = await fetch(`${API_CONFIG.API_URL}/crawl`, {
                method: 'POST',
            });
            if (!response.ok) throw new Error('Crawl trigger failed');
            Toast.success('Crawl triggered', 'Topology refresh in progress...');
            // Give backend a moment before requesting new data
            setTimeout(() => requestTopology(), 1000);
        } catch (error) {
            console.error('Failed to trigger manual crawl:', error);
            Toast.error('Crawl failed', 'Could not trigger network discovery');
        }
    }, [requestTopology]);

    // --- Context Menu handler ---
    const handleNodeContextMenu = useCallback((nodeId: string, x: number, y: number) => {
        setContextMenu({ nodeId, x, y });
    }, []);

    const closeContextMenu = useCallback(() => setContextMenu(null), []);

    // --- SSH session launcher ---
    const openSSHTerminal = useCallback(async (hostname: string) => {
        // Check if we already have a tab for this host
        const existing = sshTabs.find(t => t.hostname === hostname);
        if (existing) {
            setActiveSSHTab(existing.sessionId);
            setSshMinimized(false);
            return;
        }

        try {
            const result = await sshApi.createSession(hostname);

            if (result.status === 'connected') {
                // Vault credentials worked — open terminal tab
                const wsUrl = sshApi.getWsUrl(result.session_id);
                const newTab: SSHTab = {
                    sessionId: result.session_id,
                    hostname: result.hostname,
                    host: result.host,
                    wsUrl,
                };
                setSshTabs(prev => [...prev, newTab]);
                setActiveSSHTab(result.session_id);
                setSshMinimized(false);
                Toast.success('SSH Connected', `Connected to ${hostname}`);
            } else if (result.status === 'credentials_required') {
                // Show credential prompt
                setSshConnectTarget({
                    hostname: result.hostname,
                    host: result.host,
                });
            }
        } catch (err: any) {
            Toast.error('SSH Error', err.message || 'Failed to create SSH session');
        }
    }, [sshTabs]);

    // --- SSH connect with user-provided credentials ---
    const handleSSHConnect = useCallback(async (username: string, password: string, port: number, saveToVault: boolean) => {
        if (!sshConnectTarget) return;

        try {
            const result = await sshApi.connectWithCreds(
                sshConnectTarget.hostname,
                username,
                password,
                port,
                saveToVault,
            );

            const wsUrl = sshApi.getWsUrl(result.session_id);
            const newTab: SSHTab = {
                sessionId: result.session_id,
                hostname: result.hostname,
                host: result.host,
                wsUrl,
            };
            setSshTabs(prev => [...prev, newTab]);
            setActiveSSHTab(result.session_id);
            setSshMinimized(false);
            setSshConnectTarget(null);
            if (result.saved_to_vault) {
                Toast.success('SSH Connected', `Connected to ${sshConnectTarget.hostname} — credentials saved to vault`);
            } else {
                Toast.success('SSH Connected', `Connected to ${sshConnectTarget.hostname}`);
            }
        } catch (err: any) {
            const msg = err.message || 'Failed to connect';
            const isGateway = msg.includes('502') || msg.toLowerCase().includes('bad gateway');
            Toast.error(
                isGateway ? 'SSH Gateway Error' : 'SSH Connection Failed',
                isGateway
                    ? 'The SSH backend is unreachable (502 Bad Gateway). Ensure the server is running.'
                    : msg,
            );
        }
    }, [sshConnectTarget]);

    // --- Close SSH tab ---
    const handleCloseSSHTab = useCallback((sessionId: string) => {
        setSshTabs(prev => {
            const remaining = prev.filter(t => t.sessionId !== sessionId);
            if (activeSSHTab === sessionId) {
                setActiveSSHTab(remaining.length > 0 ? remaining[remaining.length - 1].sessionId : null);
            }
            return remaining;
        });
        setTabStates(prev => {
            const next = { ...prev };
            delete next[sessionId];
            return next;
        });
    }, [activeSSHTab]);

    // --- Per-tab connection-state change ---
    const handleTabConnectionChange = useCallback((sessionId: string, state: SSHConnectionState) => {
        setTabStates(prev => ({ ...prev, [sessionId]: state }));
    }, []);

    // --- SSH panel resize ---
    const handleSSHPanelResize = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        const startY = e.clientY;
        const startHeight = sshPanelHeight;

        const handleMouseMove = (moveEvent: MouseEvent) => {
            const newHeight = startHeight - (moveEvent.clientY - startY);
            setSshPanelHeight(Math.max(150, Math.min(newHeight, window.innerHeight - 200)));
        };

        const handleMouseUp = () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
    }, [sshPanelHeight]);

    // --- Context Menu actions ---
    const contextMenuActions: ContextMenuAction[] = useMemo(() => {
        if (!contextMenu) return [];
        return [
            {
                label: 'SSH Terminal',
                icon: <Terminal size={14} />,
                onClick: () => openSSHTerminal(contextMenu.nodeId),
                disabled: contextMenu.nodeId === CONTROLLER_NODE_ID,
            },
            {
                label: 'View Details',
                icon: <Info size={14} />,
                onClick: () => handleNodeClick(contextMenu.nodeId),
            },
        ];
    }, [contextMenu, openSSHTerminal, handleNodeClick]);

    const statusIndicator = {
        connecting: <><span className="loading loading-ring loading-xs"></span> Connecting...</>,
        connected: <><span className="w-2 h-2 rounded-full bg-success animate-pulse"></span> Connected</>,
        disconnected: <><span className="w-2 h-2 rounded-full bg-warning"></span> Disconnected</>,
        error: <><span className="w-2 h-2 rounded-full bg-error"></span> Error</>,
    };

    return (
        <div className="w-full h-full flex flex-col">
             {isLoading && (
                <div className="absolute inset-0 bg-base-300/50 backdrop-blur-sm flex items-center justify-center z-15" role="status" aria-label="Loading real-time topology">
                    <div className="text-center">
                        <span className="loading loading-spinner loading-lg text-primary"></span>
                        <p className="mt-2 text-base-content/80">Loading Real-Time Topology...</p>
                    </div>
                </div>
            )}
            <div className="p-2 bg-base-300/80 backdrop-blur-sm shrink-0 z-10 flex items-center justify-between px-4">
                <div className="flex items-center gap-2 text-sm text-base-content/70" aria-live="polite">
                   {statusIndicator[status]}
                </div>
                <div className="join">
                    <button className={`btn join-item btn-sm ${topologyView === 'planned' ? 'btn-active' : ''}`} onClick={() => setTopologyView('planned')}>Planned</button>
                    <button className={`btn join-item btn-sm ${topologyView === 'actual' ? 'btn-active' : ''}`} onClick={() => setTopologyView('actual')}>Real-Time</button>
                    <button className={`btn join-item btn-sm ${topologyView === 'diff' ? 'btn-active' : ''}`} onClick={() => setTopologyView('diff')}>Diff</button>
                </div>
                <div className="join">
                    <button className={`btn join-item btn-sm ${vizEngine === 'd3' ? 'btn-active' : ''}`} onClick={() => setVizEngine('d3')}>D3.js</button>
                    <button className={`btn join-item btn-sm ${vizEngine === 'cytoscape' ? 'btn-active' : ''}`} onClick={() => setVizEngine('cytoscape')}>Cytoscape</button>
                </div>
            </div>

            <div className="grow relative overflow-hidden">
                {vizEngine === 'd3' ? (
                    <NetworkGraph 
                        nodes={graphData.nodes} 
                        links={graphData.links}
                        onNodeClick={handleNodeClick}
                        onNodeContextMenu={handleNodeContextMenu}
                        selectedNodeId={selectedNodeId}
                        isGridVisible={isGridVisible}
                        setIsGridVisible={setIsGridVisible}
                        theme={theme}
                        topologyView={topologyView}
                    />
                ) : (
                    <NetworkGraphCytoscape
                        nodes={graphData.nodes} 
                        links={graphData.links}
                        onNodeClick={handleNodeClick}
                        onNodeContextMenu={handleNodeContextMenu}
                        selectedNodeId={selectedNodeId}
                        theme={theme}
                        topologyView={topologyView}
                    />
                )}
                <Legend />
                <div 
                  className="absolute bottom-0 left-0 right-0 flex flex-col bg-base-100/90 backdrop-blur-sm rounded-t-lg shadow-[0_-8px_20px_rgba(0,0,0,0.25)]"
                  style={{ height: `${panelHeight}px` }}
                >
                    <div 
                        className="w-full h-5 shrink-0 cursor-row-resize flex items-center justify-center"
                        onMouseDown={handleMouseDown}
                        aria-label="Resize connection list panel"
                        role="separator"
                    >
                        <div className="w-12 h-1.5 bg-base-300/70 rounded-full"></div>
                    </div>
                    <TopologyTable links={graphData.links} onNodeClick={handleNodeClick} showStatus={topologyView === 'diff'}/>
                </div>
                <NodeDetailPanel node={selectedNodeData} onClose={handlePanelClose} />

                {/* SSH Terminal Panel */}
                <SSHTerminalPanel
                    tabs={sshTabs}
                    activeTabId={activeSSHTab}
                    onSelectTab={setActiveSSHTab}
                    onCloseTab={handleCloseSSHTab}
                    height={sshPanelHeight}
                    minimized={sshMinimized}
                    onToggleMinimize={() => setSshMinimized(prev => !prev)}
                    onMouseDown={handleSSHPanelResize}
                    tabStates={tabStates}
                    onConnectionChange={handleTabConnectionChange}
                />
            </div>

            {/* Context Menu (portal-like, positioned absolute to viewport) */}
            {contextMenu && (
                <ContextMenu
                    x={contextMenu.x}
                    y={contextMenu.y}
                    actions={contextMenuActions}
                    onClose={closeContextMenu}
                />
            )}

            {/* SSH Connect Modal */}
            <SSHConnectModal
                hostname={sshConnectTarget?.hostname ?? ''}
                host={sshConnectTarget?.host ?? ''}
                isOpen={sshConnectTarget !== null}
                onConnect={handleSSHConnect}
                onClose={() => setSshConnectTarget(null)}
            />
        </div>
    );
};

export default Dashboard;
