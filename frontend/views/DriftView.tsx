import React, { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw, GitCompareArrows, CheckCircle, AlertTriangle, XCircle,
  ChevronDown, ChevronRight, Play, Shield
} from 'lucide-react';
import { driftApi, type DriftEventRecord } from '../src/services/api';
import { Toast } from '../src/utils/swal';

// ── Severity badge ────────────────────────────────────────────────────────────
const SeverityBadge: React.FC<{ severity: string }> = ({ severity }) => {
  const cfg: Record<string, string> = {
    low:    'badge-success',
    medium: 'badge-warning',
    high:   'badge-error',
  };
  return (
    <span className={`badge badge-sm ${cfg[severity] ?? 'badge-ghost'} capitalize`}>
      {severity}
    </span>
  );
};

// ── Per-row diff expander ─────────────────────────────────────────────────────
const DriftRow: React.FC<{
  event: DriftEventRecord;
  onCheck: (hostname: string) => void;
  checking: boolean;
}> = ({ event, onCheck, checking }) => {
  const [open, setOpen] = useState(false);

  return (
    <>
      <tr className="hover cursor-pointer" onClick={() => setOpen(o => !o)}>
        <td className="w-6">
          {open
            ? <ChevronDown className="w-4 h-4 opacity-50" />
            : <ChevronRight className="w-4 h-4 opacity-50" />}
        </td>
        <td className="font-mono font-semibold">{event.hostname ?? '—'}</td>
        <td>
          {event.has_drift ? (
            <span className="flex items-center gap-1 text-error">
              <AlertTriangle className="w-4 h-4" /> Drifted
            </span>
          ) : (
            <span className="flex items-center gap-1 text-success">
              <CheckCircle className="w-4 h-4" /> Compliant
            </span>
          )}
        </td>
        <td><SeverityBadge severity={event.severity ?? 'low'} /></td>
        <td className="text-xs opacity-60">{new Date(event.timestamp).toLocaleString()}</td>
        <td>
          <button
            className="btn btn-xs btn-ghost gap-1"
            disabled={checking}
            onClick={(e) => { e.stopPropagation(); onCheck(event.hostname ?? ''); }}
          >
            {checking
              ? <span className="loading loading-spinner loading-xs" />
              : <><RefreshCw className="w-3 h-3" /> Re-check</>}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="p-0">
            <div className="bg-base-300 border-t border-base-200 p-4">
              {event.diff_text ? (
                <>
                  <p className="text-xs font-semibold mb-2 flex items-center gap-1 text-warning">
                    <GitCompareArrows className="w-3 h-3" /> Configuration Diff
                  </p>
                  <pre className="text-xs font-mono bg-neutral-900 text-green-300 p-3 rounded-lg
                                  overflow-x-auto max-h-64 overflow-y-auto whitespace-pre">
                    {event.diff_text}
                  </pre>
                </>
              ) : (
                <p className="text-xs opacity-50 italic">No diff available — device is compliant.</p>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

// ── Main view ─────────────────────────────────────────────────────────────────
const DriftView: React.FC = () => {
  const [events, setEvents]           = useState<DriftEventRecord[]>([]);
  const [loading, setLoading]         = useState(true);
  const [scanning, setScanning]       = useState(false);
  const [checkingHost, setCheckingHost] = useState<string | null>(null);

  const loadEvents = useCallback(async () => {
    try {
      const res = await driftApi.listEvents(100);
      setEvents(res.events);
    } catch (err: any) {
      Toast.error('Failed to load drift events', err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadEvents(); }, [loadEvents]);

  const handleScanAll = async () => {
    setScanning(true);
    try {
      const res = await driftApi.checkAll();
      Toast.success('Drift scan started', res.message);
      // Poll after a short delay to let background task finish
      setTimeout(loadEvents, 4000);
    } catch (err: any) {
      Toast.error('Scan failed', err.message);
    } finally {
      setScanning(false);
    }
  };

  const handleCheckDevice = async (hostname: string) => {
    setCheckingHost(hostname);
    try {
      const event = await driftApi.checkDevice(hostname);
      setEvents(prev => {
        const filtered = prev.filter(e => e.hostname !== hostname);
        return [event, ...filtered].sort(
          (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
        );
      });
      if (event.has_drift) {
        Toast.warning(`Drift detected on ${hostname}`, `Severity: ${event.severity}`);
      } else {
        Toast.success(`${hostname} is compliant`);
      }
    } catch (err: any) {
      Toast.error(`Check failed for ${hostname}`, err.message);
    } finally {
      setCheckingHost(null);
    }
  };

  // Derived stats
  const driftCount   = events.filter(e => e.has_drift).length;
  const totalDevices = new Set(events.map(e => e.hostname)).size;
  const compliancePct = totalDevices > 0
    ? Math.round(((totalDevices - driftCount) / totalDevices) * 100)
    : 100;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-base-300 bg-base-100 shrink-0">
        <div className="flex items-center gap-3">
          <GitCompareArrows className="w-5 h-5 text-primary" />
          <div>
            <h1 className="font-bold text-lg leading-tight">Compliance &amp; Drift</h1>
            <p className="text-xs opacity-50">Compare running configs against last known-good snapshots</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-ghost btn-sm gap-1" onClick={loadEvents} disabled={loading}>
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button className="btn btn-primary btn-sm gap-1" onClick={handleScanAll} disabled={scanning}>
            {scanning
              ? <><span className="loading loading-spinner loading-xs" /> Scanning…</>
              : <><Play className="w-4 h-4" /> Scan All Devices</>}
          </button>
        </div>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-3 gap-4 px-6 py-4 shrink-0">
        <div className="stat bg-base-100 rounded-xl border border-base-300 p-4">
          <div className="stat-title text-xs">Compliance Rate</div>
          <div className={`stat-value text-2xl ${compliancePct === 100 ? 'text-success' : compliancePct < 70 ? 'text-error' : 'text-warning'}`}>
            {compliancePct}%
          </div>
          <div className="stat-desc">{totalDevices} devices checked</div>
        </div>
        <div className="stat bg-base-100 rounded-xl border border-base-300 p-4">
          <div className="stat-title text-xs">Devices with Drift</div>
          <div className={`stat-value text-2xl ${driftCount === 0 ? 'text-success' : 'text-error'}`}>
            {driftCount}
          </div>
          <div className="stat-desc">of {totalDevices} total</div>
        </div>
        <div className="stat bg-base-100 rounded-xl border border-base-300 p-4">
          <div className="stat-title text-xs">Policy Status</div>
          <div className="stat-value text-2xl flex items-center gap-2">
            {driftCount === 0
              ? <><Shield className="w-6 h-6 text-success" /><span className="text-success text-lg">Clean</span></>
              : <><XCircle className="w-6 h-6 text-error" /><span className="text-error text-lg">Violations</span></>}
          </div>
          <div className="stat-desc">{events.length} total events</div>
        </div>
      </div>

      {/* Events table */}
      <div className="grow overflow-y-auto px-6 pb-6 min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-40 gap-2 opacity-50">
            <span className="loading loading-spinner" />
            <span className="text-sm">Loading drift events…</span>
          </div>
        ) : events.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-3 opacity-40">
            <CheckCircle className="w-12 h-12 text-success" />
            <p className="text-sm font-medium">No drift events found</p>
            <p className="text-xs">Run a scan to check device compliance</p>
          </div>
        ) : (
          <div className="overflow-x-auto bg-base-100 rounded-xl border border-base-300">
            <table className="table table-sm w-full">
              <thead className="bg-base-200 sticky top-0 z-10">
                <tr>
                  <th className="w-6"></th>
                  <th>Device</th>
                  <th>Status</th>
                  <th>Severity</th>
                  <th>Last Checked</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event, i) => (
                  <DriftRow
                    key={`${event.hostname}-${i}`}
                    event={event}
                    onCheck={handleCheckDevice}
                    checking={checkingHost === event.hostname}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default DriftView;
