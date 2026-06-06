import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  RefreshCw, GitCompareArrows, CheckCircle, AlertTriangle,
  ChevronRight, Play, Shield,
  Search, Smartphone, Calendar, ArrowRight, Layers,
  ExternalLink, Wrench,
} from 'lucide-react';
import { complianceApi, netboxApi, type ComplianceResultRecord, type DeviceRecord } from '../src/services/api';
import { Toast } from '../src/utils/swal';
import type { ViewId } from '../types';
import {
  buildRemediationIntentPayload,
  storeRemediationIntentAndGoStudio,
} from '../utils/remediationPrompt';
import {
  COMPLIANCE_FILTER_DEVICE_KEY,
  COMPLIANCE_HUB_TAB_KEY,
  POLICY_FOCUS_IDS_KEY,
} from '../utils/complianceNavigationKeys';

export interface DriftViewProps {
  setView?: (v: ViewId) => void;
  setSelectedDeviceId?: (id: number | null) => void;
  /** When true, omit full-page hero + KPI strip (used inside Compliance hub). */
  variant?: 'page' | 'embedded';
}

// ── Severity badge ────────────────────────────────────────────────────────────
const SeverityBadge: React.FC<{ severity: string }> = ({ severity }) => {
  const cfg: Record<string, string> = {
    low: 'badge-success',
    medium: 'badge-warning',
    high: 'badge-error',
    critical: 'badge-error',
  };
  return (
    <span
      className={`badge badge-sm font-mono uppercase tracking-tight ${cfg[severity] ?? 'badge-ghost'} text-[10px]`}
    >
      {severity}
    </span>
  );
};

// ── Per-row diff expander ─────────────────────────────────────────────────────
const DriftRow: React.FC<{
  event: ComplianceResultRecord;
  onCheck: (deviceId: number) => void;
  checking: boolean;
  deviceMeta?: DeviceRecord | undefined;
  setView?: (v: ViewId) => void;
  setSelectedDeviceId?: (id: number | null) => void;
}> = ({ event, onCheck, checking, deviceMeta, setView, setSelectedDeviceId }) => {
  const [open, setOpen] = useState(false);
  const status = String(event.status || '').toLowerCase();

  const getStatusIcon = () => {
    if (status === 'compliant') return <CheckCircle className="w-4 h-4 text-success" />;
    if (['no_baseline', 'unreachable', 'no_actual'].includes(status)) {
      return <AlertTriangle className="w-4 h-4 text-warning" />;
    }
    return <AlertTriangle className="w-4 h-4 text-error" />;
  };

  const getStatusClass = () => {
    if (status === 'compliant') return 'text-success';
    if (['no_baseline', 'unreachable', 'no_actual'].includes(status)) return 'text-warning';
    return 'text-error';
  };

  const hostname =
    (event as { hostname?: string }).hostname ||
    deviceMeta?.hostname ||
    (event.device_id != null ? `#${event.device_id}` : '—');
  const roleLabel = deviceMeta?.role || '—';
  const vendorLabel = deviceMeta?.vendor || '—';
  const lifecycleLabel = [deviceMeta?.status, deviceMeta?.source].filter(Boolean).join(' · ') || '—';

  const goDevice360 = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!setView || !setSelectedDeviceId || event.device_id == null) return;
    setSelectedDeviceId(Number(event.device_id));
    setView('compliance_device_detail');
  };

  const goPolicies = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!setView) return;
    const ids = (event.violated_policies || [])
      .map((v: { policy_id?: string }) => v.policy_id)
      .filter(Boolean) as string[];
    try {
      if (ids.length) sessionStorage.setItem(POLICY_FOCUS_IDS_KEY, JSON.stringify(ids));
      sessionStorage.setItem(COMPLIANCE_HUB_TAB_KEY, 'policies');
    } catch {
      /* ignore */
    }
    setView('compliance_hub');
  };

  const goInventory = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!setView || !setSelectedDeviceId || event.device_id == null) return;
    setSelectedDeviceId(Number(event.device_id));
    setView('netbox_devices');
  };

  const goRemediate = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!setView) return;
    const payload = buildRemediationIntentPayload(event, {
      vendor: deviceMeta?.vendor,
      role: deviceMeta?.role,
      hostname: deviceMeta?.hostname || (event as { hostname?: string }).hostname,
    });
    storeRemediationIntentAndGoStudio(payload, setView);
  };

  const needsRemediation = status !== 'compliant' && !['no_baseline', 'unreachable', 'no_actual'].includes(status);

  return (
    <>
      <tr
        className={`hover:bg-base-200/50 cursor-pointer transition-colors border-b border-base-300/40 group ${
          open ? 'bg-base-200/30' : ''
        }`}
        onClick={() => setOpen((o) => !o)}
      >
        <td className="w-10 text-center">
          <div className={`p-1 rounded transition-transform duration-200 ${open ? 'rotate-90' : ''}`}>
            <ChevronRight className="w-3.5 h-3.5 opacity-40" />
          </div>
        </td>
        <td className="font-mono font-bold text-xs py-2">
          <div className="flex items-center gap-2">
            <Smartphone className="w-3.5 h-3.5 opacity-30" />
            {hostname}
          </div>
        </td>
        <td className="py-2 text-[10px] opacity-70 max-w-[160px]">
          <div className="truncate" title={`${roleLabel} · ${vendorLabel}`}>
            <span className="font-semibold">{roleLabel}</span>
            <span className="opacity-50"> · </span>
            <span>{vendorLabel}</span>
          </div>
          <div
            className="truncate text-[9px] opacity-50 mt-0.5"
            title={`Planned inventory: ${lifecycleLabel}`}
          >
            {lifecycleLabel}
          </div>
        </td>
        <td className="py-2">
          <div className={`flex items-center gap-2 text-xs font-bold uppercase tracking-wider ${getStatusClass()}`}>
            {getStatusIcon()}
            {status === 'no_actual' ? 'Snapshot Error' : status || 'drifted'}
          </div>
        </td>
        <td className="py-2">
          <SeverityBadge severity={event.severity ?? 'low'} />
        </td>
        <td className="py-2">
          <div className="flex items-center gap-1.5 text-[11px] opacity-60">
            <Calendar className="w-3 h-3" />
            {event.checked_at ? new Date(event.checked_at).toLocaleString() : '-'}
          </div>
        </td>
        <td className="py-2 text-right w-36">
          <button
            className="btn btn-xs btn-ghost gap-1.5 opacity-0 group-hover:opacity-100 group-hover:bg-base-300"
            disabled={checking}
            onClick={(e) => {
              e.stopPropagation();
              onCheck(Number(event.device_id));
            }}
          >
            {checking ? (
              <span className="loading loading-spinner loading-xs text-primary" />
            ) : (
              <>
                <RefreshCw className="w-3 h-3" />{' '}
                <span className="text-[10px] uppercase font-bold tracking-widest">Re-check</span>
              </>
            )}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="p-0 border-none">
            <div className="bg-base-300/50 backdrop-blur-sm p-6 space-y-4 shadow-inner">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded bg-warning/10 flex items-center justify-center text-warning">
                    <GitCompareArrows className="w-3.5 h-3.5" />
                  </div>
                  <h4 className="text-xs font-bold uppercase tracking-widest opacity-80">Configuration insight</h4>
                </div>
                <div className="flex flex-wrap gap-1">
                  {setView && setSelectedDeviceId && (
                    <button type="button" className="btn btn-xs btn-primary gap-1" onClick={goDevice360}>
                      <ExternalLink className="w-3 h-3" /> Device compliance 360
                    </button>
                  )}
                  {setView && setSelectedDeviceId && (
                    <button type="button" className="btn btn-xs btn-outline gap-1" onClick={goInventory}>
                      Inventory
                    </button>
                  )}
                  {setView && (event.violated_policies?.length ?? 0) > 0 && (
                    <button type="button" className="btn btn-xs btn-outline gap-1" onClick={goPolicies}>
                      Related policies
                    </button>
                  )}
                  {setView && needsRemediation && (
                    <button type="button" className="btn btn-xs btn-accent gap-1" onClick={goRemediate}>
                      <Wrench className="w-3 h-3" /> Remediate in Ansible Studio
                    </button>
                  )}
                </div>
              </div>

              <p className="text-[10px] uppercase tracking-widest opacity-50">
                Planned inventory context: role <b className="text-base-content">{roleLabel}</b>, vendor{' '}
                <b className="text-base-content">{vendorLabel}</b>
                {deviceMeta?.management_ip ? (
                  <>
                    {' '}
                    · mgmt <b className="font-mono text-base-content">{deviceMeta.management_ip}</b>
                  </>
                ) : null}
              </p>
              {(event.violated_policies?.length ?? 0) > 0 && (
                <p className="text-[10px] leading-relaxed opacity-60">
                  <span className="font-bold uppercase tracking-widest opacity-50">Why this device is in scope: </span>
                  policies{' '}
                  <b className="text-base-content">
                    {(event.violated_policies || [])
                      .map((v: { policy_id?: string; policy_name?: string }) => v.policy_name || v.policy_id || '—')
                      .filter(Boolean)
                      .join(', ')}
                  </b>{' '}
                  — evaluated against this host using planned inventory role, vendor, and device record (same source as
                  the compliance engine).
                </p>
              )}

              <div className="flex gap-4 text-[10px] font-mono tracking-tighter uppercase opacity-50">
                <span>
                  Score: <b className="text-base-content">{event.compliance_score ?? '-'}</b>
                </span>
                <span>
                  Drift: <b className="text-base-content">{event.drift_pct ?? '-'}%</b>
                </span>
              </div>

              {(event.missing_lines?.length ||
                event.extra_lines?.length ||
                event.changed_lines?.length ||
                event.reason ||
                event.violated_policies?.length) ? (
                <div className="space-y-3">
                  {event.reason && (
                    <div className="p-3 bg-base-100/50 border border-base-300/50 rounded-lg text-xs leading-relaxed">
                      <span className="font-bold text-[10px] uppercase opacity-40 block mb-1">Reason / analysis</span>
                      {event.reason}
                    </div>
                  )}

                  <div className="relative group/code">
                    <div className="absolute top-2 right-2 text-[8px] font-bold uppercase tracking-widest opacity-30 select-none">
                      Config diff
                    </div>
                    <pre
                      className="text-xs font-mono bg-[#0D1117] text-[#C9D1D9] p-5 rounded-xl
                                    overflow-x-auto max-h-80 overflow-y-auto whitespace-pre border border-white/5
                                    shadow-2xl custom-scrollbar leading-relaxed"
                    >
                      {[
                        ...((event.missing_lines || []).map((x) => `<span style="color: #4ade80;">+ ${x}</span>`)),
                        ...((event.extra_lines || []).map((x) => `<span style="color: #fb7185;">- ${x}</span>`)),
                        ...((event.changed_lines || []).map(
                          (x: { baseline?: string; actual?: string }) =>
                            `<span style="color: #60a5fa;">- ${x?.baseline || ''}</span>\n<span style="color: #c084fc;">+ ${x?.actual || ''}</span>`,
                        )),
                      ].length > 0 ? (
                        <div
                          dangerouslySetInnerHTML={{
                            __html: [
                              ...((event.missing_lines || []).map((x) => `<span style="color: #4ade80;">+ ${x}</span>`)),
                              ...((event.extra_lines || []).map((x) => `<span style="color: #fb7185;">- ${x}</span>`)),
                              ...((event.changed_lines || []).map(
                                (x: { baseline?: string; actual?: string }) =>
                                  `<span style="color: #60a5fa;">- ${x?.baseline || ''}</span>\n<span style="color: #c084fc;">+ ${x?.actual || ''}</span>`,
                              )),
                            ].join('\n'),
                          }}
                        />
                      ) : (
                        <span className="opacity-40 italic">
                          Structural compliance maintained. No line-by-line differences found.
                        </span>
                      )}
                    </pre>
                  </div>

                  {event.violated_policies && event.violated_policies.length > 0 && (
                    <div className="space-y-2">
                      <span className="font-bold text-[10px] uppercase opacity-40 block">Policy violations</span>
                      <div className="flex flex-wrap gap-2">
                        {event.violated_policies.map((v: { policy_id?: string; message?: string }, idx: number) => (
                          <div
                            key={idx}
                            className="bg-error/10 border border-error/20 p-2 rounded-lg flex items-start gap-2 max-w-sm"
                          >
                            <Shield className="w-3 h-3 text-error mt-0.5 shrink-0" />
                            <div className="text-[10px]">
                              <b className="uppercase">{v.policy_id || 'Policy'}</b>: {v.message || 'Validation failed'}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-6 gap-2 bg-success/5 rounded-xl border border-success/20">
                  <CheckCircle className="w-8 h-8 text-success opacity-40" />
                  <p className="text-[11px] font-bold uppercase tracking-widest text-success/80">
                    Device is fully compliant
                  </p>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

// ── Main view ─────────────────────────────────────────────────────────────────
const DriftView: React.FC<DriftViewProps> = ({
  setView,
  setSelectedDeviceId,
  variant = 'page',
}) => {
  const [events, setEvents] = useState<ComplianceResultRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [checkingDevice, setCheckingDevice] = useState<number | null>(null);
  const [summary, setSummary] = useState<{
    counts?: Record<string, number>;
    compliance_pct?: number;
  } | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [deviceById, setDeviceById] = useState<Record<number, DeviceRecord>>({});

  const loadEvents = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([complianceApi.listResults(500), complianceApi.summary()]);
      setEvents(
        (r.items || []).sort(
          (a, b) => new Date(b.checked_at || '').getTime() - new Date(a.checked_at || '').getTime(),
        ),
      );
      setSummary(s);
    } catch (err: any) {
      Toast.error('Failed to load compliance results', err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await netboxApi.listDevices();
        const list = res?.devices || [];
        const map: Record<number, DeviceRecord> = {};
        for (const d of list) {
          if (d?.id != null) map[d.id] = d;
        }
        if (!cancelled) setDeviceById(map);
      } catch {
        if (!cancelled) setDeviceById({});
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(COMPLIANCE_FILTER_DEVICE_KEY);
      if (raw) {
        sessionStorage.removeItem(COMPLIANCE_FILTER_DEVICE_KEY);
        const id = parseInt(raw, 10);
        if (Number.isFinite(id)) setSearchTerm(String(id));
      }
    } catch {
      /* ignore */
    }
  }, []);

  const handleScanAll = async () => {
    setScanning(true);
    try {
      const res = await complianceApi.scanAll();
      Toast.success('Compliance scan started', `job_id=${res.job_id}`);
      setTimeout(loadEvents, 4000);
    } catch (err: any) {
      Toast.error('Scan failed', err.message);
    } finally {
      setScanning(false);
    }
  };

  const handleCheckDevice = async (deviceId: number) => {
    setCheckingDevice(deviceId);
    try {
      const wrap = await complianceApi.scanDevice(deviceId);
      const event = wrap.result;
      setEvents((prev) => {
        const filtered = prev.filter((e) => e.device_id !== deviceId);
        return [event, ...filtered].sort(
          (a, b) => new Date(b.checked_at || '').getTime() - new Date(a.checked_at || '').getTime(),
        );
      });
      if (event.status && event.status !== 'compliant') {
        Toast.warning(`Device #${deviceId} not compliant`, `Severity: ${event.severity || '-'}`);
      } else {
        Toast.success(`Device #${deviceId} is compliant`);
      }
    } catch (err: any) {
      Toast.error(`Check failed for device #${deviceId}`, err.message);
    } finally {
      setCheckingDevice(null);
    }
  };

  const filteredEvents = useMemo(() => {
    return events.filter((e) => {
      const meta = e.device_id != null ? deviceById[e.device_id] : undefined;
      const host = String((e as { hostname?: string }).hostname || meta?.hostname || e.device_id || '').toLowerCase();
      const role = String(meta?.role || '').toLowerCase();
      const vendor = String(meta?.vendor || '').toLowerCase();
      const invStatus = String(meta?.status || '').toLowerCase();
      const invSource = String(meta?.source || '').toLowerCase();
      const q = searchTerm.toLowerCase();
      return (
        host.includes(q) ||
        role.includes(q) ||
        vendor.includes(q) ||
        invStatus.includes(q) ||
        invSource.includes(q) ||
        String(e.status).toLowerCase().includes(q) ||
        String(e.device_id).includes(q)
      );
    });
  }, [events, searchTerm, deviceById]);

  const totalDevices = summary?.counts?.total ?? events.length;
  const compliantCount = summary?.counts?.compliant ?? events.filter((e) => e.status === 'compliant').length;
  const driftCount = (summary?.counts?.drifted ?? 0) + (summary?.counts?.violated ?? 0);
  const compliancePct =
    summary?.compliance_pct ?? (totalDevices > 0 ? Math.round((compliantCount / totalDevices) * 100) : 100);

  const tableSection = (
    <div className="grow overflow-hidden px-6 pb-4 flex flex-col min-h-0">
      <div className="bg-base-100/60 backdrop-blur-md rounded-2xl border border-base-300 shadow-sm flex flex-col overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-base-300/40 bg-base-100/20">
          <div className="flex items-center gap-2">
            <div className="bg-primary/10 p-1.5 rounded-lg text-primary">
              <Layers className="w-3.5 h-3.5" />
            </div>
            <span className="text-[10px] font-black uppercase tracking-[.15em] opacity-40">Drift inventory</span>
          </div>
          <div className="relative">
            <Search className="absolute left-3 top-2.5 w-3.5 h-3.5 opacity-30" />
            <input
              className="input input-xs input-bordered h-8 pl-8 rounded-lg bg-base-100/50 w-64 text-xs"
              placeholder="Search hostname, role, status, id…"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
        </div>

        <div className="grow overflow-y-auto custom-scrollbar overflow-x-auto">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-64 gap-3 opacity-40 animate-pulse">
              <span className="loading loading-spinner text-primary" />
              <span className="text-[10px] font-bold uppercase tracking-widest">Hydrating events…</span>
            </div>
          ) : filteredEvents.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 gap-4 py-12">
              <div className="w-16 h-16 rounded-full bg-success/5 flex items-center justify-center text-success/20">
                <CheckCircle className="w-8 h-8" />
              </div>
              <div className="text-center">
                <p className="text-sm font-bold uppercase tracking-widest opacity-60">No matching rows</p>
                <p className="text-xs opacity-40 mt-1">Adjust search or run a global scan from Overview.</p>
              </div>
            </div>
          ) : (
            <table className="table table-sm w-full border-separate border-spacing-0">
              <thead className="sticky top-0 bg-base-100 z-10">
                <tr>
                  <th className="w-10 bg-base-100" />
                  <th className="text-[10px] font-bold uppercase tracking-widest opacity-50 bg-base-100">
                    Node / hostname
                  </th>
                  <th className="text-[10px] font-bold uppercase tracking-widest opacity-50 bg-base-100">
                    Planned context
                  </th>
                  <th className="text-[10px] font-bold uppercase tracking-widest opacity-50 bg-base-100">Status</th>
                  <th className="text-[10px] font-bold uppercase tracking-widest opacity-50 bg-base-100">
                    Criticality
                  </th>
                  <th className="text-[10px] font-bold uppercase tracking-widest opacity-50 bg-base-100">Checked</th>
                  <th className="text-[10px] font-bold uppercase tracking-widest opacity-50 bg-base-100 text-right pr-6">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredEvents.map((event: ComplianceResultRecord, i: number) => (
                  <DriftRow
                    key={`${event.hostname || event.device_id}-${i}`}
                    event={event}
                    onCheck={handleCheckDevice}
                    checking={checkingDevice === event.device_id}
                    deviceMeta={event.device_id != null ? deviceById[event.device_id] : undefined}
                    setView={setView}
                    setSelectedDeviceId={setSelectedDeviceId}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );

  if (variant === 'embedded') {
    return <div className="flex flex-col h-full min-h-0 overflow-hidden bg-base-200">{tableSection}</div>;
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-base-200">
      <div className="px-6 py-4 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 bg-primary/10 rounded-2xl flex items-center justify-center text-primary shadow-sm border border-primary/20">
              <GitCompareArrows className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight leading-none">Compliance &amp; drift</h1>
              <div className="flex items-center gap-2 mt-1.5">
                <span className="label-text text-xs font-bold tracking-wider opacity-60">
                  Full-page results (use Compliance hub for policies &amp; alerts)
                </span>
                {setView && (
                  <button
                    type="button"
                    className="btn btn-xs btn-outline"
                    onClick={() => setView('compliance_hub')}
                  >
                    Open governance hub
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button className="btn btn-sm btn-ghost gap-2 group" onClick={loadEvents} disabled={loading}>
              <RefreshCw
                className={`w-4 h-4 transition-transform group-hover:rotate-180 ${
                  loading ? 'animate-spin text-primary' : 'opacity-40'
                }`}
              />
              <span className="text-xs uppercase font-bold tracking-widest">Refresh</span>
            </button>
            <button
              className={`btn btn-sm gap-2 ${scanning ? 'btn-disabled' : 'btn-primary shadow-lg shadow-primary/20'}`}
              onClick={handleScanAll}
              disabled={scanning}
            >
              {scanning ? <span className="loading loading-spinner loading-xs" /> : <Play className="w-4 h-4" />}
              <span className="text-xs uppercase font-bold tracking-widest">Global scan</span>
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="relative overflow-hidden group bg-base-100/60 backdrop-blur-md rounded-2xl border border-base-300 p-4 shadow-sm transition-all hover:bg-base-100/80">
            <div className="absolute top-0 left-0 w-1 h-full bg-primary opacity-40" />
            <div className="flex justify-between items-start">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.2em] opacity-30 mb-1">Health index</p>
                <div className="flex items-baseline gap-2">
                  <span
                    className={`text-4xl font-black ${
                      compliancePct === 100 ? 'text-success' : compliancePct < 70 ? 'text-error' : 'text-warning'
                    }`}
                  >
                    {compliancePct}
                  </span>
                  <span className="text-lg font-bold opacity-30">%</span>
                </div>
              </div>
              <div className="p-2 bg-primary/5 rounded-xl border border-primary/10">
                <Shield className={`w-5 h-5 ${compliancePct === 100 ? 'text-success' : 'text-primary'}`} />
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between text-[11px]">
              <span className="opacity-50 font-medium">Compliance rate</span>
              <span className="font-bold opacity-80 uppercase tracking-tighter">{totalDevices} devices trackable</span>
            </div>
            <progress
              className={`progress w-full mt-2 ${
                compliancePct === 100 ? 'progress-success' : compliancePct < 70 ? 'progress-error' : 'progress-warning'
              }`}
              value={compliancePct}
              max={100}
            />
          </div>

          <div className="relative overflow-hidden group bg-base-100/60 backdrop-blur-md rounded-2xl border border-base-300 p-4 shadow-sm transition-all hover:bg-base-100/80">
            <div className="absolute top-0 left-0 w-1 h-full bg-error opacity-40" />
            <div className="flex justify-between items-start">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.2em] opacity-30 mb-1">Drift detected</p>
                <div className="flex items-baseline gap-2">
                  <span className={`text-4xl font-black ${driftCount === 0 ? 'text-success' : 'text-error'}`}>
                    {driftCount}
                  </span>
                  <span className="text-lg font-bold opacity-30">Nodes</span>
                </div>
              </div>
              <div className="p-2 bg-error/5 rounded-xl border border-error/10">
                <GitCompareArrows className={`w-5 h-5 ${driftCount === 0 ? 'text-success' : 'text-error'}`} />
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between text-[11px]">
              <span className="opacity-50 font-medium">Configuration drift</span>
              <span className="font-bold opacity-80 uppercase tracking-tighter">Requires attention</span>
            </div>
            <div className="flex gap-1.5 mt-2.5">
              {Array.from({ length: 8 }).map((_, i) => (
                <div
                  key={i}
                  className={`h-1 cursor-help grow rounded-full ${
                    i < (driftCount / Math.max(totalDevices, 1)) * 8 ? 'bg-error' : 'bg-base-300'
                  } transition-colors`}
                />
              ))}
            </div>
          </div>

          <div className="relative overflow-hidden group bg-base-100/60 backdrop-blur-md rounded-2xl border border-base-300 p-4 shadow-sm transition-all hover:bg-base-100/80">
            <div className="absolute top-0 left-0 w-1 h-full bg-info opacity-40" />
            <div className="flex justify-between items-start">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.2em] opacity-30 mb-1">Latest scan</p>
                <div className="flex items-baseline gap-2">
                  <span className="text-[13px] font-black opacity-80 uppercase tracking-tight truncate max-w-[140px]">
                    {events[0]?.checked_at ? new Date(events[0].checked_at).toLocaleTimeString() : 'N/A'}
                  </span>
                </div>
              </div>
              <div className="p-2 bg-info/5 rounded-xl border border-info/10">
                <Calendar className="w-5 h-5 text-info" />
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between text-[11px]">
              <span className="opacity-50 font-medium">Status</span>
              <div className="flex items-center gap-1.5">
                <span className="font-bold opacity-80 uppercase tracking-tighter">Completed</span>
                <ArrowRight className="w-3 h-3 opacity-40" />
              </div>
            </div>
          </div>
        </div>
      </div>

      {tableSection}
    </div>
  );
};

export default DriftView;
