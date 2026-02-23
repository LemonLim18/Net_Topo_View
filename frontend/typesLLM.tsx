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