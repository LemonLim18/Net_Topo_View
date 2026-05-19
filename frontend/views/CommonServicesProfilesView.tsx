import React, { useEffect, useState } from 'react';
import { Layers, Save, Trash2, RefreshCw, Download, X, AlertTriangle, Clock, Server, Upload, Rocket } from 'lucide-react';
import { authApi, commonServicesApi, onboardingApi, syslogApi, type ZtpPublishJob, type ZtpPublishJobItem } from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';

const GLOBAL_SCOPE_TYPE = 'global';
const GLOBAL_SCOPE_VALUE = 'global';
const ONBOARDING_ZTP_TARGET_IP_KEY = 'onboarding.ztpTargetIp';
const ONBOARDING_ZTP_DRY_RUN_KEY = 'onboarding.ztpDryRun';
const ONBOARDING_ZTP_RESTART_KEY = 'onboarding.ztpRestartServices';
const ONBOARDING_ZTP_RESET_LEASES_KEY = 'onboarding.ztpResetLeases';

const CommonServicesProfilesView: React.FC = () => {
  const [snmpCommunity, setSnmpCommunity] = useState('');
  const [snmpLocation, setSnmpLocation] = useState('');
  const [snmpContact, setSnmpContact] = useState('');
  const [snmpHostIp, setSnmpHostIp] = useState('');
  const [syslogPort, setSyslogPort] = useState('6514');
  const [syslogHostIp, setSyslogHostIp] = useState('');
  const [ntpServerIp, setNtpServerIp] = useState('');

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const [targetServerIp, setTargetServerIp] = useState(() => localStorage.getItem(ONBOARDING_ZTP_TARGET_IP_KEY) || '192.168.1.240');
  const [dryRun, setDryRun] = useState(() => localStorage.getItem(ONBOARDING_ZTP_DRY_RUN_KEY) === 'true');
  const [restartServices, setRestartServices] = useState(() => localStorage.getItem(ONBOARDING_ZTP_RESTART_KEY) !== 'false');
  const [resetLeases, setResetLeases] = useState(() => localStorage.getItem(ONBOARDING_ZTP_RESET_LEASES_KEY) !== 'false');

  const credentialLabel = 'ZTP_SERVER_SSH';
  const [canManageServerCredential, setCanManageServerCredential] = useState(false);
  const [serverCredentialConfigured, setServerCredentialConfigured] = useState(false);
  const [serverCredentialUsername, setServerCredentialUsername] = useState('');
  const [serverCredentialPassword, setServerCredentialPassword] = useState('');
  const [serverCredentialSaving, setServerCredentialSaving] = useState(false);
  const [serverConnectionTesting, setServerConnectionTesting] = useState(false);
  const [serverConnectionResult, setServerConnectionResult] = useState<{ ok: boolean; message: string; details?: string } | null>(null);

  const [publishJob, setPublishJob] = useState<ZtpPublishJob | null>(null);
  const [publishLoading, setPublishLoading] = useState(false);
  const [manifestLoading, setManifestLoading] = useState(false);
  const [recentSyslogEvents, setRecentSyslogEvents] = useState<any[]>([]);
  const [syslogSources, setSyslogSources] = useState<any[]>([]);
  const [selectedSyslogDevice, setSelectedSyslogDevice] = useState('');
  const [syslogEventsLoading, setSyslogEventsLoading] = useState(false);
  const [syslogSyncLoading, setSyslogSyncLoading] = useState(false);
  const [testSyslogLoading, setTestSyslogLoading] = useState(false);
  const [testNtpLoading, setTestNtpLoading] = useState(false);

  const loadProfile = async () => {
    setIsLoading(true);
    try {
      const profile = await commonServicesApi.getProfile(GLOBAL_SCOPE_TYPE, GLOBAL_SCOPE_VALUE);
      setSnmpCommunity(profile.snmp_community || '');
      setSnmpLocation(profile.snmp_location || '');
      setSnmpContact(profile.snmp_contact || '');
      setSnmpHostIp(profile.snmp_host_ip || '');
      setSyslogPort(profile.syslog_port ? String(profile.syslog_port) : '6514');
      setSyslogHostIp(profile.syslog_host_ip || '');
      setNtpServerIp(profile.ntp_server_ip || '');
    } catch {
      setSnmpCommunity('');
      setSnmpLocation('');
      setSnmpContact('');
      setSnmpHostIp('');
      setSyslogPort('6514');
      setSyslogHostIp('');
      setNtpServerIp('');
    } finally {
      setIsLoading(false);
    }
  };

  const isPublishTerminal = (status: string | undefined) => {
    const state = (status || '').toLowerCase();
    return state === 'success' || state === 'failed' || state === 'partial';
  };

  const loadLatestPublishJob = async () => {
    try {
      setPublishLoading(true);
      const res = await onboardingApi.listPublishJobs(1);
      setPublishJob(res.items?.[0] || null);
    } catch {
      setPublishJob(null);
    } finally {
      setPublishLoading(false);
    }
  };

  const loadRecentSyslogEvents = async () => {
    setSyslogEventsLoading(true);
    try {
      const events = await syslogApi.listEvents(80, selectedSyslogDevice || undefined);
      setRecentSyslogEvents(Array.isArray(events) ? events : []);
    } catch {
      setRecentSyslogEvents([]);
    } finally {
      setSyslogEventsLoading(false);
    }
  };

  const loadSyslogSources = async () => {
    try {
      const res = await syslogApi.listSources(200);
      setSyslogSources(Array.isArray(res?.items) ? res.items : []);
    } catch {
      setSyslogSources([]);
    }
  };

  const handleSyncSyslogServer = async () => {
    setSyslogSyncLoading(true);
    try {
      const res = await syslogApi.syncServer('hostname');
      if (res?.synced) {
        Toast.success(res.message || 'Syslog server sync completed');
      } else {
        Toast.error('Syslog sync incomplete', res?.message || 'Check backend settings and credentials');
      }
      loadSyslogSources();
    } catch (e: any) {
      Toast.error('Syslog sync failed', e?.message || 'Unable to sync syslog server');
    } finally {
      setSyslogSyncLoading(false);
    }
  };

  useEffect(() => {
    loadProfile();
    loadLatestPublishJob();
    loadSyslogSources();
    loadRecentSyslogEvents();
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      loadSyslogSources();
      loadRecentSyslogEvents();
    }, 10000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    loadRecentSyslogEvents();
  }, [selectedSyslogDevice]);

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

  useEffect(() => {
    if (!publishJob?.id || isPublishTerminal(publishJob.status)) return;
    const intervalId = window.setInterval(() => {
      onboardingApi.getPublishJob(publishJob.id)
        .then((job) => {
          setPublishJob(job);
          if (isPublishTerminal(job.status)) {
            setPublishLoading(false);
          }
        })
        .catch(() => {
          setPublishLoading(false);
        });
    }, 2000);
    return () => window.clearInterval(intervalId);
  }, [publishJob?.id, publishJob?.status]);

  const handleSaveServerCredential = async () => {
    const username = serverCredentialUsername.trim();
    if (!username || !serverCredentialPassword) {
      Toast.error('Save failed', 'Server credential username and password are required');
      return;
    }
    setServerCredentialSaving(true);
    try {
      await onboardingApi.setServerCredential(username, serverCredentialPassword, credentialLabel);
      setServerCredentialConfigured(true);
      setServerCredentialPassword('');
      Toast.success('Server credential saved');
    } catch (e: any) {
      Toast.error('Save failed', e.message || 'Failed to store server credential');
    } finally {
      setServerCredentialSaving(false);
    }
  };

  const handleTestServerConnection = async () => {
    setServerConnectionTesting(true);
    setServerConnectionResult(null);
    try {
      const result = await onboardingApi.testServerConnection(targetServerIp, 22, credentialLabel);
      const details = result.ok
        ? `Connected in ${result.duration_ms} ms using ${result.credential_label}`
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
      Toast.error('Download failed', e.message || 'Failed to download publish manifest');
    } finally {
      setManifestLoading(false);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const payload = {
        snmp_community: snmpCommunity.trim() || null,
        snmp_location: snmpLocation.trim() || null,
        snmp_contact: snmpContact.trim() || null,
        snmp_host_ip: snmpHostIp.trim() || null,
        syslog_port: syslogPort.trim() ? parseInt(syslogPort.trim(), 10) : null,
        syslog_host_ip: syslogHostIp.trim() || null,
        ntp_server_ip: ntpServerIp.trim() || null,
      };
      await commonServicesApi.upsertProfile(GLOBAL_SCOPE_TYPE, GLOBAL_SCOPE_VALUE, payload);
      Toast.success('Global common services profile saved');
    } catch (e: any) {
      Toast.error('Save failed', e.message || 'Unable to save profile');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    const ok = await Confirm.danger(
      'Delete global common services profile?',
      'This removes global defaults for SNMP, Syslog, and NTP.',
    );
    if (!ok) return;

    try {
      await commonServicesApi.deleteProfile(GLOBAL_SCOPE_TYPE, GLOBAL_SCOPE_VALUE);
      Toast.success('Profile deleted');
      loadProfile();
    } catch (e: any) {
      Toast.error('Delete failed', e.message || 'Unable to delete profile');
    }
  };

  const handleTestSyslog = async () => {
    if (!syslogHostIp || !syslogHostIp.trim()) {
      Toast.error('Test failed', 'Syslog Host IP is not configured');
      return;
    }
    setTestSyslogLoading(true);
    try {
      const res: any = await commonServicesApi.testSyslog();
      Toast.success('Syslog test sent', `Sent to ${res.host}:${res.port}`);
      // Refresh recent events after a short delay so incoming messages may appear
      setTimeout(() => loadRecentSyslogEvents(), 1200);
    } catch (e: any) {
      Toast.error('Syslog test failed', e?.message || 'Unable to send test syslog');
    } finally {
      setTestSyslogLoading(false);
    }
  };

  const handleTestNtp = async () => {
    if (!ntpServerIp || !ntpServerIp.trim()) {
      Toast.error('Test failed', 'NTP Server IP is not configured');
      return;
    }
    setTestNtpLoading(true);
    try {
      const res: any = await commonServicesApi.testNtp();
      Toast.success('NTP OK', `Server ${res.server} responded — offset ${res.offset_seconds.toFixed(2)}s`);
    } catch (e: any) {
      Toast.error('NTP test failed', e?.message || 'Failed to query NTP server');
    } finally {
      setTestNtpLoading(false);
    }
  };

  return (
    <div className="w-full h-full overflow-y-auto p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary/15 text-primary ring-1 ring-primary/30">
            <Layers className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Global Service Profiles</h1>
            <p className="text-sm opacity-70 mt-0.5">
              Define SNMP, TFTP, Syslog, and NTP servers defaults injected into onboarding and template rendering.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <div className="card shadow-xl border border-base-300 bg-gradient-to-br from-base-100 via-base-100 to-base-200/70">
            <div className="card-body gap-4">
              <div className="flex items-center justify-between">
                <h2 className="card-title text-base flex items-center gap-2">
                  <Server className="h-4 w-4 text-info" />
                  Edit Service Profile
                </h2>
                <button className="btn btn-ghost btn-xs" onClick={loadProfile}>
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* <div className="alert alert-info text-sm py-2">
                <span>Scope: Global defaults only</span>
              </div> */}

              <div className="divider text-xs opacity-70 my-1">SNMP</div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control flex flex-col gap-1">
                  <div className="label"><span className="label-text">Community Name</span></div>
                  <input className="input input-bordered input-sm" value={snmpCommunity} onChange={(e) => setSnmpCommunity(e.target.value)} placeholder="public" />
                </label>
                <label className="form-control flex flex-col gap-1">
                  <div className="label"><span className="label-text">SNMP Host IP</span></div>
                  <input className="input input-bordered input-sm" value={snmpHostIp} onChange={(e) => setSnmpHostIp(e.target.value)} placeholder="192.168.1.250" />
                </label>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control flex flex-col gap-1">
                  <div className="label"><span className="label-text">Location</span></div>
                  <input className="input input-bordered input-sm" value={snmpLocation} onChange={(e) => setSnmpLocation(e.target.value)} placeholder="MDF Floor 2" />
                </label>
                <label className="form-control flex flex-col gap-1">
                  <div className="label"><span className="label-text">Contact</span></div>
                  <input className="input input-bordered input-sm" value={snmpContact} onChange={(e) => setSnmpContact(e.target.value)} placeholder="noc@example.com" />
                </label>
              </div>

              <div className="divider text-xs opacity-70 my-1">Syslog</div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control flex flex-col gap-1">
                  <div className="label"><span className="label-text">Syslog Host IP</span></div>
                  <input className="input input-bordered input-sm" value={syslogHostIp} onChange={(e) => setSyslogHostIp(e.target.value)} placeholder="192.168.1.251" />
                </label>
                <label className="form-control flex flex-col gap-1">
                  <div className="label"><span className="label-text">Syslog Port</span></div>
                  <input className="input input-bordered input-sm" value={syslogPort} onChange={(e) => setSyslogPort(e.target.value)} placeholder="6514" />
                </label>
              </div>
              <div className="flex justify-end gap-2 mt-1">
                <button
                  className="btn btn-outline btn-sm min-w-36"
                  onClick={handleTestSyslog}
                  disabled={!syslogHostIp || testSyslogLoading}
                >
                  {testSyslogLoading ? <span className="loading loading-spinner loading-xs" /> : 'Send Test Syslog'}
                </button>
              </div>

              <div className="divider text-xs opacity-70 my-1">NTP</div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control flex flex-col gap-1">
                  <div className="label"><span className="label-text">NTP Server IP</span></div>
                  <input className="input input-bordered input-sm" value={ntpServerIp} onChange={(e) => setNtpServerIp(e.target.value)} placeholder="192.168.1.252" />
                </label>
                <div className="flex items-end">
                  <button
                    className="btn btn-outline btn-sm min-w-28 w-full"
                    onClick={handleTestNtp}
                    disabled={!ntpServerIp || testNtpLoading}
                  >
                    {testNtpLoading ? <span className="loading loading-spinner loading-xs" /> : 'Test NTP'}
                  </button>
                </div>
              </div>

              <div className="card-actions justify-end mt-2">
                  <button className="btn btn-outline btn-error btn-sm gap-1 min-w-24" onClick={handleDelete} disabled={isLoading}>
                  <Trash2 className="w-4 h-4" /> Delete
                </button>
                  <button className="btn btn-primary btn-sm gap-1 min-w-24" onClick={handleSave} disabled={isSaving || isLoading}>
                  <Save className="w-4 h-4" /> Save
                </button>
              </div>
            </div>
          </div>

          <div className="space-y-6 self-start">
            <div className="card shadow-xl border border-base-300 bg-gradient-to-br from-base-100 via-base-100 to-base-200/70">
              <div className="card-body gap-4">
                <div className="flex items-center justify-between gap-2 border-b border-base-300 pb-2">
                  <h2 className="card-title text-base flex items-center gap-2">
                    <Upload className="h-4 w-4 text-success" />
                    ZTP Server Settings
                  </h2>
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

                <div className={`grid grid-cols-1 gap-3 ${canManageServerCredential ? 'md:grid-cols-3' : 'md:grid-cols-1'}`}>
                  <label className="form-control w-full flex flex-col gap-1">
                    <span className="label-text text-xs">Target Server IP</span>
                    <input
                      className="input input-bordered input-sm"
                      value={targetServerIp}
                      onChange={(event) => setTargetServerIp(event.target.value)}
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

                <div className="rounded-xl border border-base-300 bg-base-200/40 px-3 py-2">
                  {/* <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs"> */}
                  <div className="grid grid-cols-1 gap-2 text-xs">
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
                      className="btn btn-outline btn-sm min-w-40"
                      onClick={handleSaveServerCredential}
                      disabled={serverCredentialSaving}
                    >
                      {serverCredentialSaving ? <span className="loading loading-spinner loading-xs" /> : 'Save Server Credential'}
                    </button>
                  </div>
                )}
              </div>
            </div>

            <div className="card shadow-xl border border-base-300 bg-gradient-to-br from-base-100 via-base-100 to-base-200/70">
              <div className="card-body gap-4">
                <div className="flex items-center justify-between">
                <h2 className="card-title text-base flex items-center gap-2">
                    <Rocket className="h-4 w-4 text-primary" />
                    ZTP Publish Job
                  </h2>
                  <button className="btn btn-ghost btn-xs" onClick={loadLatestPublishJob} disabled={publishLoading}>
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                </div>

                {!publishJob ? (
                  <div className="text-sm opacity-70">No publish jobs found yet.</div>
                ) : (
                  <>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="text-sm">
                        <span className="font-semibold">Job #{publishJob.id}</span>
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
                  </>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* ─── Full-width: Recent Syslog Events ─── */}
        <div className="card shadow-xl border border-base-300 bg-gradient-to-br from-base-100 via-base-100 to-base-200/70">
              <div className="card-body gap-4">
                <div className="flex items-center justify-between">
                  <h2 className="card-title text-base flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 text-warning" />
                    Recent Syslog Events
                  </h2>
                  <div className="flex items-center gap-2">
                    <button className="btn btn-ghost btn-sm" onClick={loadRecentSyslogEvents} disabled={syslogEventsLoading}>
                      <RefreshCw className="w-3.5 h-3.5" />
                    </button>
                    <button className="btn btn-outline btn-sm" onClick={handleSyncSyslogServer} disabled={syslogSyncLoading}>
                      {syslogSyncLoading ? <span className="loading loading-spinner loading-xs" /> : null}
                      Sync Server
                    </button>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-xs opacity-70">View</span>
                  <select
                    className="select select-bordered select-sm w-full"
                    value={selectedSyslogDevice}
                    onChange={(e) => setSelectedSyslogDevice(e.target.value)}
                  >
                    <option value="">All Devices (Aggregate)</option>
                    {syslogSources.map((source: any) => {
                      const label = source?.device_hostname || source?.source_host || source?.source_ip || 'unknown';
                      const value = source?.device_hostname || source?.source_ip || '';
                      return (
                        <option key={`${source?.source_ip || 'unknown'}-${source?.device_hostname || ''}`} value={value}>
                          {label}{source?.source_ip ? ` (${source.source_ip})` : ''} · {source?.event_count || 0}
                        </option>
                      );
                    })}
                  </select>
                </div>

                {recentSyslogEvents.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-base-300 bg-base-100/60 px-4 py-4 text-sm opacity-70">
                    No syslog events yet. Ensure your devices are sending syslog to the configured server/port and trigger traffic.
                  </div>
                ) : (
                  <div className="space-y-2 max-h-60 overflow-y-auto">
                    {recentSyslogEvents.map((evt: any) => (
                      <div key={evt.id} className="rounded border border-base-300 bg-base-200/40 px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className={`badge badge-xs ${String(evt.severity || '').toLowerCase() === 'critical' ? 'badge-error' : String(evt.severity || '').toLowerCase() === 'high' ? 'badge-warning' : String(evt.severity || '').toLowerCase() === 'medium' ? 'badge-info' : 'badge-ghost'}`}>
                              {evt.severity || 'info'}
                            </span>
                            <span className="font-semibold truncate">{evt.mnemonic || evt.facility || 'syslog'}</span>
                          </div>
                          <span className="opacity-50 flex items-center gap-1 shrink-0">
                            <Clock className="h-3 w-3" />
                            {evt.created_at || '-'}
                          </span>
                        </div>
                        <div className="mt-1 opacity-70 break-words">
                          <span className="font-semibold">{evt.device_hostname || evt.source_host || 'unknown device'}</span>
                          {evt.source_ip ? <span className="ml-2 font-mono">{evt.source_ip}</span> : null}
                          {evt.message ? <span className="ml-2">{evt.message}</span> : <span className="ml-2">{evt.raw_message}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
      </div>
    </div>
  );
};

export default CommonServicesProfilesView;
