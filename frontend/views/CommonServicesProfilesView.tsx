import React, { useEffect, useState } from 'react';
import { Layers, Save, Trash2, RefreshCw } from 'lucide-react';
import { commonServicesApi } from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';

const GLOBAL_SCOPE_TYPE = 'global';
const GLOBAL_SCOPE_VALUE = 'global';

const CommonServicesProfilesView: React.FC = () => {
  const [snmpCommunity, setSnmpCommunity] = useState('');
  const [snmpLocation, setSnmpLocation] = useState('');
  const [snmpContact, setSnmpContact] = useState('');
  const [snmpHostIp, setSnmpHostIp] = useState('');
  const [syslogHostIp, setSyslogHostIp] = useState('');
  const [ntpServerIp, setNtpServerIp] = useState('');

  const [effective, setEffective] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const loadProfile = async () => {
    setIsLoading(true);
    try {
      const profile = await commonServicesApi.getProfile(GLOBAL_SCOPE_TYPE, GLOBAL_SCOPE_VALUE);
      setSnmpCommunity(profile.snmp_community || '');
      setSnmpLocation(profile.snmp_location || '');
      setSnmpContact(profile.snmp_contact || '');
      setSnmpHostIp(profile.snmp_host_ip || '');
      setSyslogHostIp(profile.syslog_host_ip || '');
      setNtpServerIp(profile.ntp_server_ip || '');
    } catch {
      setSnmpCommunity('');
      setSnmpLocation('');
      setSnmpContact('');
      setSnmpHostIp('');
      setSyslogHostIp('');
      setNtpServerIp('');
    } finally {
      setIsLoading(false);
    }
  };

  const loadEffective = async () => {
    try {
      const res = await commonServicesApi.getEffective();
      setEffective(res.effective);
    } catch {
      setEffective(null);
    }
  };

  useEffect(() => {
    loadProfile();
    loadEffective();
  }, []);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const payload = {
        snmp_community: snmpCommunity.trim() || null,
        snmp_location: snmpLocation.trim() || null,
        snmp_contact: snmpContact.trim() || null,
        snmp_host_ip: snmpHostIp.trim() || null,
        syslog_host_ip: syslogHostIp.trim() || null,
        ntp_server_ip: ntpServerIp.trim() || null,
      };
      await commonServicesApi.upsertProfile(GLOBAL_SCOPE_TYPE, GLOBAL_SCOPE_VALUE, payload);
      Toast.success('Global common services profile saved');
      loadEffective();
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
      loadEffective();
    } catch (e: any) {
      Toast.error('Delete failed', e.message || 'Unable to delete profile');
    }
  };

  return (
    <div className="w-full h-full overflow-y-auto p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex items-center gap-3">
          <Layers className="h-6 w-6 text-primary" />
          <div>
            <h1 className="text-xl font-bold">Global Common Services</h1>
            <p className="text-sm opacity-70">
              Define global SNMP, Syslog, and NTP defaults injected into onboarding and template rendering.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="card bg-base-100 shadow">
            <div className="card-body gap-3">
              <div className="flex items-center justify-between">
                <h2 className="card-title text-base">Edit Global Profile</h2>
                <button className="btn btn-ghost btn-xs" onClick={loadProfile}>
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
              </div>

              <div className="alert alert-info text-sm py-2">
                <span>Scope: Global defaults only</span>
              </div>

              <div className="divider text-xs opacity-70 my-1">SNMP</div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control">
                  <div className="label"><span className="label-text">Community Name</span></div>
                  <input className="input input-bordered" value={snmpCommunity} onChange={(e) => setSnmpCommunity(e.target.value)} placeholder="public" />
                </label>
                <label className="form-control">
                  <div className="label"><span className="label-text">SNMP Host IP</span></div>
                  <input className="input input-bordered" value={snmpHostIp} onChange={(e) => setSnmpHostIp(e.target.value)} placeholder="192.168.1.250" />
                </label>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control">
                  <div className="label"><span className="label-text">Location</span></div>
                  <input className="input input-bordered" value={snmpLocation} onChange={(e) => setSnmpLocation(e.target.value)} placeholder="MDF Floor 2" />
                </label>
                <label className="form-control">
                  <div className="label"><span className="label-text">Contact</span></div>
                  <input className="input input-bordered" value={snmpContact} onChange={(e) => setSnmpContact(e.target.value)} placeholder="noc@example.com" />
                </label>
              </div>

              <div className="divider text-xs opacity-70 my-1">Syslog</div>
              <label className="form-control">
                <div className="label"><span className="label-text">Syslog Host IP</span></div>
                <input className="input input-bordered" value={syslogHostIp} onChange={(e) => setSyslogHostIp(e.target.value)} placeholder="192.168.1.251" />
              </label>

              <div className="divider text-xs opacity-70 my-1">NTP</div>
              <label className="form-control">
                <div className="label"><span className="label-text">NTP Server IP</span></div>
                <input className="input input-bordered" value={ntpServerIp} onChange={(e) => setNtpServerIp(e.target.value)} placeholder="192.168.1.252" />
              </label>

              <div className="card-actions justify-end mt-2">
                <button className="btn btn-outline btn-error btn-sm gap-1" onClick={handleDelete} disabled={isLoading}>
                  <Trash2 className="w-4 h-4" /> Delete
                </button>
                <button className="btn btn-primary btn-sm gap-1" onClick={handleSave} disabled={isSaving || isLoading}>
                  <Save className="w-4 h-4" /> Save
                </button>
              </div>
            </div>
          </div>

          <div className="card bg-base-100 shadow">
            <div className="card-body gap-3">
              <h2 className="card-title text-base">Effective Global Values</h2>

              <div className="rounded-lg border border-base-300 p-3 text-sm">
                <div className="space-y-1 wrap-break-word">
                  <div><span className="font-semibold">SNMP Community:</span> {effective?.snmp_community || 'not set'}</div>
                  <div><span className="font-semibold">SNMP Location:</span> {effective?.snmp_location || 'not set'}</div>
                  <div><span className="font-semibold">SNMP Contact:</span> {effective?.snmp_contact || 'not set'}</div>
                  <div><span className="font-semibold">SNMP Host IP:</span> {effective?.snmp_host_ip || 'not set'}</div>
                </div>
                <div className="mt-2">
                  <span className="font-semibold">Syslog Host IP:</span>
                  <div className="ml-2">{effective?.syslog_host_ip || 'not set'}</div>
                </div>
                <div className="mt-2">
                  <span className="font-semibold">NTP Server IP:</span>
                  <div className="ml-2">{effective?.ntp_server_ip || 'not set'}</div>
                </div>
                <div className="mt-3 text-xs opacity-70">Sources:</div>
                <pre className="mt-1 max-h-24 overflow-auto rounded bg-base-200 p-2 text-[11px] leading-4 break-all whitespace-pre-wrap">
                  {effective?.sources ? JSON.stringify(effective.sources, null, 2) : 'n/a'}
                </pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CommonServicesProfilesView;
