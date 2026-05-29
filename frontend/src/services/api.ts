import { API_CONFIG } from '../config';

const BASE_URL = API_CONFIG.API_URL;

// Generic fetch wrapper
async function request<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}/api/v1${endpoint}`;
  const token = localStorage.getItem('auth_token');
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    const detail = errorBody.detail;
    let message = `API Error: ${response.statusText}`;
    const _issueText = (x: any): string => {
      if (x == null) return '';
      if (typeof x === 'string') return x;
      if (typeof x === 'number' || typeof x === 'boolean') return String(x);
      if (typeof x === 'object') {
        if (typeof x.message === 'string' && x.message.trim()) return x.message.trim();
        try {
          return JSON.stringify(x);
        } catch {
          return String(x);
        }
      }
      return String(x);
    };

    if (detail != null) {
      if (typeof detail === 'string') message = detail;
      else if (typeof detail === 'object' && detail.message) {
        message = detail.message;
        if (Array.isArray(detail.issues) && detail.issues.length) {
          const rendered = detail.issues
            .map((issue: any) => _issueText(issue))
            .filter((line: string) => Boolean(line && line.trim()));
          if (rendered.length) message += '\n' + rendered.join('\n');
        }
      }
    }
    const err: any = new Error(message);
    err.status = response.status;
    err.detail = detail;
    throw err;
  }

  return response.json();
}

// --- M2: Topology API ---
export const topologyApi = {
  get: () => request<any>('/topology'),
  crawl: () => request<{ status: string }>('/topology/crawl', { method: 'POST' }),
};

export interface OnboardingDeviceSummary {
  id: number;
  hostname: string;
  management_ip: string | null;
  vendor: string | null;
  ssh_user: string | null;
  ssh_port: number;
  status: string;
}

export interface OnboardingGenerateResponse {
  config: string;
  filename: string;
  is_override?: boolean;
}

export interface ZtpPublishRequestPayload {
  device_ids: number[];
  credential_device_id?: number;
  credential_label?: string;
  config_overrides?: Record<string, string>;
  target_server_ip?: string;
  target_server_port?: number;
  restart_services?: boolean;
  reset_leases?: boolean;
  dry_run?: boolean;
  requested_by?: string;
}

export interface ZtpPublishQueuedResponse {
  job_id: number;
  status: string;
  total_items: number;
}

export interface ZtpServerCredentialStatus {
  configured: boolean;
  device_id: number | null;
  label: string;
  username?: string | null;
}

export interface ZtpServerConnectionTestResult {
  ok: boolean;
  server_ip: string;
  server_port: number;
  duration_ms: number;
  credential_label: string;
  credential_device_id: number | null;
  stdout: string;
  stderr: string;
  message: string;
}

export interface ZtpPublishJobItem {
  id: number;
  job_id: number;
  device_id: number;
  hostname: string;
  vendor: string;
  management_ip: string;
  management_mac?: string;
  filename?: string;
  remote_path?: string;
  status: string;
  stage?: string;
  error_text?: string;
  details?: Record<string, any>;
  updated_at?: string;
}

export interface ZtpPublishJob {
  id: number;
  status: string;
  mode: string;
  target_server_ip: string;
  credential_label: string;
  requested_by?: string;
  total_items: number;
  success_items: number;
  failed_items: number;
  error_text?: string;
  summary?: Record<string, any>;
  manifest?: Record<string, any>;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  updated_at?: string;
  items?: ZtpPublishJobItem[];
}

export const onboardingApi = {
  listDevices: () => request<OnboardingDeviceSummary[]>('/onboarding/devices'),

  generateForDevice: (deviceId: number) =>
    request<OnboardingGenerateResponse>(`/onboarding/generate/${deviceId}`, {
      method: 'POST',
    }),

  saveDay0Override: (deviceId: number, config: string, filename?: string) =>
    request<{ status: string; device_id: number }>(`/onboarding/day0/${deviceId}/override`, {
      method: 'POST',
      body: JSON.stringify({ config, filename: filename ?? '' }),
    }),

  clearDay0Override: (deviceId: number) =>
    request<{ status: string; device_id: number }>(`/onboarding/day0/${deviceId}/override`, {
      method: 'DELETE',
    }),

  publishToZtp: (payload: ZtpPublishRequestPayload) =>
    request<ZtpPublishQueuedResponse>('/onboarding/ztp/publish', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getServerCredentialStatus: (label = 'ZTP_SERVER_SSH', deviceId?: number) => {
    const params = new URLSearchParams();
    params.set('label', label);
    if (typeof deviceId === 'number') params.set('device_id', String(deviceId));
    return request<ZtpServerCredentialStatus>(`/onboarding/ztp/server-credential?${params.toString()}`);
  },

  setServerCredential: (username: string, password: string, label = 'ZTP_SERVER_SSH', deviceId?: number) =>
    request<{ id: number; device_id: number | null; label: string; stored: boolean }>('/onboarding/ztp/server-credential', {
      method: 'POST',
      body: JSON.stringify({
        username,
        password,
        label,
        ...(typeof deviceId === 'number' ? { device_id: deviceId } : {}),
      }),
    }),

  testServerConnection: (
    targetServerIp: string,
    targetServerPort = 22,
    credentialLabel = 'ZTP_SERVER_SSH',
    credentialDeviceId?: number,
  ) =>
    request<ZtpServerConnectionTestResult>('/onboarding/ztp/server-credential/test', {
      method: 'POST',
      body: JSON.stringify({
        target_server_ip: targetServerIp,
        target_server_port: targetServerPort,
        credential_label: credentialLabel,
        credential_device_id: credentialDeviceId,
      }),
    }),

  listPublishJobs: (limit = 20) =>
    request<{ items: ZtpPublishJob[]; total: number }>(`/onboarding/ztp/publish?limit=${limit}`),

  getPublishJob: (jobId: number) =>
    request<ZtpPublishJob>(`/onboarding/ztp/publish/${jobId}`),

  getPublishManifest: (jobId: number) =>
    request<Record<string, any>>(`/onboarding/ztp/publish/${jobId}/manifest`),
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

  /** Fetch recent audit entries (newest first). */
  getAuditLog: (limit = 50) =>
    request<{ entries: any[] }>(`/deploy/audit?limit=${limit}`),

  /** Fetch audit entries for a specific job. */
  getAuditForJob: (jobId: number) =>
    request<{ entries: any[] }>(`/deploy/audit/job/${jobId}`),

  /** List all deployment jobs. */
  listJobs: () =>
    request<any[]>('/deploy/jobs'),

  /** Approval queue (Pending / Approved / Rejected). */
  listApprovalQueue: (statusFilter: 'pending' | 'approved' | 'rejected' = 'pending', limit = 50) => {
    const url = `${BASE_URL}/api/deploy/queue?status_filter=${statusFilter}&limit=${limit}`;
    const token = localStorage.getItem('auth_token');
    return fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    }).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      return r.json() as Promise<{
        items: {
          id: number;
          type: string;
          title: string;
          requester: string;
          status: string;
          created_at: string;
          request_id: number;
          entrypoint_path?: string | null;
          review_comment?: string | null;
          review_actor?: string | null;
          reviewed_at?: string | null;
        }[];
        total: number;
        status_filter: string;
      }>;
    });
  },

  listPendingApprovals: (limit = 50) => deployApi.listApprovalQueue('pending', limit),

  /** Phase 16 — Approve a change request. */
  approveRequest: (requestId: number, actor = 'admin', comment?: string) => {
    const url = `${BASE_URL}/api/deploy/${requestId}/approve`;
    const token = localStorage.getItem('auth_token');
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ actor, comment }),
    }).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      return r.json() as Promise<{ status: string; request_id: number }>;
    });
  },

  /** Phase 16 — Reject a change request. */
  rejectRequest: (requestId: number, actor = 'admin', comment?: string) => {
    const url = `${BASE_URL}/api/deploy/${requestId}/reject`;
    const token = localStorage.getItem('auth_token');
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ actor, comment }),
    }).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      return r.json() as Promise<{ status: string; request_id: number }>;
    });
  },
};

// --- M5: LLM Agent API ---
export interface LlmRuntimeProviderDescriptor {
  name: string;
  label: string;
  available: boolean;
  base_url: string | null;
  models: string[];
  catalog_source?: string;
  reachable?: boolean;
  default_model_injected?: boolean;
  error?: string;
}

export interface LlmRuntimeState {
  active_provider: string;
  active_model: string;
  active_base_url: string | null;
  scope: string;
  providers: LlmRuntimeProviderDescriptor[];
}

export interface LlmRuntimeUpdatePayload {
  provider: string;
  model?: string;
  ollama_base_url?: string;
  clear_history?: boolean;
}

export interface PlaybookAnalysisResponse {
  explanation: string;
  rating: number;
  safety_level: string;
  findings: string[];
  operational_rating: number;
  operational_level: string;
  operational_explanation: string;
}

export interface PlaybookQAResponse {
  answer: string;
}

export const llmApi = {
  chat: (message: string, options?: { signal?: AbortSignal }) =>
    request<{
      role: string;
      content: string;
      model: string;
      type: string;
      proposal: any | null;
      intent_meta: any | null;
      grounding_meta?: {
        answer_scope?: string;
        freshness_class?: string;
        display_freshness_class?: string;
        confidence?: number;
        source_used?: string[];
        display_source_used?: string[];
        source_age?: Array<{ source?: string; scope?: string; source_category?: string; age_seconds?: number | null; freshness_class?: string }>;
        display_source_age?: Array<{ source?: string; scope?: string; source_category?: string; age_seconds?: number | null; freshness_class?: string }>;
        conflicts?: Array<{ code?: string; summary?: string; message?: string; severity?: string }>;
        display_conflicts?: Array<{ code?: string; summary?: string; message?: string; severity?: string }>;
      };
    }>('/llm/chat', {
      method: 'POST',
      body: JSON.stringify({ message }),
      signal: options?.signal,
    }),

  /**
   * SSE streaming chat — emits progress phases then a final response.
   *
   * Usage:
   *   llmApi.chatStream(msg, {
   *     onProgress: ({ phase, label, elapsed_ms }) => ...,
   *     onComplete: (response) => ...,
   *     onError: (err) => ...,
   *   });
   */
  chatStream: async (
    message: string,
    callbacks: {
      onProgress?: (data: { phase: string; label: string; elapsed_ms: number }) => void;
      onComplete?: (data: any) => void;
      onError?: (err: Error) => void;
      onAbort?: () => void;
    },
    options?: { signal?: AbortSignal },
  ) => {
    const url = `${BASE_URL}/api/v1/llm/chat/stream`;
    const token = localStorage.getItem('auth_token');
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ message }),
        signal: options?.signal,
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        throw new Error(errorBody.detail || `API Error: ${response.statusText}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No readable stream');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let eventType = 'progress';
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            const raw = line.slice(6);
            try {
              const data = JSON.parse(raw);
              if (eventType === 'progress' && callbacks.onProgress) {
                callbacks.onProgress(data);
              } else if (eventType === 'complete' && callbacks.onComplete) {
                callbacks.onComplete(data);
              } else if (eventType === 'error' && callbacks.onError) {
                callbacks.onError(new Error(data.error || 'Stream error'));
              }
            } catch { /* skip malformed */ }
          }
        }
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        callbacks.onAbort?.();
        return;
      }
      if (callbacks.onError) callbacks.onError(err);
      else throw err;
    }
  },

  getRuntime: (ollamaBaseUrl?: string) => {
    const params = new URLSearchParams();
    if (ollamaBaseUrl) params.set('ollama_base_url', ollamaBaseUrl);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return request<LlmRuntimeState>(`/llm/chat/runtime${suffix}`);
  },

  updateRuntime: (payload: LlmRuntimeUpdatePayload, options?: { signal?: AbortSignal }) =>
    request<LlmRuntimeState>('/llm/chat/runtime', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: options?.signal,
    }),
  
  clearHistory: () =>
    request<{ status: string }>('/llm/chat/clear', { method: 'POST' }),
    
  getHistory: () =>
    request<{ history: any[] }>('/llm/chat/history'),

  analyzePlaybook: (playbookContent: string, promptRequest?: string) =>
    request<PlaybookAnalysisResponse>('/llm/chat/playbook/analyze', {
      method: 'POST',
      body: JSON.stringify({
        playbook_content: playbookContent,
        prompt_request: promptRequest,
      }),
    }),

  qaPlaybook: (playbookContent: string, question: string, history: { role: string; content: string }[]) =>
    request<PlaybookQAResponse>('/llm/chat/playbook/qa', {
      method: 'POST',
      body: JSON.stringify({
        playbook_content: playbookContent,
        question,
        history,
      }),
    }),
};

// --- M19: Proposal API (Agentic Workflow) ---
export interface ProposalReviewPayload {
  merged_config: string;
  accepted_line_indices: number[];
  rejected_line_indices: number[];
}

export const proposalApi = {
  list: (status?: string) =>
    request<{ proposals: any[] }>(
      status ? `/llm/chat/proposals?status=${status}` : '/llm/chat/proposals'
    ),

  get: (id: string) =>
    request<any>(`/llm/chat/proposals/${id}`),

  review: (id: string, payload: ProposalReviewPayload) =>
    request<{
      status: string;
      message: string;
      proposal: any;
      review: { accepted_count: number; rejected_count: number; reviewed_by: string };
    }>(`/llm/chat/proposals/${id}/review`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  approve: (id: string) =>
    request<{ status: string; message: string; job_id?: number; proposal: any }>(
      `/llm/chat/proposals/${id}/approve`, { method: 'POST' }
    ),

  reject: (id: string) =>
    request<{ status: string; message: string; proposal: any }>(
      `/llm/chat/proposals/${id}/reject`, { method: 'POST' }
    ),

  rollback: (id: string) =>
    request<{ status: string; message: string; rollback_config?: string; proposal: any }>(
      `/llm/chat/proposals/${id}/rollback`, { method: 'POST' }
    ),
};

// --- M17: Network Monitor API ---
export const monitorApi = {
  updateConfig: (deployRemote?: boolean) =>
    request<{ status: string; message: string; details?: any }>(`/monitor/update-config${deployRemote ? '?deploy_remote=true' : ''}`, {
      method: 'POST',
    }),
    
  getStatus: () =>
    request<{ status: string; message: string }>('/monitor/status'),

  /** Phase 10 — Get latest metrics (raw InfluxDB CSV or SQLite fallback) */
  getLatestMetrics: (device?: string, limit = 30) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (device) params.set('device', device);
    return request<any>(`/monitor/metrics/latest?${params}`);
  },

  /** Phase 10 — Get time-series metric history for charting */
  getMetricHistory: (
    device?: string,
    metric = 'cpu',
    rangeMinutes = 60,
    limit = 120,
    interfaceName?: string,
    devices?: string[],
  ) => {
    const params = new URLSearchParams({ metric, range_minutes: String(rangeMinutes), limit: String(limit) });
    if (device) params.set('device', device);
    if (interfaceName) params.set('interface', interfaceName);
    if (devices && devices.length > 0) params.set('devices', devices.join(','));
    return request<{ source: string; metric: string; data: any[] }>(`/monitor/metrics/history?${params}`);
  },

  getMetricHistoryBatch: (
    metrics: string[],
    device?: string,
    rangeMinutes = 60,
    limit = 120,
    interfaceName?: string,
    devices?: string[],
  ) => {
    const params = new URLSearchParams({
      metrics: metrics.join(','),
      range_minutes: String(rangeMinutes),
      limit: String(limit),
    });
    if (device) params.set('device', device);
    if (interfaceName) params.set('interface', interfaceName);
    if (devices && devices.length > 0) params.set('devices', devices.join(','));
    return request<{ source: string; metrics: string[]; data: Record<string, any[]> }>(`/monitor/metrics/history-batch?${params}`);
  },

  /** Phase 10 — Get per-device summary with CPU/memory enrichment */
  getDeviceSummary: (limit = 50) =>
    request<{ devices: any[] }>(`/monitor/metrics/summary?limit=${limit}`),

  /** Get latest interface snapshot for a selected device */
  getDeviceInterfaces: (device: string, interfaceName?: string) => {
    const params = new URLSearchParams({ device });
    if (interfaceName) params.set('interface', interfaceName);
    return request<{ device: string; interfaces: any[] }>(`/monitor/metrics/interfaces?${params}`);
  },

  /** Get IP → sysName hostname mapping from InfluxDB */
  getDeviceHostnames: () =>
    request<{ hostnames: Record<string, string> }>('/monitor/metrics/hostnames'),
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

  /** Read raw .j2 template source for Monaco editing. */
  getTemplateSource: (vendor: string, feature: string) =>
    request<{ vendor: string; feature: string; content: string }>(
      `/config/templates/${vendor}/${feature}/source`
    ),

  /** Save edited .j2 template source (admin only). */
  saveTemplateSource: (vendor: string, feature: string, content: string) =>
    request<{ status: string }>(
      `/config/templates/${vendor}/${feature}/source`,
      { method: 'PUT', body: JSON.stringify({ content }) }
    ),

  /** Read raw JSON schema source for Monaco editing. */
  getSchemaSource: (feature: string) =>
    request<{ feature: string; content: string }>(
      `/config/schemas/${feature}/source`
    ),

  /** Save edited JSON schema source (admin only). */
  saveSchemaSource: (feature: string, content: string) =>
    request<{ status: string }>(
      `/config/schemas/${feature}/source`,
      { method: 'PUT', body: JSON.stringify({ content }) }
    ),

  /** Delete a template file (admin only). */
  deleteTemplate: (vendor: string, feature: string) =>
    request<{ status: string }>(`/config/templates/${vendor}/${feature}`, {
      method: 'DELETE',
    }),

  /** Create a new template file (admin only). */
  createTemplate: (vendor: string, feature: string, content: string) =>
    request<{ status: string }>(`/config/templates/${vendor}/${feature}/create`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
};

// --- Template Studio Git Terminal API ---
export const templateGitApi = {
  status: () =>
    request<{
      repo_root: string;
      branch: string;
      is_dirty: boolean;
      origin: string;
      github_remote: boolean;
      github_cli_available: boolean;
      github_authenticated: boolean;
      github_username?: string | null;
    }>('/template-git/status'),

  exec: (command: string) =>
    request<{
      ok: boolean;
      exit_code: number;
      stdout: string;
      stderr: string;
      branch: string;
      is_dirty: boolean;
    }>('/template-git/exec', {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),

  githubLogin: (payload: { username: string; token: string; git_name?: string; git_email?: string }) =>
    request<{ status: string; username: string; branch: string; origin: string }>('/template-git/github/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  githubLogout: () =>
    request<{ status: string }>('/template-git/github/logout', {
      method: 'POST',
    }),
};

// --- NetBox Device Inventory API ---
export interface DeviceRecord {
  id: number;
  hostname: string;
  management_ip: string | null;
  management_subnet_mask: string | null;
  management_interface: string | null;
  vendor: string | null;
  model: string | null;
  os_version: string | null;
  serial_number: string | null;
  status: string;
  role: string;
  source: string;
  last_polled: string | null;
  created_at: string | null;
  updated_at: string | null;
  ssh_user: string | null;
  ssh_password: string | null;
  ssh_port: number | null;
  netbox_synced?: boolean;
  netbox_sync_pending?: boolean;
  netbox_error?: string;
  netbox_message?: string;
}

export interface CommonServicesProfile {
  id?: number;
  scope_type: 'global' | 'site' | 'role' | 'device';
  scope_value: string;
  snmp_community: string | null;
  snmp_location: string | null;
  snmp_contact: string | null;
  snmp_host_ip: string | null;
  syslog_port: number | null;
  syslog_device_port: number | null;
  syslog_host_ip: string | null;
  ntp_server_ip: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export const commonServicesApi = {
  listProfiles: () => request<{ items: CommonServicesProfile[] }>('/common-services/profiles'),

  getProfile: (scopeType: string, scopeValue: string) =>
    request<CommonServicesProfile>(`/common-services/profiles/${encodeURIComponent(scopeType)}/${encodeURIComponent(scopeValue)}`),

  upsertProfile: (
    scopeType: string,
    scopeValue: string,
    payload: {
      snmp_community?: string | null;
      snmp_location?: string | null;
      snmp_contact?: string | null;
      snmp_host_ip?: string | null;
      syslog_port?: number | null;
      syslog_device_port?: number | null;
      syslog_host_ip?: string | null;
      ntp_server_ip?: string | null;
    },
  ) =>
    request<CommonServicesProfile>(`/common-services/profiles/${encodeURIComponent(scopeType)}/${encodeURIComponent(scopeValue)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  deleteProfile: (scopeType: string, scopeValue: string) =>
    request<{ deleted: boolean }>(`/common-services/profiles/${encodeURIComponent(scopeType)}/${encodeURIComponent(scopeValue)}`, {
      method: 'DELETE',
    }),

  testSyslog: () =>
    request<{ ok: boolean; host: string; port: number }>('/common-services/test-syslog', { method: 'POST' }),

  testNtp: () =>
    request<{ ok: boolean; server: string; transmit_time: string; offset_seconds: number }>('/common-services/test-ntp', { method: 'POST' }),

  getEffective: (params?: { device_id?: number; role?: string; site?: string }) => {
    const qp = new URLSearchParams();
    if (params?.device_id != null) qp.set('device_id', String(params.device_id));
    if (params?.role) qp.set('role', params.role);
    if (params?.site) qp.set('site', params.site);
    const suffix = qp.toString() ? `?${qp.toString()}` : '';
    return request<{
      site: string | null;
      role: string | null;
      device_id: number | null;
      effective: {
        snmp_community: string | null;
        snmp_location: string | null;
        snmp_contact: string | null;
        snmp_host_ip: string | null;
        syslog_port: number | null;
        syslog_device_port: number | null;
        syslog_host_ip: string | null;
        ntp_server_ip: string | null;
        sources: Record<string, string | null>;
        chain: string[];
      };
    }>(`/common-services/effective${suffix}`);
  },
};

export interface InterfaceRecord {
  id: number;
  device_id: number;
  name: string;
  ip_address: string | null;
  subnet_mask: string | null;
  mac_address: string | null;
  speed: string | null;
  status: string;
  connected_to?: { device: { id: number; name: string }; interface: { id: number; name: string } } | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface LinkRecord {
  id: number;
  src_device_id: number;
  dst_device_id: number;
  src_interface_id: number | null;
  dst_interface_id: number | null;
  discovery_protocol: string | null;
  created_at: string | null;
  src_hostname: string;
  dst_hostname: string;
  src_interface_name: string | null;
  dst_interface_name: string | null;
}

// --- Network: Config Backup & Restore ---
export interface ConfigBackupDevice {
  id: number;
  hostname: string;
  management_ip?: string;
  vendor?: string;
  status?: string;
  role?: string;
  /** Read-only: true if device is in current live topology (from crawler cache; not stored). */
  live_in_topology?: boolean;
}
export interface ConfigBackupSnapshot {
  id: number;
  device_id: number;
  captured_at?: string;
  description?: string | null;
  config_preview: string;
  config_size: number;
  owner_username?: string | null;
}
export interface ConfigBackupCaptureResponse {
  snapshot_id: number;
  device_id: number;
  hostname: string;
  captured_at: string;
  message: string;
}
export interface ConfigBackupRestoreResponse {
  snapshot_id: number;
  device_id: number;
  hostname: string;
  vendor: string;
  backup_snapshot_id?: number | null;
  restored_snapshot_id?: number | null;
  commands_applied: number;
  message: string;
}
export interface ConfigBackupExplainResponse {
  snapshot_id: number;
  model: string;
  summary: string;
}
export const networkConfigBackupApi = {
  listDevices: () =>
    request<ConfigBackupDevice[]>('/network/config-backup/devices'),
  listSnapshots: (deviceId: number, limit = 20) =>
    request<ConfigBackupSnapshot[]>(`/network/config-backup/snapshots?device_id=${deviceId}&limit=${limit}`),
  captureSnapshot: (deviceId: number, description?: string) =>
    request<ConfigBackupCaptureResponse>('/network/config-backup/capture', {
      method: 'POST',
      body: JSON.stringify({ device_id: deviceId, description: description || null }),
    }),
  restoreSnapshot: (snapshotId: number) =>
    request<ConfigBackupRestoreResponse>(`/network/config-backup/snapshots/${snapshotId}/restore`, {
      method: 'POST',
    }),
  explainSnapshot: (snapshotId: number) =>
    request<ConfigBackupExplainResponse>(`/network/config-backup/snapshots/${snapshotId}/explain`, { method: 'POST' }),
  getSnapshotContent: (snapshotId: number) =>
    request<{ id: number; config_text: string }>(`/network/config-backup/snapshots/${snapshotId}/content`),
  getSnapshotDiff: (snapshotId: number) =>
    request<{
      snapshot_id: number;
      has_previous: boolean;
      previous_snapshot_id: number | null;
      additions: number;
      deletions: number;
      diff_text: string;
    }>(`/network/config-backup/snapshots/${snapshotId}/diff`),
  /** Download snapshot as text file (triggers browser save). */
  async downloadSnapshot(snapshotId: number, filename?: string): Promise<void> {
    const url = `${API_CONFIG.API_URL}/api/v1/network/config-backup/snapshots/${snapshotId}/download`;
    const token = localStorage.getItem('auth_token');
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(res.statusText || 'Download failed');
    const blob = await res.blob();
    const name = filename || res.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] || `config_snapshot_${snapshotId}.txt`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  },
  /** Update a snapshot's description after capture. */
  updateSnapshotDescription: (snapshotId: number, description: string) =>
    request<{ snapshot_id: number; description: string }>(`/network/config-backup/snapshots/${snapshotId}/description`, {
      method: 'PATCH',
      body: JSON.stringify({ description }),
    }),
  /** AI-suggest a short description based on the captured config. */
  suggestDescription: (snapshotId: number) =>
    request<{ description: string }>(`/network/config-backup/snapshots/${snapshotId}/suggest-description`, {
      method: 'POST',
    }),
};

export const netboxApi = {
  listDevices: () =>
    request<{ devices: DeviceRecord[] }>('/netbox/devices'),

  getDevice: (id: number) =>
    request<DeviceRecord>(`/netbox/devices/${id}`),

  createDevice: (device: {
    hostname: string;
    management_ip?: string;
    management_subnet_mask?: string;
    management_interface?: string;
    vendor?: string;
    model?: string;
    os_version?: string;
    serial_number?: string;
    status?: string;
    role?: string;
    ssh_user?: string;
    ssh_password?: string;
    ssh_port?: number;
  }) =>
    request<DeviceRecord & { netbox_synced?: boolean; netbox_sync_pending?: boolean; netbox_error?: string; netbox_message?: string }>('/netbox/devices', {
      method: 'POST',
      body: JSON.stringify(device),
    }),

  updateDevice: (id: number, device: {
    hostname?: string;
    management_ip?: string;
    management_subnet_mask?: string;
    management_interface?: string;
    vendor?: string;
    model?: string;
    os_version?: string;
    serial_number?: string;
    status?: string;
    role?: string;
    ssh_user?: string;
    ssh_password?: string;
    ssh_port?: number;
  }) =>
    request<DeviceRecord & { netbox_synced?: boolean; netbox_sync_pending?: boolean; netbox_error?: string; netbox_message?: string }>(`/netbox/devices/${id}`, {
      method: 'PUT',
      body: JSON.stringify(device),
    }),

  deleteDevice: (id: number) =>
    request<{ deleted: boolean; id: number; hostname: string; netbox_sync_pending?: boolean; netbox_message?: string }>(
      `/netbox/devices/${id}`, { method: 'DELETE' }
    ),

  syncFromNetbox: () =>
    request<{ status: string; synced_count?: number }>('/netbox/sync/devices', {
      method: 'POST',
    }),

  // Interface CRUD
  listInterfaces: (deviceId: number) =>
    request<{ interfaces: InterfaceRecord[] }>(`/netbox/devices/${deviceId}/interfaces`),

  createInterface: (deviceId: number, iface: {
    name: string;
    ip_address?: string;
    subnet_mask?: string;
    mac_address?: string;
    speed?: string;
    status?: string;
  }) =>
    request<InterfaceRecord>(`/netbox/devices/${deviceId}/interfaces`, {
      method: 'POST',
      body: JSON.stringify(iface),
    }),

  updateInterface: (deviceId: number, ifaceId: number, iface: {
    name?: string;
    ip_address?: string;
    subnet_mask?: string;
    mac_address?: string;
    speed?: string;
    status?: string;
  }) =>
    request<InterfaceRecord>(`/netbox/devices/${deviceId}/interfaces/${ifaceId}`, {
      method: 'PUT',
      body: JSON.stringify(iface),
    }),

  deleteInterface: (deviceId: number, ifaceId: number) =>
    request<{ deleted: boolean }>(`/netbox/devices/${deviceId}/interfaces/${ifaceId}`, {
      method: 'DELETE',
    }),

  // Link CRUD
  listLinks: () =>
    request<{ links: LinkRecord[] }>('/netbox/links'),

  createLink: (link: {
    src_device_id: number;
    src_interface_id?: number;
    dst_device_id: number;
    dst_interface_id?: number;
    discovery_protocol?: string;
  }) =>
    request<{ id: number; created: boolean }>('/netbox/links', {
      method: 'POST',
      body: JSON.stringify(link),
    }),

  deleteLink: (linkId: number) =>
    request<{ deleted: boolean }>(`/netbox/links/${linkId}`, {
      method: 'DELETE',
    }),

  // Push to NetBox
  pushDevice: (deviceId: number) =>
    request<DeviceRecord & { pushed: boolean }>(`/netbox/devices/${deviceId}/push`, {
      method: 'POST',
    }),

  pushAll: () =>
    request<{ pushed_count: number; message: string }>('/netbox/push-all', {
      method: 'POST',
    }),

  /** Phase 4: Get planned (design-of-record) topology from NetBox */
  getPlannedTopology: () =>
    request<{
      nodes: Record<string, any>;
      source: string;
      device_count?: number;
      timestamp?: string | null;
    }>('/netbox/topology/planned'),

  /** Phase 4b: Get planned topology from LOCAL DB (works without NetBox) */
  getLocalPlannedTopology: () =>
    request<{
      nodes: Record<string, any>;
      source: string;
      device_count?: number;
      seeds?: string[];
      timestamp?: string | null;
    }>('/netbox/topology/planned/local'),
};

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

export const syslogApi = {
  listEvents: (limit = 100, device?: string, sourceIp?: string, q?: string) => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (device) params.set('device', device);
    if (sourceIp) params.set('source_ip', sourceIp);
    if (q) params.set('q', q);
    return request<Array<{
      id: number;
      source_ip: string;
      source_host?: string | null;
      device_id?: number | null;
      device_hostname?: string | null;
      severity?: string | null;
      facility?: string | null;
      mnemonic?: string | null;
      message?: string | null;
      raw_message: string;
      created_at?: string | null;
    }>>(`/syslog/events?${params.toString()}`);
  },

  listSources: (limit = 200) =>
    request<{
      items: Array<{
        source_ip: string;
        source_host?: string | null;
        device_id?: number | null;
        device_hostname?: string | null;
        event_count: number;
        last_seen?: string | null;
      }>;
    }>(`/syslog/sources?limit=${limit}`),

  syncServer: (mode?: 'hostname' | 'ip') =>
    request<{
      enabled: boolean;
      synced: boolean;
      mode: string;
      message: string;
      inventory_device_count: number;
    }>(`/syslog/sync-server${mode ? `?mode=${mode}` : ''}`, { method: 'POST' }),
};

// --- M15: Auth API ---
export const authApi = {
  login: (username: string, password: string) =>
    request<{ access_token: string; token_type: string; user: { id: number; username: string; role: string; lastLogin: string; fullName: string; email: string; avatar: string } }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  signup: (payload: { username: string; password: string; full_name: string; email: string; avatar?: string | null; security_question: string; security_answer: string }) =>
    request<{ access_token: string; token_type: string; user: { id: number; username: string; role: string; lastLogin: string; fullName: string; email: string; avatar: string } }>('/auth/signup', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  me: () =>
    request<{ id: number; username: string; role: string; lastLogin: string; createdAt: string; fullName: string; email: string; avatar: string }>('/auth/me'),

  updateProfile: (payload: { full_name?: string; email?: string; avatar?: string; security_question?: string; security_answer?: string; current_password?: string; new_password?: string }) =>
    request<{ status: string; message: string }>('/auth/me/profile', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  getResetQuestion: (username: string) =>
    request<{ question: string }>('/auth/reset-password/question', {
      method: 'POST',
      body: JSON.stringify({ username }),
    }),

  resetPassword: (payload: { username: string; security_answer: string; new_password: string }) =>
    request<{ status: string; message: string }>('/auth/reset-password/reset', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listUsers: () =>
    request<{ id: number; username: string; role: string; is_active: boolean; last_login: string; created_at: string }[]>('/auth/users'),

  createUser: (username: string, password: string, role: string = 'engineer') =>
    request<{ id: number; username: string; role: string }>('/auth/users', {
      method: 'POST',
      body: JSON.stringify({ username, password, role }),
    }),

  updateRole: (userId: number, role: string) =>
    request<{ id: number; role: string }>(`/auth/users/${userId}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role }),
    }),

  deleteUser: (userId: number) =>
    request<{ deleted: number }>(`/auth/users/${userId}`, { method: 'DELETE' }),

  // Credential vault
  storeCredential: (deviceId: number, username: string, password: string, label: string = 'SSH') =>
    request<{ id: number; device_id: number; label: string }>('/auth/credentials', {
      method: 'POST',
      body: JSON.stringify({ device_id: deviceId, label, username, password }),
    }),

  listCredentials: (deviceId: number) =>
    request<{ id: number; device_id: number; label: string; created_at: string; updated_at: string }[]>(
      `/auth/credentials/${deviceId}`
    ),

  deleteCredential: (deviceId: number, label: string) =>
    request<{ deleted: boolean }>(`/auth/credentials/${deviceId}/${label}`, { method: 'DELETE' }),
};

// --- M21: RAG Knowledge Base API ---
export interface RagDocument {
  doc_id: string;
  doc_name: string;
  doc_type: string;
  vendor: string;
  ingested_at: string;
  chunk_count: number;
  applicable_feature?: string;
  partition?: string;
}

export interface RagStatus {
  available: boolean;
  total_chunks: number;
  total_documents: number;
  chroma_path: string;
  redis_host?: string;
  redis_port?: number;
  redis_online?: boolean;
  redis_bypassed?: boolean;
  redis_has_password?: boolean;
}

export const ragApi = {
  getStatus: () =>
    request<RagStatus>('/rag/status'),

  listDocuments: () =>
    request<RagDocument[]>('/rag/documents'),

  getDocumentPreview: (docId: string, limit = 120) =>
    request<{
      doc_id: string;
      doc_name: string;
      doc_type: string;
      vendor: string;
      chunk_count: number;
      preview_text: string;
      chunks: Array<{ text: string; chunk_index: number; page_number: number }>;
    }>(`/rag/documents/${docId}/preview?limit=${limit}`),

  uploadDocument: (file: File, docType: string, vendor: string, docName?: string) => {
    const token = localStorage.getItem('auth_token');
    const form = new FormData();
    form.append('file', file);
    form.append('doc_type', docType);
    form.append('vendor', vendor);
    if (docName) form.append('doc_name', docName);
    return fetch(`${BASE_URL}/api/v1/rag/documents/upload`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    }).then(async r => {
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.detail || `Upload failed: ${r.statusText}`);
      }
      return r.json() as Promise<{ doc_id: string; doc_name: string; chunks_stored: number; message: string }>;
    });
  },

  deleteDocument: (docId: string) =>
    request<{ doc_id: string; chunks_deleted: number; message: string }>(`/rag/documents/${docId}`, { method: 'DELETE' }),

  rebuild: () =>
    request<{ files_processed: number; total_chunks: number; errors: string[] }>('/rag/documents/rebuild', { method: 'POST' }),

  /** Preview the fused context bundle (inventory + telemetry + SDN) */
  previewBundle: (params: {
    device_hostnames?: string[];
    vendor_filter?: string;
    include_telemetry?: boolean;
    include_sdn?: boolean;
  } = {}) =>
    request<{ prompt_text: string; bundle: Record<string, unknown> }>('/rag/context-bundle/preview', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  /** Phase 9 — Get network state embedding status */
  stateEmbeddingStatus: () =>
    request<{ available: boolean; last_embedded_at: string | null }>('/rag/state-embedding/status'),

  /** Phase 9 — Manually trigger network state re-embedding */
  triggerStateEmbedding: () =>
    request<{
      status: string;
      doc_id?: string;
      devices_embedded?: number;
      link_chunks?: number;
      config_chunks?: number;
      interface_chunks?: number;
      total_chunks?: number;
      embedded_at?: string;
      reason?: string;
    }>('/rag/state-embedding/trigger', { method: 'POST' }),

  setRedisBypass: (bypass: boolean) =>
    request<{ status: string; redis_bypassed: boolean }>('/rag/redis/bypass', {
      method: 'POST',
      body: JSON.stringify({ bypass }),
    }),

  updateRedisConfig: (host: string, port: number, password?: string) =>
    request<{ status: string; message: string }>('/rag/redis/config', {
      method: 'POST',
      body: JSON.stringify({ host, port, password }),
    }),

  deleteNonAnsibleDocuments: () =>
    request<{ docs_deleted: number; chunks_deleted: number; message: string }>('/rag/documents/non-ansible', {
      method: 'DELETE',
    }),

  testRedisActiveConnection: () =>
    request<{ status: string; message: string }>('/rag/redis/test-active', {
      method: 'POST',
    }),
};

// --- SSH Terminal API ---
export const sshApi = {
  createSession: (hostname: string, port: number = 22) =>
    request<{ status: string; session_id: string; host: string; hostname: string }>('/ssh/sessions', {
      method: 'POST',
      body: JSON.stringify({ hostname, port }),
    }),

  connectWithCreds: (hostname: string, username: string, password: string, port: number = 22, saveToVault: boolean = false) =>
    request<{ status: string; session_id: string; host: string; hostname: string; saved_to_vault: boolean }>('/ssh/sessions/connect', {
      method: 'POST',
      body: JSON.stringify({ hostname, port, username, password, save_to_vault: saveToVault }),
    }),

  /** Connect using device's stored SSH credentials (one-click from inventory list). */
  connectByDevice: (hostname: string, port: number = 22) =>
    request<{ status: string; session_id: string; host: string; hostname: string }>('/ssh/sessions/connect-by-device', {
      method: 'POST',
      body: JSON.stringify({ hostname, port }),
    }),

  listSessions: () =>
    request<{ session_id: string; host: string; port: number; connected: boolean }[]>('/ssh/sessions'),

  closeSession: (sessionId: string) =>
    request<{ status: string; session_id: string }>(`/ssh/sessions/${sessionId}`, { method: 'DELETE' }),

  /** Build the WebSocket URL for an SSH session */
  getWsUrl: (sessionId: string): string => {
    const wsBase = API_CONFIG.API_URL.replace(/^http/, 'ws');
    return `${wsBase}/api/v1/ws/ssh/${sessionId}`;
  },
};

// --- M23: Ansible Workflow Studio API ---
export const ansibleApi = {
  /** Preview intent: categories + RAG reference templates + reasoning (for studio overlay). */
  previewIntent: (
    req: {
      intent_description: string;
      target_hosts?: string[];
      vendor?: string;
      feature?: string;
      provider?: string;
      model?: string;
      ollama_base_url?: string;
      params?: Record<string, unknown>; // e.g. interfaces_to_shutdown, vlan_id, vlan_name, target_hosts
      /** Skip vector RAG (faster Ansible AI chat); Studio refresh still runs full RAG. */
      skip_rag?: boolean;
    },
    opts?: { signal?: AbortSignal }
  ) =>
    request<{
      categories: string[];
      references: { doc_name: string; feature: string; vendor: string; path: string }[];
      parsed_intent: Record<string, unknown> & {
        inventory_unknown_device_tokens?: string[];
        planned_all_interfaces_preview?: {
          devices?: { hostname: string; interface_count: number; interfaces: { name: string }[] }[];
          note?: string;
        };
      };
      disambiguation?: {
        kind: string;
        question: string;
        selector_numbers?: number[];
        options: { id: string; label: string; clarification?: string; matches?: string[] }[];
      } | null;
      interfaces_disambiguation?: {
        question: string;
        devices: { hostname: string; interfaces: { name: string }[] }[];
        validation_notice?: string;
        invalid_entities?: {
          hostname: string;
          value: string;
          kind: string;
          message: string;
          suggestions?: string[];
        }[];
        params_need_context_fix?: boolean;
      } | null;
      params_disambiguation?: {
        question: string;
        fields: { field: string; question: string }[];
      } | null;
      policy_approved: boolean;
      reasoning: string[];
      target_hosts?: string[];
      reasoning_trace?: {
        steps: { title: string; detail: string }[];
      };
      schema_editable_fields?: { field: string; label: string; required: boolean; type: string }[];
      interfaces_for_selection?: { question: string; devices: { hostname: string; interfaces: { name: string }[] }[] };
      params_need_context_fix?: boolean;
      devices_disambiguation?: {
        ambiguous_devices?: string[];
        missing_devices?: string[];
        unknown_inventory_aliases?: string[];
        ask_user_selection?: boolean;
        candidate_devices?: string[];
        closest_candidates?: string[];
        other_candidates?: string[];
        question?: string;
        /** ambiguous_hint | not_in_inventory | missing_targets */
        disambiguation_kind?: string;
      } | null;
      /** Operation-specific intent schema inspection (primary + secondary slices). */
      intent_schema_inspector?: {
        slices?: Array<{
          role: string;
          index: number;
          title: string;
          action?: string | null;
          feature?: string | null;
          schema_action?: string | null;
          schema_file?: string | null;
          missing_required?: string[];
          validation_errors?: string[];
          fields?: Array<{
            schema_field: string;
            param_key: string;
            label: string;
            required: boolean;
            status: string;
            value_preview: string;
            question?: string;
          }>;
          note?: string | null;
        }>;
        error?: string;
      };
    }>('/ansible/preview-intent', { method: 'POST', body: JSON.stringify(req), signal: opts?.signal }),

  /** Intent catalog YAML (categories, operations) for Intent Parse workbench. */
  getIntentCatalog: () =>
    request<{
      version: number;
      global_fields: Record<string, unknown>;
      keyword_map: Record<string, unknown>;
      categories: Array<{
        key: string;
        display_name: string;
        category_description?: string;
        legacy_feature?: string;
        shared_fields?: Array<{ name: string; data_type?: string; required?: boolean }>;
        operations: Array<{
          name: string;
          action?: string;
          feature?: string;
          required_parameters: string[];
          rules: string[];
        }>;
      }>;
      canonical_adapter: { mappings: string[] };
    }>('/ansible/intent-catalog'),

  /** Parse + pipeline trace (split, cross-check, interactive asks) for workbench demo. */
  intentParseWorkbench: (req: {
    prompt: string;
    provider?: string;
    model?: string;
    ollama_base_url?: string;
  }) =>
    request<{
      parsed_intent_json: string;
      parsed_intent: Record<string, unknown>;
      pipeline: Record<string, unknown>;
    }>('/ansible/intent-parse-workbench', { method: 'POST', body: JSON.stringify(req) }),

  /** Whitelist of intent_catalog YAML paths (for workbench editor). */
  listIntentCatalogEditableFiles: () =>
    request<{ paths: string[] }>('/ansible/intent-catalog/editable-files'),

  getIntentCatalogFile: (filePath: string) =>
    request<{ path: string; content: string }>(
      `/ansible/intent-catalog/file/${encodeURIComponent(filePath)}`
    ),

  /** Admin only — writes disk under backend/ansible/intent_catalog/ */
  putIntentCatalogFile: (filePath: string, content: string) =>
    request<{ path: string; status: string }>(
      `/ansible/intent-catalog/file/${encodeURIComponent(filePath)}`,
      { method: 'PUT', body: JSON.stringify({ content }) }
    ),

  /** Structured category schema from local YAML (form UI, not raw editor). */
  getIntentCategoryUiModel: (categoryKey: string) =>
    request<{
      category_key: string;
      display_name: string;
      category_description: string;
      legacy_feature: string;
      shared_fields: Array<{ name: string; data_type: string; required: boolean }>;
      operations: Array<{
        name: string;
        action: string;
        feature: string;
        required_parameters: string[];
        rules: string[];
      }>;
    }>(`/ansible/intent-catalog/category/${encodeURIComponent(categoryKey)}/ui-model`),

  /** Admin only — saves categories/{key}.yaml and reloads catalog. */
  putIntentCategoryUiModel: (
    categoryKey: string,
    body: {
      display_name: string;
      category_description?: string;
      legacy_feature?: string;
      shared_fields: Array<{ name: string; data_type: string; required: boolean }>;
      operations: Array<{
        name: string;
        action: string;
        feature: string;
        required_parameters: string[];
        rules: string[];
      }>;
    }
  ) =>
    request<{ category_key: string; status: string }>(
      `/ansible/intent-catalog/category/${encodeURIComponent(categoryKey)}/ui-model`,
      { method: 'PUT', body: JSON.stringify(body) }
    ),

  /** Admin only — create categories/{key}.yaml skeleton and reload catalog. */
  createIntentCategory: (body: {
    category_key: string;
    display_name: string;
    category_description?: string;
    legacy_feature?: string;
  }) =>
    request<{ category_key: string; status: string }>(
      '/ansible/intent-catalog/category',
      { method: 'POST', body: JSON.stringify(body) }
    ),

  /** Admin only — delete categories/{key}.yaml and reload catalog. */
  deleteIntentCategory: (categoryKey: string) =>
    request<{ category_key: string; status: string }>(
      `/ansible/intent-catalog/category/${encodeURIComponent(categoryKey)}`,
      { method: 'DELETE' }
    ),

  /** List all reference playbook paths (for add-reference in Playbook Studio). */
  listReferencePaths: () =>
    request<{ paths: { path: string; feature: string; vendor: string }[] }>('/ansible/reference-paths'),

  /** Create a change-request + job and immediately generate a playbook via LLM. */
  createAndPlan: (
    req: {
      intent_description: string;
      target_hosts: string[];
      vendor: string;
      feature: string;
      params: Record<string, any>;
      device_id?: number;
      provider?: string;
      model?: string;
      ollama_base_url?: string;
    },
    opts?: { signal?: AbortSignal }
  ) =>
    request<{
      job_id: number;
      request_id: number;
      status: string;
      playbook: string;
      artifact_type?: 'playbook' | 'bundle';
      entrypoint_path?: string | null;
      files?: { path: string; content: string; writable?: boolean; kind?: string }[];
      manifest?: Record<string, any>;
      target_hosts?: string[];
    }>(
      '/ansible/create-and-plan',
      { method: 'POST', body: JSON.stringify(req), signal: opts?.signal }
    ),

  /** Create a playbook from a Config Engine template (deterministic, no LLM). */
  createFromTemplate: (
    req: {
      vendor: string;
      feature: string;
      params: Record<string, any>;
      target_hosts?: string[];
      device_id?: number;
    },
    opts?: { signal?: AbortSignal }
  ) =>
    request<{
      job_id: number; request_id: number; status: string;
      playbook: string; config: string; undo_config: string | null;
      entrypoint_path?: string | null;
      files?: { path: string; content: string; writable?: boolean; kind?: string }[];
    }>(
      '/ansible/create-from-template',
      { method: 'POST', body: JSON.stringify(req), signal: opts?.signal }
    ),

  /** Dry-run the stored playbook (--check --diff). */
  checkPlaybook: (jobId: number) =>
    request<{ rc: number; changed: boolean; diff: string; summary: string; stderr?: string }>(
      `/ansible/jobs/${jobId}/check`,
      { method: 'POST' }
    ),

  /** Approve the job for execution (admin only). */
  submitForApproval: (jobId: number) =>
    request<{ job_id: number; status: string; submitted_by: string }>(
      `/ansible/jobs/${jobId}/submit-for-approval`,
      { method: 'POST' }
    ),

  /** Approve the job for execution (queue reviewer). */
  approveJob: (jobId: number) =>
    request<{ job_id: number; status: string; approved_by: string }>(
      `/ansible/jobs/${jobId}/approve`,
      { method: 'POST' }
    ),

  rejectJob: (jobId: number, comment?: string) =>
    request<{ job_id: number; status: string; reviewed_by: string }>(
      `/ansible/jobs/${jobId}/reject`,
      { method: 'POST', body: JSON.stringify({ comment: comment || '' }) }
    ),

  /** Execute the approved playbook (admin only). */
  executeJob: (jobId: number) =>
    request<{ rc: number; success: boolean; changed: boolean; summary: string }>(
      `/ansible/jobs/${jobId}/execute`,
      { method: 'POST' }
    ),

  /** Poll job result and step logs. */
  getResult: (jobId: number) =>
    request<{
      job_id: number;
      status: string;
      stage: string;
      error: string | null;
      has_playbook: boolean;
      artifact_type?: 'playbook' | 'bundle';
      entrypoint_path?: string | null;
      files?: { path: string }[];
      steps: { stage: string; status: string; details: string; created_at: string }[];
    }>(`/ansible/jobs/${jobId}/result`),

  /** Latest Ansible job id for a deploy change request (if any). */
  getJobForRequest: (requestId: number) =>
    request<{ job_id: number; entrypoint_path: string | null }>(
      `/ansible/jobs/by-request/${requestId}`
    ),

  /** Get effective inventory for a job (hosts, IPs, credential source). Admin only: includes ssh_user, ssh_password from vault. */
  getJobInventory: (jobId: number) =>
    request<{
      job_id: number;
      hosts: {
        hostname: string;
        ansible_host: string;
        vendor: string;
        role: string;
        credentials_source: string;
        ssh_user?: string;
        ssh_password?: string;
      }[];
      credentials_note: string;
    }>(`/ansible/jobs/${jobId}/inventory`),

  /** List the ansible file tree (playbooks/, roles/, inventory/, awx/). */
  listFiles: () =>
    request<{ tree: any[] }>('/ansible/files'),

  /** Read a single file's content from the ansible directory. */
  readFile: (path: string) =>
    request<{ path: string; content: string; size: number; modified_at: string }>(
      `/ansible/files/${encodeURIComponent(path)}`
    ),

  /** Save edited file content back to disk (operator+). */
  writeFile: (path: string, content: string) =>
    request<{ path: string; status: string }>(
      `/ansible/files/${encodeURIComponent(path)}`,
      { method: 'PUT', body: JSON.stringify({ content }) }
    ),

  /** Create a new file under writable roots (operator/admin). */
  createFile: (path: string, content: string = '') =>
    request<{ path: string; status: string }>(
      `/ansible/files/create-file/${encodeURIComponent(path)}`,
      { method: 'POST', body: JSON.stringify({ content }) }
    ),

  /** Create a new folder under writable roots (operator/admin). */
  createFolder: (path: string) =>
    request<{ path: string; status: string }>(
      `/ansible/files/create-folder/${encodeURIComponent(path)}`,
      { method: 'POST' }
    ),

  /** Delete a file/folder under writable roots (operator/admin). */
  deletePath: (path: string) =>
    request<{ path: string; status: string }>(
      `/ansible/files/${encodeURIComponent(path)}`,
      { method: 'DELETE' }
    ),

  listChatSessions: () =>
    request<{
      sessions: {
        id: string;
        title: string;
        updatedAt: number | string;
        createdAt?: string;
        messages: any[];
      }[];
    }>('/ansible/chat-sessions'),

  upsertChatSession: (session: { id: string; title: string; messages: any[] }) =>
    request<{
      session: {
        id: string;
        title: string;
        updatedAt: number | string;
        createdAt?: string;
        messages: any[];
      };
    }>(`/ansible/chat-sessions/${encodeURIComponent(session.id)}`, {
      method: 'PUT',
      body: JSON.stringify(session),
    }),

  deleteChatSession: (sessionId: string) =>
    request<{ deleted: string }>(`/ansible/chat-sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    }),

  /** Rename (or move) a file/folder. Returns new path. */
  renamePath: (fromPath: string, toPath: string) =>
    request<{ from_path: string; to_path: string; status: string }>('/ansible/files/rename', {
      method: 'POST',
      body: JSON.stringify({ from_path: fromPath, to_path: toPath }),
    }),

  /**
   * Open an SSE stream for live ansible job output.
   * Returns an EventSource — caller is responsible for closing it.
   */
  streamJobOutput: (jobId: number): EventSource => {
    const token = localStorage.getItem('auth_token') ?? '';
    const url = `${BASE_URL}/api/v1/ansible/jobs/${jobId}/stream?token=${encodeURIComponent(token)}`;
    return new EventSource(url);
  },

  /** Phase 6: Generate multiple candidate playbooks (conservative/balanced/aggressive) */
  planCandidates: (jobId: number, req: {
    intent_description: string;
    target_hosts: string[];
    vendor: string;
    feature: string;
    params: Record<string, any>;
  }, n: number = 3) =>
    request<{
      job_id: number;
      candidates: {
        index: number;
        yaml: string;
        approach: string;
        valid: boolean;
        errors: string[];
      }[];
    }>(`/ansible/jobs/${jobId}/plan-candidates?n=${n}`, {
      method: 'POST',
      body: JSON.stringify(req),
    }),

  /** Phase 6: Select a candidate playbook after review */
  selectCandidate: (jobId: number, candidateIndex: number, playbookYaml: string) =>
    request<{ job_id: number; selected_index: number; status: string }>(
      `/ansible/jobs/${jobId}/select-candidate?candidate_index=${candidateIndex}`,
      { method: 'POST', body: JSON.stringify({ playbook_yaml: playbookYaml }) }
    ),
};

// ───────────────────────── Drift (M8) ─────────────────────────
export interface DriftEventRecord {
  id: number;
  device_id: number;
  has_drift: boolean;
  severity: string | null;
  diff_text: string | null;
  timestamp: string;
  hostname?: string;
}

export const driftApi = {
  /** Trigger a drift check for a single device. */
  checkDevice: (hostname: string) =>
    request<DriftEventRecord>(`/drift/check/${encodeURIComponent(hostname)}`, { method: 'POST' }),

  /** Trigger a network-wide drift check (background). */
  checkAll: () =>
    request<{ status: string; message: string }>('/drift/check/all', { method: 'POST' }),

  /** Fetch recent drift events. */
  listEvents: (limit = 50, deviceId?: number) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (deviceId !== undefined) params.set('device_id', String(deviceId));
    return request<{ events: DriftEventRecord[] }>(`/drift/events?${params}`);
  },

  /** Phase 13 — Get drift KPI summary (compliance rate, severity breakdown). */
  getSummary: () =>
    request<{
      total_devices: number;
      compliant: number;
      drifted: number;
      compliance_pct: number;
      severity_breakdown: Record<string, number>;
    }>('/drift/summary'),
};

// ───────────────────────── Policy Compliance APIs (M26) ─────────────────────────
export interface PolicyRule {
  id?: string;
  action: string;
  target?: string;
  path?: string;
  expected?: any;
  severity?: 'low' | 'medium' | 'high' | 'critical' | string;
  explanation?: string;
  remediation?: string;
  match_method?: string;
  operator?: string;
  role?: string;
}

export interface PolicyRecord {
  policy_id: string;
  name: string;
  description?: string;
  category: string;
  enabled: boolean;
  version?: number;
  scope: {
    vendors?: string[];
    device_roles?: string[];
    device_groups?: string[];
    devices?: string[];
  };
  rules: PolicyRule[];
  normalization?: { ignore_patterns?: string[]; [k: string]: any };
  thresholds?: { auto_alert?: boolean; alert_severity_min?: string; [k: string]: any };
  created_at?: string;
  updated_at?: string;
}

export interface ComplianceResultRecord {
  id?: number;
  device_id: number;
  hostname?: string;
  vendor?: string | null;
  role?: string | null;
  baseline_snapshot_id?: number | null;
  actual_snapshot_id?: number | null;
  status: 'compliant' | 'drifted' | 'violated' | 'no_baseline' | 'no_actual' | 'unreachable' | 'partial' | string;
  reason?: string;
  compliance_score?: number | null;
  severity?: string | null;
  drift_pct?: number | null;
  missing_lines?: string[];
  extra_lines?: string[];
  changed_lines?: Array<{ key?: string; baseline: string; actual: string }>;
  violated_policies?: Array<{ policy_id?: string; policy_name?: string; severity?: string; message?: string }>;
  checked_at?: string;
}

export const policyApi = {
  list: (includeDisabled = true) =>
    request<{ items: PolicyRecord[]; total: number }>(`/policies?include_disabled=${includeDisabled ? 'true' : 'false'}`),
  get: (policyId: string) =>
    request<{ policy: PolicyRecord }>(`/policies/${encodeURIComponent(policyId)}`),
  create: (policy: PolicyRecord) =>
    request<{ policy: PolicyRecord }>('/policies', { method: 'POST', body: JSON.stringify(policy) }),
  update: (policyId: string, patch: Partial<PolicyRecord>) =>
    request<{ policy: PolicyRecord }>(`/policies/${encodeURIComponent(policyId)}`, { method: 'PUT', body: JSON.stringify(patch) }),
  remove: (policyId: string) =>
    request<{ deleted: boolean; policy_id: string }>(`/policies/${encodeURIComponent(policyId)}`, { method: 'DELETE' }),
  clone: (policyId: string, suffix = 'clone') =>
    request<{ policy: PolicyRecord }>(`/policies/${encodeURIComponent(policyId)}/clone`, { method: 'POST', body: JSON.stringify({ suffix }) }),
  test: (policyId: string, body: { snapshot_id?: number; device_id?: number }) =>
    request<{ status: string; violations: any[] }>(`/policies/${encodeURIComponent(policyId)}/test`, { method: 'POST', body: JSON.stringify(body) }),
  audit: (policyId: string, limit = 50) =>
    request<{ items: any[]; total: number }>(`/policies/${encodeURIComponent(policyId)}/audit?limit=${limit}`),
};

export const complianceApi = {
  summary: () =>
    request<{
      counts: Record<string, number>;
      severity_breakdown: Record<string, number>;
      compliance_pct: number;
    }>('/compliance/summary'),
  listResults: (limit = 200) =>
    request<{ items: ComplianceResultRecord[]; total: number }>(`/compliance/results?limit=${limit}`),
  deviceResults: (deviceId: number, limit = 20) =>
    request<{ items: ComplianceResultRecord[]; total: number }>(`/compliance/results/${deviceId}?limit=${limit}`),
  scanAll: () =>
    request<{ status: string; job_id: number }>('/compliance/scan', { method: 'POST' }),
  scanDevice: (deviceId: number) =>
    request<{ result: ComplianceResultRecord }>(`/compliance/scan/${deviceId}`, { method: 'POST' }),
  listAlerts: (limit = 50) =>
    request<{ items: any[]; total: number }>(`/compliance/alerts?limit=${limit}`),
  ackAlert: (alertId: number) =>
    request<{ status: string; alert_id: number }>(`/compliance/alerts/${alertId}/ack`, { method: 'POST' }),
  getBaseline: (deviceId: number) =>
    request<{ baseline: { snapshot_id: number; device_id: number; captured_at?: string; snapshot_type?: string } | null }>(
      `/devices/${deviceId}/baseline`
    ),
  setBaseline: (deviceId: number, snapshotId: number) =>
    request<{ status: string; baseline_snapshot_id: number }>(`/devices/${deviceId}/baseline/set`, {
      method: 'POST',
      body: JSON.stringify({ snapshot_id: snapshotId }),
    }),
  listSnapshots: (deviceId: number, limit = 50) =>
    request<{ items: any[]; total: number }>(`/devices/${deviceId}/snapshots?limit=${limit}`),
  collectSnapshot: (deviceId: number) =>
    request<{ status: string; snapshot_id: number }>(`/devices/${deviceId}/snapshots/collect`, { method: 'POST' }),
  scopeOptions: () =>
    request<{ available: boolean; roles: string[]; tags: string[]; sites: string[]; tenants: string[] }>('/compliance/scope/options'),
};

// ───────────────────────── Dashboard Summary ─────────────────────────
export interface DashboardSummary {
  devices: { total: number; up: number; down: number; unknown: number };
  interfaces: { total: number; up: number; down: number };
  security: { avg_score: number; total_audits: number; critical_events: number };
  drift: { total_events: number; devices_with_drift: number; compliance_pct: number };
  deployments: { total_jobs: number; recent_7d: number; last_status: string | null };
  recent_events: Array<{ id: number; event_type: string; severity: string; description: string; created_at: string }>;
  recent_audit_log: Array<{ id: number; action: string; target: string; details: string; created_at: string }>;
}

export const dashboardApi = {
  getSummary: () => request<DashboardSummary>('/dashboard/summary'),
};

// ───────────────────────── M25: Incidents API ─────────────────────────

export interface IncidentSummary {
  id: string;
  anomaly_type: string;
  severity: string;
  affected_devices: string[];
  detected_at: string;
  status: string;
  root_cause_summary: string;
  created_at: string;
}

export interface AnomalyMetrics {
  current: number;
  baseline: number;
  threshold: number;
}

export interface IncidentReport {
  id: string;
  anomaly: {
    id: string;
    type: string;
    severity: string;
    affected_devices: string[];
    detected_at: string;
    metrics: AnomalyMetrics;
  };
  collected_context: {
    logs: string[];
    configs: Record<string, string>;
    telemetry_snapshot: Record<string, any>;
  };
  root_cause_analysis: {
    summary: string;
    confidence: number;
    possible_causes: string[];
  };
  proposed_solution: {
    description: string;
    playbook: {
      name: string;
      filename: string;
      content: string;
      hosts: string[];
      tasks: any[];
    };
    estimated_impact: string;
    rollback_plan: string;
  };
  status: string;
  approver: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export const incidentApi = {
  listIncidents: (status?: string, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (status) params.set('status', status);
    return request<IncidentSummary[]>(`/incidents?${params}`);
  },

  getIncident: (id: string) =>
    request<IncidentReport>(`/incidents/${encodeURIComponent(id)}`),

  approveIncident: (id: string) =>
    request<{ status: string; incident_id: string; playbook: string }>(
      `/incidents/${encodeURIComponent(id)}/approve`,
      { method: 'POST' }
    ),

  dismissIncident: (id: string) =>
    request<{ status: string; incident_id: string }>(
      `/incidents/${encodeURIComponent(id)}/dismiss`,
      { method: 'POST' }
    ),

  resolveIncident: (id: string) =>
    request<{ status: string; incident_id: string; resolved_at: string }>(
      `/incidents/${encodeURIComponent(id)}/resolve`,
      { method: 'POST' }
    ),

  ingestIncident: (data: {
    anomaly_type: string;
    severity?: string;
    affected_devices?: string[];
    metrics?: Record<string, number>;
    detected_at?: string;
    logs?: string[];
  }) =>
    request<{ status: string; incident_id: string; message: string }>(
      '/incidents/ingest',
      { method: 'POST', body: JSON.stringify(data) }
    ),

  // Phase 8 — Auto-Remediation (UC37)
  autoRemediate: (id: string, opts?: { skip_analysis?: boolean; require_approval?: boolean }) =>
    request<AutoRemediationResult>(
      `/incidents/${encodeURIComponent(id)}/auto-remediate`,
      { method: 'POST', body: JSON.stringify(opts ?? {}) }
    ),

  processPendingRemediations: () =>
    request<{ processed: number; succeeded: number; failed: number; errors?: string[] }>(
      '/incidents/auto-remediate/process-pending',
      { method: 'POST' }
    ),

  executePlaybook: (id: string) =>
    request<{ status: string; incident_id: string; result: any }>(
      `/incidents/${encodeURIComponent(id)}/execute`,
      { method: 'POST' }
    ),
};

// Auto-Remediation types
export interface AutoRemediationTimeline {
  step: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
  detail?: any;
}

export interface AutoRemediationResult {
  success: boolean;
  incident_id?: string;
  status?: string;
  error?: string;
  message?: string;
  execution_result?: any;
  timeline: AutoRemediationTimeline[];
}

// ───────────────────────── M25: Agent Plan/Execute API ─────────────────────────

export interface PlanResponse {
  plan_id: string;
  mode: 'planning';
  context_summary: {
    topology: string;
    current_state: string;
    affected_devices: string[];
  };
  proposed_actions: string[];
  expected_outcomes: {
    positive: string[];
    warnings: string[];
  };
  risk_level: 'low' | 'medium' | 'high';
}

export interface ExecuteResponse {
  mode: 'execution';
  plan_id: string;
  playbook: {
    name: string;
    filename: string;
    content: string;
    hosts: string[];
    tasks: any[];
  };
  estimated_duration: string;
  rollback_plan: string | null;
}

export const agentApi = {
  /** Phase 1: Plan — returns structured plan for user review. */
  plan: (message: string, domain: 'network' | 'security' = 'network', context?: {
    current_view?: string;
    selected_device?: string;
  }) =>
    request<PlanResponse>('/agent/plan', {
      method: 'POST',
      body: JSON.stringify({ message, domain, context: context ?? {} }),
    }),

  /** Phase 2: Execute — generate playbook from approved plan. */
  execute: (planId: string, action: 'generate' | 'revise' | 'cancel' = 'generate') =>
    request<ExecuteResponse>('/agent/execute', {
      method: 'POST',
      body: JSON.stringify({ plan_id: planId, action }),
    }),
};

// ───────────────────────── M25: Playbook Refine/History API ─────────────────────────

export interface ValidationError {
  line?: number;
  message: string;
  rule?: string;
}

export interface PlaybookVersion {
  version: number;
  playbook: string;
  created_at: string;
  refinement_notes?: string;
}

export interface RefineResponse {
  job_id: number;
  version: number;
  refined_playbook: string;
  changes: string[];
  iteration: number;
}

// Extends ansibleApi with refine + history
export const playbookApi = {
  refine: (jobId: number, errors: ValidationError[], feedback = '') =>
    request<RefineResponse>(
      `/ansible/jobs/${jobId}/refine`,
      { method: 'POST', body: JSON.stringify({ errors, feedback }) }
    ),

  getHistory: (jobId: number) =>
    request<PlaybookVersion[]>(`/ansible/jobs/${jobId}/history`),
};

// ───────────────────────── M25: Telemetry WebSocket helper ─────────────────────────

// ───────────────────────── Phase 1: Intent Lifecycle API (UC02-UC05) ─────────────────────────

export interface IntentSummary {
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
}

export interface IntentListResponse {
  items: IntentSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface IntentDetailResponse extends IntentSummary {
  parameters?: Record<string, any>;
  scope?: Record<string, any>;
  policy_result?: Record<string, any>;
  proposal_id?: string;
  linked_proposals: Record<string, any>[];
  linked_plans: Record<string, any>[];
}

export const intentApi = {
  /** List intents with optional filters and pagination */
  list: (params?: {
    status?: string;
    intent_type?: string;
    risk_level?: string;
    search?: string;
    page?: number;
    page_size?: number;
  }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set('status', params.status);
    if (params?.intent_type) query.set('intent_type', params.intent_type);
    if (params?.risk_level) query.set('risk_level', params.risk_level);
    if (params?.search) query.set('search', params.search);
    if (params?.page) query.set('page', String(params.page));
    if (params?.page_size) query.set('page_size', String(params.page_size));
    const qs = query.toString();
    return request<IntentListResponse>(`/intents${qs ? '?' + qs : ''}`);
  },

  /** Get full intent detail */
  get: (id: string) =>
    request<IntentDetailResponse>(`/intents/${id}`),

  /** Edit an intent (only draft/parsed/validated status) */
  update: (id: string, body: {
    user_prompt?: string;
    parameters?: Record<string, any>;
    scope?: Record<string, any>;
    risk_level?: string;
    vendor?: string;
    feature?: string;
  }) =>
    request<IntentSummary>(`/intents/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  /** Cancel an intent */
  cancel: (id: string) =>
    request<IntentSummary>(`/intents/${id}/cancel`, { method: 'POST' }),

  /** Duplicate an intent */
  duplicate: (id: string) =>
    request<IntentSummary>(`/intents/${id}/duplicate`, { method: 'POST' }),

  /** Get linked playbooks/proposals for an intent */
  getPlaybooks: (id: string) =>
    request<{
      intent_id: string;
      proposals: Record<string, any>[];
      agent_plans: Record<string, any>[];
    }>(`/intents/${id}/playbooks`),
};

// ───────────────────────── Phase 3: SDN Controller Management ─────────────────────────

export interface SDNControllerDetail {
  id: number;
  name: string;
  controller_type: string;
  url: string;
  username?: string;
  verify_ssl: boolean;
  status: string;
  managed_device_count: number;
  last_synced?: string;
  created_at?: string;
  updated_at?: string;
  managed_devices?: Record<string, any>[];
  topology_links?: Record<string, any>[];
}

export const sdnApi = {
  /** List all registered SDN controllers */
  list: () => request<SDNControllerDetail[]>('/sdn/controllers'),

  /** Get detailed info for one controller */
  get: (id: number) => request<SDNControllerDetail>(`/sdn/controllers/${id}`),

  /** Register a new SDN controller */
  create: (body: {
    name: string;
    controller_type: string;
    url: string;
    username?: string;
    password?: string;
    verify_ssl?: boolean;
  }) => request<SDNControllerDetail>('/sdn/controllers', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  /** Update controller settings */
  update: (id: number, body: {
    name?: string;
    url?: string;
    username?: string;
    password?: string;
    verify_ssl?: boolean;
  }) => request<SDNControllerDetail>(`/sdn/controllers/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }),

  /** Delete a controller */
  delete: (id: number) => request<{ status: string }>(`/sdn/controllers/${id}`, { method: 'DELETE' }),

  /** Trigger live sync */
  sync: (id: number) => request<{
    status: string;
    managed_device_count: number;
    topology_links: number;
    devices: Record<string, any>[];
    links: Record<string, any>[];
    synced_at: string;
  }>(`/sdn/controllers/${id}/sync`, { method: 'POST' }),

  /** Get topology from controller */
  topology: (id: number) => request<{
    controller_id: number;
    controller_name: string;
    devices: Record<string, any>[];
    links: Record<string, any>[];
  }>(`/sdn/controllers/${id}/topology`),
};

// ───────────────────────── Phase 5: Threshold Monitoring ─────────────────────────

export interface ThresholdRule {
  id: number;
  name: string;
  metric: string;
  measurement: string;
  field_key: string;
  operator: string;
  threshold: number;
  severity: string;
  device_filter?: Record<string, any> | null;
  cooldown_minutes: number;
  enabled: boolean;
  auto_remediate: boolean;
  remediation_playbook?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ThresholdAlert {
  id: number;
  rule_id: number;
  device?: string;
  metric?: string;
  value?: number;
  threshold?: number;
  severity?: string;
  status: string;
  incident_id?: string;
  created_at?: string;
  resolved_at?: string;
}

export const thresholdApi = {
  /** List all threshold rules */
  listRules: () => request<ThresholdRule[]>('/monitor/thresholds/rules'),

  /** Create a new threshold rule */
  createRule: (body: Omit<ThresholdRule, 'id' | 'created_at' | 'updated_at'>) =>
    request<ThresholdRule>('/monitor/thresholds/rules', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Update a rule */
  updateRule: (id: number, body: Partial<ThresholdRule>) =>
    request<ThresholdRule>(`/monitor/thresholds/rules/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  /** Delete a rule */
  deleteRule: (id: number) =>
    request<{ status: string }>(`/monitor/thresholds/rules/${id}`, { method: 'DELETE' }),

  /** List alerts */
  listAlerts: (statusFilter?: string, limit?: number) => {
    const params = new URLSearchParams();
    if (statusFilter) params.set('status_filter', statusFilter);
    if (limit) params.set('limit', String(limit));
    const qs = params.toString();
    return request<ThresholdAlert[]>(`/monitor/thresholds/alerts${qs ? '?' + qs : ''}`);
  },

  /** Acknowledge an alert */
  acknowledgeAlert: (id: number) =>
    request<{ status: string }>(`/monitor/thresholds/alerts/${id}/acknowledge`, { method: 'POST' }),

  /** Resolve an alert */
  resolveAlert: (id: number) =>
    request<{ status: string }>(`/monitor/thresholds/alerts/${id}/resolve`, { method: 'POST' }),

  /** Trigger manual evaluation of all rules */
  evaluate: () =>
    request<{ evaluated: number; alerts_created: number; errors: string[] }>(
      '/monitor/thresholds/evaluate', { method: 'POST' }),

  /** Evaluate all rules AND trigger auto-remediation for breaches */
  evaluateAndRemediate: () =>
    request<{
      evaluation: { evaluated: number; alerts_created: number; errors: string[] };
      remediation: { processed: number; succeeded: number; failed: number; error?: string };
    }>('/monitor/thresholds/evaluate-and-remediate', { method: 'POST' }),
};

// ───────────────────────── Phase 7: Unified Validator ─────────────────────────

export interface ValidationStage {
  name: string;
  passed: boolean;
  warnings: string[];
  errors: string[];
  details?: Record<string, any>;
  duration_ms: number;
}

export interface ValidationReport {
  report_id: string;
  overall_pass: boolean;
  stages: ValidationStage[];
  risk_level: string;
  blast_radius: number;
  timestamp: string;
}

export const validatorApi = {
  /** Run unified validation across all stages */
  runReport: (body: {
    playbook_yaml?: string;
    config_text?: string;
    vendor?: string;
    target_hosts?: string[];
    run_dry_run?: boolean;
    check_policy?: boolean;
  }) => request<ValidationReport>('/validate/report', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
};

// ───────────────────────── M25: Telemetry WebSocket helper ─────────────────────────

export const telemetryWs = {
  /**
   * Connect to the real-time telemetry stream.
   * @param onMessage - called for every incoming JSON payload
   * @returns WebSocket instance (close() to disconnect)
   */
  connect: (onMessage: (data: any) => void): WebSocket => {
    const wsBase = API_CONFIG.API_URL.replace(/^http/, 'ws');
    const ws = new WebSocket(`${wsBase}/api/telemetry`);
    ws.onopen  = () => console.debug('[telemetry] connected');
    ws.onclose = () => console.debug('[telemetry] disconnected');
    ws.onerror = (e) => console.warn('[telemetry] error', e);
    ws.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data)); } catch { /* ignore parse errors */ }
    };
    return ws;
  },
};
