import React, { useState, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import { PackagePlus, Download, RefreshCw, ChevronRight, ChevronLeft, CheckCircle, Terminal, Maximize2, Minimize2 } from 'lucide-react';
import type { ViewId } from '../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DeviceSummary {
  id:            number;
  hostname:      string;
  management_ip: string | null;
  vendor:        string | null;
  ssh_user:      string | null;
  ssh_port:      number;
  status:        string;
}

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
  const [step, setStep]               = useState<1 | 2 | 3>(1);

  // Step 1 state
  const [devices, setDevices]         = useState<DeviceSummary[]>([]);
  const [loadingDevices, setLoadingDevices] = useState(false);
  const [selected, setSelected]       = useState<DeviceSummary | null>(null);

  // Step 2 state
  const [config, setConfig]           = useState('');
  const [filename, setFilename]       = useState('day0_config.txt');
  const [loadingConfig, setLoadingConfig] = useState(false);

  const [error, setError]             = useState('');
  const [isFullWidth, setIsFullWidth] = useState(false);

  // Fetch device list on mount
  useEffect(() => { fetchDevices(); }, []);

  const fetchDevices = async () => {
    setLoadingDevices(true);
    setError('');
    try {
      const res = await fetch('/api/v1/onboarding/devices');
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data: DeviceSummary[] = await res.json();
      setDevices(data);
    } catch (e: any) {
      setError(e.message ?? 'Failed to load devices');
    } finally {
      setLoadingDevices(false);
    }
  };

  const handleGenerate = async () => {
    if (!selected) return;
    setLoadingConfig(true);
    setError('');
    try {
      const res = await fetch(`/api/v1/onboarding/generate/${selected.id}`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail ?? `Server error ${res.status}`);
      }
      const data = await res.json();
      setConfig(data.config);
      setFilename(data.filename);
      setStep(2);
    } catch (e: any) {
      setError(e.message ?? 'Failed to generate config');
    } finally {
      setLoadingConfig(false);
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
    setStep(3);
  };

  const vendorLabel  = (v: string | null) => VENDOR_LABELS[v ?? ''] ?? v ?? 'Unknown';
  const stepLabels   = ['Select Device', 'Review & Edit', 'Download Done'];

  return (
    <div className="w-full h-full overflow-y-auto">
      <div className={`p-6 min-h-full flex flex-col gap-6 transition-all duration-300 ${isFullWidth ? 'w-full' : 'max-w-5xl mx-auto'}`}>

      {/* Header */}
      <div className="flex items-center gap-3">
        <PackagePlus className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-xl font-bold">Onboard New Device</h1>
          <p className="text-sm opacity-60">
            Select a planned device, review the generated Day-0 config in the editor, then download the script for console-paste.
            All manually added devices (status: "Planned") appear here.
          </p>
        </div>
      </div>

      {/* Progress steps */}
      <ul className="steps steps-horizontal w-full text-xs">
        {stepLabels.map((label, i) => (
          <li key={label} className={`step ${step > i ? 'step-primary' : ''}`}>{label}</li>
        ))}
      </ul>

      {/* Global error */}
      {error && (
        <div className="alert alert-error text-sm">
          <span>{error}</span>
        </div>
      )}

      {/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
          Step 1 â€” Pick a device from inventory
      â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
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
              <div className="overflow-x-auto rounded-lg border border-base-300">
                <table className="table table-sm w-full">
                  <thead className="bg-base-200">
                    <tr>
                      <th></th>
                      <th>Hostname</th>
                      <th>Management IP</th>
                      <th>Vendor</th>
                      <th>SSH User</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {devices.map((d) => (
                      <tr
                        key={d.id}
                        className={`cursor-pointer hover:bg-base-200 transition-colors ${
                          selected?.id === d.id ? 'bg-primary/10 font-semibold' : ''
                        }`}
                        onClick={() => setSelected(d)}
                      >
                        <td>
                          <input
                            type="radio"
                            className="radio radio-primary radio-sm"
                            checked={selected?.id === d.id}
                            onChange={() => setSelected(d)}
                          />
                        </td>
                        <td className="font-mono">{d.hostname}</td>
                        <td className="font-mono text-xs">{d.management_ip || 'â€”'}</td>
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
                  The config will use placeholder values â€” edit them in the next step or
                  <button className="btn btn-link btn-xs px-1" onClick={() => setView('netbox_devices')}>
                    update the device
                  </button>
                  first.
                </span>
              </div>
            )}

            <div className="card-actions justify-end">
              <button
                className="btn btn-primary btn-sm gap-2"
                onClick={handleGenerate}
                disabled={!selected || loadingConfig}
              >
                {loadingConfig
                  ? <span className="loading loading-spinner loading-xs" />
                  : <ChevronRight className="h-4 w-4" />}
                Generate Config
              </button>
            </div>
          </div>
        </div>
      )}

      {/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
          Step 2 â€” Monaco editor: review + edit
      â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      {step === 2 && (
        <div className="card bg-base-100 shadow">
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
                title={isFullWidth ? "Standard View" : "Expand View"}
              >
                {isFullWidth ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
                {isFullWidth ? "Narrow" : "Full Screen"}
              </button>
            </div>

            <div className="alert alert-info text-sm">
              Edit the script if needed (e.g. change the subnet mask, interface name, or gateway), then click <strong>Download Script</strong> and paste it into the device console.
            </div>

            <div className="rounded-lg overflow-hidden border border-base-300 transition-all duration-300" style={{ height: isFullWidth ? '700px' : '480px' }}>
              <Editor
                height={isFullWidth ? "700px" : "480px"}
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
              <button className="btn btn-ghost btn-sm gap-1" onClick={() => setStep(1)}>
                <ChevronLeft className="h-4 w-4" />
                Back
              </button>
              <button className="btn btn-primary btn-sm gap-2" onClick={handleDownload}>
                <Download className="h-4 w-4" />
                Download Script
              </button>
            </div>
          </div>
        </div>
      )}

      {/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
          Step 3 â€” Done
      â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      {step === 3 && (
        <div className="card bg-base-100 shadow">
          <div className="card-body gap-4 items-center text-center">
            <CheckCircle className="h-14 w-14 text-success" />
            <h2 className="card-title text-lg">Script Downloaded!</h2>
            <p className="text-sm opacity-70 max-w-md">
              Paste <strong>{filename}</strong> into the device console to bootstrap it.
              The device remains in <strong>Planned</strong> status so you can revisit this configuration later.
              Once the device is reachable via SSH, the network crawler will automatically transition it to <strong>Active</strong>.
            </p>

            <div className="flex gap-3 mt-2">
              <button className="btn btn-ghost btn-sm gap-1" onClick={() => setStep(2)}>
                <ChevronLeft className="h-4 w-4" />
                Back to Editor
              </button>
              <button className="btn btn-primary btn-sm gap-2" onClick={handleDownload}>
                <Download className="h-4 w-4" />
                Re-download
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => { setSelected(null); setConfig(''); setStep(1); }}
              >
                Onboard Another
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

