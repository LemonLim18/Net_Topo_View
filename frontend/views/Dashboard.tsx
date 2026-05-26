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
import { CONTROLLER_NODE_ID } from '../constants';
import { useWebSocket } from '../src/hooks/useWebSocket';
import { API_CONFIG } from '../src/config';
import { topologyApi, netboxApi, type DeviceRecord, type InterfaceRecord, type LinkRecord } from '../src/services/api';
import { useDeviceContext } from '../contexts/DeviceContext';
import { Terminal, Info, RefreshCw, Cable, AlertTriangle, Loader2 } from 'lucide-react';
import { Toast } from '../src/utils/swal';
import { sshApi } from '../src/services/api';
import { validateLinkInterfaceCompatibility } from '../src/utils/linkValidation';
import {
  formatIpInput,
  formatSubnetMaskInput,
  isValidIp,
  isValidSubnetMask,
} from '../src/utils/validation';

type TopologyView = 'actual' | 'planned' | 'diff';

const DASHBOARD_VIEW_KEY = 'dashboard.topologyView';
const DASHBOARD_NODE_KEY = 'dashboard.selectedNodeId';
const DASHBOARD_PANEL_HEIGHT_KEY = 'dashboard.panelHeight';
const DASHBOARD_GRID_VISIBLE_KEY = 'dashboard.gridVisible';
const DASHBOARD_SSH_PANEL_HEIGHT_KEY = 'dashboard.sshPanelHeight';
const DASHBOARD_SSH_MINIMIZED_KEY = 'dashboard.sshMinimized';
const DASHBOARD_PLANNED_CACHE_KEY = 'dashboard.plannedTopologyCache';

// Helper to convert abbreviated interface names to full names
const expandInterfaceName = (abbr: string): string => {
  const raw = String(abbr || '').trim();
  if (!raw) return raw;
  if (/^GigabitEthernet/i.test(raw)) return raw;
  if (/^FastEthernet/i.test(raw)) return raw;
  if (/^TenGigabitEthernet/i.test(raw)) return raw;
  if (/^TwentyFiveGigE/i.test(raw)) return raw;
  if (/^FortyGigabitEthernet/i.test(raw)) return raw;
  if (/^HundredGigE/i.test(raw)) return raw;
  if (/^Port-Channel/i.test(raw)) return raw;
  if (/^Loopback/i.test(raw)) return raw;
  if (/^Tunnel/i.test(raw)) return raw;
  if (/^Serial/i.test(raw)) return raw;
  if (/^Vlan/i.test(raw)) return raw;
  if (/^Ethernet/i.test(raw)) return raw;

  if (/^Eth/i.test(raw)) return raw.replace(/^Eth/i, 'Ethernet');
  if (/^Et/i.test(raw)) return raw.replace(/^Et/i, 'Ethernet');
  if (/^Gi/i.test(raw)) return raw.replace(/^Gi/i, 'GigabitEthernet');
  if (/^Fa/i.test(raw)) return raw.replace(/^Fa/i, 'FastEthernet');
  if (/^Te/i.test(raw)) return raw.replace(/^Te/i, 'TenGigabitEthernet');
  if (/^Twe/i.test(raw)) return raw.replace(/^Twe/i, 'TwentyFiveGigE');
  if (/^Fo/i.test(raw)) return raw.replace(/^Fo/i, 'FortyGigabitEthernet');
  if (/^Hu/i.test(raw)) return raw.replace(/^Hu/i, 'HundredGigE');
  if (/^Po/i.test(raw)) return raw.replace(/^Po/i, 'Port-Channel');
  if (/^Lo/i.test(raw)) return raw.replace(/^Lo/i, 'Loopback');
  if (/^Tu/i.test(raw)) return raw.replace(/^Tu/i, 'Tunnel');
  if (/^Se/i.test(raw)) return raw.replace(/^Se/i, 'Serial');
  if (/^Vl/i.test(raw)) return raw.replace(/^Vl/i, 'Vlan');
  return raw;
};

const normalizeInterfaceName = (name: string): string => {
  return expandInterfaceName(name).replace(/\s+/g, '').toLowerCase();
};

const resolveInterfaceIp = (interfaceList: Array<Record<string, string>> | undefined, ifaceName: string): string => {
  if (!Array.isArray(interfaceList) || interfaceList.length === 0) return '';
  const wanted = normalizeInterfaceName(ifaceName);
  if (!wanted) return '';

  for (const row of interfaceList) {
    for (const [key, value] of Object.entries(row || {})) {
      const actual = normalizeInterfaceName(key);
      if (actual === wanted) return String(value || '').trim();
    }
  }
  return '';
};

/** Map hostname → device id (exact + lowercase) for inventory / NetBox rows. */
const buildHostnameToDeviceId = (devices: DeviceRecord[]): Map<string, number> => {
  const m = new Map<string, number>();
  for (const d of devices) {
    const h = (d.hostname || '').trim();
    if (!h) continue;
    m.set(h, d.id);
    m.set(h.toLowerCase(), d.id);
  }
  return m;
};

const resolveDeviceId = (hostnameToId: Map<string, number>, hostname: string): number | undefined => {
  const h = (hostname || '').trim();
  if (!h) return undefined;
  return hostnameToId.get(h) ?? hostnameToId.get(h.toLowerCase());
};

const undirectedPairKey = (a: string, b: string): string => [a, b].sort().join('\0');

/**
 * Attach netbox link id when strict interface matching fails (LLDP vs DB naming).
 * Add rows for links that exist in inventory but are missing from topology edgeLink.
 */
const mergeInventoryLinksIntoTopologyLinks = (
  links: D3Link[],
  nodes: D3Node[],
  deviceLinks: LinkRecord[],
  hostnameToId: Map<string, number>
): void => {
  const canonicalByLower = new Map<string, string>();
  for (const n of nodes) {
    canonicalByLower.set(n.id.toLowerCase(), n.id);
  }
  const hasNode = (name: string) =>
    nodes.some((n) => n.id === name) || canonicalByLower.has(name.trim().toLowerCase());
  const canonical = (name: string) =>
    nodes.find((n) => n.id === name)?.id ?? canonicalByLower.get(name.trim().toLowerCase()) ?? name;

  const seenPairs = new Set<string>();
  for (const l of links) {
    const s = typeof l.source === 'string' ? l.source : (l.source as D3Node).id;
    const t = typeof l.target === 'string' ? l.target : (l.target as D3Node).id;
    seenPairs.add(undirectedPairKey(s, t));
  }

  for (const l of links) {
    if (l.netboxLinkId != null) continue;
    const s = typeof l.source === 'string' ? l.source : (l.source as D3Node).id;
    const t = typeof l.target === 'string' ? l.target : (l.target as D3Node).id;
    const sid = resolveDeviceId(hostnameToId, s);
    const tid = resolveDeviceId(hostnameToId, t);
    if (sid == null || tid == null) continue;
    const match = deviceLinks.find(
      (dl) =>
        (dl.src_device_id === sid && dl.dst_device_id === tid) ||
        (dl.src_device_id === tid && dl.dst_device_id === sid)
    );
    if (match) {
      l.netboxLinkId = match.id;
    }
  }

  for (const dl of deviceLinks) {
    const a = (dl.src_hostname || '').trim();
    const b = (dl.dst_hostname || '').trim();
    if (!a || !b || !hasNode(a) || !hasNode(b)) continue;
    const ca = canonical(a);
    const cb = canonical(b);
    const pk = undirectedPairKey(ca, cb);
    if (seenPairs.has(pk)) continue;
    seenPairs.add(pk);
    links.push({
      source: ca,
      target: cb,
      sourceInterface: dl.src_interface_name || '—',
      targetInterface: dl.dst_interface_name || '—',
      sourceIp: '',
      targetIp: '',
      info: {
        remote_node: cb,
        remote_interface: dl.dst_interface_name || '',
        remote_ip: '',
      },
      netboxLinkId: dl.id,
    });
  }
};

// --- Custom Interface Dropdown to support Badges ---
const InterfaceSelect: React.FC<{
  interfaces: InterfaceRecord[];
  value: number;
  onChange: (val: number) => void;
  disabled?: boolean;
  compact?: boolean;
  onCreateIface?: () => void;
  onEditIface?: (iface: InterfaceRecord) => void;
}> = ({ interfaces, value, onChange, disabled, compact, onCreateIface, onEditIface }) => {
  const selected = interfaces.find(i => i.id === value);

  // Separate available from unavailable
  const availableIfaces = interfaces.filter(i => !i.connected_to);
  const connectedIfaces = interfaces.filter(i => i.connected_to);

  const closeDropdown = () => {
    const elem = document.activeElement as HTMLElement;
    if (elem) elem.blur();
  };

  const handleSelect = (id: number) => {
    onChange(id);
    closeDropdown();
  };

  const handleCreate = () => {
    if (onCreateIface) {
      onCreateIface();
      closeDropdown();
    }
  };

  return (
    <div className="dropdown w-full iface-dropdown">
      <label 
        tabIndex={0} 
        className={`select select-bordered select-sm w-full flex items-center justify-between text-xs cursor-pointer ${disabled ? 'btn-disabled opacity-50' : ''}`}
      >
        <div className="truncate flex-1 text-left min-w-0 pr-1">
          {value === 0 ? <span className="text-base-content/50">Select interface…</span> : 
            selected ? (
              <div className="flex items-center gap-1.5 min-w-0 overflow-hidden">
                <span className="truncate">{selected.name}</span>
                {selected.ip_address && <span className="text-primary font-bold opacity-60 text-[10px] shrink-0">({selected.ip_address})</span>}
              </div>
            ) : 'Select interface…'}
        </div>
      </label>
      
      {!disabled && (
        <ul tabIndex={0} className="dropdown-content z-100 menu p-2 shadow bg-base-200 border border-base-300 rounded-box w-full max-h-64 flex-nowrap overflow-y-auto mt-1">
          <li>
            <a onClick={() => handleSelect(0)} className={value === 0 ? 'bg-base-300' : ''}>
              <span className="text-base-content/50 text-xs text-left">Select interface…</span>
            </a>
          </li>
          
          {availableIfaces.length === 0 && connectedIfaces.length === 0 && (
             <li className="disabled"><span className="opacity-50 text-[10px] italic p-3 text-left">No interfaces found</span></li>
          )}
          
          {availableIfaces.length > 0 && (
            <li className="menu-title px-4 py-1">
              <div className="flex items-center gap-2 text-success/80 text-[10px] tracking-wider uppercase">
                <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse shrink-0"></span>
                <span>Available</span>
              </div>
            </li>
          )}
          {availableIfaces.map(i => (
            <li key={i.id} className="group mr-1.5">
              <div className="flex items-center p-0">
                <a 
                  onClick={() => handleSelect(i.id)}
                  className={`flex-1 flex justify-between items-center py-1.5 px-3 min-w-0 ${value === i.id ? 'bg-primary text-primary-content rounded-r-none' : ''}`}
                >
                  <div className="flex flex-col flex-1 min-w-0 mr-2">
                    <span className="text-[11px] font-semibold truncate leading-tight">{i.name}</span>
                    {i.ip_address && <span className={`text-[9px] truncate ${value === i.id ? 'text-primary-content/70' : 'opacity-60'}`}>{i.ip_address}</span>}
                  </div>
                  {value !== i.id && (
                    <div className="flex items-center justify-center shrink-0 min-w-5" title="Available">
                      {compact ? (
                        <div className="w-1.5 h-1.5 rounded-full bg-success animate-pulse shadow-[0_0_5px_rgba(52,211,153,0.5)]"></div>
                      ) : (
                        <span className="badge badge-success badge-xs h-3.5 min-h-0 text-[9px] opacity-70 px-1 border-none">Available</span>
                      )}
                    </div>
                  )}
                </a>
                {onEditIface && (
                  <button 
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onEditIface(i); }}
                    className={`p-1.5 transition-opacity ${value === i.id ? 'bg-primary text-primary-content rounded-l-none' : 'text-base-content opacity-50 hover:opacity-100!'}`}
                    title="Edit IP"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                  </button>
                )}
              </div>
            </li>
          ))}

          {connectedIfaces.length > 0 && (
            <li className="menu-title px-4 py-1 mt-2">
              <div className="flex items-center gap-2 text-base-content/40 text-[10px] tracking-wider uppercase">
                <span className="w-1.5 h-1.5 rounded-full bg-base-content/30 shrink-0"></span>
                {/* <span>Already Connected (Unavailable)</span> */}
                <span>Unavailable</span>
              </div>
            </li>
          )}
          {connectedIfaces.map(i => (
            <li key={i.id} className="disabled group mr-1.5">
              <div className="flex items-center p-0">
                <span className="flex-1 flex justify-between items-center py-1.5 px-3 min-w-0 opacity-40 cursor-not-allowed">
                  <div className="flex flex-col flex-1 min-w-0 mr-2">
                    <span className="text-[11px] font-semibold truncate leading-tight">{i.name}</span>
                    {i.ip_address && <span className="text-[9px] truncate opacity-80">{i.ip_address}</span>}
                  </div>
                  <span className="badge badge-ghost badge-xs h-3.5 min-h-0 text-[8px] shrink-0 px-1 truncate max-w-[60px]" title={`Connected to ${i.connected_to?.device.name}`}>
                    {i.connected_to?.device.name}
                  </span>
                </span>
                {onEditIface && (
                  <button 
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onEditIface(i); }}
                    className="p-1.5 text-base-content cursor-pointer opacity-70 hover:opacity-100! transition-opacity"
                    title="Edit IP"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                  </button>
                )}
              </div>
            </li>
          ))}

          {onCreateIface && (
            <li className="p-1.5 mt-1 border-t border-base-content/5">
              <button 
                type="button" 
                onClick={handleCreate} 
                className="flex items-center gap-2 px-3 py-1.5 w-full text-[11px] font-semibold text-primary hover:bg-primary/10 rounded-lg transition-colors"
              >
                <div className="w-4 h-4 rounded-md flex items-center justify-center bg-primary/20 text-primary shrink-0">
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                </div>
                <span>Create Interface</span>
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
};

const processTopologyData = (
    data: TopologyData | null,
    deviceLinks: LinkRecord[] = [],
    devices: DeviceRecord[] = [],
    options?: { hideDownNodes?: boolean },
) => {
    if (!data) { console.debug('[Topo] processTopologyData called with null'); return { nodes: [], links: [] }; }
    const nodeKeys = Object.keys(data.nodes);
    console.debug(`[Topo] processTopologyData: ${nodeKeys.length} nodes:`, nodeKeys);

    const hostnameToId = buildHostnameToDeviceId(devices);

    const rawNodes: D3Node[] = Object.keys(data.nodes).map(nodeId => ({
        id: nodeId,
        ...data.nodes[nodeId],
    }));
    const nodes: D3Node[] = (options?.hideDownNodes
        ? rawNodes.filter((n) => {
            const down = Boolean((n as any).down);
            const ifaceList = (n as any).interfaceList;
            const hasInterfaces = Array.isArray(ifaceList)
                && ifaceList.some((row: unknown) => !!row && typeof row === 'object' && Object.keys(row as Record<string, unknown>).length > 0);
            return !down && hasInterfaces;
        })
        : rawNodes);
    const visibleNodeIds = new Set(nodes.map((n) => n.id));

    const links: D3Link[] = [];
    const linkSet = new Set<string>();

    Object.entries(data.nodes).forEach(([sourceId, sourceDetails]) => {
        if (sourceDetails.edgeLink) {
            Object.entries(sourceDetails.edgeLink).forEach(([localIfaceAbbr, linkInfo]) => {
                const targetId = linkInfo.remote_node;
                const targetDetails = data.nodes[targetId];
                if (!targetDetails) return;
                if (options?.hideDownNodes && (!visibleNodeIds.has(sourceId) || !visibleNodeIds.has(targetId))) {
                    return;
                }

                const linkKey1 = `${sourceId}-${targetId}`;
                const linkKey2 = `${targetId}-${sourceId}`;

                if (!linkSet.has(linkKey1) && !linkSet.has(linkKey2)) {
                    const localIfaceFull = expandInterfaceName(localIfaceAbbr);
                    const remoteIfaceFull = expandInterfaceName(linkInfo.remote_interface);
                    const sourceIp = resolveInterfaceIp(sourceDetails.interfaceList, localIfaceAbbr);
                    const targetIp = resolveInterfaceIp(targetDetails.interfaceList, linkInfo.remote_interface);

                    // Match inventory link for delete: prefer exact iface names, then any link between the pair.
                    const srcDevId = resolveDeviceId(hostnameToId, sourceId);
                    const dstDevId = resolveDeviceId(hostnameToId, targetId);
                    let netboxLink = deviceLinks.find(
                        (l) =>
                            (l.src_device_id === srcDevId &&
                                l.dst_device_id === dstDevId &&
                                (l.src_interface_name === localIfaceAbbr ||
                                    l.src_interface_name === localIfaceFull)) ||
                            (l.src_device_id === dstDevId &&
                                l.dst_device_id === srcDevId &&
                                (l.src_interface_name === linkInfo.remote_interface ||
                                    l.src_interface_name === remoteIfaceFull))
                    );
                    if (!netboxLink && srcDevId != null && dstDevId != null) {
                        netboxLink = deviceLinks.find(
                            (l) =>
                                (l.src_device_id === srcDevId && l.dst_device_id === dstDevId) ||
                                (l.src_device_id === dstDevId && l.dst_device_id === srcDevId)
                        );
                    }

                    links.push({
                        source: sourceId,
                        target: targetId,
                        sourceInterface: localIfaceAbbr,
                        targetInterface: linkInfo.remote_interface,
                        sourceIp,
                        targetIp,
                        info: linkInfo,
                        netboxLinkId: netboxLink?.id
                    });
                    linkSet.add(linkKey1);
                }
            });
        }
    });

    mergeInventoryLinksIntoTopologyLinks(links, nodes, deviceLinks, hostnameToId);

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

    // --- Data from Context ---
    const { 
        devices: connectDevices, 
        links: allLinks, 
        refreshAll: refreshConnectData 
    } = useDeviceContext();
    
    // --- Planned topology from local DB ---
    const [plannedTopology, setPlannedTopology] = useState<TopologyData | null>(() => {
        const cached = localStorage.getItem(DASHBOARD_PLANNED_CACHE_KEY);
        return cached ? JSON.parse(cached) : null;
    });
    
    // --- Standard Add Link Modal state ---
    const [showLinkModal, setShowLinkModal] = useState(false);
    const [linkForm, setLinkForm] = useState({
        src_device_id: 0,
        src_interface_id: 0,
        dst_device_id: 0,
        dst_interface_id: 0,
    });
    const [linkFormError, setLinkFormError] = useState<string | null>(null);
    const [srcInterfaces, setSrcInterfaces] = useState<InterfaceRecord[]>([]);
    const [dstInterfaces, setDstInterfaces] = useState<InterfaceRecord[]>([]);
    
    // --- Drawer Resizer State ---
    const [linkDrawerWidth, setLinkDrawerWidth] = useState(300);

    const handleLinkDrawerDragStart = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        const startX = e.clientX;
        const startWidth = linkDrawerWidth;

        const handleMouseMove = (moveEvent: MouseEvent) => {
            const deltaX = startX - moveEvent.clientX; // calculate how far we moved mouse left
            const newWidth = Math.max(300, Math.min(startWidth + deltaX, window.innerWidth - 100));
            setLinkDrawerWidth(newWidth);
        };

        const handleMouseUp = () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
        
        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
    }, [linkDrawerWidth]);
    
    const [ifaceFormError, setIfaceFormError] = useState<string | null>(null);
    
    // --- Interface Creation Modal state ---
    const [showIfaceModal, setShowIfaceModal] = useState(false);
    const [ifaceCreateSide, setIfaceCreateSide] = useState<'src' | 'dst' | null>(null);
    const [ifaceForm, setIfaceForm] = useState({
      name: '',
      ip_address: '',
      subnet_mask: '255.255.255.0',
      mac_address: '',
      speed: '',
    });

    // --- Interface Edit Modal state ---
    const [showEditIfaceModal, setShowEditIfaceModal] = useState(false);
    const [editingIface, setEditingIface] = useState<InterfaceRecord | null>(null);
    const [editIfaceForm, setEditIfaceForm] = useState({
      ip_address: '',
    });
    const [editIfaceFormError, setEditIfaceFormError] = useState<string | null>(null);

    // refreshConnectData is now the context's refreshAll

    const fetchPlanned = useCallback(async (updateCache = false) => {
        try {
            const data = await netboxApi.getLocalPlannedTopology();
            const topoData = data as any as TopologyData;
            setPlannedTopology(topoData);
            
            if (updateCache) {
                localStorage.setItem(DASHBOARD_PLANNED_CACHE_KEY, JSON.stringify(topoData));
            }
        } catch (e) {
            console.warn('[Topo] Failed to fetch local planned topology:', e);
        }
    }, []);

    useEffect(() => {
        fetchPlanned(false); // Initial load -> Don't update cache
    }, [fetchPlanned]);

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

    // Bootstrap Real-Time graph from the latest persisted crawl snapshot.
    // This prevents an empty Real-Time view when WS has not pushed yet.
    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const snap = await topologyApi.get();
                const count = snap?.nodes ? Object.keys(snap.nodes).length : 0;
                if (active && count > 0) {
                    setWsTopology(snap);
                }
            } catch {
                // Ignore bootstrap failures; WS refresh path remains active.
            }
        })();
        return () => {
            active = false;
        };
    }, [setWsTopology]);

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
    const [isNetboxOpLoading, setIsNetboxOpLoading] = useState(false);
    const [netboxOpMessage, setNetboxOpMessage] = useState('Syncing with NetBox...');

    const handleRefreshTopology = useCallback(async () => {
        setIsRefreshing(true);
        try {
            // 1. Trigger cache-updating refresh of devices/links
            await refreshConnectData(true);
            await fetchPlanned(true);

            // 2. Trigger a new crawl on the backend
            await topologyApi.crawl();
            Toast.success('Crawl Triggered', 'Manual refresh started. Cache updated, topology will update via WebSocket when done.');

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
        const planned = processTopologyData(plannedTopology, allLinks, connectDevices);
        const actual = processTopologyData(realTimeTopology, allLinks, connectDevices, { hideDownNodes: true });

        if (topologyView === 'planned') return planned;
        if (topologyView === 'actual') return actual;

        // Diff view: planned vs real-time (crawl/WebSocket). If live topology is not loaded
        // yet, `actual` is already empty — still merge so planned nodes/links show instead of
        // a blank Connection List.
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

    }, [topologyView, realTimeTopology, plannedTopology, allLinks, connectDevices]);

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
    
    // --- Context Menu handler ---
    const handleNodeContextMenu = useCallback((nodeId: string, x: number, y: number) => {
        setContextMenu({ nodeId, x, y });
    }, []);

    const closeContextMenu = useCallback(() => setContextMenu(null), []);

    const openLinkModal = useCallback(async (sourceNodeId: string) => {
        setIsNetboxOpLoading(true);
        setNetboxOpMessage('Loading devices and interfaces from NetBox...');
        try {
            await refreshConnectData();
            const srcDev = connectDevices.find((d: DeviceRecord) => d.hostname === sourceNodeId);
            if (!srcDev) {
                Toast.error('Connect Failed', `Source device '${sourceNodeId}' is not in local inventory`);
                return;
            }
            
            const ifaceResp = await netboxApi.listInterfaces(srcDev.id);
            setSrcInterfaces(ifaceResp.interfaces || []);
            setDstInterfaces([]);
            
            setLinkForm({
                src_device_id: srcDev.id,
                src_interface_id: 0,
                dst_device_id: 0,
                dst_interface_id: 0,
            });
            setLinkFormError(null);
            setShowLinkModal(true);
        } catch (err: any) {
            Toast.error('Connect Failed', err?.message || 'Unable to open connection modal');
        } finally {
            setIsNetboxOpLoading(false);
        }
    }, [refreshConnectData]);

    const loadIfacesForLink = async (deviceId: number, side: 'src' | 'dst') => {
        if (!deviceId) return;
        try {
            const res = await netboxApi.listInterfaces(deviceId);
            if (side === 'src') setSrcInterfaces(res.interfaces || []);
            else setDstInterfaces(res.interfaces || []);
        } catch { /* ignore */ }
    };

    const handleSaveLink = async () => {
        if (!linkForm.src_device_id || !linkForm.dst_device_id) {
            setLinkFormError('Both source and destination devices are required');
            return;
        }
        const srcIface = srcInterfaces.find((i) => i.id === linkForm.src_interface_id);
        const dstIface = dstInterfaces.find((i) => i.id === linkForm.dst_interface_id);
        const compatibilityError = validateLinkInterfaceCompatibility(srcIface, dstIface);
        if (compatibilityError) {
            setLinkFormError(compatibilityError);
            return;
        }
        setLinkFormError(null);
        setIsNetboxOpLoading(true);
        setNetboxOpMessage('Creating link in NetBox and refreshing topology...');
        try {
            await netboxApi.createLink({
                src_device_id: linkForm.src_device_id,
                src_interface_id: linkForm.src_interface_id || undefined,
                dst_device_id: linkForm.dst_device_id,
                dst_interface_id: linkForm.dst_interface_id || undefined,
                discovery_protocol: 'manual',
            });
            
            const data = await netboxApi.getLocalPlannedTopology();
            setPlannedTopology(data as any as TopologyData);
            try {
                localStorage.setItem(DASHBOARD_PLANNED_CACHE_KEY, JSON.stringify(data));
            } catch {
                /* ignore quota / private mode */
            }

            // Refresh interfaces for both sides to show "Connected" status immediately
            await Promise.all([
                loadIfacesForLink(linkForm.src_device_id, 'src'),
                loadIfacesForLink(linkForm.dst_device_id, 'dst'),
                refreshConnectData()
            ]);

            // Connection List reads `graphData.links`. Real-Time view uses WebSocket/crawl data
            // only, which is not pushed when a link is saved locally — switch to Planned so the
            // new edge appears immediately (Diff also uses planned; user may stay on Diff).
            setTopologyView((v) => (v === 'actual' ? 'planned' : v));

            Toast.success(
                'Link created',
                'Connection synchronized. The list shows Planned/Diff data; use Real-Time after Refresh/scan for live discovery.'
            );
            setShowLinkModal(false);
        } catch (e: any) {
            setLinkFormError(e.message || 'Failed to create link');
        } finally {
            setIsNetboxOpLoading(false);
        }
    };

    const handleDeleteLink = useCallback(async (linkId: number) => {
        setIsNetboxOpLoading(true);
        setNetboxOpMessage('Deleting link from NetBox...');
        try {
            await netboxApi.deleteLink(linkId);
            Toast.success('Link Deleted', 'Connection removed from NetBox.');
            
            // Refresh everything
            const data = await netboxApi.getLocalPlannedTopology();
            setPlannedTopology(data as any as TopologyData);
            await refreshConnectData();
            
            // If the add link side-panel is open, refresh those lists too
            if (linkForm.src_device_id) loadIfacesForLink(linkForm.src_device_id, 'src');
            if (linkForm.dst_device_id) loadIfacesForLink(linkForm.dst_device_id, 'dst');
        } catch (e: any) {
            Toast.error('Delete Failed', e.message || 'Unable to delete link');
        } finally {
            setIsNetboxOpLoading(false);
        }
    }, [refreshConnectData]);

    const handleSaveInterface = async () => {
        const targetDeviceId = ifaceCreateSide === 'src' ? linkForm.src_device_id : linkForm.dst_device_id;
        if (!targetDeviceId) {
            setIfaceFormError('Target device not selected.');
            return;
        }

        if (!ifaceForm.name.trim()) {
            setIfaceFormError('Interface name is required.');
            return;
        }

        if (ifaceForm.ip_address && !isValidIp(ifaceForm.ip_address)) {
            setIfaceFormError('IP address is invalid (must be 4 octets between 0 and 255)');
            return;
        }

        if (ifaceForm.subnet_mask && !isValidSubnetMask(ifaceForm.subnet_mask)) {
            setIfaceFormError('Subnet Mask is invalid (must be 0-32 or a valid dot-decimal mask)');
            return;
        }

        try {
            setIsNetboxOpLoading(true);
            setNetboxOpMessage('Creating interface in NetBox...');
            const iface = await netboxApi.createInterface(targetDeviceId, {
                ...ifaceForm,
                status: 'planned'
            });

            Toast.success('Interface Created', `Created ${iface.name}`);
            
            // Reload interfaces and auto-select
            await Promise.all([
                loadIfacesForLink(targetDeviceId, ifaceCreateSide!),
                refreshConnectData()
            ]);
            
            if (ifaceCreateSide === 'src') {
                setLinkForm(prev => ({ ...prev, src_interface_id: iface.id }));
            } else {
                setLinkForm(prev => ({ ...prev, dst_interface_id: iface.id }));
            }
            
            setShowIfaceModal(false);
        } catch (e: any) {
            setIfaceFormError(e.message || 'Failed to create interface');
        } finally {
            setIsNetboxOpLoading(false);
        }
    };

    const openEditIfaceModal = (iface: InterfaceRecord) => {
      setEditingIface(iface);
      setEditIfaceForm({ ip_address: iface.ip_address || '' });
      setEditIfaceFormError(null);
      setShowEditIfaceModal(true);
    };

    const handleEditPanelInterface = useCallback(async (ifaceName: string) => {
        if (!selectedNodeId) return;
        
        // Find the device in connectDevices
        const dev = connectDevices.find(d => d.hostname === selectedNodeId);
        if (!dev) {
            Toast.error('Edit Failed', `Device '${selectedNodeId}' not found in inventory.`);
            return;
        }

        try {
            setIsNetboxOpLoading(true);
            setNetboxOpMessage('Loading interface details from NetBox...');
            // Fetch interfaces to get the ID
            const res = await netboxApi.listInterfaces(dev.id);
            // Match name exactly or abbreviated form
            const iface = res.interfaces.find(i => 
                i.name === ifaceName || 
                expandInterfaceName(i.name) === expandInterfaceName(ifaceName) ||
                i.name === expandInterfaceName(ifaceName)
            );
            
            if (!iface) {
                Toast.error('Edit Failed', `Interface '${ifaceName}' not found in NetBox for ${dev.hostname}`);
                return;
            }

            openEditIfaceModal(iface);
        } catch (err: any) {
            Toast.error('Failed to load interface', err.message);
        } finally {
            setIsNetboxOpLoading(false);
        }
    }, [selectedNodeId, connectDevices]);

    const handleUpdateInterface = async () => {
      if (!editingIface) return;

      if (editIfaceForm.ip_address && !isValidIp(editIfaceForm.ip_address)) {
        setEditIfaceFormError('IP address is invalid (must be 4 octets between 0 and 255)');
        return;
      }
      
      try {
        setIsNetboxOpLoading(true);
        setNetboxOpMessage('Updating interface in NetBox...');
        await netboxApi.updateInterface(editingIface.device_id, editingIface.id, {
          ip_address: editIfaceForm.ip_address,
        });
        
        // Refresh interface lists in the drawer if those devices are currently active
        if (linkForm.src_device_id === editingIface.device_id) loadIfacesForLink(editingIface.device_id, 'src');
        if (linkForm.dst_device_id === editingIface.device_id) loadIfacesForLink(editingIface.device_id, 'dst');
        
        // Refresh global inventory and planned topology
        refreshConnectData();
        const freshPlanned = await netboxApi.getLocalPlannedTopology();
        setPlannedTopology(freshPlanned as any as TopologyData);
        
        Toast.success('Interface Updated', `Updated IP address for ${editingIface.name}`);
        setShowEditIfaceModal(false);
      } catch (e: any) {
        setEditIfaceFormError(e.message || 'Failed to update interface');
      } finally {
        setIsNetboxOpLoading(false);
      }
    };

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
    const handleSSHConnect = useCallback(async (
        hostname: string,
        host: string,
        username: string,
        password: string,
        port: number,
        saveToVault: boolean,
    ) => {
        const targetHostname = hostname || sshConnectTarget?.hostname || host;
        if (!targetHostname) return;

        try {
            const result = await sshApi.connectWithCreds(
                targetHostname,
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
                Toast.success('SSH Connected', `Connected to ${targetHostname} — credentials saved to vault`);
            } else {
                Toast.success('SSH Connected', `Connected to ${targetHostname}`);
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
                label: 'Connect',
                icon: <Cable size={14} />,
                onClick: () => openLinkModal(contextMenu.nodeId),
                disabled: contextMenu.nodeId === CONTROLLER_NODE_ID,
            },
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
    }, [contextMenu, openSSHTerminal, handleNodeClick, openLinkModal]);

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
            {isNetboxOpLoading && (
                <div className="absolute inset-0 bg-base-300/45 backdrop-blur-[1px] flex items-center justify-center z-20" role="status" aria-label="Syncing NetBox data">
                    <div className="text-center px-6">
                        <Loader2 className="w-8 h-8 mx-auto text-primary animate-spin" />
                        <p className="mt-2 text-base-content/85 text-sm">{netboxOpMessage}</p>
                    </div>
                </div>
            )}
            <div className="p-2 bg-base-300/80 backdrop-blur-sm shrink-0 z-10 flex items-center justify-between px-4">
                <div className="flex items-center gap-2 text-sm text-base-content/70" aria-live="polite">
                   {statusIndicator[status]}
                </div>
                <div className="join">
                    {/* info, accent, primary, secondary */}
                    <button className={`btn join-item btn-sm ${topologyView === 'planned' ? 'btn-active text-accent' : ''}`} onClick={() => setTopologyView('planned')}>Planned</button>
                    <button className={`btn join-item btn-sm ${topologyView === 'actual' ? 'btn-active text-accent' : ''}`} onClick={() => setTopologyView('actual')}>Real-Time</button>
                    <button className={`btn join-item btn-sm ${topologyView === 'diff' ? 'btn-active text-accent' : ''}`} onClick={() => setTopologyView('diff')}>Diff</button>
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

                {/* Replaced floating connection panel with a standard modal dialog below */}
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
                    <TopologyTable 
                        links={graphData.links} 
                        onNodeClick={handleNodeClick} 
                        onDeleteLink={handleDeleteLink}
                        showStatus={topologyView === 'diff'}
                    />
                </div>
                    <NodeDetailPanel 
                        node={selectedNodeData} 
                        onClose={handlePanelClose} 
                        onEditInterface={handleEditPanelInterface}
                    />

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

            {showLinkModal && (
                <div className="fixed top-16 bottom-0 right-0 z-50 flex pointer-events-none" style={{ width: `${linkDrawerWidth}px` }}>
                    <div className="relative w-full h-full bg-base-100 shadow-2xl flex flex-col border-l border-base-300 pointer-events-auto overflow-hidden">
                        
                        {/* Drag Handle */}
                        <div 
                            className="absolute left-0 top-0 bottom-0 w-2 cursor-col-resize hover:bg-primary/20 transition-colors z-60"
                            onMouseDown={handleLinkDrawerDragStart}
                        />

                        <div className="p-4 border-b border-base-200 flex justify-between items-center bg-base-200/50">
                            <h3 className="font-bold text-base flex items-center gap-2">
                                <Cable size={18} className="text-primary" />
                                Add Link (Neighbor)
                            </h3>
                            <button className="btn btn-ghost btn-xs btn-circle" onClick={() => setShowLinkModal(false)}>✕</button>
                        </div>

                        <div className="flex-1 overflow-y-auto p-4  scrollbar-thin">
                            {linkFormError && (
                                <div className="alert alert-error mb-2 p-3 text-xs">
                                    <AlertTriangle className="w-4 h-4" />
                                    <span>{linkFormError}</span>
                                </div>
                            )}
                            
                            {/* Source side */}
                            <div className="px-4 py-6 bg-base-200 rounded-xl border border-base-300 space-y-3">
                                <p className="text-[10px] font-bold opacity-60 uppercase tracking-widest flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full bg-primary"></span>
                                    Source
                                </p>
                                <select
                                    className="select select-bordered select-sm w-full text-xs"
                                    value={linkForm.src_device_id}
                                    onChange={(e) => {
                                        const id = Number(e.target.value);
                                        setLinkForm({ ...linkForm, src_device_id: id, src_interface_id: 0 });
                                        loadIfacesForLink(id, 'src');
                                    }}
                                >
                                    <option value={0}>Select source device…</option>
                                    {connectDevices.map(d => (
                                        <option key={d.id} value={d.id}>{d.hostname} ({d.management_ip || 'no IP'})</option>
                                    ))}
                                </select>
                                
                                <InterfaceSelect 
                                  interfaces={srcInterfaces.filter(i => !i.name.toLowerCase().includes('management'))}
                                  value={linkForm.src_interface_id}
                                  onChange={(val) => setLinkForm({ ...linkForm, src_interface_id: val })}
                                  disabled={!linkForm.src_device_id}
                                  compact={linkDrawerWidth < 400}
                                  onCreateIface={() => {
                                      setIfaceCreateSide('src');
                                      setIfaceForm({ name: '', ip_address: '', subnet_mask: '255.255.255.0', mac_address: '', speed: '' });
                                      setIfaceFormError(null);
                                      setShowIfaceModal(true);
                                  }}
                                  onEditIface={openEditIfaceModal}
                                />
                            </div>

                            <div className="flex -my-2 justify-center items-center gap-2 relative z-10">
                                <div className="bg-base-100 rounded-full border border-base-300 p-1.5 shadow-sm relative -translate-y-0.5">
                                  <Cable className="w-6 h-6 opacity-40 rotate-90" />
                                </div>
                                {/* <div className='text-center opacity-40 text-xs'>Connect</div> */}
                            </div>

                            {/* Destination side */}
                            <div className="px-4 py-6 bg-base-200 rounded-xl border border-base-300 space-y-3">
                                <p className="text-[10px] font-bold opacity-60 uppercase tracking-widest flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full bg-secondary"></span>
                                    Destination
                                </p>
                                <select
                                    className="select select-bordered select-sm w-full text-xs"
                                    value={linkForm.dst_device_id}
                                    onChange={(e) => {
                                        const id = Number(e.target.value);
                                        setLinkForm({ ...linkForm, dst_device_id: id, dst_interface_id: 0 });
                                        loadIfacesForLink(id, 'dst');
                                    }}
                                >
                                    <option value={0}>Select destination device…</option>
                                    {connectDevices.map(d => (
                                        <option key={d.id} value={d.id}>{d.hostname} ({d.management_ip || 'no IP'})</option>
                                    ))}
                                </select>
                                
                                <InterfaceSelect 
                                  interfaces={dstInterfaces.filter(i => !i.name.toLowerCase().includes('management'))}
                                  value={linkForm.dst_interface_id}
                                  onChange={(val) => setLinkForm({ ...linkForm, dst_interface_id: val })}
                                  disabled={!linkForm.dst_device_id}
                                  compact={linkDrawerWidth < 400}
                                  onCreateIface={() => {
                                      setIfaceCreateSide('dst');
                                      setIfaceForm({ name: '', ip_address: '', subnet_mask: '255.255.255.0', mac_address: '', speed: '' });
                                      setIfaceFormError(null);
                                      setShowIfaceModal(true);
                                  }}
                                  onEditIface={openEditIfaceModal}
                                />
                            </div>
                        </div>

                        <div className="p-4 border-t border-base-200 bg-base-100">
                            <button 
                                className="btn btn-primary btn-sm w-full gap-2 text-xs" 
                                onClick={handleSaveLink}
                                disabled={isNetboxOpLoading || !linkForm.src_device_id || !linkForm.src_interface_id || !linkForm.dst_device_id || !linkForm.dst_interface_id}
                            >
                                {isNetboxOpLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Cable className="w-4 h-4" />}
                                {isNetboxOpLoading ? 'Working...' : 'Create Link'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* --- Add Interface Modal --- */}
            {showIfaceModal && (
                <div className="fixed inset-0 z-60 flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity" onClick={() => setShowIfaceModal(false)} />
                    <div className="relative w-full max-w-md bg-base-200 border border-base-300 shadow-2xl rounded-2xl overflow-hidden animate-in fade-in zoom-in duration-200">
                        <div className="bg-primary/10 px-6 py-4 border-b border-primary/20 flex items-center gap-3">
                            <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center text-primary">
                                <Cable size={18} />
                            </div>
                            <div>
                                <h3 className="font-bold text-lg leading-tight">Create Interface</h3>
                                <p className="text-[10px] uppercase tracking-wider opacity-60">Adding to {ifaceCreateSide === 'src' ? 'Source' : 'Destination'} Device</p>
                            </div>
                        </div>

                        <div className="p-6 space-y-4">
                            {ifaceFormError && (
                                <div className="alert alert-error text-xs py-2 shadow-sm rounded-lg">
                                    <AlertTriangle size={14} />
                                    <span>{ifaceFormError}</span>
                                </div>
                            )}

                            <div className="form-control">
                                <label className="label py-1">
                                    <span className="label-text text-[10px] uppercase font-bold opacity-70">Interface Name</span>
                                </label>
                                <input 
                                    type="text" 
                                    className="input input-bordered input-sm w-full bg-base-100 focus:input-primary text-xs" 
                                    placeholder="e.g. Ethernet1/1"
                                    value={ifaceForm.name}
                                    onChange={e => setIfaceForm({ ...ifaceForm, name: e.target.value })}
                                />
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div className="form-control">
                                    <label className="label py-1">
                                        <span className="label-text text-[10px] uppercase font-bold opacity-70">IP Address</span>
                                    </label>
                                    <input 
                                        type="text" 
                                        className="input input-bordered input-sm w-full bg-base-100 focus:input-primary text-xs" 
                                        placeholder="10.0.0.1/24"
                                        value={ifaceForm.ip_address}
                                        onChange={e => setIfaceForm({ ...ifaceForm, ip_address: formatIpInput(e.target.value) })}
                                    />
                                </div>
                                <div className="form-control">
                                    <label className="label py-1">
                                        <span className="label-text text-[10px] uppercase font-bold opacity-70">Subnet Mask</span>
                                    </label>
                                    <input 
                                        type="text" 
                                        className="input input-bordered input-sm w-full bg-base-100 focus:input-primary text-xs" 
                                        placeholder="255.255.255.0"
                                        value={ifaceForm.subnet_mask}
                                        onChange={e => setIfaceForm({ ...ifaceForm, subnet_mask: formatSubnetMaskInput(e.target.value) })}
                                    />
                                </div>
                            </div>
                        </div>

                        <div className="px-6 py-4 bg-base-300/50 border-t border-base-300 flex justify-end gap-2">
                            <button 
                                className="btn btn-ghost btn-sm text-xs h-9" 
                                onClick={() => setShowIfaceModal(false)}
                            >
                                Cancel
                            </button>
                            <button 
                                className="btn btn-primary btn-sm text-xs px-6 h-9" 
                                onClick={handleSaveInterface}
                                disabled={isNetboxOpLoading}
                            >
                                {isNetboxOpLoading ? (
                                    <span className="inline-flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Saving...</span>
                                ) : 'Add Interface'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
            {/* --- Edit Interface Modal --- */}
            {showEditIfaceModal && (
                <div className="modal modal-open">
                    <div className="modal-box max-w-md bg-base-200 border border-base-content/10 shadow-2xl p-0 overflow-hidden">
                        <div className="bg-linear-to-r from-primary/10 to-base-content/5 p-6 border-b border-base-content/5">
                            <h3 className="text-lg font-bold flex items-center gap-2">
                                <Cable className="w-5 h-5 text-primary" />
                                Edit Interface IP
                            </h3>
                            <p className="text-xs opacity-60 mt-1">Updating {editingIface?.name}</p>
                        </div>

                        <div className="p-6 space-y-4">
                            {editIfaceFormError && (
                                <div className="alert alert-error text-xs p-3 flex gap-2">
                                    <AlertTriangle className="w-4 h-4 shrink-0" />
                                    <span>{editIfaceFormError}</span>
                                </div>
                            )}

                            <div>
                                <label className="block text-[10px] uppercase tracking-wider font-bold opacity-50 mb-1.5 ml-1">IP Address</label>
                                <input 
                                    type="text" 
                                    placeholder="e.g. 192.168.1.1" 
                                    className="input input-bordered w-full bg-base-300/50 text-sm focus:border-primary transition-all"
                                    value={editIfaceForm.ip_address}
                                    onChange={(e) => setEditIfaceForm(prev => ({ ...prev, ip_address: formatIpInput(e.target.value) }))}
                                />
                            </div>
                        </div>

                        <div className="bg-base-300/30 p-4 flex justify-end gap-3 border-t border-base-content/5">
                            <button className="btn btn-ghost btn-sm text-xs" onClick={() => setShowEditIfaceModal(false)}>Cancel</button>
                            <button className="btn btn-primary btn-sm text-xs px-6" onClick={handleUpdateInterface} disabled={isNetboxOpLoading}>
                                {isNetboxOpLoading ? (
                                    <span className="inline-flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Updating...</span>
                                ) : 'Update IP'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Dashboard;
