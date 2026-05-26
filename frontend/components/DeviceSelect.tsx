import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Search } from 'lucide-react';
import DeviceStatusDot from './DeviceStatusDot';
import type { ConfigBackupDevice } from '../src/services/api';

interface DeviceSelectProps {
  devices: ConfigBackupDevice[];
  selectedId: number | null;
  onChange: (device: ConfigBackupDevice | null) => void;
  loading?: boolean;
}

const DeviceSelect: React.FC<DeviceSelectProps> = ({ 
  devices, 
  selectedId, 
  onChange,
  loading = false 
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  const selectedDevice = devices.find(d => d.id === selectedId) || null;

  const filteredDevices = devices.filter(d => 
    d.hostname.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (d.management_ip && d.management_ip.includes(searchTerm)) ||
    (d.vendor && d.vendor.toLowerCase().includes(searchTerm.toLowerCase()))
  );

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="relative w-full" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => !loading && setIsOpen(!isOpen)}
        className={`
          flex items-center justify-between w-full px-4 py-2 text-left 
          bg-base-100 border border-base-content/20 rounded-lg shadow-sm
          hover:border-primary/50 transition-all active:scale-[0.99]
          ${loading ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
        `}
      >
        <div className="flex items-center gap-3">
          {selectedDevice ? (
            <>
              <div className="flex-1 truncate">
                <span className="font-medium">{selectedDevice.hostname}</span>
                {selectedDevice.management_ip && (
                  <span className="ml-2 text-xs opacity-60">({selectedDevice.management_ip})</span>
                )}
                {selectedDevice.vendor && (
                  <span className="ml-2 text-xs opacity-60">· {selectedDevice.vendor}</span>
                )}
              </div>
              <DeviceStatusDot
                status={selectedDevice.live_in_topology === true ? 'active' : selectedDevice.live_in_topology === false ? 'down' : selectedDevice.status}
                size="sm"
                title={selectedDevice.live_in_topology === true ? 'Live (in topology)' : selectedDevice.live_in_topology === false ? 'Not in live topology' : selectedDevice.status}
              />
            </>
          ) : (
            <span className="opacity-60">Choose a device…</span>
          )}
        </div>
        <ChevronDown className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute z-100 w-full mt-2 overflow-hidden bg-base-100 border border-base-content/10 rounded-xl shadow-2xl animate-in fade-in zoom-in duration-200">
          <div className="p-2 border-b border-base-content/5">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 opacity-40" />
              <input
                type="text"
                placeholder="Search devices..."
                autoFocus
                className="w-full pl-9 pr-4 py-2 text-sm bg-base-200/50 rounded-lg border-none focus:ring-1 focus:ring-primary/50 outline-none"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>
          
          <div className="max-h-64 overflow-y-auto p-1 custom-scrollbar">
            {filteredDevices.length > 0 ? (
              filteredDevices.map(device => (
                <button
                  key={device.id}
                  onClick={() => {
                    onChange(device);
                    setIsOpen(false);
                    setSearchTerm('');
                  }}
                  className={`
                    flex items-center justify-between w-full p-3 rounded-lg text-sm text-left
                    hover:bg-primary/10 transition-colors
                    ${selectedId === device.id ? 'bg-primary/5 text-primary font-medium' : ''}
                  `}
                >
                  <div className="flex flex-col min-w-0">
                    <div className="truncate">
                      {device.hostname}
                      {device.management_ip && (
                        <span className="ml-2 opacity-50 text-[11px]">({device.management_ip})</span>
                      )}
                    </div>
                    {device.vendor && (
                      <div className="text-[10px] opacity-40 uppercase tracking-wider mt-0.5">
                        {device.vendor}
                      </div>
                    )}
                  </div>
                  <DeviceStatusDot
                    status={device.live_in_topology === true ? 'active' : device.live_in_topology === false ? 'down' : device.status}
                    size="sm"
                    title={device.live_in_topology === true ? 'Live (in topology)' : device.live_in_topology === false ? 'Not in live topology' : device.status}
                  />
                </button>
              ))
            ) : (
              <div className="p-4 text-center text-sm opacity-40">No devices found</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default DeviceSelect;
