// FIX: Import d3 types to resolve namespace errors.
import * as d3 from 'd3';

export interface InterfaceInfo {
  [interfaceName: string]: string;
}

export interface EdgeLinkInfo {
  remote_node: string;
  remote_interface: string;
  remote_ip: string;
}

export interface NodeData {
  interfaceList: InterfaceInfo[];
  connectedNode: string[];
  edgeLink: {
    [interfaceAbbr: string]: EdgeLinkInfo;
  };
  mgmt_ip: string;
}

export interface TopologyData {
  timestamp: string;
  seeds: string[];
  nodes: {
    [nodeId: string]: NodeData;
  };
}

export type DiffStatus = 'added' | 'removed' | 'unchanged';

// D3 specific types
export interface D3Node extends d3.SimulationNodeDatum, NodeData {
    id: string;
    diffStatus?: DiffStatus;
}

// FIX: Remove incorrect 'source' and 'target' type overrides. D3's force
// simulation populates these with D3Node objects, not just strings. The base
// d3.SimulationLinkDatum<D3Node> provides the correct union type, resolving
// conversion errors in the simulation tick function.
export interface D3Link extends d3.SimulationLinkDatum<D3Node> {
    info: EdgeLinkInfo;
    sourceInterface: string;
    targetInterface: string;
    sourceIp: string;
    targetIp: string;
    diffStatus?: DiffStatus;
    index?: number;
}

// AAA Types
export type UserRole = 'admin' | 'operator' | 'auditor';

export interface User {
    username: string;
    role: UserRole;
    lastLogin: string;
}

// Domain Types
export type AppDomain = 'network' | 'security';
export type ViewId = 
  | 'dashboard' 
  | 'configuration' 
  | 'net_perf' 
  | 'net_chat'
  | 'sec_events'
  | 'sec_analysis'
  | 'sec_chat';

// Notification Types
export type NotificationType = 'info' | 'success' | 'warning' | 'error';

export interface AppNotification {
    id: string;
    title: string;
    message: string;
    type: NotificationType;
    timestamp: number;
    read: boolean;
}
