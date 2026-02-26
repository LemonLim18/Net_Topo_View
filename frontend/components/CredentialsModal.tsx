import React, { useState } from 'react';
import { Server, Key, X } from 'lucide-react';

export interface DeviceCredentials {
  host: string;
  username: string;
  password: string;
  driver_type: string;
}

interface CredentialsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (creds: DeviceCredentials) => void;
  defaultVendor?: string;
  title?: string;
  submitLabel?: string;
  isLoading?: boolean;
}

const CredentialsModal: React.FC<CredentialsModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  defaultVendor = 'cisco_ios',
  title = 'Device Connection',
  submitLabel = 'Connect',
  isLoading = false,
}) => {
  const [host, setHost] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [driverType, setDriverType] = useState(defaultVendor);

  if (!isOpen) return null;

  const handleSubmit = () => {
    if (!host || !username || !password) return;
    onSubmit({ host, username, password, driver_type: driverType });
  };

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-md">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-lg flex items-center gap-2">
            <Server className="h-5 w-5" />
            {title}
          </h3>
          <button className="btn btn-ghost btn-sm btn-circle" onClick={onClose} disabled={isLoading}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 mb-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs">Host / IP</span></label>
              <input
                type="text"
                placeholder="192.168.1.1"
                className="input input-bordered input-sm"
                value={host}
                onChange={e => setHost(e.target.value)}
                disabled={isLoading}
              />
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs">Driver Type</span></label>
              <select
                className="select select-bordered select-sm"
                value={driverType}
                onChange={e => setDriverType(e.target.value)}
                disabled={isLoading}
              >
                <option value="cisco_ios">Cisco IOS</option>
                <option value="huawei_vrp">Huawei VRP</option>
                <option value="juniper_junos">Juniper Junos</option>
                <option value="cisco_nxos">Cisco NX-OS</option>
              </select>
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs">Username</span></label>
              <input
                type="text"
                placeholder="admin"
                className="input input-bordered input-sm"
                value={username}
                onChange={e => setUsername(e.target.value)}
                disabled={isLoading}
              />
            </div>
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text text-xs flex items-center gap-1">
                  <Key className="h-3 w-3" /> Password
                </span>
              </label>
              <input
                type="password"
                placeholder="••••••••"
                className="input input-bordered input-sm"
                value={password}
                onChange={e => setPassword(e.target.value)}
                disabled={isLoading}
              />
            </div>
          </div>
        </div>

        <div className="modal-action">
          <button className="btn btn-ghost" onClick={onClose} disabled={isLoading}>Cancel</button>
          <button
            className={`btn btn-primary gap-2 ${isLoading ? 'loading' : ''}`}
            onClick={handleSubmit}
            disabled={isLoading || !host || !username || !password}
          >
            {submitLabel}
          </button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={!isLoading ? onClose : undefined}></div>
    </div>
  );
};

export default CredentialsModal;
