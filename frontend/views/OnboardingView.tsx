import React, { useState, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import { PackagePlus, Download, RefreshCw, ChevronLeft, ChevronDown, ChevronUp, Terminal, Maximize2, Minimize2, Copy, Check, Lightbulb, X } from 'lucide-react';
import type { ViewId } from '../types';
import {
  authApi,
  onboardingApi,
  type OnboardingDeviceSummary,
  type ZtpPublishJob,
  type ZtpPublishJobItem,
} from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';
import { formatIpInput, isValidIp } from '../src/utils/validation';

const ONBOARDING_STEP_KEY = 'onboarding.step';
const ONBOARDING_SELECTED_DEVICE_KEY = 'onboarding.selectedDeviceId';
const ONBOARDING_CONFIG_KEY = 'onboarding.config';
const ONBOARDING_FILENAME_KEY = 'onboarding.filename';
const ONBOARDING_FULL_WIDTH_KEY = 'onboarding.fullWidth';
const ONBOARDING_ZTP_TARGET_IP_KEY = 'onboarding.ztpTargetIp';
const ONBOARDING_ZTP_DRY_RUN_KEY = 'onboarding.ztpDryRun';
const ONBOARDING_ZTP_RESTART_KEY = 'onboarding.ztpRestartServices';
const ONBOARDING_ZTP_RESET_LEASES_KEY = 'onboarding.ztpResetLeases';
const ONBOARDING_ZTP_SETTINGS_OPEN_KEY = 'onboarding.ztpSettingsOpen';

const VENDOR_LABELS: Record<string, string> = {
  cisco_ios:     'Cisco IOS',
  cisco_xe:      'Cisco IOS-XE',
  huawei_vrp:    'Huawei VRP',
  juniper_junos: 'Juniper JunOS',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const OnboardingView: React.FC<{ setView: (id: ViewId) => void }> = ({ setView }) => {
  const [step, setStep]               = useState<1 | 2 | 3>(() => {
    const raw = Number(localStorage.getItem(ONBOARDING_STEP_KEY));
    return raw === 1 || raw === 2 || raw === 3 ? raw : 1;
  });

  // Step 1 state
  const [devices, setDevices]         = useState<OnboardingDeviceSummary[]>([]);
  const [loadingDevices, setLoadingDevices] = useState(false);
  const [selected, setSelected]       = useState<OnboardingDeviceSummary | null>(null);
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<number[]>([]);
  const [persistedSelectedDeviceId]   = useState<number | null>(() => {
    const raw = Number(localStorage.getItem(ONBOARDING_SELECTED_DEVICE_KEY));
    return Number.isFinite(raw) ? raw : null;
  });

  // Step 2 state
  const [config, setConfig]           = useState(() => localStorage.getItem(ONBOARDING_CONFIG_KEY) || '');
  const [filename, setFilename]       = useState(() => localStorage.getItem(ONBOARDING_FILENAME_KEY) || 'day0_config.txt');
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [showEditorModal, setShowEditorModal] = useState(false);
  const [editorBaseConfig, setEditorBaseConfig] = useState('');
  const [savingOverride, setSavingOverride] = useState(false);

  const [error, setError]             = useState('');
  const [isFullWidth, setIsFullWidth] = useState(() => localStorage.getItem(ONBOARDING_FULL_WIDTH_KEY) === 'true');
  const [copied, setCopied]           = useState(false);
  const [publishLoading, setPublishLoading] = useState(false);
  const [publishJobId, setPublishJobId] = useState<number | null>(null);
  const [publishJob, setPublishJob] = useState<ZtpPublishJob | null>(null);
  const [manifestLoading, setManifestLoading] = useState(false);

  const [targetServerIp, setTargetServerIp] = useState(() => localStorage.getItem(ONBOARDING_ZTP_TARGET_IP_KEY) || '192.168.1.240');
  const credentialLabel = 'ZTP_SERVER_SSH';
  const [dryRun, setDryRun] = useState(() => localStorage.getItem(ONBOARDING_ZTP_DRY_RUN_KEY) === 'true');
  const [restartServices, setRestartServices] = useState(() => localStorage.getItem(ONBOARDING_ZTP_RESTART_KEY) !== 'false');
  const [resetLeases, setResetLeases] = useState(() => localStorage.getItem(ONBOARDING_ZTP_RESET_LEASES_KEY) !== 'false');
  const [ztpSettingsOpen, setZtpSettingsOpen] = useState(() => localStorage.getItem(ONBOARDING_ZTP_SETTINGS_OPEN_KEY) === 'true');

  const [canManageServerCredential, setCanManageServerCredential] = useState(false);
  const [serverCredentialConfigured, setServerCredentialConfigured] = useState(false);
  const [serverCredentialUsername, setServerCredentialUsername] = useState('');
  const [serverCredentialPassword, setServerCredentialPassword] = useState('');
  const [serverCredentialSaving, setServerCredentialSaving] = useState(false);
  const [serverConnectionTesting, setServerConnectionTesting] = useState(false);
  const [serverConnectionResult, setServerConnectionResult] = useState<{ ok: boolean; message: string; details?: string } | null>(null);

  // Fetch device list on mount
  useEffect(() => { fetchDevices(); }, []);

  // Force this page to start at device list; review is now modal-based.
  useEffect(() => {
    setStep(1);
  }, []);

  const fetchDevices = async () => {
    setLoadingDevices(true);
    setError('');
    try {
      const data = await onboardingApi.listDevices();
      setDevices(data);
      setSelectedDeviceIds((prev) => prev.filter((id) => data.some((d) => d.id === id)));

      if (!selected && persistedSelectedDeviceId) {
        const restored = data.find(d => d.id === persistedSelectedDeviceId) || null;
        setSelected(restored);
      }
    } catch (e: any) {
      setError(e.message ?? 'Failed to load devices');
    } finally {
      setLoadingDevices(false);
    }
  };

  useEffect(() => {
    localStorage.setItem(ONBOARDING_STEP_KEY, String(step));
  }, [step]);

  useEffect(() => {
    if (selected?.id) {
      localStorage.setItem(ONBOARDING_SELECTED_DEVICE_KEY, String(selected.id));
    } else {
      localStorage.removeItem(ONBOARDING_SELECTED_DEVICE_KEY);
    }
  }, [selected]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_CONFIG_KEY, config);
  }, [config]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_FILENAME_KEY, filename);
  }, [filename]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_FULL_WIDTH_KEY, String(isFullWidth));
  }, [isFullWidth]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_ZTP_TARGET_IP_KEY, targetServerIp);
  }, [targetServerIp]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_ZTP_DRY_RUN_KEY, String(dryRun));
  }, [dryRun]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_ZTP_RESTART_KEY, String(restartServices));
  }, [restartServices]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_ZTP_RESET_LEASES_KEY, String(resetLeases));
  }, [resetLeases]);

  useEffect(() => {
    localStorage.setItem(ONBOARDING_ZTP_SETTINGS_OPEN_KEY, String(ztpSettingsOpen));
  }, [ztpSettingsOpen]);

  useEffect(() => {
    authApi.me()
      .then((me) => {
        setCanManageServerCredential(String(me?.role || '').toLowerCase() === 'admin');
      })
      .catch(() => {
        setCanManageServerCredential(false);
      });
  }, []);

  useEffect(() => {
    onboardingApi.getServerCredentialStatus(credentialLabel)
      .then((result) => {
        setServerCredentialConfigured(Boolean(result.configured));
        setServerCredentialUsername(String(result.username || ''));
      })
      .catch(() => {
        setServerCredentialConfigured(false);
        setServerCredentialUsername('');
      });
  }, [credentialLabel]);

  const handleGenerate = async (device: OnboardingDeviceSummary) => {
    setSelected(device);
    setLoadingConfig(true);
    setError('');
    try {
      const data = await onboardingApi.generateForDevice(device.id);
      setConfig(data.config);
      setFilename(data.filename);
      setEditorBaseConfig(data.config);
      setShowEditorModal(true);
    } catch (e: any) {
      setError(e.message ?? 'Failed to generate config');
    } finally {
      setLoadingConfig(false);
    }
  };

  const isEditorDirty = Boolean(editorBaseConfig) && config !== editorBaseConfig;

  const handleSaveOverride = async () => {
    if (!selected) return;
    if (!isEditorDirty) return;
    setSavingOverride(true);
    setError('');
    try {
      await onboardingApi.saveDay0Override(selected.id, config, filename);
      setEditorBaseConfig(config);
      Toast.success('Saved', `Day-0 config saved for ${selected.hostname}`);
    } catch (e: any) {
      setError(e.message ?? 'Failed to save config override');
    } finally {
      setSavingOverride(false);
    }
  };

  const handleRevertToDefault = async () => {
    if (!selected) return;
    const ok = await Confirm.ask(
      `Revert ${selected.hostname} to default Day-0 config?`,
      'This will discard the saved override (if any) and reload the generated default configuration.'
    );
    if (!ok) return;

    setSavingOverride(true);
    setError('');
    try {
      await onboardingApi.clearDay0Override(selected.id);
      const fresh = await onboardingApi.generateForDevice(selected.id);
      setConfig(fresh.config);
      setFilename(fresh.filename);
      setEditorBaseConfig(fresh.config);
      Toast.success('Reverted', `Default Day-0 config reloaded for ${selected.hostname}`);
    } catch (e: any) {
      setError(e.message ?? 'Failed to revert config');
    } finally {
      setSavingOverride(false);
    }
  };

  const handleDownload = () => {
    const blob = new Blob([config], { type: 'text/plain' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };
 
  const handleCopy = async () => {
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(config);
      } else {
        // Fallback for browsers/contexts without Clipboard API
        const ta = document.createElement('textarea');
        ta.value = config;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, ta.value.length);
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        if (!ok) throw new Error('copy command failed');
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e: any) {
      setError(e?.message || 'Copy to clipboard failed');
    }
  };

  const isPublishTerminal = (status: string | undefined) => {
    const state = (status || '').toLowerCase();
    return state === 'success' || state === 'failed' || state === 'partial';
  };

  const refreshPublishJob = async (jobId: number) => {
    try {
      const job = await onboardingApi.getPublishJob(jobId);
      setPublishJob(job);
      if (isPublishTerminal(job.status)) {
        setPublishLoading(false);
      }
    } catch (e: any) {
      setPublishLoading(false);
      setError(e.message ?? 'Failed to load publish job status');
    }
  };

  useEffect(() => {
    if (!publishJobId) return;
    const status = (publishJob?.status || '').toLowerCase();
    if (status === 'success' || status === 'failed' || status === 'partial') return;
    const intervalId = window.setInterval(() => {
      onboardingApi.getPublishJob(publishJobId)
        .then((job) => {
          setPublishJob(job);
          if (isPublishTerminal(job.status)) {
            setPublishLoading(false);
          }
        })
        .catch((e: any) => {
          setPublishLoading(false);
          setError(e.message ?? 'Failed to load publish job status');
        });
    }, 2000);
    return () => window.clearInterval(intervalId);
  }, [publishJobId, publishJob?.status]);

  const startPublish = async (deviceIds: number[], configOverrides?: Record<string, string>) => {
    if (!deviceIds.length) return;
    if (targetServerIp && !isValidIp(targetServerIp)) {
      setError('ZTP Target Server IP is invalid (must be 4 octets between 0 and 255)');
      return;
    }
    setPublishLoading(true);
    setError('');
    try {
      const queued = await onboardingApi.publishToZtp({
        device_ids: deviceIds,
        target_server_ip: targetServerIp,
        credential_label: credentialLabel,
        dry_run: dryRun,
        restart_services: restartServices,
        reset_leases: resetLeases,
        config_overrides: configOverrides,
        requested_by: 'frontend-user',
      });
      setPublishJobId(queued.job_id);
      await refreshPublishJob(queued.job_id);
    } catch (e: any) {
      setPublishLoading(false);
      setError(e.message ?? 'Failed to publish to ZTP server');
    }
  };

  const handlePublishCurrent = async () => {
    if (!selected) return;
    setShowEditorModal(false);
    await startPublish([selected.id], { [String(selected.id)]: config });
  };

  const toggleSelectedDevice = (deviceId: number, enabled: boolean) => {
    setSelectedDeviceIds((prev) => {
      if (enabled) {
        if (prev.includes(deviceId)) return prev;
        return [...prev, deviceId];
      }
      return prev.filter((id) => id !== deviceId);
    });
  };

  const toggleSelectAll = (enabled: boolean) => {
    setSelectedDeviceIds(enabled ? devices.map((d) => d.id) : []);
  };

  const handlePublishSelected = async () => {
    if (!selectedDeviceIds.length) return;
    await startPublish(selectedDeviceIds);
  };

  const handleSaveServerCredential = async () => {
    const username = serverCredentialUsername.trim();
    if (!username || !serverCredentialPassword) {
      setError('Server credential username and password are required');
      return;
    }
    setServerCredentialSaving(true);
    setError('');
    try {
      await onboardingApi.setServerCredential(username, serverCredentialPassword, credentialLabel);
      setServerCredentialConfigured(true);
      setServerCredentialPassword('');
    } catch (e: any) {
      setError(e.message ?? 'Failed to store server credential');
    } finally {
      setServerCredentialSaving(false);
    }
  };

  const handleTestServerConnection = async () => {
    if (targetServerIp && !isValidIp(targetServerIp)) {
      setServerConnectionResult({
        ok: false,
        message: 'Validation failed',
        details: 'ZTP Target Server IP is invalid',
      });
      return;
    }
    setServerConnectionTesting(true);
    setServerConnectionResult(null);
    setError('');
    try {
      const result = await onboardingApi.testServerConnection(targetServerIp, 22, credentialLabel);
      const details = result.ok
        ? `Connected in ${result.duration_ms} ms using ${result.credential_label})`
        : (result.stderr || result.message || 'Connection test failed');
      setServerConnectionResult({
        ok: Boolean(result.ok),
        message: result.ok ? 'ZTP server connection successful' : 'ZTP server connection failed',
        details,
      });
    } catch (e: any) {
      setServerConnectionResult({
        ok: false,
        message: 'ZTP server connection failed',
        details: e.message ?? 'Unable to reach target server',
      });
    } finally {
      setServerConnectionTesting(false);
    }
  };

  const handleDownloadManifest = async () => {
    if (!publishJob?.id) return;
    setManifestLoading(true);
    setError('');
    try {
      const manifest = await onboardingApi.getPublishManifest(publishJob.id);
      const content = JSON.stringify(manifest, null, 2);
      const blob = new Blob([content], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `ztp_manifest_job_${publishJob.id}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(e.message ?? 'Failed to download publish manifest');
    } finally {
      setManifestLoading(false);
    }
  };

  const vendorLabel  = (v: string | null) => VENDOR_LABELS[v ?? ''] ?? v ?? 'Unknown';

  return (
    <div className="w-full h-full overflow-y-auto">
      <div className={`p-6 min-h-full flex flex-col gap-6 transition-all duration-300 ${isFullWidth ? 'w-full' : 'max-w-5xl mx-auto'}`}>

      {/* Header */}
      <div className="flex items-center gap-3">
        <PackagePlus className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-xl font-bold">Onboard New Device</h1>
          <p className="text-sm opacity-60">
            Select a planned device, review the generated Day-0 config in the editor, then publish the configuration to the ZTP server.
          </p>
        </div>
      </div>

      {/* Progress steps intentionally hidden for modal-based review flow */}

      {/* Global error */}
      {error && (
        <div className="alert alert-error text-sm">
          <span>{error}</span>
        </div>
      )}

      <div className="card bg-base-100 shadow border border-base-300">
        <div className="card-body gap-3">
          <div className="flex items-center justify-between">
            <button
              className="btn btn-ghost btn-sm px-0 min-h-0 h-auto hover:bg-transparent"
              onClick={() => setZtpSettingsOpen((prev) => !prev)}
            >
              <h2 className="card-title text-base mr-2">ZTP Server Settings</h2>
              {ztpSettingsOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
            <div className="flex items-center gap-2">
              <span className={`badge badge-sm ${serverCredentialConfigured ? 'badge-success' : 'badge-warning'}`}>
                {serverCredentialConfigured ? 'Credential Configured' : 'Credential Missing'}
              </span>
              <button
                className="btn btn-outline btn-xs"
                onClick={handleTestServerConnection}
                disabled={serverConnectionTesting}
              >
                {serverConnectionTesting ? <span className="loading loading-spinner loading-xs" /> : 'Test Connection'}
              </button>
            </div>
          </div>

          {serverConnectionResult && (
            <div className={`alert flex justify-between text-xs py-1 ${serverConnectionResult.ok ? 'alert-success' : 'alert-warning'}`}>
              <span>{serverConnectionResult.message}{serverConnectionResult.details ? ` - ${serverConnectionResult.details}` : ''}</span>
              <button
                className="btn btn-ghost btn-xs"
                onClick={() => setServerConnectionResult(null)}
                aria-label="Dismiss server connection result"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {ztpSettingsOpen && (
            <>
              <div className={`grid grid-cols-1 gap-2 ${canManageServerCredential ? 'md:grid-cols-3' : 'md:grid-cols-1'}`}>
                <label className="form-control w-full flex flex-col gap-1">
                  <span className="label-text text-xs">Target Server IP</span>
                  <input
                    className="input input-bordered input-sm"
                    value={targetServerIp}
                    onChange={(event) => setTargetServerIp(formatIpInput(event.target.value))}
                    placeholder="192.168.1.240"
                  />
                </label>

                {canManageServerCredential && (
                  <>
                    <label className="form-control w-full flex flex-col gap-1">
                      <span className="label-text text-xs">SSH Username</span>
                      <input
                        className="input input-bordered input-sm"
                        placeholder="Enter SSH username"
                        value={serverCredentialUsername}
                        onChange={(event) => setServerCredentialUsername(event.target.value)}
                        autoComplete="off"
                        name="ztp_ssh_username"
                        data-lpignore="true"
                      />
                    </label>
                    <label className="form-control w-full flex flex-col gap-1">
                      <span className="label-text text-xs">SSH Password</span>
                      <input
                        className="input input-bordered input-sm"
                        placeholder="••••••••"
                        type="password"
                        value={serverCredentialPassword}
                        onChange={(event) => setServerCredentialPassword(event.target.value)}
                        autoComplete="new-password"
                        name="ztp_ssh_password"
                        data-lpignore="true"
                      />
                    </label>
                  </>
                )}
              </div>


              <div className="rounded border border-base-300 bg-base-200/40 px-3 py-2">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
                  <label className="label cursor-pointer justify-start gap-2">
                    <input type="checkbox" className="checkbox checkbox-sm" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                    <span className="label-text">Dry run (no upload/restart)</span>
                  </label>
                  <label className="label cursor-pointer justify-start gap-2">
                    <input type="checkbox" className="checkbox checkbox-sm" checked={restartServices} onChange={(e) => setRestartServices(e.target.checked)} disabled={dryRun} />
                    <span className="label-text">Restart DHCP and TFTP after validation</span>
                  </label>
                  <label className="label cursor-pointer justify-start gap-2">
                    <input type="checkbox" className="checkbox checkbox-sm" checked={resetLeases} onChange={(e) => setResetLeases(e.target.checked)} disabled={dryRun} />
                    <span className="label-text">Reset DHCP lease file</span>
                  </label>
                </div>
              </div>
                  {canManageServerCredential && (
                    <div className="flex justify-end">
                      <button
                        className="btn btn-outline btn-sm"
                        onClick={handleSaveServerCredential}
                        disabled={serverCredentialSaving}
                      >
                        {serverCredentialSaving ? <span className="loading loading-spinner loading-xs" /> : 'Save Server Credential'}
                      </button>
                    </div>
                  )}
            </>
          )}
        </div>
      </div>

      {publishJob && (
        <div className="card bg-base-100 shadow border border-base-300">
          <div className="card-body gap-3 h-full max-h-full overflow-y-auto">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm">
                <span className="font-semibold">ZTP Publish Job #{publishJob.id}</span>
                <span className="opacity-70 ml-2">target {publishJob.target_server_ip}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={`badge badge-sm ${
                  publishJob.status === 'success' ? 'badge-success' :
                  publishJob.status === 'partial' ? 'badge-warning' :
                  publishJob.status === 'failed' ? 'badge-error' :
                  'badge-info'
                }`}>
                  {publishJob.status}
                </span>
                {publishLoading && !isPublishTerminal(publishJob.status) && (
                  <span className="loading loading-spinner loading-xs" />
                )}
              </div>
            </div>

            <div className="text-xs opacity-80">
              {publishJob.success_items ?? 0} succeeded / {publishJob.failed_items ?? 0} failed / {publishJob.total_items ?? 0} total
            </div>

            <div className="flex justify-end">
              <button
                className="btn btn-ghost btn-xs gap-1"
                onClick={handleDownloadManifest}
                disabled={manifestLoading}
              >
                {manifestLoading ? <span className="loading loading-spinner loading-xs" /> : <Download className="h-3.5 w-3.5" />}
                Download Manifest
              </button>
            </div>

            {publishJob.error_text && (
              <div className="alert alert-warning text-xs py-2">
                <span>{publishJob.error_text}</span>
              </div>
            )}

            {Array.isArray(publishJob.items) && publishJob.items.length > 0 && (
              <div className="overflow-x-auto max-h-52 rounded border border-base-300">
                <table className="table table-xs w-full">
                  <thead className="bg-base-200">
                    <tr>
                      <th>Device</th>
                      <th>Status</th>
                      <th>Stage</th>
                      <th>Filename</th>
                      <th>Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {publishJob.items.map((item: ZtpPublishJobItem) => (
                      <tr key={item.id}>
                        <td className="font-mono">{item.hostname}</td>
                        <td>
                          <span className={`badge badge-xs ${
                            item.status === 'success' ? 'badge-success' :
                            item.status === 'failed' ? 'badge-error' :
                            item.status === 'running' ? 'badge-info' :
                            'badge-ghost'
                          }`}>
                            {item.status}
                          </span>
                        </td>
                        <td className="text-xs">{item.stage || '-'}</td>
                        <td className="font-mono text-xs">{item.filename || '-'}</td>
                        <td className="text-xs text-error max-w-[18rem] truncate">{item.error_text || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {showEditorModal && (
        <div className="fixed inset-0 z-50 bg-black/20 backdrop-blur-sm p-3 md:p-6 overflow-y-auto">
          <div className="mx-auto max-w-6xl">
            <div className="card bg-base-100 shadow-xl border border-base-300">
              <div className="card-body gap-4">
                <div className="flex items-center gap-2">
                  <Terminal className="h-4 w-4 text-primary" />
                  <h2 className="card-title text-base">
                    Review Config for{' '}
                    <span className="badge badge-primary badge-sm ml-1">{selected?.hostname}</span>
                  </h2>
                  <span className="badge badge-ghost badge-sm ml-auto">{vendorLabel(selected?.vendor ?? null)}</span>
                  <button
                    className="btn btn-ghost btn-xs gap-1 ml-1"
                    onClick={() => setIsFullWidth(!isFullWidth)}
                    title={isFullWidth ? 'Standard View' : 'Expand View'}
                  >
                    {isFullWidth ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
                    {isFullWidth ? 'Narrow' : 'Full Screen'}
                  </button>
                  <button
                    className="btn btn-ghost btn-xs"
                    onClick={() => setShowEditorModal(false)}
                    aria-label="Close preview editor"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                <div className="alert alert-info text-sm py-2">
                  <div className="flex items-center gap-2">
                    <Lightbulb className="h-4 w-4 mr-1" />
                    <span>
                      Edit the script if needed (e.g. change the subnet mask, interface name, or gateway), then click
                      <span className="inline-flex items-center gap-1 font-bold mx-1">
                        <Download className="h-3.5 w-3.5" /> Download Script
                      </span>
                      and paste it into the device console.
                    </span>
                  </div>
                </div>

                <div className="rounded-lg overflow-hidden border border-base-300 transition-all duration-300" style={{ height: isFullWidth ? '700px' : '480px' }}>
                  <Editor
                     height={isFullWidth ? '700px' : '480px'}
                    defaultLanguage="shell"
                    theme="vs-dark"
                    value={config}
                    onChange={(val) => setConfig(val ?? '')}
                    options={{
                      fontSize: 13,
                      minimap: { enabled: false },
                      lineNumbers: 'on',
                      wordWrap: 'off',
                      scrollBeyondLastLine: false,
                      automaticLayout: true,
                      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
                    }}
                  />
                </div>

                <div className="card-actions justify-between">
                  <button className="btn btn-ghost btn-sm gap-1" onClick={() => setShowEditorModal(false)}>
                    <ChevronLeft className="h-4 w-4" />
                    Back
                  </button>
                  <div className="flex gap-2">
                    <button
                      className="btn btn-outline btn-sm"
                      onClick={handleRevertToDefault}
                      disabled={!selected || savingOverride}
                      title="Restore generated default config"
                    >
                      {savingOverride ? <span className="loading loading-spinner loading-xs" /> : 'Revert'}
                    </button>
                    <button
                      className="btn btn-outline btn-sm"
                      onClick={handleSaveOverride}
                      disabled={!selected || !isEditorDirty || savingOverride}
                      title={isEditorDirty ? 'Save edited config for this device' : 'No changes to save'}
                    >
                      {savingOverride ? <span className="loading loading-spinner loading-xs" /> : 'Save'}
                    </button>
                    <button className="btn btn-ghost btn-sm gap-2" onClick={handleCopy}>
                      {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
                      {copied ? 'Copied' : 'Copy All'}
                    </button>
                    <button
                      className="btn btn-outline btn-sm gap-2"
                      onClick={handlePublishCurrent}
                      disabled={!selected || publishLoading}
                    >
                      {publishLoading
                        ? <span className="loading loading-spinner loading-xs" />
                        : <Terminal className="h-4 w-4" />}
                      Publish to ZTP
                    </button>
                    <button className="btn btn-primary btn-sm gap-2" onClick={handleDownload}>
                      <Download className="h-4 w-4" />
                      Download Script
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="card bg-base-100 shadow">
          <div className="card-body gap-4">
            <div className="flex items-center justify-between">
              <h2 className="card-title text-base">Select a Planned Device</h2>
              <button
                className="btn btn-ghost btn-xs gap-1"
                onClick={fetchDevices}
                disabled={loadingDevices}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loadingDevices ? 'animate-spin' : ''}`} />
                Refresh
              </button>
            </div>

            {devices.length === 0 && !loadingDevices && (
              <div className="flex flex-col items-center justify-center gap-4 py-14 text-center">
                <div className="rounded-full bg-base-200 p-4 shadow-sm flex items-center justify-center" aria-hidden>
                  <PackagePlus className="h-8 w-8 text-base-content/50" strokeWidth={1.5} />
                </div>
                <div>
                  <p className="text-base font-semibold text-base-content">No planned devices ready for onboarding</p>
                  <p className="mt-1 text-sm text-base-content/50">
                    Go to Device Inventory and add a device first. All manually added devices are automatically available for onboarding.
                  </p>
                </div>
                <button
                  className="btn btn-primary btn-sm gap-2"
                  onClick={() => setView('netbox_devices')}
                >
                  <Terminal className="h-4 w-4" />
                  Go to Device Inventory
                </button>
              </div>
            )}

            {devices.length > 0 && (
              <div className="overflow-x-auto max-h-[370px] rounded-lg border border-base-300">
                <table className="table table-sm w-full">
                  <thead className="bg-base-200">
                    <tr>
                      <th className="w-10">
                        <input
                          type="checkbox"
                          className="checkbox checkbox-primary checkbox-xs"
                          checked={devices.length > 0 && selectedDeviceIds.length === devices.length}
                          onChange={(event) => toggleSelectAll(event.target.checked)}
                          aria-label="Select all devices"
                        />
                      </th>
                      <th>Hostname</th>
                      <th>Management IP</th>
                      <th>Vendor</th>
                      <th>SSH User</th>
                      <th>Status</th>
                      <th className="text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {devices.map((d) => (
                      <tr
                        key={d.id}
                        className={`hover:bg-base-200 transition-colors ${
                          selected?.id === d.id ? 'bg-primary/10 font-semibold' : ''
                        }`}
                      >
                        <td>
                          <input
                            type="checkbox"
                            className="checkbox checkbox-primary checkbox-xs"
                            checked={selectedDeviceIds.includes(d.id)}
                            onChange={(event) => toggleSelectedDevice(d.id, event.target.checked)}
                            aria-label={`Select ${d.hostname}`}
                          />
                        </td>
                        <td className="font-mono">{d.hostname}</td>
                        <td className="font-mono text-xs">{d.management_ip || '—'}</td>
                        <td className="text-xs">{vendorLabel(d.vendor)}</td>
                        <td className="font-mono text-xs">{d.ssh_user || <span className="opacity-40 italic">not set</span>}</td>
                        <td>
                          <span className={`badge badge-xs ${
                            d.status === 'planned' ? 'badge-info' :
                            d.status === 'active' ? 'badge-success' :
                            d.status === 'down' ? 'badge-error' : 'badge-ghost'
                          }`}>
                            {d.status}
                          </span>
                        </td>
                        <td className="text-right">
                          <button
                            className="btn btn-primary btn-xs"
                            onClick={() => handleGenerate(d)}
                            disabled={loadingConfig}
                          >
                            {loadingConfig && selected?.id === d.id
                              ? <span className="loading loading-spinner loading-xs" />
                              : 'Preview/Edit'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {selected && (!selected.ssh_user || !selected.management_ip) && (
              <div className="alert alert-warning text-sm">
                <span>
                  <strong>{selected.hostname}</strong> is missing{' '}
                  {!selected.management_ip ? 'a Management IP' : ''}
                  {!selected.management_ip && !selected.ssh_user ? ' and ' : ''}
                  {!selected.ssh_user ? 'SSH credentials' : ''}.
                  The config will use placeholder values — edit them in the next step or
                  <button className="btn btn-link btn-xs px-1" onClick={() => setView('netbox_devices')}>
                    update the device
                  </button>
                  first.
                </span>
              </div>
            )}

            <div className="text-xs opacity-70">
              Click <strong>Preview/Edit</strong> on any row to open that device configuration editor.
            </div>

            <div className="card-actions justify-end">
              <button
                className="btn btn-outline btn-sm gap-2"
                onClick={handlePublishSelected}
                disabled={!selectedDeviceIds.length || publishLoading}
              >
                {publishLoading
                  ? <span className="loading loading-spinner loading-xs" />
                  : <Terminal className="h-4 w-4" />}
                Publish Selected ({selectedDeviceIds.length})
              </button>
            </div>
          </div>
        </div>
      )}

      </div>
    </div>
  );
};

export default OnboardingView;
