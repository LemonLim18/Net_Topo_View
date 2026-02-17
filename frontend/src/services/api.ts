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
    request<{ status: string }>('/deploy/jobs/' + jobId + '/execute', {
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

// --- M14: Device Inventory (Using Topology for now) ---
// Ideally we should have a dedicated /devices endpoint in M14, 
// allows fallback to topology if needed.
