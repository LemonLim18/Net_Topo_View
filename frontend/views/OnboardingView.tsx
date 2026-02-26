import React, { useState, useEffect, useRef, useCallback } from 'react';
import { CheckCircle, Copy, Loader2, Terminal, Wifi, WifiOff, PackagePlus } from 'lucide-react';
import type { ViewId } from '../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface OnboardingParams {
  hostname: string;
  mgmt_ip: string;
  subnet_mask: string;
  gateway: string;
  mgmt_interface: string;
  vendor: string;
  ssh_user: string;
  ssh_password: string;
  port: number;
  timeout_minutes: number;
}

interface WsEvent {
  event: string;
  message?: string;
  attempts?: number;
}

const VENDOR_OPTIONS = [
  { value: 'cisco_ios',     label: 'Cisco IOS' },
  { value: 'cisco_xe',      label: 'Cisco IOS-XE' },
  { value: 'huawei_vrp',    label: 'Huawei VRP' },
  { value: 'juniper_junos', label: 'Juniper JunOS' },
];

const DEFAULT_PARAMS: OnboardingParams = {
  hostname:        '',
  mgmt_ip:         '',
  subnet_mask:     '255.255.255.0',
  gateway:         '',
  mgmt_interface:  'GigabitEthernet0/0',
  vendor:          'cisco_ios',
  ssh_user:        'admin',
  ssh_password:    '',
  port:            22,
  timeout_minutes: 10,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const OnboardingView: React.FC<{ setView: (id: ViewId) => void }> = ({ setView }) => {
  const [step, setStep]               = useState<1 | 2 | 3 | 4>(1);
  const [params, setParams]           = useState<OnboardingParams>(DEFAULT_PARAMS);
  const [generatedCfg, setGeneratedCfg] = useState('');
  const [deviceId, setDeviceId]       = useState<number | null>(null);
  const [log, setLog]                 = useState<string[]>([]);
  const [pollStatus, setPollStatus]   = useState<string>('polling');
  const [copied, setCopied]           = useState(false);
  const [loadingGenerate, setLoadingGenerate] = useState(false);
  const [loadingPoll, setLoadingPoll] = useState(false);
  const [error, setError]             = useState('');
  const wsRef                         = useRef<WebSocket | null>(null);
  const logEndRef                     = useRef<HTMLDivElement | null>(null);

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [log]);

  // Cleanup WebSocket on unmount
  useEffect(() => () => { wsRef.current?.close(); }, []);

  // ------------------------------------------------------------------
  // Step helpers
  // ------------------------------------------------------------------

  const appendLog = useCallback((msg: string) => {
    const ts = new Date().toLocaleTimeString();
    setLog(prev => [...prev, `[${ts}] ${msg}`]);
  }, []);

  const handleGenerate = async () => {
    setError('');
    // Basic validation
    if (!params.hostname || !params.mgmt_ip || !params.gateway || !params.ssh_password) {
      setError('Hostname, Management IP, Gateway, and SSH Password are required.');
      return;
    }
    setLoadingGenerate(true);
    try {
      const res = await fetch('/api/v1/onboarding/generate', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(params),
      });
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setGeneratedCfg(data.config);
      setStep(2);
    } catch (e: any) {
      setError(e.message ?? 'Failed to generate config');
    } finally {
      setLoadingGenerate(false);
    }
  };

  const handleCopy = async () => {
    await navigator.clipboard.writeText(generatedCfg);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleStartPoll = async () => {
    setError('');
    setLog([]);
    setPollStatus('polling');
    setLoadingPoll(true);

    try {
      const res = await fetch('/api/v1/onboarding/start-poll', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(params),
      });
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setDeviceId(data.device_id);
      setStep(3);

      // Open WebSocket
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const wsUrl = `${protocol}://${window.location.host}/api/v1/onboarding/ws/${data.device_id}?timeout_minutes=${params.timeout_minutes}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = (evt) => {
        try {
          const event: WsEvent = JSON.parse(evt.data);
          if (event.event === 'ping') return;
          if (event.message) appendLog(event.message);
          if (event.event === 'registered') {
            setPollStatus('registered');
            setStep(4);
          } else if (event.event === 'timeout') {
            setPollStatus('timeout');
          } else if (event.event === 'connected') {
            setPollStatus('connected');
          }
        } catch {/* ignore parse errors */}
      };

      ws.onerror = () => appendLog('WebSocket error — falling back to polling.');
      ws.onclose = () => {
        if (pollStatus !== 'registered') {
          appendLog('WebSocket closed.');
        }
      };
    } catch (e: any) {
      setError(e.message ?? 'Failed to start polling');
    } finally {
      setLoadingPoll(false);
    }
  };

  const handleRetry = () => {
    wsRef.current?.close();
    setStep(1);
    setLog([]);
    setError('');
    setDeviceId(null);
  };

  // ------------------------------------------------------------------
  // Field helper
  // ------------------------------------------------------------------

  const field = (
    label: string,
    key: keyof OnboardingParams,
    type = 'text',
    placeholder = '',
  ) => (
    <div className="form-control w-full">
      <label className="label py-1">
        <span className="label-text text-sm font-medium">{label}</span>
      </label>
      <input
        type={type}
        placeholder={placeholder}
        className="input input-bordered input-sm w-full"
        value={params[key] as string | number}
        onChange={e => setParams(prev => ({
          ...prev,
          [key]: type === 'number' ? Number(e.target.value) : e.target.value,
        }))}
      />
    </div>
  );

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  const stepLabels = ['Device Details', 'Console Config', 'Waiting…', 'Done'];

  return (
    <div className="p-6 max-w-3xl mx-auto flex flex-col gap-6">

      {/* Header */}
      <div className="flex items-center gap-3">
        <PackagePlus className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-xl font-bold">Onboard New Device</h1>
          <p className="text-sm opacity-60">Day-0 bootstrap wizard — generates vendor config & auto-registers device.</p>
        </div>
      </div>

      {/* Steps */}
      <ul className="steps steps-horizontal w-full text-xs">
        {stepLabels.map((label, i) => (
          <li key={label} className={`step ${step > i ? 'step-primary' : ''}`}>{label}</li>
        ))}
      </ul>

      {/* Global error */}
      {error && (
        <div className="alert alert-error">
          <span>{error}</span>
        </div>
      )}

      {/* ----------------------------------------------------------------
          Step 1 — Device Details
      ---------------------------------------------------------------- */}
      {step === 1 && (
        <div className="card bg-base-100 shadow">
          <div className="card-body gap-3">
            <h2 className="card-title text-base">Device Details</h2>

            <div className="grid grid-cols-2 gap-3">
              {field('Hostname *', 'hostname', 'text', 'e.g. CORE-RTR-01')}
              {field('Management IP *', 'mgmt_ip', 'text', 'e.g. 192.168.100.10')}
              {field('Subnet Mask', 'subnet_mask', 'text', '255.255.255.0')}
              {field('Default Gateway *', 'gateway', 'text', 'e.g. 192.168.100.1')}
              {field('Management Interface', 'mgmt_interface', 'text', 'GigabitEthernet0/0')}

              <div className="form-control w-full">
                <label className="label py-1">
                  <span className="label-text text-sm font-medium">Vendor</span>
                </label>
                <select
                  className="select select-bordered select-sm w-full"
                  value={params.vendor}
                  onChange={e => setParams(prev => ({ ...prev, vendor: e.target.value }))}
                >
                  {VENDOR_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

              {field('SSH Username *', 'ssh_user', 'text', 'admin')}
              {field('SSH Password *', 'ssh_password', 'password', '••••••••')}
              {field('SSH Port', 'port', 'number', '22')}
              {field('Poll Timeout (min)', 'timeout_minutes', 'number', '10')}
            </div>

            <div className="card-actions justify-end mt-2">
              <button
                className={`btn btn-primary btn-sm gap-2 ${loadingGenerate ? 'loading' : ''}`}
                onClick={handleGenerate}
                disabled={loadingGenerate}
              >
                {loadingGenerate ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Generate Config
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ----------------------------------------------------------------
          Step 2 — Config block
      ---------------------------------------------------------------- */}
      {step === 2 && (
        <div className="card bg-base-100 shadow">
          <div className="card-body gap-3">
            <h2 className="card-title text-base flex items-center gap-2">
              <Terminal className="h-4 w-4" />
              Console Config for <span className="badge badge-primary badge-sm ml-1">{params.hostname}</span>
            </h2>

            <div className="alert alert-info text-sm">
              Paste the block below into your device console, wait for it to apply, then click <strong>Continue</strong>.
            </div>

            <div className="relative">
              <pre className="bg-base-300 rounded-lg p-4 text-xs overflow-x-auto whitespace-pre leading-5 max-h-80 overflow-y-auto">
                {generatedCfg}
              </pre>
              <button
                className="btn btn-xs btn-ghost absolute top-2 right-2 gap-1"
                onClick={handleCopy}
              >
                <Copy className="h-3 w-3" />
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>

            <div className="card-actions justify-between mt-2">
              <button className="btn btn-ghost btn-sm" onClick={() => setStep(1)}>← Back</button>
              <button
                className={`btn btn-primary btn-sm gap-2 ${loadingPoll ? 'loading' : ''}`}
                onClick={handleStartPoll}
                disabled={loadingPoll}
              >
                {loadingPoll ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Continue — Start Polling
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ----------------------------------------------------------------
          Step 3 — Waiting for SSH
      ---------------------------------------------------------------- */}
      {step === 3 && (
        <div className="card bg-base-100 shadow">
          <div className="card-body gap-4">
            <h2 className="card-title text-base flex items-center gap-2">
              {pollStatus === 'timeout' ? (
                <WifiOff className="h-5 w-5 text-error" />
              ) : (
                <Loader2 className="h-5 w-5 text-primary animate-spin" />
              )}
              {pollStatus === 'timeout'
                ? 'Timed Out'
                : `Waiting for SSH on ${params.mgmt_ip}…`}
            </h2>

            {pollStatus === 'timeout' && (
              <div className="alert alert-error text-sm">
                Device did not respond within {params.timeout_minutes} minutes.
                Check the console config was applied and the device is reachable.
              </div>
            )}

            {/* Attempt log */}
            <div className="bg-base-300 rounded-lg p-3 h-48 overflow-y-auto text-xs font-mono flex flex-col gap-0.5">
              {log.length === 0 && <span className="opacity-50">Connecting…</span>}
              {log.map((line, i) => (
                <span key={i} className="opacity-80">{line}</span>
              ))}
              <div ref={logEndRef} />
            </div>

            <div className="card-actions justify-between">
              <button className="btn btn-ghost btn-sm" onClick={handleRetry}>← Start Over</button>
              {pollStatus === 'timeout' && (
                <button className="btn btn-warning btn-sm" onClick={handleRetry}>Retry</button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ----------------------------------------------------------------
          Step 4 — Success
      ---------------------------------------------------------------- */}
      {step === 4 && (
        <div className="card bg-base-100 shadow">
          <div className="card-body gap-4 items-center text-center">
            <CheckCircle className="h-14 w-14 text-success" />
            <h2 className="card-title text-lg">Device Registered!</h2>

            <div className="flex gap-6 mt-1">
              <div>
                <div className="text-xs opacity-60 mb-0.5">Hostname</div>
                <div className="badge badge-lg badge-primary">{params.hostname}</div>
              </div>
              <div>
                <div className="text-xs opacity-60 mb-0.5">Management IP</div>
                <div className="badge badge-lg badge-secondary">{params.mgmt_ip}</div>
              </div>
              {deviceId && (
                <div>
                  <div className="text-xs opacity-60 mb-0.5">Device ID</div>
                  <div className="badge badge-lg badge-neutral">#{deviceId}</div>
                </div>
              )}
            </div>

            <div className="flex gap-3 mt-4">
              <button
                className="btn btn-primary btn-sm gap-2"
                onClick={() => setView('netbox_devices')}
              >
                <Wifi className="h-4 w-4" />
                View in Device Inventory
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setParams(DEFAULT_PARAMS);
                  setGeneratedCfg('');
                  setDeviceId(null);
                  setLog([]);
                  setStep(1);
                }}
              >
                Onboard Another
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default OnboardingView;
