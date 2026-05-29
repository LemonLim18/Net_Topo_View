import React, { useState, useEffect, useCallback } from 'react';
import {
  Server,
  Save,
  RefreshCw,
  Loader2,
  HardDrive,
  AlertCircle,
  CheckCircle,
  Download,
  Eye,
  FileText,
  X,
  Edit2,
  Check,
  Sparkles,
} from 'lucide-react';
import {
  networkConfigBackupApi,
  type ConfigBackupDevice,
  type ConfigBackupSnapshot,
} from '../src/services/api';
import { Toast } from '../src/utils/swal';
import DeviceSelect from '../components/DeviceSelect';
import CaptureSnapshotModal from '../components/CaptureSnapshotModal';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const ConfigBackupView: React.FC = () => {
  const [devices, setDevices] = useState<ConfigBackupDevice[]>([]);
  const [selectedDevice, setSelectedDevice] = useState<ConfigBackupDevice | null>(null);
  const [snapshots, setSnapshots] = useState<ConfigBackupSnapshot[]>([]);
  const [loadingDevices, setLoadingDevices] = useState(true);
  const [loadingSnapshots, setLoadingSnapshots] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [showCaptureModal, setShowCaptureModal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewingSnapshot, setViewingSnapshot] = useState<ConfigBackupSnapshot | null>(null);


  const fetchDevices = useCallback(async (showLoading = true) => {
    if (showLoading) setLoadingDevices(true);
    setError(null);
    try {
      const list = await networkConfigBackupApi.listDevices();
      setDevices(list);
      setSelectedDevice((prev) => {
        if (!prev) return list.length > 0 ? list[0] : null;
        const updated = list.find((d) => d.id === prev.id);
        return updated ?? prev;
      });
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load devices');
      if (showLoading) Toast.error('Load failed', e?.message);
    } finally {
      setLoadingDevices(false);
    }
  }, []);

  const fetchSnapshots = useCallback(async () => {
    if (!selectedDevice) {
      setSnapshots([]);
      return;
    }
    setLoadingSnapshots(true);
    setError(null);
    try {
      const list = await networkConfigBackupApi.listSnapshots(selectedDevice.id);
      setSnapshots(list);
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load snapshots');
    } finally {
      setLoadingSnapshots(false);
    }
  }, [selectedDevice]);

  useEffect(() => {
    fetchDevices(true);
  }, [fetchDevices]);

  // Real-time device status: poll device list (with live topology) every 15s
  useEffect(() => {
    const interval = setInterval(() => {
      fetchDevices(false);
    }, 15_000);
    return () => clearInterval(interval);
  }, [fetchDevices]);

  useEffect(() => {
    fetchSnapshots();
  }, [fetchSnapshots]);

  const handleCapture = () => {
    if (!selectedDevice) return;
    setShowCaptureModal(true);
  };

  const handleDownload = async (snapshotId: number, capturedAt?: string) => {
    try {
      const name = selectedDevice && capturedAt
        ? `config_${selectedDevice.hostname}_${new Date(capturedAt).toISOString().replace(/[:.]/g, '-').slice(0, 19)}.txt`
        : undefined;
      await networkConfigBackupApi.downloadSnapshot(snapshotId, name);
      Toast.success('Download started');
    } catch (e: any) {
      Toast.error('Download failed', e?.message);
    }
  };

  const openViewModal = (snapshot: ConfigBackupSnapshot) => {
    setViewingSnapshot(snapshot);
  };

  const handleCloseViewModal = () => {
    setViewingSnapshot(null);
  };


  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  };

  const formatCapturedAt = (capturedAt?: string) => {
    if (!capturedAt) return null;
    try {
      let normalized = capturedAt;
      if (!capturedAt.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(capturedAt)) {
        normalized = capturedAt + 'Z';
      }
      const d = new Date(normalized);
      return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    } catch {
      return capturedAt;
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <header className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <HardDrive className="h-8 w-8 text-primary" />
          <h1 className="text-2xl font-bold">Config Backup & Restore</h1>
        </div>
        <p className="text-slate-400 text-sm mt-1">
          Store the current <strong>live</strong> running-configuration of any device for backup.
          Snapshots are fetched from the device over SSH and saved so you can restore later if needed.
        </p>
      </header>

      {error && (
        <div className="alert alert-warning gap-2 mb-6">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {/* Row 1: Select Device & Actions */}
        <section className="card bg-base-200/60 shadow-sm border border-base-300">
          <div className="card-body p-6">
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-6">
              {/* Select Device (Left - Long/Growing) */}
              <div className="grow w-full">
                {/* flex items-center gap-2 text-lg font-semibold text-base-content */}
                <label className="flex items-center gap-2 text-lg font-semibold text-base-content mb-2">
                  <Server className="h-5 w-5 text-primary" />
                  Select Device
                </label>
                {loadingDevices ? (
                  <div className="flex items-center gap-2 text-base-content/50 py-2.5 text-sm">
                    <Loader2 className="h-4 w-4 animate-spin text-primary" />
                    Loading devices…
                  </div>
                ) : (
                  <DeviceSelect
                    devices={devices}
                    selectedId={selectedDevice?.id ?? null}
                    onChange={(dev: ConfigBackupDevice | null) => setSelectedDevice(dev)}
                    loading={loadingDevices}
                  />
                )}
              </div>

              {/* Actions (Right - Inline smaller buttons) */}
              {selectedDevice && (
                <div className="flex flex-wrap items-center gap-3 w-full md:w-auto shrink-0 md:mb-px">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm gap-2 shadow-sm font-medium"
                    onClick={handleCapture}
                    disabled={loadingDevices}
                  >
                    <Save className="h-4 w-4" />
                    Capture Snapshot
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline btn-sm gap-2 font-medium"
                    onClick={() => { fetchSnapshots(); fetchDevices(); }}
                    disabled={loadingSnapshots || loadingDevices}
                  >
                    <RefreshCw className={`h-4 w-4 ${loadingSnapshots || loadingDevices ? 'animate-spin' : ''}`} />
                    Refresh Data
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* Row 2: Snapshot List */}
        <main className="w-full">
          <section className="card bg-base-100 rounded-xl shadow-md border border-base-300 overflow-hidden">
            <div className="card-body p-0">
              <div className="p-6 border-b border-base-300 bg-base-200/30 flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-lg font-semibold text-base-content">
                  <HardDrive className="h-5 w-5 text-primary" />
                  {selectedDevice 
                    ? `Snapshots for ${selectedDevice.hostname}` 
                    : 'System Snapshots'}
                </h2>
                {selectedDevice && (
                  <span className="badge badge-primary badge-outline">
                    {snapshots.length} Found
                  </span>
                )}
              </div>

              {!selectedDevice ? (
                <div className="flex flex-col items-center justify-center py-24 opacity-50 space-y-4">
                  <div className="p-4 rounded-full bg-base-200">
                    <Server className="h-10 w-10 text-primary/40" />
                  </div>
                  <p className="text-center italic">Please select a device above to view stored snapshots.</p>
                </div>
              ) : loadingSnapshots ? (
                <div className="flex flex-col items-center justify-center py-24 gap-3 text-base-content/70">
                  <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  <p className="text-sm font-medium">Retrieving configuration history...</p>
                </div>
              ) : snapshots.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-24 opacity-50 space-y-4">
                  <div className="p-4 rounded-full bg-base-200">
                    <FileText className="h-10 w-10 text-primary/40" />
                  </div>
                  <p className="text-center italic">No snapshots stored yet for this device.</p>
                </div>
              ) : (
                <div className="overflow-x-auto overflow-y-auto max-h-[400px] custom-scrollbar">
                  <table className="table table-zebra table-pin-rows w-full text-center">
                    <thead>
                      <tr className="bg-base-200 text-base-content/70">
                        <th className="font-bold pl-6">Snapshot</th>
                        <th className="font-bold">Description</th>
                        <th className="font-bold">Owner</th>
                        <th className="font-bold">Status</th>
                        <th className="font-bold">Captured At</th>
                        <th className="font-bold pr-6">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshots.map((s) => (
                        <tr key={s.id} className="hover:bg-primary/5 transition-colors">
                          <td className="pl-6 py-4">
                            <div className="flex items-center gap-3">
                              <div className="p-2 rounded bg-primary/10">
                                <FileText className="h-4 w-4 text-primary" />
                              </div>
                              <div className="flex flex-col">
                                <span className="font-bold text-base">#{s.id}</span>
                                <span className="text-[10px] uppercase tracking-wider opacity-60 font-semibold">
                                  {formatSize(s.config_size)}
                                </span>
                              </div>
                            </div>
                          </td>
                          <td className="py-4 text-sm opacity-80 max-w-[260px]">
                            <div className="truncate" title={s.description ?? ''}>
                              {s.description ? s.description : <span className="opacity-40 italic">No description</span>}
                            </div>
                          </td>
                          <td className="py-4 text-sm opacity-80">
                            {s.owner_username ? (
                              <span className="badge badge-outline badge-sm font-semibold tracking-wider text-xs">
                                {s.owner_username}
                              </span>
                            ) : (
                              <span className="badge badge-ghost badge-sm text-[11px] opacity-70">
                                Unassigned
                              </span>
                            )}
                          </td>
                          <td className="py-4">
                            <div className="badge badge-success badge-sm gap-1.5 py-2.5 px-3 font-medium text-black/70 border-none shadow-sm">
                              <CheckCircle className="h-3.5 w-3.5" />
                              Saved
                            </div>
                          </td>
                          <td className="text-sm font-medium opacity-80 whitespace-nowrap py-4">
                            {formatCapturedAt(s.captured_at)}
                          </td>
                          <td className="pr-6 py-4">
                            <div className="flex justify-end gap-2">
                              <button
                                type="button"
                                className="btn btn-primary btn-sm btn-outline shadow-sm gap-1.5"
                                onClick={() => openViewModal(s)}
                              >
                                <Eye className="h-3.5 w-3.5" />
                                View
                              </button>
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm text-primary gap-1.5"
                                onClick={() => handleDownload(s.id, s.captured_at)}
                              >
                                <Download className="h-3.5 w-3.5" />
                                Download
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </section>
        </main>
      </div>

      {/* View Modal */}
      {viewingSnapshot && selectedDevice && (
        <CaptureSnapshotModal
          device={selectedDevice}
          snapshot={viewingSnapshot}
          onClose={handleCloseViewModal}
          onComplete={fetchSnapshots}
        />
      )}

      {/* Capture Snapshot Modal */}
      {showCaptureModal && selectedDevice && (
        <CaptureSnapshotModal
          device={selectedDevice}
          onClose={() => setShowCaptureModal(false)}
          onComplete={() => fetchSnapshots()}
        />
      )}
    </div>
  );
};

export default ConfigBackupView;
