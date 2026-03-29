import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';
import { complianceApi } from '../../src/services/api';
import {
  COMPLIANCE_FILTER_DEVICE_KEY,
  type ComplianceHubTab,
} from '../../utils/complianceNavigationKeys';
import type { ViewId } from '../../types';
import { Toast } from '../../src/utils/swal';

export interface ComplianceAlertRow {
  id: number;
  severity?: string;
  description?: string;
  device_id?: number | null;
  created_at?: string;
  status?: string;
}

interface Props {
  setView: (v: ViewId) => void;
  setSelectedDeviceId: (id: number | null) => void;
  onSwitchHubTab: (t: ComplianceHubTab) => void;
}

const ComplianceAlertsPanel: React.FC<Props> = ({
  setView,
  setSelectedDeviceId,
  onSwitchHubTab,
}) => {
  const [items, setItems] = useState<ComplianceAlertRow[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await complianceApi.listAlerts(80);
      setItems((r.items || []) as ComplianceAlertRow[]);
    } catch (e: any) {
      Toast.error('Failed to load alerts', e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const ack = async (id: number) => {
    try {
      await complianceApi.ackAlert(id);
      Toast.success('Alert acknowledged');
      await load();
    } catch (e: any) {
      Toast.error('Ack failed', e.message);
    }
  };

  const openDevice = (deviceId: number | null | undefined) => {
    if (deviceId == null) return;
    setSelectedDeviceId(deviceId);
    setView('compliance_device_detail');
  };

  const openResultsForDevice = (deviceId: number | null | undefined) => {
    if (deviceId == null) return;
    try {
      sessionStorage.setItem(COMPLIANCE_FILTER_DEVICE_KEY, String(deviceId));
    } catch {
      /* ignore */
    }
    onSwitchHubTab('results');
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-primary" />
      </div>
    );
  }

  if (!items.length) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center opacity-60 gap-2">
        <CheckCircle className="w-12 h-12 text-success/40" />
        <p className="font-semibold">No compliance drift alerts</p>
        <p className="text-sm max-w-md">
          High/critical drift generates entries in the security event log. Run a global scan from the Overview tab.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-base-300 bg-base-100/60">
      <table className="table table-sm">
        <thead>
          <tr>
            <th>Severity</th>
            <th>Description</th>
            <th>Device</th>
            <th>When</th>
            <th className="text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id}>
              <td>
                <span className="badge badge-sm badge-warning gap-1">
                  <AlertTriangle className="w-3 h-3" />
                  {a.severity || '—'}
                </span>
              </td>
              <td className="max-w-md text-xs">{a.description || '—'}</td>
              <td className="font-mono text-xs">{a.device_id != null ? `#${a.device_id}` : '—'}</td>
              <td className="text-xs opacity-70 whitespace-nowrap">
                {a.created_at ? new Date(a.created_at).toLocaleString() : '—'}
              </td>
              <td className="text-right">
                <div className="flex flex-wrap justify-end gap-1">
                  {a.device_id != null && (
                    <>
                      <button
                        type="button"
                        className="btn btn-xs btn-ghost"
                        onClick={() => openDevice(a.device_id)}
                      >
                        Device 360
                      </button>
                      <button
                        type="button"
                        className="btn btn-xs btn-outline"
                        onClick={() => openResultsForDevice(a.device_id)}
                      >
                        Results
                      </button>
                    </>
                  )}
                  <button type="button" className="btn btn-xs btn-primary" onClick={() => ack(a.id)}>
                    Ack
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default ComplianceAlertsPanel;
