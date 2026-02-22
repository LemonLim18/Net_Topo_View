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

// --- M13: Security Module API ---
export const securityApi = {
  runAudit: (deviceId: number) =>
    request<{
      device_id: number; score: number; total_checks: number;
      passed: number; failed: number; critical: number;
      high: number; medium: number; low: number;
      findings: { rule_id: string; category: string; severity: string; status: string; description: string; evidence: string; remediation: string }[];
    }>(`/security/audit/${deviceId}`, { method: 'POST' }),

  runAuditAll: () =>
    request<{ status: string; message: string }>('/security/audit/all', { method: 'POST' }),

  listAudits: (deviceId?: number) =>
    request<{ device_id: number; score: number; total_checks: number; passed: number; failed: number; critical: number; high: number; medium: number; low: number }[]>(
      deviceId ? `/security/audits?device_id=${deviceId}` : '/security/audits'
    ),

  getFindings: (deviceId: number) =>
    request<{ rule_id: string; category: string; severity: string; status: string; description: string; evidence: string; remediation: string }[]>(
      `/security/audits/${deviceId}/findings`
    ),

  getScore: (deviceId: number) =>
    request<{ device_id: number; score: number; as_of: string }>(`/security/score/${deviceId}`),

  listEvents: (limit?: number, severity?: string) => {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (severity) params.set('severity', severity);
    const qs = params.toString();
    return request<{ id: number; event_type: string; severity: string; source_ip: string; dest_ip: string; device_id: number | null; description: string; status: string; created_at: string }[]>(
      `/security/events${qs ? '?' + qs : ''}`
    );
  },

  ingestAlert: (alert: { event_type: string; severity: string; source_ip?: string; dest_ip?: string; description?: string; raw_data?: any }) =>
    request<{ id: number; event_type: string; severity: string; status: string }>('/security/events', {
      method: 'POST',
      body: JSON.stringify(alert),
    }),

  // --- M13c: Incident Response ---
  respondToEvent: (eventId: number, vendor?: string) =>
    request<{ action_type: string; severity: string; description: string; config_snippet: string; vendor: string; requires_approval: boolean }>(
      `/security/respond/${eventId}${vendor ? '?vendor=' + vendor : ''}`, { method: 'POST' }
    ),

  executePlaybook: (eventId: number, vendor?: string) =>
    request<{ playbook_name: string; status: string; steps_executed: any[]; config_snippet: string | null }>(
      `/security/playbook/${eventId}${vendor ? '?vendor=' + vendor : ''}`, { method: 'POST' }
    ),

  listPlaybooks: () =>
    request<{ name: string; description: string; trigger_types: string[]; mode: string; steps: number }[]>('/security/playbooks'),

  // --- M13d: CVE Correlation ---
  getDeviceCves: (deviceId: number) =>
    request<{ device_id: number; hostname: string; vendor: string; os_version: string; cve_count: number; cves: { id: string; severity: string; cvss: number; description: string; remediation: string }[] }>(
      `/security/cve/${deviceId}`
    ),

  getCveStats: () =>
    request<{ total_cves: number; by_severity: Record<string, number>; by_vendor: Record<string, number> }>('/security/cve'),
};
