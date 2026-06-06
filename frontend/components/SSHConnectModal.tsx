import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Terminal, Loader2, Save, ArrowLeft, Server, Globe, ChevronDown } from 'lucide-react';
import { netboxApi, authApi, networkConfigBackupApi, type DeviceRecord } from '../src/services/api';

export type SSHConnectInitialTarget = { hostname: string; host: string };

type View = 'choice' | 'inventory' | 'custom';

interface SSHConnectModalProps {
  hostname: string;
  host: string;
  isOpen: boolean;
  onConnect: (hostname: string, host: string, username: string, password: string, port: number, saveToVault: boolean) => Promise<void>;
  onClose: () => void;
}

const SSHConnectModal: React.FC<SSHConnectModalProps> = ({
  hostname: initialHostname,
  host: initialHost,
  isOpen,
  onConnect,
  onClose,
}) => {
  const [view, setView] = useState<View>('choice');
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [selectedDevice, setSelectedDevice] = useState<DeviceRecord | null>(null);
  const [pendingDeviceId, setPendingDeviceId] = useState<number | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [showDropdown, setShowDropdown] = useState(false);
  const [liveByHostname, setLiveByHostname] = useState<Record<string, boolean>>({});
  const [formHostname, setFormHostname] = useState('');
  const [formHost, setFormHost] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [port, setPort] = useState(22);
  const [saveToVault, setSaveToVault] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const usernameRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const normalizeHostname = useCallback((value: string | null | undefined): string => {
    return String(value || '').trim().toLowerCase();
  }, []);

  const isDeviceAlive = useCallback((device: DeviceRecord): boolean => {
    const key = normalizeHostname(device.hostname);
    if (key && Object.prototype.hasOwnProperty.call(liveByHostname, key)) {
      return liveByHostname[key] === true;
    }
    const status = String(device.status || '').trim().toLowerCase();
    return status === 'active' || status === 'registered' || status === 'provisioning';
  }, [liveByHostname, normalizeHostname]);

  useEffect(() => {
    if (isOpen) {
      setView('choice');
      setSelectedDevice(null);
      setSearchTerm('');
      setShowDropdown(false);
      setFormHostname('');
      setFormHost('');
      setUsername('');
      setPassword('');
      setPort(22);
      setSaveToVault(false);
      setError('');
      setLoading(false);
      setLiveByHostname({});
      netboxApi.listDevices()
        .then((res) => setDevices(res.devices || []))
        .catch(() => setDevices([]));
    }
  }, [isOpen, initialHostname, initialHost]);

  useEffect(() => {
    if (!isOpen) return;
    let disposed = false;
    let timerId: number | null = null;

    const refreshLiveStatus = async () => {
      try {
        const rows = await networkConfigBackupApi.listDevices();
        if (disposed) return;
        const next: Record<string, boolean> = {};
        for (const row of rows || []) {
          const key = normalizeHostname(row.hostname);
          if (!key) continue;
          next[key] = row.live_in_topology === true;
        }
        setLiveByHostname(next);
      } catch {
        // keep previous status snapshot if polling fails
      }
    };

    refreshLiveStatus();
    timerId = window.setInterval(refreshLiveStatus, 7000);

    return () => {
      disposed = true;
      if (timerId !== null) window.clearInterval(timerId);
    };
  }, [isOpen, normalizeHostname]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (isOpen && (view === 'inventory' || view === 'custom')) {
      setTimeout(() => usernameRef.current?.focus(), 100);
    }
  }, [isOpen, view]);

  const goBackToChoice = useCallback(() => {
    setView('choice');
    setError('');
    setSelectedDevice(null);
    setSearchTerm('');
    setShowDropdown(false);
  }, []);

  const goBackFromInventoryForm = useCallback(() => {
    setView('inventory');
    setSelectedDevice(null);
    setError('');
  }, []);


  const handleChooseInventory = useCallback(() => {
    setView('inventory');
    setError('');
  }, []);

  const handleChooseCustom = useCallback(() => {
    setView('custom');
    setFormHostname('');
    setFormHost('');
    setUsername('');
    setPassword('');
    setPort(22);
    setSaveToVault(false);
    setError('');
  }, []);

  const handleSelectInventoryDevice = useCallback((deviceId: number, devName: string) => {
    setPendingDeviceId(deviceId);
    setSearchTerm(devName);
    setShowDropdown(false);
    setError('');
  }, []);

  const handleInventoryContinue = useCallback(async () => {
    if (!pendingDeviceId) return;
    setLoading(true);
    setError('');
    try {
      const d = await netboxApi.getDevice(pendingDeviceId);
      setSelectedDevice(d);
      const host = d.management_ip || d.hostname;
      setFormHostname(d.hostname);
      setFormHost(host);
      
      let user = d.ssh_user ?? '';
      let pass = d.ssh_password ?? '';
      
      if (!user || !pass) {
        try {
          const creds = await authApi.listCredentials(pendingDeviceId);
          const sshCred = creds.find((c: any) => c.label.toUpperCase() === 'SSH' || c.label.toUpperCase().includes('SSH'));
          if (sshCred) {
            // ...
          }
        } catch {}
      }
      
      setUsername(user);
      setPassword(pass);
      setPort(d.ssh_port ?? 22);
      setSaveToVault(false);
    } catch (e: any) {
      setError(e.message || 'Failed to load device');
    } finally {
      setLoading(false);
    }
  }, [pendingDeviceId]);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) {
      setError('Username is required');
      return;
    }
    const hostname = formHostname.trim() || formHost.trim();
    const host = formHost.trim() || hostname;
    if (!hostname && !host) {
      setError('IP address or hostname is required');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await onConnect(hostname || host, host, username.trim(), password, port, saveToVault);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Connection failed');
      setLoading(false);
    }
  }, [selectedDevice, formHostname, formHost, username, password, port, saveToVault, onConnect, onClose]);

  if (!isOpen) return null;

  return (
    <dialog className="modal modal-open" onClick={onClose}>
      <div 
        className="modal-box max-w-md p-0 bg-base-100 border border-base-300 shadow-2xl flex flex-col rounded-2xl overflow-visible" 
        onClick={(e) => e.stopPropagation()}
      >
        <style>{`
          @keyframes ssh-status-breath-green {
            0%, 100% { transform: scale(1); opacity: 0.8; box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.35); }
            50% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 8px 2px rgba(34, 197, 94, 0.55); }
          }
          @keyframes ssh-status-breath-red {
            0%, 100% { transform: scale(1); opacity: 0.75; box-shadow: 0 0 0 0 rgba(248, 113, 113, 0.3); }
            50% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 8px 2px rgba(248, 113, 113, 0.5); }
          }
          .ssh-live-dot--up { animation: ssh-status-breath-green 2.8s ease-in-out infinite; }
          .ssh-live-dot--down { animation: ssh-status-breath-red 2.8s ease-in-out infinite; }
        `}</style>
        {/* Header */}
        <div className="p-4 border-b border-base-300 flex items-center justify-between bg-base-200/50">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-primary/10 text-primary">
              <Terminal size={18} strokeWidth={2.5} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                {view !== 'choice' && !(view === 'inventory' && !selectedDevice) && (
                  <button 
                    type="button" 
                    className="btn btn-ghost btn-xs btn-circle text-base-content/60 hover:text-primary transition-colors"
                    onClick={view === 'inventory' && selectedDevice ? goBackFromInventoryForm : goBackToChoice}
                  >
                    <ArrowLeft size={16} />
                  </button>
                )}
                <h3 className="font-bold text-lg text-base-content tracking-tight leading-tight">SSH Connect</h3>
              </div>
              <p className="text-[10px] text-base-content/60 mt-0.5 ml-0.5">Secure remote infrastructure access</p>
            </div>
          </div>
          <button 
            type="button" 
            className="btn btn-ghost btn-xs btn-circle text-base-content/50 hover:text-base-content hover:bg-base-content/10"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div className="p-5 overflow-visible">
          {view === 'choice' && (
            <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">
              <p className="text-xs text-base-content/80 mb-4 px-1">
                Establish a new connection:
              </p>
              <div className="grid gap-3">
                <button
                  type="button"
                  className="group relative flex items-start gap-3.5 p-4 rounded-xl border border-base-300 bg-base-200/30 hover:bg-primary/5 hover:border-primary/30 transition-all duration-300 text-left overflow-hidden"
                  onClick={handleChooseInventory}
                >
                  <div className="absolute inset-0 bg-linear-to-br from-primary/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                  <div className="p-2.5 rounded-lg bg-base-300 text-base-content/70 group-hover:bg-primary group-hover:text-primary-content transition-colors">
                    <Server size={20} />
                  </div>
                  <div className="flex-1 text-base-content">
                    <span className="font-semibold text-sm block mb-0.5 group-hover:text-primary transition-colors">Inventory Device</span>
                    <p className="text-[11px] opacity-60 leading-tight group-hover:opacity-90 transition-colors">
                      Managed device from NetBox with stored credentials.
                    </p>
                  </div>
                </button>

                <button
                  type="button"
                  className="group relative flex items-start gap-3.5 p-4 rounded-xl border border-base-300 bg-base-200/30 hover:bg-secondary/5 hover:border-secondary/30 transition-all duration-300 text-left overflow-hidden"
                  onClick={handleChooseCustom}
                >
                  <div className="absolute inset-0 bg-linear-to-br from-secondary/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                  <div className="p-2.5 rounded-lg bg-base-300 text-base-content/70 group-hover:bg-secondary group-hover:text-secondary-content transition-colors">
                    <Globe size={20} />
                  </div>
                  <div className="flex-1 text-base-content">
                    <span className="font-semibold text-sm block mb-0.5 group-hover:text-secondary transition-colors">Custom Host</span>
                    <p className="text-[11px] opacity-60 leading-tight group-hover:opacity-90 transition-colors">
                      Manually enter IP and credentials for any host.
                    </p>
                  </div>
                </button>
              </div>
            </div>
          )}

          {view === 'inventory' && !selectedDevice && (
            <div className="animate-in fade-in slide-in-from-right-4 duration-300">
              <div className="space-y-4 relative">
                <div ref={dropdownRef}>
                  <label className="label pt-0 pb-2 px-1">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/40">Select Device</span>
                  </label>
                  <div className="relative group">
                    <button
                      type="button"
                      className="flex items-center justify-between w-full h-8 px-3 text-sm transition-all border rounded-lg bg-base-100 border-base-300 text-base-content hover:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/50"
                      onClick={() => setShowDropdown(!showDropdown)}
                    >
                      <span className={`inline-flex items-center gap-2 ${searchTerm ? "text-base-content" : "text-base-content/40"}`}>
                        {searchTerm && pendingDeviceId ? (() => {
                          const selected = devices.find(d => d.id === pendingDeviceId);
                          if (!selected) return null;
                          const alive = isDeviceAlive(selected);
                          return (
                            <span
                              className={`inline-block w-2 h-2 rounded-full shrink-0 ${alive ? 'bg-success ssh-live-dot--up' : 'bg-error ssh-live-dot--down'}`}
                              title={alive ? 'Live / reachable' : 'Down / unavailable'}
                            />
                          );
                        })() : null}
                        {searchTerm || '— Search inventory —'}
                      </span>
                      <ChevronDown size={14} className={`opacity-50 transition-transform duration-200 ${showDropdown ? 'rotate-180' : ''}`} />
                    </button>
                    
                    {showDropdown && (
                      <div className="absolute top-full left-0 right-0 z-50 mt-1 bg-base-100 border border-base-300 rounded-lg shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-1 duration-200">
                        <ul className="max-h-[200px] overflow-y-auto custom-scrollbar py-1">
                          {devices.length > 0 ? (
                            devices.map((d) => (
                              <li key={d.id}>
                                <button
                                  type="button"
                                  className="w-full px-4 py-2 text-left text-sm hover:bg-base-300 hover:text-base-content transition-colors flex items-center justify-between group/item"
                                  onClick={() => handleSelectInventoryDevice(d.id, d.hostname)}
                                >
                                  <span className="inline-flex items-center gap-2 min-w-0">
                                    <span
                                      className={`inline-block w-2 h-2 rounded-full shrink-0 ${isDeviceAlive(d) ? 'bg-success ssh-live-dot--up' : 'bg-error ssh-live-dot--down'}`}
                                      title={isDeviceAlive(d) ? 'Live / reachable' : 'Down / unavailable'}
                                    />
                                    <span className="font-medium truncate">{d.hostname}</span>
                                  </span>
                                  {d.management_ip && (
                                    <span className="text-[10px] opacity-50 group-hover/item:opacity-80">({d.management_ip})</span>
                                  )}
                                </button>
                              </li>
                            ))
                          ) : (
                            <li className="px-4 py-3 text-xs text-center text-base-content/40">No devices available</li>
                          )}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
                
                {error && (
                  <div className="alert alert-error bg-error/10 border-error/20 text-error p-2 text-[11px] rounded-lg">
                    {error}
                  </div>
                )}

                <div className="pt-6 border-t border-base-300 flex items-center justify-between">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm text-base-content/50 hover:bg-base-content/5 gap-2"
                    onClick={goBackToChoice}
                  >
                    <ArrowLeft size={14} /> Back
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm px-8 gap-2 shadow-lg shadow-primary/20"
                    onClick={handleInventoryContinue}
                    disabled={!pendingDeviceId || loading}
                  >
                    {loading ? <Loader2 size={14} className="animate-spin" /> : 'Go'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {(view === 'inventory' && selectedDevice || view === 'custom') && (
            <div className="animate-in fade-in slide-in-from-right-4 duration-300">
              <div className="flex items-center justify-between mb-5 px-1">
                <div className="flex items-center gap-2">
                  <div className={`w-1.5 h-1.5 rounded-full ${view === 'custom' ? 'bg-secondary' : 'bg-primary'} animate-pulse`} />
                  <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/40">
                    {view === 'custom' ? 'Custom Host Configuration' : 'Inventory Credentials'}
                  </span>
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div className="form-control">
                    <label className="label py-0.5"><span className="label-text text-base-content/60 text-[10px] uppercase font-bold">Host / IP</span></label>
                    <input 
                      type="text" 
                      className={`input input-bordered input-sm w-full bg-base-100 text-base-content ${view === 'inventory' ? 'opacity-70 grayscale' : 'focus:border-primary/50'}`}
                      value={formHost} 
                      onChange={(e) => { 
                        if(view === 'custom') {
                          setFormHost(e.target.value); 
                          if (!formHostname) setFormHostname(e.target.value); 
                        }
                      }}
                      placeholder="192.168.1.1"
                      readOnly={view === 'inventory'} 
                      disabled={loading}
                    />
                  </div>
                  <div className="form-control">
                    <label className="label py-0.5"><span className="label-text text-base-content/60 text-[10px] uppercase font-bold">Label</span></label>
                    <input 
                      type="text" 
                      className={`input input-bordered input-sm w-full bg-base-100 text-base-content ${view === 'inventory' ? 'opacity-70 grayscale' : 'focus:border-primary/50'}`}
                      value={formHostname} 
                      onChange={(e) => view === 'custom' && setFormHostname(e.target.value)}
                      placeholder="Device name"
                      readOnly={view === 'inventory'}
                      disabled={loading}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-12 gap-3">
                  <div className="form-control col-span-8">
                    <label className="label py-0.5"><span className="label-text text-base-content/60 text-[10px] uppercase font-bold">Username</span></label>
                    <input
                      ref={usernameRef}
                      type="text"
                      className="input input-bordered input-sm w-full bg-base-100 text-base-content focus:border-primary/50"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="admin"
                      autoComplete="username"
                      disabled={loading}
                    />
                  </div>
                  <div className="form-control col-span-4">
                    <label className="label py-0.5"><span className="label-text text-base-content/60 text-[10px] uppercase font-bold">Port</span></label>
                    <input
                      type="number"
                      className="input input-bordered input-sm w-full bg-base-100 text-base-content focus:border-primary/50"
                      value={port}
                      onChange={(e) => setPort(Number(e.target.value))}
                      min={1}
                      max={65535}
                      disabled={loading}
                    />
                  </div>
                </div>

                <div className="form-control">
                  <label className="label py-0.5"><span className="label-text text-base-content/60 text-[10px] uppercase font-bold">Password</span></label>
                  <input
                    type="password"
                    className="input input-bordered input-sm w-full bg-base-100 text-base-content focus:border-primary/50"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    autoComplete="current-password"
                    disabled={loading}
                  />
                </div>

                <div className="flex items-center justify-between pt-1">
                  <label className="flex items-center gap-2.5 cursor-pointer group">
                    <input 
                      type="checkbox" 
                      className="checkbox checkbox-primary checkbox-xs" 
                      checked={saveToVault} 
                      onChange={(e) => setSaveToVault(e.target.checked)} 
                      disabled={loading} 
                    />
                    <span className="text-[10px] text-base-content/60 group-hover:text-base-content transition-colors flex items-center gap-1">
                      <Save size={12} className="text-primary" /> 
                      Vault save
                    </span>
                  </label>

                  {error && <span className="text-error text-[9px] font-bold uppercase tracking-tight">{error}</span>}
                </div>

                <div className="pt-4 border-t border-base-300 flex gap-2.5">
                  <button 
                    type="button" 
                    className="btn btn-ghost btn-sm flex-1 text-base-content/50 hover:bg-base-content/5" 
                    onClick={view === 'custom' ? goBackToChoice : goBackFromInventoryForm} 
                    disabled={loading}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary btn-sm flex-[1.5] gap-2 shadow-lg shadow-primary/20" disabled={loading}>
                    {loading ? (
                      <><Loader2 size={14} className="animate-spin" /> Link...</>
                    ) : (
                      <><Terminal size={14} strokeWidth={2.5} /> Establish</>
                    )}
                  </button>
                </div>
              </form>
            </div>
          )}
        </div>
      </div>
    </dialog>
  );
};

export default SSHConnectModal;
