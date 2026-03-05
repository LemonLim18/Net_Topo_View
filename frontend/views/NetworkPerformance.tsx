import React, { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw, Activity, Server, Cpu,
  AlertTriangle, CheckCircle, BarChart3, Clock,
} from 'lucide-react';
import {
  AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { monitorApi, thresholdApi } from '../src/services/api';
import type { ThresholdAlert } from '../src/services/api';
import { Toast } from '../src/utils/swal';

// ───────────────────────── Types ─────────────────────────

interface DeviceSummary {
  hostname: string;
  mgmt_ip: string;
  status: string;
  vendor: string;
  model: string;
  os_version: string;
  last_polled: string;
  cpu: number | null;
  memory: number | null;
}

interface MetricPoint {
  time: string;
  device: string;
  field: string;
  value: number;
}

// ───────────────────────── Component ─────────────────────────

const NetworkPerformance: React.FC = () => {
  const [stackStatus, setStackStatus] = useState<string>('unknown');
  const [devices, setDevices] = useState<DeviceSummary[]>([]);
  const [alerts, setAlerts] = useState<ThresholdAlert[]>([]);
  const [cpuHistory, setCpuHistory] = useState<{ time: string; value: number }[]>([]);
  const [memHistory, setMemHistory] = useState<{ time: string; value: number }[]>([]);
  const [trafficHistory, setTrafficHistory] = useState<{ time: string; value: number }[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedDevice, setSelectedDevice] = useState<string>('');
  const [rangeMinutes, setRangeMinutes] = useState<number>(60);

  // ───── Data Fetchers ─────

  const fetchAll = useCallback(async () => {
    setIsLoading(true);
    try {
      const [statusRes, summaryRes, alertsRes] = await Promise.all([
        monitorApi.getStatus().catch(() => ({ status: 'error' })),
        monitorApi.getDeviceSummary().catch(() => ({ devices: [] })),
        thresholdApi.listAlerts('active', 20).catch(() => []),
      ]);

      setStackStatus((statusRes as any).status || 'error');
      setDevices((summaryRes as any).devices || []);
      setAlerts(alertsRes as ThresholdAlert[]);
    } catch {
      Toast.error('Load failed', 'Could not fetch monitoring data');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const [cpu, mem, traffic] = await Promise.all([
        monitorApi.getMetricHistory(selectedDevice || undefined, 'cpu', rangeMinutes),
        monitorApi.getMetricHistory(selectedDevice || undefined, 'memory', rangeMinutes),
        monitorApi.getMetricHistory(selectedDevice || undefined, 'interface_traffic', rangeMinutes),
      ]);

      setCpuHistory(formatTimeSeries((cpu as any).data || []));
      setMemHistory(formatTimeSeries((mem as any).data || []));
      setTrafficHistory(formatTimeSeries((traffic as any).data || []));
    } catch {
      // Silently handle — charts will show empty
    }
  }, [selectedDevice, rangeMinutes]);

  useEffect(() => { fetchAll(); }, [fetchAll]);
  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Auto-refresh every 30s
  useEffect(() => {
    const interval = setInterval(() => {
      fetchAll();
      fetchHistory();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchAll, fetchHistory]);

  const handleRegenerateConfig = async () => {
    setIsLoading(true);
    try {
      const res = await monitorApi.updateConfig();
      Toast.success('Config Updated', (res as any).message);
    } catch (e: any) {
      Toast.error('Failed', e.message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleEvaluate = async () => {
    try {
      const res = await thresholdApi.evaluate();
      Toast.success(
        'Evaluation Complete',
        `${res.evaluated} rules checked, ${res.alerts_created} alerts created`
      );
      fetchAll();
    } catch (e: any) {
      Toast.error('Evaluation Failed', e.message);
    }
  };

  // ───── Derived Stats ─────

  const upDevices = devices.filter(d => d.status === 'up').length;
  const activeAlerts = alerts.filter(a => a.status === 'active').length;
  const cpuDevices = devices.filter(d => d.cpu !== null);
  const avgCpu = cpuDevices.length > 0
    ? cpuDevices.reduce((s, d) => s + (d.cpu || 0), 0) / cpuDevices.length
    : 0;

  // ───── Render ─────

  return (
    <div className="p-6 space-y-6 h-full overflow-y-auto">

      {/* Header */}
      <div className="flex justify-between items-center flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Activity className="h-8 w-8 text-primary" />
            Telemetry &amp; Performance
          </h1>
          <p className="text-sm opacity-70 mt-1">
            Real-time metrics from InfluxDB/Telegraf + threshold alerts
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-sm btn-outline" onClick={fetchAll} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button className="btn btn-sm btn-outline" onClick={handleEvaluate}>
            <AlertTriangle className="h-4 w-4" />
            Evaluate Rules
          </button>
          <button className="btn btn-sm btn-primary" onClick={handleRegenerateConfig} disabled={isLoading}>
            <Server className="h-4 w-4" />
            Update Telegraf
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <KPICard icon={<Server className="h-5 w-5" />} label="Stack Status"
          value={stackStatus} color={stackStatus === 'healthy' ? 'text-success' : 'text-warning'} />
        <KPICard icon={<CheckCircle className="h-5 w-5 text-success" />} label="Devices Up"
          value={`${upDevices} / ${devices.length}`} color="text-success" />
        <KPICard icon={<Cpu className="h-5 w-5 text-info" />} label="Avg CPU"
          value={avgCpu > 0 ? `${avgCpu.toFixed(1)}%` : 'N/A'} color="text-info" />
        <KPICard icon={<AlertTriangle className="h-5 w-5 text-error" />} label="Active Alerts"
          value={String(activeAlerts)} color={activeAlerts > 0 ? 'text-error' : 'text-success'} />
      </div>

      {/* Controls row */}
      <div className="flex gap-3 items-center flex-wrap">
        <select className="select select-bordered select-sm"
          value={selectedDevice} onChange={e => setSelectedDevice(e.target.value)}>
          <option value="">All Devices</option>
          {devices.map(d => (
            <option key={d.hostname} value={d.mgmt_ip || d.hostname}>
              {d.hostname}
            </option>
          ))}
        </select>
        <select className="select select-bordered select-sm"
          value={rangeMinutes} onChange={e => setRangeMinutes(Number(e.target.value))}>
          <option value={15}>Last 15m</option>
          <option value={60}>Last 1h</option>
          <option value={360}>Last 6h</option>
          <option value={1440}>Last 24h</option>
        </select>
        <button className="btn btn-xs btn-ghost" onClick={fetchHistory}>
          <Clock className="h-3 w-3 mr-1" /> Reload Charts
        </button>
      </div>

      {/* Charts Row */}
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="CPU Utilization (%)" data={cpuHistory} color="#3b82f6" yUnit="%" />
        <ChartCard title="Memory Usage" data={memHistory} color="#8b5cf6" yUnit="" />
      </div>
      <div className="grid gap-4 lg:grid-cols-1">
        <ChartCard title="Interface Traffic (Octets)" data={trafficHistory} color="#10b981" yUnit="" wide />
      </div>

      {/* Device Table */}
      <div className="card bg-base-100 shadow border border-base-300">
        <div className="card-body p-4">
          <h2 className="card-title text-lg mb-3">
            <BarChart3 className="h-5 w-5" /> Device Health Overview
          </h2>
          <div className="overflow-x-auto">
            <table className="table table-sm table-zebra">
              <thead>
                <tr>
                  <th>Hostname</th>
                  <th>IP</th>
                  <th>Status</th>
                  <th>Vendor</th>
                  <th>Model</th>
                  <th>CPU %</th>
                  <th>Memory</th>
                  <th>Last Polled</th>
                </tr>
              </thead>
              <tbody>
                {devices.length === 0 ? (
                  <tr><td colSpan={8} className="text-center opacity-50">No devices found</td></tr>
                ) : devices.map(d => (
                  <tr key={d.hostname}>
                    <td className="font-mono text-sm">{d.hostname}</td>
                    <td className="font-mono text-sm">{d.mgmt_ip}</td>
                    <td>
                      {d.status === 'up' ? (
                        <span className="badge badge-success badge-sm">UP</span>
                      ) : (
                        <span className="badge badge-error badge-sm">{d.status?.toUpperCase()}</span>
                      )}
                    </td>
                    <td>{d.vendor}</td>
                    <td>{d.model}</td>
                    <td>
                      {d.cpu !== null ? (
                        <span className={d.cpu > 80 ? 'text-error font-bold' : d.cpu > 50 ? 'text-warning' : ''}>
                          {d.cpu}%
                        </span>
                      ) : <span className="opacity-30">—</span>}
                    </td>
                    <td>
                      {d.memory !== null ? (
                        <span className="font-mono text-sm">{formatBytes(d.memory)}</span>
                      ) : <span className="opacity-30">—</span>}
                    </td>
                    <td className="text-xs opacity-60">{d.last_polled ? new Date(d.last_polled).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Active Alerts */}
      {alerts.length > 0 && (
        <div className="card bg-base-100 shadow border border-error/30">
          <div className="card-body p-4">
            <h2 className="card-title text-lg text-error mb-3">
              <AlertTriangle className="h-5 w-5" /> Active Threshold Alerts
            </h2>
            <div className="space-y-2">
              {alerts.map(a => (
                <div key={a.id} className="alert alert-warning py-2">
                  <div>
                    <span className={`badge ${a.severity === 'critical' ? 'badge-error' : 'badge-warning'} badge-sm mr-2`}>
                      {a.severity}
                    </span>
                    <span className="font-mono text-sm">
                      {a.device}: {a.metric} = {a.value} (threshold: {a.threshold})
                    </span>
                    <span className="text-xs opacity-50 ml-2">{a.created_at}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// ───────────────────────── Sub-components ─────────────────────────

const KPICard: React.FC<{ icon: React.ReactNode; label: string; value: string; color?: string }> = ({
  icon, label, value, color = '',
}) => (
  <div className="card bg-base-100 shadow border border-base-300">
    <div className="card-body p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium opacity-70">{label}</span>
        {icon}
      </div>
      <div className={`text-2xl font-bold mt-1 ${color}`}>{value}</div>
    </div>
  </div>
);

const ChartCard: React.FC<{
  title: string;
  data: { time: string; value: number }[];
  color: string;
  yUnit?: string;
  wide?: boolean;
}> = ({ title, data, color, yUnit = '', wide }) => (
  <div className={`card bg-base-100 shadow border border-base-300 ${wide ? 'col-span-full' : ''}`}>
    <div className="card-body p-4">
      <h3 className="text-sm font-semibold mb-2">{title}</h3>
      {data.length === 0 ? (
        <div className="h-48 flex items-center justify-center opacity-30">
          <div className="text-center">
            <BarChart3 className="h-10 w-10 mx-auto mb-2" />
            <p className="text-sm">No data available</p>
            <p className="text-xs">Metrics will appear when InfluxDB reports data</p>
          </div>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={data}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
            <XAxis
              dataKey="time"
              tick={{ fontSize: 10 }}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fontSize: 10 }}
              tickFormatter={(v: number) => `${v}${yUnit}`}
              width={60}
            />
            <Tooltip
              formatter={(value: any) => [`${value}${yUnit}`, 'Value']}
              labelFormatter={(label: any) => `Time: ${label}`}
              contentStyle={{ 
                backgroundColor: 'rgba(15, 23, 42, 0.9)', 
                border: '1px solid rgba(255, 255, 255, 0.1)',
                borderRadius: '0.75rem',
                fontSize: '12px',
                backdropFilter: 'blur(8px)',
                boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.3)',
              }}
              itemStyle={{ color: '#e2e8f0' }}
              labelStyle={{ color: '#94a3b8', fontWeight: 'bold', marginBottom: '4px' }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={color}
              fill={color}
              fillOpacity={0.15}
              strokeWidth={2}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  </div>
);

// ───────────────────────── Helpers ─────────────────────────

function formatTimeSeries(points: MetricPoint[]): { time: string; value: number }[] {
  return points.map(p => ({
    time: p.time ? new Date(p.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '',
    value: p.value,
  }));
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

export default NetworkPerformance;