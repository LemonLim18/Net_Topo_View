export const Role = {
  User: 'user',
  Model: 'model'
} as const;

export type Role = typeof Role[keyof typeof Role];

// M19: Config proposal from the AI agent
export interface ConfigProposal {
  id: string;
  status: string;
  user_prompt: string;
  target_device: string;
  target_device_id: number;
  vendor: string;
  feature: string;
  params: Record<string, any>;
  generated_config: string;
  rollback_config: string;
  diff_text: string;
  risk_level: 'low' | 'medium' | 'high';
  ai_explanation: string;
  job_id?: number;
  request_id?: number;
  created_at?: string;
  updated_at?: string;
}

export interface DeployResult {
  status: string;
  message: string;
  job_id?: number;
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  isStreaming?: boolean;
  isError?: boolean;
  timestamp: number;
  // M19: Agentic workflow fields
  proposal?: ConfigProposal;
  deployResult?: DeployResult;
}

export interface ChatSession {
  id: string;
  title: string;
  updatedAt: number;
}

// ─── M23: Ansible Workflow Studio ────────────────────────────────────────────

export interface AnsibleFileNode {
  type: 'file' | 'dir';
  name: string;
  path: string;            // relative to backend/ansible/
  size?: number;
  modified_at?: string;
  children?: AnsibleFileNode[];
}

export interface AnsibleTab {
  id: string;              // usually the file path
  path: string;
  name: string;
  content: string;
  isDirty: boolean;
  isReadOnly: boolean;
  jobId?: number;          // set when opened from AI generation
}

export interface AnsibleJobStep {
  stage: string;
  status: string;
  details: string;
  created_at: string;
}

export interface AnsibleJob {
  jobId: number;
  requestId: number;
  status: string;          // pending | running | approved | success | failed
  stage: string;           // plan | approve | apply | commit | rollback
  hasPlaybook: boolean;
  steps: AnsibleJobStep[];
}

export interface AnsibleCheckResult {
  rc: number;
  changed: boolean;
  diff: string;
  summary: string;
}

export interface AnsibleExecuteResult {
  rc: number;
  success: boolean;
  changed: boolean;
  summary: string;
}

export interface AnsibleMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  isLoading?: boolean;
  isError?: boolean;
  jobId?: number;
  playbookPath?: string;   // path opened in editor when playbook is ready
}

export interface CreateAndPlanRequest {
  intent_description: string;
  target_hosts: string[];
  vendor: string;
  feature: string;
  params: Record<string, any>;
  device_id?: number;      // optional; defaults to 0 (unknown)
}

export interface CreateAndPlanResult {
  job_id: number;
  request_id: number;
  status: string;
  playbook: string;
}