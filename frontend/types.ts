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
    id?: number;
    username: string;
    role: UserRole;
    lastLogin: string;
}

// Domain Types
export type AppDomain = 'network' | 'security';
export type ViewId = 
  | 'overview'
  | 'dashboard'
  | 'topology'
  | 'netbox_devices'
  | 'drift'
  | 'net_perf' 
  | 'net_chat'
  | 'knowledge_base'
  | 'ansible_studio'
  | 'device_detail'
  | 'sec_dashboard'
  | 'sec_events'
  | 'sec_analysis'
  | 'sec_chat'
  | 'incidents'
  | 'onboarding'
  | 'intent_history'
  | 'sdn_dashboard'
  | 'playbook_comparison'
  | 'approval_queue'
  | 'template_studio';

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

// Intent Lifecycle types (Phase 1 — UC02-UC05)
export interface IntentRecord {
    id: string;
    user_prompt: string;
    intent_type: string;
    action?: string;
    feature?: string;
    vendor?: string;
    risk_level: string;
    status: string;
    blast_radius: number;
    resolved_devices?: string[];
    policy_approved?: boolean;
    created_at?: string;
    parameters?: Record<string, any>;
    scope?: Record<string, any>;
    policy_result?: Record<string, any>;
    proposal_id?: string;
    linked_proposals?: Record<string, any>[];
    linked_plans?: Record<string, any>[];
}

// SDN Controller types (Phase 3)
export interface SDNController {
    id: number;
    name: string;
    controller_type: string;
    url: string;
    username: string;
    status: string;
    managed_device_count: number;
    last_synced?: string;
    created_at?: string;
}
