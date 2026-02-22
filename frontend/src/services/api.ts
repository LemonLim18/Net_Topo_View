import { API_CONFIG } from '../config';

const BASE_URL = API_CONFIG.API_URL;

// Generic fetch wrapper
async function request<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}/api/v1${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody.detail || `API Error: ${response.statusText}`);
  }

  return response.json();
}

// --- M2: Topology API ---
export const topologyApi = {
  get: () => request<any>('/topology'),
  crawl: () => request<{ status: string }>('/topology/crawl', { method: 'POST' }),
};

// --- M4: Deployment API ---
export const deployApi = {
  createRequest: (title: string, description: string) => 
    request<{ request_id: number }>('/deploy/requests', {
      method: 'POST',
      body: JSON.stringify({ title, description, requester: 'frontend-user' }),
    }),

  addJob: (requestId: number, deviceId: number) =>
    request<{ job_id: number }>('/deploy/jobs', {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, device_id: deviceId }),
    }),

  planJob: (jobId: number, vendor: string, feature: string, params: any) =>
    request<{ status: string }>('/deploy/jobs/' + jobId + '/plan', {
      method: 'POST',
      body: JSON.stringify({ vendor, feature, params }),
    }),

  executeJob: (jobId: number, creds: any) =>
    request<{ status: string; message: string }>('/deploy/jobs/' + jobId + '/execute', {
      method: 'POST',
      body: JSON.stringify(creds),
    }),

  getLogs: (jobId: number) =>
    request<{ logs: any[] }>('/deploy/jobs/' + jobId + '/logs'),
};

// --- M5: LLM Agent API ---
export const llmApi = {
  chat: (message: string) =>
    request<{ role: string; content: string; model: string }>('/llm/chat', {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),
  
  clearHistory: () =>
    request<{ status: string }>('/llm/chat/clear', { method: 'POST' }),
    
  getHistory: () =>
    request<{ history: any[] }>('/llm/chat/history'),
};

// --- M17: Network Monitor API ---
export const monitorApi = {
  updateConfig: () =>
    request<{ status: string; message: string }>('/monitor/update-config', {
      method: 'POST',
    }),
    
  getStatus: () =>
    request<{ status: string; message: string }>('/monitor/status'),
};

// --- M3: Config Engine API ---
export const configApi = {
  listTemplates: (vendor?: string) =>
    request<{ vendor: string; feature: string; has_undo: boolean; schema_exists: boolean }[]>(
      vendor ? `/config/templates?vendor=${vendor}` : '/config/templates'
    ),

  getSchema: (feature: string) =>
    request<Record<string, any>>(`/config/schemas/${feature}`),

  render: (vendor: string, feature: string, params: Record<string, any>) =>
    request<{ config: string; undo_config: string | null }>('/config/render', {
      method: 'POST',
      body: JSON.stringify({ vendor, feature, params }),
    }),

  diff: (oldConfig: string, newConfig: string) =>
    request<{
      unified: string;
      additions: number;
      deletions: number;
      changes: number;
      is_identical: boolean;
      added_lines: string[];
      removed_lines: string[];
      summary: string;
    }>('/config/diff', {
      method: 'POST',
      body: JSON.stringify({ old_config: oldConfig, new_config: newConfig }),
    }),

  validate: (vendor: string, configText: string) =>
    request<{ valid: boolean; errors: string[] }>('/config/validate', {
      method: 'POST',
      body: JSON.stringify({ vendor, config_text: configText }),
    }),
};

// --- M14: Device Inventory (Using Topology for now) ---
// Ideally we should have a dedicated /devices endpoint in M14, 
// allows fallback to topology if needed.
