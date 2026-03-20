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

// M24: Intent pipeline metadata (blast radius, policy, verification)
export interface PolicyViolation {
  rule_id: string;
  severity: 'error' | 'warning';
  message: string;
}

export interface BlastRadius {
  device_count: number;
  interface_count: number;
  link_count: number;
  level: 'low' | 'medium' | 'high' | 'critical';
}

export interface IntentMeta {
  pipeline: 'intent';
  intent_id?: string;
  intent_type?: string;
  scope?: Record<string, any>;
  resolved_devices: string[];
  blast_radius?: BlastRadius;
  policy_approved: boolean;
  policy_violations: PolicyViolation[];
  policy_warnings: PolicyViolation[];
  execution_engine?: string;
  verification_checks: Array<{ command: string; expected: string; description: string }>;
  device_count: number;
}

export interface ThinkingPhase {
  phase: string;
  label: string;
  elapsed_ms: number;
}

export interface GroundingMeta {
  answer_scope?: string;
  freshness_class?: string;
  confidence?: number;
  sources_used?: string[];
  conflicts?: Array<{ code: string; message: string }>;
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  isStreaming?: boolean;
  isError?: boolean;
  stopped?: boolean;
  timestamp: number;
  // SSE progress phases
  thinkingPhase?: ThinkingPhase;
  thinkingStartTime?: number;
  elapsedMs?: number;
  // Structured grounding metadata
  groundingMeta?: GroundingMeta;
  // M19: Agentic workflow fields
  proposal?: ConfigProposal;
  deployResult?: DeployResult;
  // M24: Intent pipeline metadata
  intentMeta?: IntentMeta;
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
  artifactType?: 'playbook' | 'bundle';
  entrypointPath?: string | null;
  steps: AnsibleJobStep[];
}

export interface AnsibleCheckResult {
  rc: number;
  changed: boolean;
  diff: string;
  summary: string;
  /** When rc !== 0, backend may return stderr (connection/auth/task errors). */
  stderr?: string;
}

/** One log stream in the Ansible Studio Output tab (Check, Deploy, Plan). */
export interface AnsibleOutputSession {
  id: string;
  label: string;
  type: 'check' | 'deploy' | 'plan';
  createdAt: number;
  lines: string[];
}

export interface AnsibleExecuteResult {
  rc: number;
  success: boolean;
  changed: boolean;
  summary: string;
}

/** One proposed edit for a single file (AI suggests changes; user can Keep or Undo per file or Keep All / Undo All). */
export interface AnsibleProposedEdit {
  path: string;
  original: string;
  suggested: string;
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
  generatedPaths?: string[];
  /** Optional disambiguation menu shown inline in chat before preview modal */
  disambiguation?: {
    question: string;
    options: { id: string; label: string; clarification?: string; matches?: string[] }[];
  } | null;
  /** Planned-inventory host multi-select (hint ambiguity, unknown name, or missing targets) */
  deviceDisambiguation?: {
    question?: string;
    candidate_devices?: string[];
  } | null;
  interfaceDisambiguation?: { question?: string; devices: unknown } | null;
  paramsDisambiguation?: { question?: string; fields: unknown } | null;
  /** AI-proposed edits to existing files; user can apply section-by-section or Keep All / Undo All */
  proposedEdits?: AnsibleProposedEdit[];
}

export interface CreateAndPlanRequest {
  intent_description: string;
  target_hosts: string[];
  vendor: string;
  feature: string;
  params: Record<string, any>;
  device_id?: number;      // optional; defaults to 0 (unknown)
  provider?: string;
  model?: string;
  ollama_base_url?: string;
}

export interface AnsibleGeneratedArtifactFile {
  path: string;
  content: string;
  writable?: boolean;
  kind?: string;
}

export interface CreateAndPlanResult {
  job_id: number;
  request_id: number;
  status: string;
  playbook: string;
  artifact_type?: 'playbook' | 'bundle';
  entrypoint_path?: string | null;
  files?: AnsibleGeneratedArtifactFile[];
  manifest?: Record<string, any>;
}