import React, { useState, useMemo, useCallback, useEffect } from 'react';
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
import { CONTROLLER_NODE_ID, CONTROLLER_NODE_DATA } from '../constants';
import { useWebSocket } from '../src/hooks/useWebSocket';
import { API_CONFIG } from '../src/config';
import { topologyApi, netboxApi, type DeviceRecord, type InterfaceRecord, type LinkRecord } from '../src/services/api';
import { Terminal, Info, RefreshCw, Cable, X } from 'lucide-react';
import { Toast } from '../src/utils/swal';
import { sshApi } from '../src/services/api';

type TopologyView = 'actual' | 'planned' | 'diff';

const DASHBOARD_VIEW_KEY = 'dashboard.topologyView';
const DASHBOARD_NODE_KEY = 'dashboard.selectedNodeId';
const DASHBOARD_PANEL_HEIGHT_KEY = 'dashboard.panelHeight';
const DASHBOARD_GRID_VISIBLE_KEY = 'dashboard.gridVisible';
const DASHBOARD_SSH_PANEL_HEIGHT_KEY = 'dashboard.sshPanelHeight';
const DASHBOARD_SSH_MINIMIZED_KEY = 'dashboard.sshMinimized';

type ConnectStage = 'idle' | 'source-interface' | 'target-device' | 'target-interface' | 'saving';

interface ConnectFlowState {
    stage: ConnectStage;
    sourceNodeId: string | null;
    sourceDeviceId: number | null;
    sourceInterfaceId: number | null;
    targetNodeId: string | null;
    targetDeviceId: number | null;
    targetInterfaceId: number | null;
    error: string | null;
}

// Helper to convert abbreviated interface names to full names
const expandInterfaceName = (abbr: string): string => {
  if (abbr.startsWith('Et')) return abbr.replace('Et', 'Ethernet');
  if (abbr.startsWith('Lo')) return abbr.replace('Lo', 'Loopback');
  return abbr;
};

const processTopologyData = (data: TopologyData | null) => {
    if (!data) { console.debug('[Topo] processTopologyData called with null'); return { nodes: [], links: [] }; }
    const nodeKeys = Object.keys(data.nodes);
    console.debug(`[Topo] processTopologyData: ${nodeKeys.length} nodes:`, nodeKeys);

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
    const [selectedNodeId, setSelectedNodeId] = useState<string | null>(() => {
        return localStorage.getItem(DASHBOARD_NODE_KEY);
    });
    const [panelHeight, setPanelHeight] = useState(() => {
        const raw = Number(localStorage.getItem(DASHBOARD_PANEL_HEIGHT_KEY));
        if (Number.isFinite(raw) && raw > 120) return raw;
        return window.innerHeight * 0.4;
    });
    const [isGridVisible, setIsGridVisible] = useState(() => {
        const raw = localStorage.getItem(DASHBOARD_GRID_VISIBLE_KEY);
        return raw === null ? true : raw === 'true';
    });
    const [topologyView, setTopologyView] = useState<TopologyView>(() => {
        const raw = localStorage.getItem(DASHBOARD_VIEW_KEY);
        if (raw === 'actual' || raw === 'planned' || raw === 'diff') return raw;
        return 'diff';
    });
    const [vizEngine] = useState<'d3' | 'cytoscape'>('d3');
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
    const [sshPanelHeight, setSshPanelHeight] = useState(() => {
        const raw = Number(localStorage.getItem(DASHBOARD_SSH_PANEL_HEIGHT_KEY));
        if (Number.isFinite(raw) && raw > 120) return raw;
        return 300;
    });
    const [sshMinimized, setSshMinimized] = useState(() => {
        const raw = localStorage.getItem(DASHBOARD_SSH_MINIMIZED_KEY);
        return raw === 'true';
    });
    const [tabStates, setTabStates] = useState<Record<string, SSHConnectionState>>({});

    // --- SSH Connect Modal state ---
    const [sshConnectTarget, setSshConnectTarget] = useState<{
      hostname: string;
      host: string;
      sessionId?: string;
    } | null>(null);

    const { status, topology: realTimeTopology, setTopology: setWsTopology } = useWebSocket({
        url: API_CONFIG.WS_URL,
    });

    // --- Planned topology from local DB ---
    const [plannedTopology, setPlannedTopology] = useState<TopologyData | null>(null);
    const [connectFlow, setConnectFlow] = useState<ConnectFlowState>({
        stage: 'idle',
        sourceNodeId: null,
        sourceDeviceId: null,
        sourceInterfaceId: null,
        targetNodeId: null,
        targetDeviceId: null,
        targetInterfaceId: null,
        error: null,
    });
    const [connectDevices, setConnectDevices] = useState<DeviceRecord[]>([]);
    const [connectLinks, setConnectLinks] = useState<LinkRecord[]>([]);
    const [sourceInterfaces, setSourceInterfaces] = useState<InterfaceRecord[]>([]);
    const [targetInterfaces, setTargetInterfaces] = useState<InterfaceRecord[]>([]);

    const resetConnectFlow = useCallback(() => {
        setConnectFlow({
            stage: 'idle',
            sourceNodeId: null,
            sourceDeviceId: null,
            sourceInterfaceId: null,
            targetNodeId: null,
            targetDeviceId: null,
            targetInterfaceId: null,
            error: null,
        });
        setSourceInterfaces([]);
        setTargetInterfaces([]);
    }, []);

    const refreshConnectData = useCallback(async () => {
        const [devResp, linkResp] = await Promise.all([
            netboxApi.listDevices(),
            netboxApi.listLinks(),
        ]);
        setConnectDevices(devResp.devices || []);
        setConnectLinks(linkResp.links || []);
        return { devices: devResp.devices || [], links: linkResp.links || [] };
    }, []);

    useEffect(() => {
        const fetchPlanned = async () => {
            try {
                const data = await netboxApi.getLocalPlannedTopology();
                setPlannedTopology(data as any as TopologyData);
            } catch (e) {
                console.warn('[Topo] Failed to fetch local planned topology:', e);
            }
        };
        fetchPlanned();
    }, []);

    useEffect(() => {
        if (selectedNodeId) {
            localStorage.setItem(DASHBOARD_NODE_KEY, selectedNodeId);
        } else {
            localStorage.removeItem(DASHBOARD_NODE_KEY);
        }
    }, [selectedNodeId]);

    useEffect(() => {
        localStorage.setItem(DASHBOARD_VIEW_KEY, topologyView);
    }, [topologyView]);

    useEffect(() => {
        localStorage.setItem(DASHBOARD_PANEL_HEIGHT_KEY, String(panelHeight));
    }, [panelHeight]);

    useEffect(() => {
        localStorage.setItem(DASHBOARD_GRID_VISIBLE_KEY, String(isGridVisible));
    }, [isGridVisible]);

    useEffect(() => {
        localStorage.setItem(DASHBOARD_SSH_PANEL_HEIGHT_KEY, String(sshPanelHeight));
    }, [sshPanelHeight]);

    useEffect(() => {
        localStorage.setItem(DASHBOARD_SSH_MINIMIZED_KEY, String(sshMinimized));
    }, [sshMinimized]);

    // --- Refresh / Manual Crawl state ---
    const [isRefreshing, setIsRefreshing] = useState(false);

    const handleRefreshTopology = useCallback(async () => {
        setIsRefreshing(true);
        try {
            // 1. Trigger a new crawl on the backend
            await topologyApi.crawl();
            Toast.success('Crawl Triggered', 'Network discovery started. Topology will update via WebSocket when done.');

            // 2. Poll for the result — SSH crawls may take 15-60s depending on device count.
            //    We poll every 5s up to 60s, stopping early once we get nodes.
            let attempts = 0;
            const maxAttempts = 12;  // 12 × 5s = 60s
            const pollInterval = setInterval(async () => {
                attempts++;
                try {
                    const freshTopo = await topologyApi.get();
                    const nodeCount = freshTopo?.nodes ? Object.keys(freshTopo.nodes).length : 0;
                    console.log(`[Refresh] Poll attempt ${attempts}/${maxAttempts}: ${nodeCount} nodes`);
                    if (nodeCount > 0) {
                        setWsTopology(freshTopo);
                        Toast.success('Topology Loaded', `${nodeCount} nodes discovered.`);
                        clearInterval(pollInterval);
                        setIsRefreshing(false);
                    } else if (attempts >= maxAttempts) {
                        clearInterval(pollInterval);
                        setIsRefreshing(false);
                        Toast.error('No Nodes Found', 'Crawl completed but discovered 0 nodes. Check backend logs for SSH/connection errors.');
                    }
                } catch {
                    if (attempts >= maxAttempts) {
                        clearInterval(pollInterval);
                        setIsRefreshing(false);
                    }
                }
            }, 5000);
        } catch (err: any) {
            Toast.error('Crawl Failed', err.message || 'Could not trigger network crawl.');
            
            // Fallback: just fetch the existing topology via REST
            try {
                const existingTopo = await topologyApi.get();
                if (existingTopo && existingTopo.nodes && Object.keys(existingTopo.nodes).length > 0) {
                    setWsTopology(existingTopo);
                    Toast.success('Topology Loaded', `Loaded cached topology with ${Object.keys(existingTopo.nodes).length} nodes.`);
                }
            } catch {
                // nothing more we can do
            }
            setIsRefreshing(false);
        }
    }, [setWsTopology]);
    
    const isLoading = status === 'connecting' || (status === 'connected' && !realTimeTopology);

    const graphData = useMemo(() => {
        const planned = processTopologyData(plannedTopology);
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

    }, [topologyView, realTimeTopology, plannedTopology]);

    const selectedNodeData = useMemo(() => {
        if (!selectedNodeId) return null;
        
        const node = graphData.nodes.find(n => n.id === selectedNodeId);
        return node ? { ...node } : null; // Return the full node data with diffStatus
    }, [selectedNodeId, graphData.nodes]);

    const handleNodeClick = useCallback((nodeId: string) => {
        // Connect-mode override: clicking a node selects target device instead of opening details.
        if (connectFlow.stage === 'target-device') {
            if (!nodeId || nodeId === CONTROLLER_NODE_ID) return;
            if (nodeId === connectFlow.sourceNodeId) {
                setConnectFlow(prev => ({ ...prev, error: 'Target device must be different from source device' }));
                return;
            }

            const targetDev = connectDevices.find(d => d.hostname === nodeId);
            if (!targetDev) {
                setConnectFlow(prev => ({
                    ...prev,
                    error: `Target device '${nodeId}' is not in local inventory. Sync devices to NetBox first.`,
                }));
                return;
            }

            netboxApi.listInterfaces(targetDev.id)
                .then(resp => {
                    setTargetInterfaces(resp.interfaces || []);
                    setConnectFlow(prev => ({
                        ...prev,
                        stage: 'target-interface',
                        targetNodeId: nodeId,
                        targetDeviceId: targetDev.id,
                        targetInterfaceId: null,
                        error: null,
                    }));
                })
                .catch((err: any) => {
                    setConnectFlow(prev => ({
                        ...prev,
                        error: err?.message || 'Failed to load target interfaces',
                    }));
                });
            return;
        }

        setSelectedNodeId(prevId => (prevId === nodeId ? null : nodeId));
    }, [connectFlow.stage, connectFlow.sourceNodeId, connectDevices]);
    
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
    
    // --- Context Menu handler ---
    const handleNodeContextMenu = useCallback((nodeId: string, x: number, y: number) => {
        setContextMenu({ nodeId, x, y });
    }, []);

    const closeContextMenu = useCallback(() => setContextMenu(null), []);

    const getInterfaceStatus = useCallback((ifaceId: number, selectedIfaceId: number | null) => {
        const link = connectLinks.find(l => l.src_interface_id === ifaceId || l.dst_interface_id === ifaceId);
        if (!link || selectedIfaceId === ifaceId) {
            return { available: true as const, remoteHost: null as string | null };
        }
        const remoteHost = link.src_interface_id === ifaceId ? link.dst_hostname : link.src_hostname;
        return { available: false as const, remoteHost };
    }, [connectLinks]);

    const startConnectFlow = useCallback(async (sourceNodeId: string) => {
        try {
            const { devices } = await refreshConnectData();
            const srcDev = devices.find(d => d.hostname === sourceNodeId);
            if (!srcDev) {
                Toast.error('Connect Failed', `Source device '${sourceNodeId}' is not in local inventory`);
                return;
            }
            const ifaceResp = await netboxApi.listInterfaces(srcDev.id);
            setSourceInterfaces(ifaceResp.interfaces || []);
            setConnectFlow({
                stage: 'source-interface',
                sourceNodeId,
                sourceDeviceId: srcDev.id,
                sourceInterfaceId: null,
                targetNodeId: null,
                targetDeviceId: null,
                targetInterfaceId: null,
                error: null,
            });
        } catch (err: any) {
            Toast.error('Connect Failed', err?.message || 'Unable to initialize connection workflow');
        }
    }, [refreshConnectData]);

    const commitConnection = useCallback(async () => {
        if (!connectFlow.sourceDeviceId || !connectFlow.targetDeviceId || !connectFlow.sourceInterfaceId || !connectFlow.targetInterfaceId) {
            setConnectFlow(prev => ({ ...prev, error: 'Source/target interface selection is incomplete' }));
            return;
        }

        setConnectFlow(prev => ({ ...prev, stage: 'saving', error: null }));
        try {
            await netboxApi.createLink({
                src_device_id: connectFlow.sourceDeviceId,
                src_interface_id: connectFlow.sourceInterfaceId,
                dst_device_id: connectFlow.targetDeviceId,
                dst_interface_id: connectFlow.targetInterfaceId,
                discovery_protocol: 'manual',
            });

            const data = await netboxApi.getLocalPlannedTopology();
            setPlannedTopology(data as any as TopologyData);
            await refreshConnectData();

            Toast.success('Connection Created', 'Link created and synchronized to NetBox.');
            resetConnectFlow();
        } catch (err: any) {
            const msg = err?.message || 'Failed to create link';
            setConnectFlow(prev => ({ ...prev, stage: 'target-interface', error: msg }));
            Toast.error('Connection Failed', msg);
        }
    }, [
        connectFlow.sourceDeviceId,
        connectFlow.targetDeviceId,
        connectFlow.sourceInterfaceId,
        connectFlow.targetInterfaceId,
        refreshConnectData,
        resetConnectFlow,
    ]);

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
                label: 'Connect',
                icon: <Cable size={14} />,
                onClick: () => startConnectFlow(contextMenu.nodeId),
                disabled: contextMenu.nodeId === CONTROLLER_NODE_ID,
            },
            {
                label: 'View Details',
                icon: <Info size={14} />,
                onClick: () => handleNodeClick(contextMenu.nodeId),
            },
        ];
    }, [contextMenu, openSSHTerminal, handleNodeClick, startConnectFlow]);

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
                {/* D3/Cytoscape engine toggle — temporarily hidden
                <div className="join">
                    <button className={`btn join-item btn-sm ${vizEngine === 'd3' ? 'btn-active' : ''}`} onClick={() => setVizEngine('d3')}>D3.js</button>
                    <button className={`btn join-item btn-sm ${vizEngine === 'cytoscape' ? 'btn-active' : ''}`} onClick={() => setVizEngine('cytoscape')}>Cytoscape</button>
                </div>
                */}
                <button
                    className={`btn btn-sm btn-outline btn-primary gap-1 ${isRefreshing ? 'btn-disabled' : ''}`}
                    onClick={handleRefreshTopology}
                    disabled={isRefreshing}
                    title="Trigger a fresh network crawl and reload topology"
                >
                    <RefreshCw size={14} className={isRefreshing ? 'animate-spin' : ''} />
                    {isRefreshing ? 'Scanning...' : 'Refresh'}
                </button>
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

                {connectFlow.stage !== 'idle' && (
                    <div className="absolute top-4 right-4 z-20 w-[360px] card bg-base-100 border border-base-300 shadow-xl">
                        <div className="card-body p-4 gap-3">
                            <div className="flex items-center justify-between">
                                <h3 className="font-semibold text-sm flex items-center gap-2"><Cable size={14} /> Connect Interfaces</h3>
                                <button className="btn btn-ghost btn-xs" onClick={resetConnectFlow} aria-label="Cancel connect flow">
                                    <X size={14} />
                                </button>
                            </div>

                            <div className="text-xs opacity-70">
                                {connectFlow.stage === 'source-interface' && `Step 1/3: Select source interface on ${connectFlow.sourceNodeId}`}
                                {connectFlow.stage === 'target-device' && 'Step 2/3: Click target device on topology graph'}
                                {connectFlow.stage === 'target-interface' && `Step 3/3: Select target interface on ${connectFlow.targetNodeId}`}
                                {connectFlow.stage === 'saving' && 'Saving connection...'}
                            </div>

                            {connectFlow.error && (
                                <div className="alert alert-error py-2 text-xs">{connectFlow.error}</div>
                            )}

                            {(connectFlow.stage === 'source-interface' || connectFlow.stage === 'target-device' || connectFlow.stage === 'target-interface' || connectFlow.stage === 'saving') && (
                                <div className="space-y-2">
                                    <div className="text-xs font-semibold opacity-70">Source Interface</div>
                                    <div className="max-h-44 overflow-auto border border-base-300 rounded-md">
                                        {sourceInterfaces.length === 0 ? (
                                            <div className="p-3 text-xs opacity-60">No interfaces found</div>
                                        ) : sourceInterfaces.map((iface) => {
                                            const status = getInterfaceStatus(iface.id, connectFlow.sourceInterfaceId);
                                            const selected = connectFlow.sourceInterfaceId === iface.id;
                                            return (
                                                <button
                                                    key={iface.id}
                                                    className={`w-full px-3 py-2 text-left text-xs flex items-center justify-between border-b border-base-200 last:border-b-0 ${selected ? 'bg-primary text-primary-content' : ''} ${!status.available && !selected ? 'opacity-50 cursor-not-allowed' : ''}`}
                                                    disabled={(!status.available && !selected) || connectFlow.stage === 'saving'}
                                                    onClick={() => {
                                                        setConnectFlow(prev => ({ ...prev, sourceInterfaceId: iface.id, stage: 'target-device', error: null }));
                                                    }}
                                                >
                                                    <span className="truncate">{iface.name}</span>
                                                    {status.available || selected ? (
                                                        <span className="badge badge-success badge-xs">Available</span>
                                                    ) : (
                                                        <span className="badge badge-neutral badge-xs truncate max-w-[120px]">{status.remoteHost}</span>
                                                    )}
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>
                            )}

                            {(connectFlow.stage === 'target-interface' || connectFlow.stage === 'saving') && (
                                <div className="space-y-2">
                                    <div className="text-xs font-semibold opacity-70">Target Interface</div>
                                    <div className="max-h-44 overflow-auto border border-base-300 rounded-md">
                                        {targetInterfaces.length === 0 ? (
                                            <div className="p-3 text-xs opacity-60">No interfaces found</div>
                                        ) : targetInterfaces.map((iface) => {
                                            const status = getInterfaceStatus(iface.id, connectFlow.targetInterfaceId);
                                            const selected = connectFlow.targetInterfaceId === iface.id;
                                            return (
                                                <button
                                                    key={iface.id}
                                                    className={`w-full px-3 py-2 text-left text-xs flex items-center justify-between border-b border-base-200 last:border-b-0 ${selected ? 'bg-primary text-primary-content' : ''} ${!status.available && !selected ? 'opacity-50 cursor-not-allowed' : ''}`}
                                                    disabled={(!status.available && !selected) || connectFlow.stage === 'saving'}
                                                    onClick={() => setConnectFlow(prev => ({ ...prev, targetInterfaceId: iface.id, error: null }))}
                                                >
                                                    <span className="truncate">{iface.name}</span>
                                                    {status.available || selected ? (
                                                        <span className="badge badge-success badge-xs">Available</span>
                                                    ) : (
                                                        <span className="badge badge-neutral badge-xs truncate max-w-[120px]">{status.remoteHost}</span>
                                                    )}
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>
                            )}

                            {connectFlow.stage === 'target-interface' && (
                                <button
                                    className="btn btn-primary btn-sm"
                                    disabled={!connectFlow.targetInterfaceId || !connectFlow.sourceInterfaceId}
                                    onClick={commitConnection}
                                >
                                    Establish Connection
                                </button>
                            )}
                        </div>
                    </div>
                )}
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
