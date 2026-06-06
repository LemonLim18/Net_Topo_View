import React from 'react';
import {
  GitCompareArrows, Shield, Calendar, ArrowRight, Play, RefreshCw,
} from 'lucide-react';
import type { ComplianceResultRecord } from '../../src/services/api';

interface Props {
  summary: {
    counts?: Record<string, number>;
    compliance_pct?: number;
  } | null;
  events: ComplianceResultRecord[];
  loading: boolean;
  scanning: boolean;
  onRefresh: () => void;
  onScanAll: () => void;
}

/**
 * KPI strip shared by Compliance hub overview and full-page drift (optional).
 */
const ComplianceOverviewCards: React.FC<Props> = ({
  summary,
  events,
  loading,
  scanning,
  onRefresh,
  onScanAll,
}) => {
  const totalDevices = summary?.counts?.total ?? events.length;
  const compliantCount = summary?.counts?.compliant ?? events.filter((e) => e.status === 'compliant').length;
  const driftCount = (summary?.counts?.drifted ?? 0) + (summary?.counts?.violated ?? 0);
  const compliancePct =
    summary?.compliance_pct ?? (totalDevices > 0 ? Math.round((compliantCount / totalDevices) * 100) : 100);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <button type="button" className="btn btn-sm btn-ghost gap-2" onClick={onRefresh} disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
        <button
          type="button"
          className={`btn btn-sm btn-primary gap-2 ${scanning ? 'btn-disabled' : ''}`}
          onClick={onScanAll}
          disabled={scanning}
        >
          {scanning ? <span className="loading loading-spinner loading-xs" /> : <Play className="w-4 h-4" />}
          Global scan
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="relative overflow-hidden bg-base-100/60 backdrop-blur-md rounded-2xl border border-base-300 p-4 shadow-sm">
          <div className="absolute top-0 left-0 w-1 h-full bg-primary opacity-40" />
          <p className="text-[10px] font-black capitalize tracking-[0.2em] opacity-30 mb-1">Health index</p>
          <div className="flex justify-between items-start">
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
            <Shield className={`w-5 h-5 ${compliancePct === 100 ? 'text-success' : 'text-primary'}`} />
          </div>
          <p className="mt-3 text-[11px] opacity-60">{totalDevices} devices in latest results</p>
          <progress
            className={`progress w-full mt-2 ${
              compliancePct === 100 ? 'progress-success' : compliancePct < 70 ? 'progress-error' : 'progress-warning'
            }`}
            value={compliancePct}
            max={100}
          />
        </div>

        <div className="relative overflow-hidden bg-base-100/60 backdrop-blur-md rounded-2xl border border-base-300 p-4 shadow-sm">
          <div className="absolute top-0 left-0 w-1 h-full bg-error opacity-40" />
          <p className="text-[10px] font-black capitalize tracking-[0.2em] opacity-30 mb-1">Drift / violations</p>
          <div className="flex items-baseline gap-2">
            <span className={`text-4xl font-black ${driftCount === 0 ? 'text-success' : 'text-error'}`}>
              {driftCount}
            </span>
            <span className="text-lg font-bold opacity-30">nodes</span>
          </div>
          <GitCompareArrows className="w-5 h-5 mt-2 opacity-40" />
        </div>

        <div className="relative overflow-hidden bg-base-100/60 backdrop-blur-md rounded-2xl border border-base-300 p-4 shadow-sm">
          <div className="absolute top-0 left-0 w-1 h-full bg-info opacity-40" />
          <p className="text-[10px] font-black capitalize tracking-[0.2em] opacity-30 mb-1">Latest check</p>
          <div className="flex items-center gap-2 text-sm font-bold">
            <Calendar className="w-4 h-4 opacity-50" />
            {events[0]?.checked_at ? new Date(events[0].checked_at).toLocaleString() : 'N/A'}
          </div>
          <div className="mt-3 flex items-center gap-1 text-[11px] opacity-50">
            <span>Results tab</span>
            <ArrowRight className="w-3 h-3" />
          </div>
        </div>
      </div>
    </div>
  );
};

export default ComplianceOverviewCards;
