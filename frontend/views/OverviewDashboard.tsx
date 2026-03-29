import React, { useState, useEffect, useCallback } from 'react';
import {
  Activity,
  Server,
  ShieldCheck,
  GitCompareArrows,
  Rocket,
  AlertTriangle,
  RefreshCw,
  ArrowRight,
  Clock,
  Wifi,
  WifiOff,
  HelpCircle,
} from 'lucide-react';
import { dashboardApi, netboxApi, incidentApi, deployApi, proposalApi } from '../src/services/api';
import type { DashboardSummary, DeviceRecord } from '../src/services/api';
import type { ViewId } from '../types';
import { Toast } from '../src/utils/swal';
import { COMPLIANCE_HUB_TAB_KEY } from '../utils/complianceNavigationKeys';

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
}

/* ────────── component ────────── */
const OverviewDashboard: React.FC<Props> = ({ setView, setSelectedDeviceId }) => {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<any[]>([]);
  const [recentDeploys, setRecentDeploys] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, d, inc, proposals, deploys] = await Promise.all([
        dashboardApi.getSummary(),
        netboxApi.listDevices(),
        incidentApi.listIncidents().catch(() => []),
        proposalApi.list('pending').catch(() => ({ proposals: [] })),
        deployApi.listJobs().catch(() => []),
      ]);
      setSummary(s);
      setDevices(Array.isArray(d?.devices) ? d.devices : []);
      setIncidents(Array.isArray(inc) ? inc : []);
      setPendingApprovals(proposals.proposals ?? []);
      setRecentDeploys(Array.isArray(deploys) ? deploys.slice(0, 5) : []);
    } catch (e: any) {
      Toast.error('Failed to load dashboard: ' + (e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

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
    <div className="h-full overflow-y-auto p-6 space-y-6">
      {/* ─── Header ─── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Network Dashboard</h1>
          <p className="text-sm opacity-60">Operational overview — CogniShield NMC</p>
        </div>
        <button className="btn btn-sm btn-ghost gap-2" onClick={refresh} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* ─── KPI Row ─── */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-6 gap-4">
        {/* Devices */}
        <div className="card bg-base-100 shadow cursor-pointer hover:shadow-lg transition-shadow"
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
        <div className="card bg-base-100 shadow">
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

        {/* Security Score */}
        <div className="card bg-base-100 shadow cursor-pointer hover:shadow-lg transition-shadow"
             onClick={() => setView('sec_dashboard')}>
          <div className="card-body p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase font-semibold opacity-60">Security Score</p>
                <p className={`text-3xl font-bold ${s.security.avg_score >= 80 ? 'text-success' : s.security.avg_score >= 50 ? 'text-warning' : 'text-error'}`}>
                  {s.security.avg_score > 0 ? pct(s.security.avg_score) : '—'}
                </p>
              </div>
              <ShieldCheck className="h-8 w-8 text-secondary opacity-40" />
            </div>
            <p className="text-xs mt-1 opacity-60">
              {s.security.total_audits} audits · {s.security.critical_events} critical (24h)
            </p>
          </div>
        </div>

        {/* Drift / Compliance */}
        <div className="card bg-base-100 shadow cursor-pointer hover:shadow-lg transition-shadow"
             onClick={() => {
               try {
                 sessionStorage.setItem(COMPLIANCE_HUB_TAB_KEY, 'results');
               } catch {
                 /* ignore */
               }
               setView('compliance_hub');
             }}>
          <div className="card-body p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase font-semibold opacity-60">Compliance</p>
                <p className={`text-3xl font-bold ${s.drift.compliance_pct >= 90 ? 'text-success' : s.drift.compliance_pct >= 70 ? 'text-warning' : 'text-error'}`}>
                  {pct(s.drift.compliance_pct)}
                </p>
              </div>
              <GitCompareArrows className="h-8 w-8 text-accent opacity-40" />
            </div>
            <p className="text-xs mt-1 opacity-60">
              {s.drift.devices_with_drift} device{s.drift.devices_with_drift !== 1 ? 's' : ''} drifted
            </p>
          </div>
        </div>

        {/* Deployments */}
        <div className="card bg-base-100 shadow cursor-pointer hover:shadow-lg transition-shadow"
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

        {/* Incidents */}
        <div className="card bg-base-100 shadow cursor-pointer hover:shadow-lg transition-shadow"
             onClick={() => setView('incidents')}>
          <div className="card-body p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase font-semibold opacity-60">Incidents</p>
                <p className={`text-3xl font-bold ${incidents.filter(i => i.status === 'open' || i.status === 'investigating').length > 0 ? 'text-error' : 'text-success'}`}>
                  {incidents.filter(i => i.status === 'open' || i.status === 'investigating').length}
                </p>
              </div>
              <AlertTriangle className="h-8 w-8 text-error opacity-40" />
            </div>
            <div className="flex gap-2 mt-1 text-xs flex-wrap">
              {['critical', 'high', 'medium'].map(sev => {
                const count = incidents.filter(i => i.severity === sev && (i.status === 'open' || i.status === 'investigating')).length;
                return count > 0 ? (
                  <span key={sev} className={`badge badge-xs ${severityColor[sev]}`}>{count} {sev}</span>
                ) : null;
              })}
              {incidents.filter(i => i.status === 'open' || i.status === 'investigating').length === 0 && (
                <span className="opacity-50">All clear</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ─── Two-Column: Events + Device Table ─── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Events */}
        <div className="card bg-base-100 shadow">
          <div className="card-body p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="card-title text-sm">Recent Security Events</h2>
              <button className="btn btn-ghost btn-xs gap-1" onClick={() => setView('sec_events')}>
                View all <ArrowRight className="h-3 w-3" />
              </button>
            </div>
            {s.recent_events.length === 0 ? (
              <p className="text-sm opacity-50 text-center py-8">No recent events</p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {s.recent_events.map((ev) => (
                  <div key={ev.id} className="flex items-start gap-3 p-2 rounded-lg hover:bg-base-200">
                    <AlertTriangle className={`h-4 w-4 mt-0.5 flex-shrink-0 ${ev.severity === 'critical' ? 'text-error' : ev.severity === 'high' ? 'text-warning' : 'text-info'}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`badge badge-xs ${severityColor[ev.severity] || 'badge-ghost'}`}>{ev.severity}</span>
                        <span className="text-xs font-medium truncate">{ev.event_type}</span>
                      </div>
                      <p className="text-xs opacity-70 truncate">{ev.description}</p>
                      <p className="text-[10px] opacity-40">{ev.created_at}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Device Inventory Table */}
        <div className="card bg-base-100 shadow">
          <div className="card-body p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="card-title text-sm">Device Inventory</h2>
              <button className="btn btn-ghost btn-xs gap-1" onClick={() => setView('netbox_devices')}>
                Manage <ArrowRight className="h-3 w-3" />
              </button>
            </div>
            <div className="overflow-x-auto max-h-64">
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
      </div>

      {/* ─── Two-Column: Deployments + Audit Log ─── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* Pending Approvals */}
        <div className="card bg-base-100 shadow">
          <div className="card-body p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="card-title text-sm">Pending Approvals</h2>
              <button className="btn btn-ghost btn-xs gap-1" onClick={() => setView('ansible_studio')}>
                Studio <ArrowRight className="h-3 w-3" />
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

        {/* Recent Deployments */}
        <div className="card bg-base-100 shadow">
          <div className="card-body p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="card-title text-sm">Recent Deployments</h2>
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
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Audit Log */}
        <div className="card bg-base-100 shadow">
          <div className="card-body p-4">
            <h2 className="card-title text-sm mb-3">Audit Log</h2>
            {s.recent_audit_log.length === 0 ? (
              <p className="text-sm opacity-50 text-center py-8">No recent actions</p>
            ) : (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {s.recent_audit_log.map((e) => (
                  <div key={e.id} className="flex items-start gap-3 p-2 rounded-lg hover:bg-base-200">
                    <Clock className="h-4 w-4 mt-0.5 flex-shrink-0 opacity-40" />
                    <div className="min-w-0">
                      <p className="text-xs font-medium">{e.action} → {e.target}</p>
                      <p className="text-[10px] opacity-50 truncate">{e.details}</p>
                      <p className="text-[10px] opacity-40">{e.created_at}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Quick Actions */}
        <div className="card bg-base-100 shadow">
          <div className="card-body p-4">
            <h2 className="card-title text-sm mb-3">Quick Actions</h2>
            <div className="grid grid-cols-2 gap-2">
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('topology')}>
                <Activity className="h-4 w-4" /> View Topology
              </button>
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('net_chat')}>
                <ArrowRight className="h-4 w-4" /> AI Copilot
              </button>
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('ansible_studio')}>
                <ArrowRight className="h-4 w-4" /> Ansible Studio
              </button>
              <button className="btn btn-sm btn-outline gap-2" onClick={() => setView('sec_dashboard')}>
                <ShieldCheck className="h-4 w-4" /> Security Audit
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default OverviewDashboard;
