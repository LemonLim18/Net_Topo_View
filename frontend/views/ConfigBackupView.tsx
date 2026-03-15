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
} from 'lucide-react';
import {
  networkConfigBackupApi,
  type ConfigBackupDevice,
  type ConfigBackupSnapshot,
} from '../src/services/api';
import { Toast } from '../src/utils/swal';
import DeviceSelect from '../components/DeviceSelect';

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
  const [error, setError] = useState<string | null>(null);
  const [viewingSnapshot, setViewingSnapshot] = useState<ConfigBackupSnapshot | null>(null);
  const [fullConfig, setFullConfig] = useState<string | null>(null);
  const [loadingConfig, setLoadingConfig] = useState(false);

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

  const handleCapture = async () => {
    if (!selectedDevice) return;
    setCapturing(true);
    setError(null);
    try {
      const res = await networkConfigBackupApi.captureSnapshot(selectedDevice.id);
      Toast.success('Snapshot stored', res.message);
      await fetchSnapshots();
    } catch (e: any) {
      const msg = e?.message ?? 'Capture failed';
      setError(msg);
      Toast.error('Capture failed', msg);
    } finally {
      setCapturing(false);
    }
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

  const openViewModal = async (snapshot: ConfigBackupSnapshot) => {
    setViewingSnapshot(snapshot);
    setFullConfig(null);
    setLoadingConfig(true);
    try {
      const data = await networkConfigBackupApi.getSnapshotContent(snapshot.id);
      setFullConfig(data.config_text);
    } catch (e: any) {
      Toast.error('View failed', e?.message);
    } finally {
      setLoadingConfig(false);
    }
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
      const d = new Date(capturedAt);
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
        <p className="text-base-content/70 text-sm leading-relaxed max-w-2xl">
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

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Sidebar: Controls */}
        <aside className="lg:col-span-4 space-y-6">
          {/* Select device */}
          <section className="card bg-base-200/60 shadow-sm border border-base-300">
            <div className="card-body p-6">
              <h2 className="flex items-center gap-2 text-lg font-semibold mb-1">
                <Server className="h-5 w-5 text-primary/80" />
                Select Device
              </h2>
              <p className="text-xs text-base-content/50 mb-4">
                Choose a device to manage backups.
              </p>
              {loadingDevices ? (
                <div className="flex items-center gap-2 text-base-content/70 py-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading devices…
                </div>
              ) : (
                <div className="w-full">
                  <DeviceSelect
                    devices={devices}
                    selectedId={selectedDevice?.id ?? null}
                    onChange={(dev: ConfigBackupDevice | null) => setSelectedDevice(dev)}
                    loading={loadingDevices}
                  />
                </div>
              )}
            </div>
          </section>

          {selectedDevice && (
            <section className="card bg-base-200/50 shadow-sm border border-base-300">
              <div className="card-body p-6">
                <h2 className="flex items-center gap-2 text-lg font-semibold mb-1">
                  <Save className="h-5 w-5 text-primary/80" />
                  Actions
                </h2>
                <div className="space-y-4 mt-2">
                  <button
                    type="button"
                    className="btn btn-primary btn-block gap-2"
                    onClick={handleCapture}
                    disabled={capturing || loadingDevices}
                  >
                    {capturing ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Save className="h-4 w-4" />
                    )}
                    {capturing ? 'Capturing…' : 'Capture Snapshot'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline btn-block gap-2"
                    onClick={() => { fetchSnapshots(); fetchDevices(); }}
                    disabled={loadingSnapshots || loadingDevices}
                  >
                    <RefreshCw className={`h-4 w-4 ${loadingSnapshots || loadingDevices ? 'animate-spin' : ''}`} />
                    Refresh Data
                  </button>
                </div>
                <div className="divider opacity-10"></div>
                <p className="text-xs text-base-content/60 leading-relaxed italic">
                  Captures <code className="bg-base-300 px-1 rounded">running-config</code> from <strong>{selectedDevice.hostname}</strong>.
                </p>
              </div>
            </section>
          )}
        </aside>

        {/* Main: Snapshot List */}
        <main className="lg:col-span-8">
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
                  <p className="text-center italic">Please select a device from the sidebar to view stored snapshots.</p>
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
                <div className="overflow-x-auto">
                  <table className="table table-zebra w-full">
                    <thead>
                      <tr className="bg-base-200/50 text-base-content/70">
                        <th className="font-bold pl-6">Snapshot</th>
                        <th className="font-bold">Status</th>
                        <th className="font-bold">Captured At</th>
                        <th className="text-right font-bold pr-6">Actions</th>
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
      {viewingSnapshot && (
        <div className="modal modal-open">
          <div className="modal-box max-w-4xl max-h-[85vh] flex flex-col p-0 overflow-hidden">
            <div className="flex items-center justify-between p-4 border-b border-base-content/10 bg-base-200">
              <div className="flex items-center gap-3">
                <FileText className="h-5 w-5 text-primary" />
                <h3 className="font-bold text-lg">
                  Snapshot #{viewingSnapshot.id} — {selectedDevice?.hostname}
                </h3>
                <span className="badge badge-sm badge-outline opacity-60">
                  {formatSize(viewingSnapshot.config_size)}
                </span>
                <span className="text-xs opacity-50">
                  {formatCapturedAt(viewingSnapshot.captured_at)}
                </span>
              </div>
              <button 
                className="btn btn-sm btn-circle btn-ghost" 
                onClick={() => { setViewingSnapshot(null); setFullConfig(null); }}
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            
            <div className="grow overflow-y-auto p-4 bg-neutral text-neutral-content font-mono text-sm custom-scrollbar">
              {loadingConfig ? (
                <div className="flex flex-col items-center justify-center py-20 gap-3 opacity-50">
                  <Loader2 className="h-8 w-8 animate-spin" />
                  <p>Loading configuration...</p>
                </div>
              ) : fullConfig ? (
                <pre className="whitespace-pre-wrap">{fullConfig}</pre>
              ) : (
                <div className="text-center py-20 opacity-40 italic">
                  No configuration content available.
                </div>
              )}
            </div>

            <div className="p-4 border-t border-base-content/10 bg-base-200 flex justify-end gap-2">
              <button 
                className="btn btn-primary"
                onClick={() => handleDownload(viewingSnapshot.id, viewingSnapshot.captured_at)}
              >
                <Download className="h-4 w-4 mr-2" />
                Download
              </button>
              <button 
                className="btn" 
                onClick={() => { setViewingSnapshot(null); setFullConfig(null); }}
              >
                Close
              </button>
            </div>
          </div>
          <div className="modal-backdrop bg-black/60" onClick={() => { setViewingSnapshot(null); setFullConfig(null); }}></div>
        </div>
      )}
    </div>
  );
};

export default ConfigBackupView;
