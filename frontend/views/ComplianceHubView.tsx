import React, { useCallback, useEffect, useState } from 'react';
import { LayoutDashboard, GitCompareArrows, ClipboardList, BellRing, Shield } from 'lucide-react';
import type { ViewId } from '../types';
import DriftView from './DriftView';
import PolicyBuilder from './PolicyBuilder';
import ComplianceOverviewCards from '../components/compliance/ComplianceOverviewCards';
import ComplianceAlertsPanel from '../components/compliance/ComplianceAlertsPanel';
import { complianceApi, type ComplianceResultRecord } from '../src/services/api';
import { Toast } from '../src/utils/swal';
import { COMPLIANCE_HUB_TAB_KEY, type ComplianceHubTab } from '../utils/complianceNavigationKeys';

interface Props {
  setView: (v: ViewId) => void;
  setSelectedDeviceId: (id: number | null) => void;
}

const ComplianceHubView: React.FC<Props> = ({ setView, setSelectedDeviceId }) => {
  const [tab, setTab] = useState<ComplianceHubTab>('overview');
  const [summary, setSummary] = useState<{
    counts?: Record<string, number>;
    compliance_pct?: number;
  } | null>(null);
  const [events, setEvents] = useState<ComplianceResultRecord[]>([]);
  const [loadingOverview, setLoadingOverview] = useState(true);
  const [scanning, setScanning] = useState(false);

  const loadOverviewData = useCallback(async () => {
    setLoadingOverview(true);
    try {
      const [r, s] = await Promise.all([complianceApi.listResults(500), complianceApi.summary()]);
      setEvents(
        (r.items || []).sort(
          (a, b) => new Date(b.checked_at || '').getTime() - new Date(a.checked_at || '').getTime(),
        ),
      );
      setSummary(s);
    } catch (e: any) {
      Toast.error('Failed to load compliance overview', e.message);
    } finally {
      setLoadingOverview(false);
    }
  }, []);

  useEffect(() => {
    void loadOverviewData();
  }, [loadOverviewData]);

  useEffect(() => {
    try {
      const t = sessionStorage.getItem(COMPLIANCE_HUB_TAB_KEY);
      if (t === 'results' || t === 'policies' || t === 'alerts' || t === 'overview') {
        setTab(t);
      }
      sessionStorage.removeItem(COMPLIANCE_HUB_TAB_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const handleScanAll = async () => {
    setScanning(true);
    try {
      const res = await complianceApi.scanAll();
      Toast.success('Compliance scan started', `job_id=${res.job_id}`);
      setTimeout(() => void loadOverviewData(), 4000);
    } catch (e: any) {
      Toast.error('Scan failed', e.message);
    } finally {
      setScanning(false);
    }
  };

  const tabs: { id: ComplianceHubTab; label: string; icon: React.ReactNode }[] = [
    { id: 'overview', label: 'Overview', icon: <LayoutDashboard className="w-4 h-4" /> },
    { id: 'results', label: 'Drift & results', icon: <GitCompareArrows className="w-4 h-4" /> },
    { id: 'policies', label: 'Policies', icon: <ClipboardList className="w-4 h-4" /> },
    { id: 'alerts', label: 'Alerts', icon: <BellRing className="w-4 h-4" /> },
  ];

  return (
    <div className="flex flex-col h-full overflow-hidden bg-base-200">
      <div className="shrink-0 border-b border-base-300 bg-base-100/80 backdrop-blur-md px-4 py-3">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center text-primary border border-primary/20">
              <Shield className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">Compliance &amp; Governance</h1>
              <p className="text-xs opacity-60">
                Policies, drift results, alerts, and remediation — tied to planned inventory
              </p>
            </div>
          </div>
          <div role="tablist" className="tabs tabs-boxed bg-base-200/80 p-1 rounded-xl flex-wrap">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                className={`tab tab-sm gap-1.5 ${tab === t.id ? 'tab-active' : ''}`}
                onClick={() => setTab(t.id)}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grow min-h-0 overflow-hidden flex flex-col">
        {tab === 'overview' && (
          <div className="grow overflow-y-auto p-6 space-y-6">
            <ComplianceOverviewCards
              summary={summary}
              events={events}
              loading={loadingOverview}
              scanning={scanning}
              onRefresh={loadOverviewData}
              onScanAll={handleScanAll}
            />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <button
                type="button"
                className="btn btn-outline btn-block h-auto py-4 flex-col gap-1"
                onClick={() => setTab('results')}
              >
                <GitCompareArrows className="w-5 h-5" />
                <span className="font-bold">Open drift inventory</span>
                <span className="text-xs opacity-60 font-normal">Per-device diffs and remediation</span>
              </button>
              <button
                type="button"
                className="btn btn-outline btn-block h-auto py-4 flex-col gap-1"
                onClick={() => setTab('policies')}
              >
                <ClipboardList className="w-5 h-5" />
                <span className="font-bold">Edit policies</span>
                <span className="text-xs opacity-60 font-normal">Scope rules to roles / NetBox</span>
              </button>
              <button
                type="button"
                className="btn btn-outline btn-block h-auto py-4 flex-col gap-1"
                onClick={() => setTab('alerts')}
              >
                <BellRing className="w-5 h-5" />
                <span className="font-bold">Review alerts</span>
                <span className="text-xs opacity-60 font-normal">High/critical drift events</span>
              </button>
            </div>
          </div>
        )}

        {tab === 'results' && (
          <div className="grow min-h-0 overflow-hidden">
            <DriftView
              setView={setView}
              setSelectedDeviceId={setSelectedDeviceId}
              variant="embedded"
            />
          </div>
        )}

        {tab === 'policies' && (
          <div className="grow min-h-0 overflow-hidden">
            <PolicyBuilder
              setView={setView}
              onRequestScan={() => {
                void handleScanAll();
                setTab('overview');
              }}
              onOpenResults={() => setTab('results')}
              variant="embedded"
            />
          </div>
        )}

        {tab === 'alerts' && (
          <div className="grow overflow-y-auto p-6">
            <ComplianceAlertsPanel
              setView={setView}
              setSelectedDeviceId={setSelectedDeviceId}
              onSwitchHubTab={(t: ComplianceHubTab) => setTab(t)}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default ComplianceHubView;
