import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Activity,
  Server,
  Rocket,
  RefreshCw,
  ArrowRight,
  Clock,
  Wifi,
  WifiOff,
  HelpCircle,
  AlertTriangle,
  Shield,
  Settings,
  GitBranch,
  Radio,
  CheckSquare,
  Zap,
} from 'lucide-react';
import { dashboardApi, netboxApi, deployApi, proposalApi, telemetryWs, syslogApi } from '../src/services/api';
import type { DashboardSummary, DeviceRecord } from '../src/services/api';
import type { ViewId, User } from '../types';
import { normalizeUserRole } from '../types';
import { Toast } from '../src/utils/swal';


/* ────────── helpers ────────── */
const pct = (n: number) => `${n.toFixed(1)}%`;

const severityColor: Record<string, string> = {
  critical: 'badge-error',
  high: 'badge-warning',
  medium: 'badge-info',
  low: 'badge-ghost',
  info: 'badge-ghost',
};

/* ────────── props ────────── */
interface Props {
  setView: (v: ViewId) => void;
  setSelectedDeviceId?: (id: number) => void;
  user?: User | null;
}

/* ────────── component ────────── */
const OverviewDashboard: React.FC<Props> = ({ setView, setSelectedDeviceId, user }) => {
  const normalizedRole = user ? normalizeUserRole(user.role) : 'viewer';
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [syslogEvents, setSyslogEvents] = useState<any[]>([]);
  const [syslogSources, setSyslogSources] = useState<any[]>([]);
  const [selectedSyslogSourceIp, setSelectedSyslogSourceIp] = useState('');

  const [pendingApprovals, setPendingApprovals] = useState<any[]>([]);
  const [recentDeploys, setRecentDeploys] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const refreshDebounceRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, d, proposals, deploys] = await Promise.all([
        dashboardApi.getSummary(),
        netboxApi.listDevices(),
        proposalApi.list('pending').catch(() => ({ proposals: [] })),
        deployApi.listJobs().catch(() => []),
      ]);
      const [events, sources] = await Promise.all([
        syslogApi.listEvents(14, selectedSyslogSourceIp || undefined).catch(() => []),
        syslogApi.listSources(30).catch(() => ({ items: [] })),
      ]);
      setSummary(s);
      setDevices(Array.isArray(d?.devices) ? d.devices : []);
      setPendingApprovals(proposals.proposals ?? []);
      setRecentDeploys(Array.isArray(deploys) ? deploys.slice(0, 5) : []);
      setSyslogEvents(Array.isArray(events) ? events : []);
      setSyslogSources(Array.isArray(sources?.items) ? sources.items : []);
    } catch (e: any) {
      Toast.error('Failed to load dashboard: ' + (e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, [selectedSyslogSourceIp]);

  useEffect(() => { refresh(); }, [refresh]);

  // Keep dashboard fresh even when user does not click "Refresh".
  useEffect(() => {
    const intervalId = window.setInterval(() => {
      refresh();
    }, 20000);
    return () => window.clearInterval(intervalId);
  }, [refresh]);

  // Real-time refresh on backend telemetry pushes.
  useEffect(() => {
    const ws = telemetryWs.connect((msg: any) => {
      const evt = String(msg?.event || '').toLowerCase();
      if (!evt) return;
      const shouldRefresh =
        evt === 'security_event' ||
        evt === 'device_syslog_event' ||
        evt === 'incident_created' ||
        evt === 'incident_updated' ||
        evt === 'incident_resolved' ||
        evt === 'drift_check_complete';
      if (!shouldRefresh) return;

      if (refreshDebounceRef.current) {
        window.clearTimeout(refreshDebounceRef.current);
      }
      refreshDebounceRef.current = window.setTimeout(() => {
        refresh();
      }, 1200);
    });

    return () => {
      if (refreshDebounceRef.current) {
        window.clearTimeout(refreshDebounceRef.current);
      }
      ws.close();
    };
  }, [refresh]);

  if (loading && !summary) {
    return (
      <div className="flex items-center justify-center h-full">
        <span className="loading loading-spinner loading-lg text-primary" />
      </div>
    );
  }

  const s = summary ?? {
    devices: { total: 0, up: 0, down: 0, unknown: 0 },
    interfaces: { total: 0, up: 0, down: 0 },
    security: { avg_score: 0, total_audits: 0, critical_events: 0 },
    drift: { total_events: 0, devices_with_drift: 0, compliance_pct: 100 },
    deployments: { total_jobs: 0, recent_7d: 0, last_status: null },
    recent_events: [],
    recent_audit_log: [],
  };

  return (
    <div className="h-full overflow-y-auto p-6 space-y-5">
      {/* ─── Header ─── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Network Dashboard</h1>
          <p className="text-sm opacity-60">Operational overview — CogniShield NMC</p>
        </div>
        <button className="btn btn-sm btn-ghost gap-2" onClick={refresh} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* ─── KPI Row ─── */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Devices */}
        <div className="card border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg cursor-pointer hover:shadow-xl transition-shadow"
             onClick={() => setView('netbox_devices')}>
          <div className="card-body p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase font-semibold opacity-60">Devices</p>
                <p className="text-3xl font-bold">{s.devices.total}</p>
              </div>
              <Server className="h-8 w-8 text-primary opacity-40" />
            </div>
            <div className="flex gap-3 mt-1 text-xs">
              <span className="flex items-center gap-1"><Wifi className="h-3 w-3 text-success" />{s.devices.up} up</span>
              <span className="flex items-center gap-1"><WifiOff className="h-3 w-3 text-error" />{s.devices.down} down</span>
              {s.devices.unknown > 0 && <span className="flex items-center gap-1"><HelpCircle className="h-3 w-3 text-warning" />{s.devices.unknown}</span>}
            </div>
          </div>
        </div>

        {/* Interfaces */}
        <div className="card border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg">
          <div className="card-body p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase font-semibold opacity-60">Interfaces</p>
                <p className="text-3xl font-bold">{s.interfaces.total}</p>
              </div>
              <Activity className="h-8 w-8 text-info opacity-40" />
            </div>
            <div className="flex gap-3 mt-1 text-xs">
              <span className="text-success">{s.interfaces.up} up</span>
              <span className="text-error">{s.interfaces.down} down</span>
            </div>
          </div>
        </div>



        {/* Deployments */}
        <div className="card border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg cursor-pointer hover:shadow-xl transition-shadow"
             onClick={() => setView('ansible_studio')}>
          <div className="card-body p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase font-semibold opacity-60">Deployments</p>
                <p className="text-3xl font-bold">{s.deployments.total_jobs}</p>
              </div>
              <Rocket className="h-8 w-8 text-primary opacity-40" />
            </div>
            <p className="text-xs mt-1 opacity-60">
              {s.deployments.recent_7d} in last 7 days
              {s.deployments.last_status && <span className="ml-1">· last: {s.deployments.last_status}</span>}
            </p>
          </div>
        </div>


      </div>

      {/* ─── Live Syslog ─── */}
      <div className="card shadow-xl border border-base-300 bg-gradient-to-br from-base-100 via-base-100 to-base-200/70">
        <div className="card-body p-0">
          <div className="px-4 py-3 border-b border-base-300/70 flex items-center justify-between">
            <div className="flex items-center gap-2">
              {/* <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-warning/15 text-warning"> */}
              <span className="inline-flex h-8 w-8 items-center justify-center text-warning">
                <AlertTriangle className="h-4 w-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold tracking-wide">Live Syslog Stream</h2>
                <p className="text-[11px] opacity-60">Real-time infrastructure logs across all onboarded devices</p>
              </div>
            </div>
            <button className="btn btn-ghost btn-xs gap-1" onClick={() => setView('common_services')}>
              Open Full View <ArrowRight className="h-3 w-3" />
            </button>
          </div>

          <div className="p-4">
            {syslogEvents.length === 0 && syslogSources.length === 0 ? (
              <div className="rounded-xl border border-dashed border-base-300 bg-base-100/60 px-4 py-5 text-sm opacity-65">
                No syslog events yet. Generate an interface/admin event on a device and this stream will populate.
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
            <div className="lg:col-span-3 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs uppercase tracking-wide opacity-60">Filter Source</span>
                <select
                  className="select select-bordered select-sm w-full max-w-sm"
                  value={selectedSyslogSourceIp}
                  onChange={(e) => setSelectedSyslogSourceIp(e.target.value)}
                >
                  <option value="">All Devices</option>
                  {syslogSources.map((source: any) => {
                    const label = source?.device_hostname || source?.source_host || source?.source_ip || 'unknown';
                    return (
                      <option key={`${source?.source_ip || 'unknown'}-${source?.device_hostname || ''}`} value={source?.source_ip || ''}>
                        {label}{source?.source_ip ? ` (${source.source_ip})` : ''} · {source?.event_count || 0}
                      </option>
                    );
                  })}
                </select>
              </div>

              {syslogEvents.length === 0 ? (
                <div className="rounded-xl border border-dashed border-base-300 bg-base-100/60 px-4 py-5 text-sm opacity-65">
                  No syslog events yet. Generate an interface/admin event on a device and this stream will populate.
                </div>
              ) : (
                <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
                  {syslogEvents.map((evt: any) => (
                    <div key={evt.id} className="rounded-lg border border-base-300 bg-base-100/80 px-3 py-1.5 shadow-sm">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`badge badge-xs ${severityColor[String(evt.severity || 'info').toLowerCase()] || 'badge-ghost'}`}>
                            {evt.severity || 'info'}
                          </span>
                          <span className="text-xs font-semibold truncate">
                            {evt.mnemonic || evt.facility || 'syslog'}
                          </span>
                          <span className="text-[10px] opacity-55 font-semibold">{evt.device_hostname || evt.source_host || 'unknown'}</span>
                          {evt.source_ip ? <span className="text-[10px] opacity-45 font-mono">{evt.source_ip}</span> : null}
                        </div>
                        <span className="text-[10px] opacity-50 shrink-0">{evt.created_at || '-'}</span>
                      </div>
                      <div className="mt-0.5 text-[11px] opacity-70 truncate">
                        {evt.message || evt.raw_message}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="lg:col-span-1 rounded-xl border border-base-300 bg-base-100/70 p-3">
              <div className="text-xs uppercase tracking-wide opacity-60 mb-2">Top Sources</div>
              {syslogSources.length === 0 ? (
                <div className="text-xs opacity-55">No active sources.</div>
              ) : (
                <div className="space-y-1.5 max-h-56 overflow-y-auto">
                  {syslogSources.slice(0, 10).map((source: any) => (
                    <button
                      key={`${source?.source_ip || 'unknown'}-${source?.device_hostname || ''}`}
                      className={`w-full text-left rounded-md px-2 py-1.5 border transition-colors ${
                        selectedSyslogSourceIp && selectedSyslogSourceIp === source?.source_ip
                          ? 'border-warning/40 bg-warning/10'
                          : 'border-base-300 hover:bg-base-200'
                      }`}
                      onClick={() => setSelectedSyslogSourceIp(source?.source_ip || '')}
                    >
                      <div className="text-xs font-semibold truncate">
                        {source?.device_hostname || source?.source_host || source?.source_ip || 'unknown'}
                      </div>
                      <div className="text-[10px] opacity-65 flex items-center justify-between mt-0.5">
                        <span className="font-mono truncate">{source?.source_ip || '-'}</span>
                        <span>{source?.event_count || 0}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
            )}
          </div>
        </div>
      </div>

      {/* ─── Device Inventory ─── */}
      <div className="card border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg">
        <div className="card-body p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="card-title text-sm flex items-center gap-2">
              <Server className="h-4 w-4 text-info" />
              Device Inventory
            </h2>
            <button className="btn btn-ghost btn-xs gap-1" onClick={() => setView('netbox_devices')}>
              Manage <ArrowRight className="h-3 w-3" />
            </button>
          </div>
          <div className="overflow-x-auto max-h-52">
            <table className="table table-xs table-pin-rows">
              <thead>
                <tr>
                  <th>Hostname</th>
                  <th>IP</th>
                  <th>Vendor</th>
                  <th>Model</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {devices.slice(0, 15).map((d) => (
                  <tr key={d.id} className="hover cursor-pointer"
                      onClick={() => { setSelectedDeviceId?.(d.id); setView('device_detail'); }}>
                    <td className="font-medium">{d.hostname}</td>
                    <td className="opacity-70">{d.management_ip || '—'}</td>
                    <td className="opacity-70">{d.vendor || '—'}</td>
                    <td className="opacity-70">{d.model || '—'}</td>
                    <td>
                      <span className={`badge badge-xs ${d.status === 'active' ? 'badge-success' : d.status === 'down' ? 'badge-error' : 'badge-warning'}`}>
                        {d.status}
                      </span>
                    </td>
                  </tr>
                ))}
                {devices.length === 0 && (
                  <tr><td colSpan={5} className="text-center opacity-50 py-8">No devices discovered yet</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ─── Two-Column: Approvals + Deployments ─── */}
      <div className={`grid grid-cols-1 ${normalizedRole === 'admin' ? 'lg:grid-cols-2' : 'lg:grid-cols-1'} gap-5 items-stretch`}>

        {/* Pending Approvals */}
        {normalizedRole === 'admin' && (
          <div className="card h-full border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg">
            <div className="card-body p-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="card-title text-sm flex items-center gap-2">
                  <CheckSquare className="h-4 w-4 text-warning" />
                  Pending Approvals
                </h2>
                <button className="btn btn-ghost btn-xs gap-1" onClick={() => setView('approval_queue')}>
                  Approvals <ArrowRight className="h-3 w-3" />
                </button>
              </div>
              {pendingApprovals.length === 0 ? (
                <p className="text-sm opacity-50 text-center py-8">No pending approvals</p>
              ) : (
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {pendingApprovals.map((p: any) => (
                    <div key={p.id} className="flex items-start gap-3 p-2 rounded-lg hover:bg-base-200">
                      <Rocket className="h-4 w-4 mt-0.5 flex-shrink-0 text-warning" />
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium truncate">{p.title ?? p.description ?? `Proposal ${p.id}`}</p>
                        <p className="text-[10px] opacity-50">{p.vendor ?? ''} / {p.feature ?? ''}</p>
                      </div>
                      <span className="badge badge-xs badge-warning">pending</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Recent Deployments */}
        <div className="card h-full border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg">
          <div className="card-body p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="card-title text-sm flex items-center gap-2">
                <Rocket className="h-4 w-4 text-primary" />
                Recent Deployments
              </h2>
              <button className="btn btn-ghost btn-xs gap-1" onClick={() => setView('ansible_studio')}>
                View all <ArrowRight className="h-3 w-3" />
              </button>
            </div>
            {recentDeploys.length === 0 ? (
              <p className="text-sm opacity-50 text-center py-8">No deployments yet</p>
            ) : (
              <table className="table table-xs">
                <thead><tr><th>Job</th><th>Status</th><th>Stage</th><th>Created</th></tr></thead>
                <tbody>
                  {recentDeploys.map((j: any) => (
                    <tr key={j.id ?? j.job_id} className="hover">
                      <td className="font-mono text-[11px]">#{j.id ?? j.job_id}</td>
                      <td>
                        <span className={`badge badge-xs ${j.status === 'success' ? 'badge-success' : j.status === 'failed' ? 'badge-error' : 'badge-ghost'}`}>
                          {j.status}
                        </span>
                      </td>
                      <td className="text-[11px] opacity-70">{j.stage ?? '—'}</td>
                      <td className="text-[11px] opacity-50">{j.created_at ? new Date(j.created_at).toLocaleString() : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* ─── Two-Column: Audit Log + Quick Actions ─── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5 items-stretch">
        {/* Recent Audit Log */}
        <div className="lg:col-span-3 card h-full border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg">
          <div className="card-body p-4">
            <h2 className="card-title text-sm mb-2 flex items-center gap-2">
              <Clock className="h-4 w-4 text-info" />
              Audit Log
            </h2>
            {s.recent_audit_log.length === 0 ? (
              <p className="text-sm opacity-50 text-center py-6">No recent actions</p>
            ) : (
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {s.recent_audit_log.map((e) => (
                  <div key={e.id} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-base-200 text-xs">
                    <Clock className="h-3.5 w-3.5 flex-shrink-0 opacity-40" />
                    <span className="font-medium whitespace-nowrap">{e.action} → {e.target}</span>
                    <span className="opacity-45 truncate flex-1 min-w-0">{e.details}</span>
                    <span className="opacity-35 shrink-0 text-[10px]">{e.created_at}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Quick Actions */}
        <div className="lg:col-span-2 card h-full border border-base-300 bg-gradient-to-br from-base-100 to-base-200/70 shadow-lg">
          <div className="card-body p-4">
            <h2 className="card-title text-sm mb-2 flex items-center gap-2">
              <Zap className="h-4 w-4 text-warning" />
              Quick Actions
            </h2>
            <div className="grid grid-cols-2 gap-2">
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('topology')}>
                <Activity className="h-3.5 w-3.5" /> Topology
              </button>
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('ansible_studio')}>
                <Rocket className="h-3.5 w-3.5" /> Ansible Studio
              </button>
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('common_services')}>
                <Settings className="h-3.5 w-3.5" /> Service Profiles
              </button>
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('config_backup')}>
                <GitBranch className="h-3.5 w-3.5" /> Config Backup
              </button>
              {/* <button className="btn btn-sm btn-outline gap-2 justify-start" onClick={() => setView('compliance_hub')}>
                <Shield className="h-3.5 w-3.5" /> Compliance
              </button> */}
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('net_perf')}>
                <Radio className="h-3.5 w-3.5" /> Performance
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default OverviewDashboard;

