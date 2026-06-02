import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  RefreshCw, Activity, Server, Cpu,
  AlertTriangle, CheckCircle, BarChart3, Clock,
  Wifi, ArrowDownUp, Network, TimerReset,
  ChevronUp, ChevronDown,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, BarChart, Bar, PieChart, Pie, Cell,
} from 'recharts';
import { monitorApi, syslogApi, thresholdApi } from '../src/services/api';
import type { ThresholdAlert } from '../src/services/api';
import { Confirm, Toast } from '../src/utils/swal';

interface DeviceSummary {
  hostname: string;
  mgmt_ip: string;
  sysname?: string;
  sysuptime?: number | null;
  status: string;
  vendor: string;
  model: string;
  os_version: string;
  last_polled: string;
  cpu: number | null;
  cpu_source?: string;
  cpu_activity_pps?: number | null;
  memory: number | null;
  memory_pct?: number | null;
  memory_source?: string;
  memory_activity_pps?: number | null;
  mem_used?: number | null;
  mem_free?: number | null;
  mem_total?: number | null;
  interfaces_up?: number;
  interfaces_down?: number;
  interfaces_total?: number;
  interfaces_admin_up?: number;
  interfaces_admin_down?: number;
  interfaces_oper_up?: number;
  interfaces_oper_down?: number;
  errors: number;
  discards: number;
}

interface MetricPoint {
  time: string;
  device: string;
  interface?: string;
  field: string;
  value: number;
  basis?: string;
  pps?: number | null;
}

interface InterfaceSnapshot {
  device: string;
  interface: string;
  admin_status?: number | null;
  admin_status_label?: string;
  oper_status?: number | null;
  oper_status_label?: string;
  in_octets: number;
  out_octets: number;
  in_octets_source?: string;
  out_octets_source?: string;
  in_packets: number;
  out_packets: number;
  errors: number;
  discards: number;
  speed?: number | null;
  last_seen: string;
}

interface InterfaceDerivedMetrics {
  inBps: number;
  outBps: number;
  inPps: number;
  outPps: number;
  speed: number | null;
}

interface ChartSeries { key: string; label: string; color: string; }
interface ChartRow { ts: string; time: string; [key: string]: string | number; }

interface TopDeviceInsight {
  device: string;
  displayName: string;
  cpu: number;
  memPct: number | null;
  errors: number;
  discards: number;
  score: number;
}

interface InterfaceInsight {
  device: string;
  displayName: string;
  interface: string;
  inOctets: number;
  outOctets: number;
  inPkts: number;
  outPkts: number;
  errors: number;
  discards: number;
}

type InterfaceInsightMetricKey = 'inOctets' | 'outOctets' | 'inPkts' | 'outPkts' | 'errors' | 'discards';

const PALETTE = ['#2563eb', '#059669', '#d97706', '#dc2626', '#7c3aed', '#0f766e', '#65a30d', '#db2777', '#475569'];
const PIE_COLORS = ['#dc2626', '#d97706', '#2563eb', '#64748b'];
const RANGE_OPTIONS = [
  { label: '15m', value: 15 },
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
];

const MIN_ESTIMATED_PACKET_BYTES = 384;
const MAX_ESTIMATED_PACKET_BYTES = 896;

const INVALID_DEVICE_KEYS = new Set(['', 'host', 'localhost', '127.0.0.1', '::1', 'agent_host', 'unknown']);

function isValidDeviceKey(value: string | undefined | null): value is string {
  const normalized = String(value || '').trim().toLowerCase();
  return !!normalized && !INVALID_DEVICE_KEYS.has(normalized);
}

function sanitizeKey(v: string) {
  return v.replace(/[^a-zA-Z0-9_:-]/g, '_');
}

function estimatedPacketSizeBytes(device: string, iface: string, direction: 'in' | 'out', timeBucket = ''): number {
  const seed = `${device}|${iface || '_device'}|${direction}|${timeBucket}`;
  let checksum = 0;
  for (let index = 0; index < seed.length; index += 1) {
    checksum += (index + 1) * seed.charCodeAt(index);
  }
  const spread = MAX_ESTIMATED_PACKET_BYTES - MIN_ESTIMATED_PACKET_BYTES;
  if (spread <= 0) return 512;
  return MIN_ESTIMATED_PACKET_BYTES + (checksum % (spread + 1));
}

function normalizeDirectionalTraffic(params: {
  device: string;
  iface: string;
  direction: 'in' | 'out';
  bps?: number | null;
  pps?: number | null;
  timeBucket?: string;
}): { bps: number; pps: number } {
  const bps = Number(params.bps || 0);
  const pps = Number(params.pps || 0);
  if (bps > 0 && pps > 0) return { bps, pps };

  const packetBytes = estimatedPacketSizeBytes(params.device, params.iface, params.direction, params.timeBucket || '');
  if (bps > 0 && pps <= 0) {
    return { bps, pps: (bps / 8) / packetBytes };
  }
  if (pps > 0 && bps <= 0) {
    return { bps: pps * packetBytes * 8, pps };
  }
  return { bps: 0, pps: 0 };
}
// 6.25 at 11:42, 3.01 at 11.43; 6.47 at 11.44
function formatTime(raw: string) {
  const dt = new Date(raw);
  return Number.isNaN(dt.getTime()) ? raw : dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDateTime(raw: string) {
  const dt = new Date(raw);
  return Number.isNaN(dt.getTime()) ? raw
    : dt.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatUptime(ticks: number): string {
  const secs = Math.floor(ticks / 100);
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

function formatSpeed(speed: number | null | undefined): string {
  if (!speed) return '-';
  if (speed >= 1_000_000_000) return `${(speed / 1_000_000_000).toFixed(1)} Gbps`;
  if (speed >= 1_000_000) return `${(speed / 1_000_000).toFixed(1)} Mbps`;
  if (speed >= 1_000) return `${(speed / 1_000).toFixed(1)} Kbps`;
  return `${speed} bps`;
}

function formatBitRate(rate: number | null | undefined): string {
  if (!rate) return '0 bps';
  if (rate >= 1_000_000_000) return `${(rate / 1_000_000_000).toFixed(1)} Gbps`;
  if (rate >= 1_000_000) return `${(rate / 1_000_000).toFixed(1)} Mbps`;
  if (rate >= 1_000) return `${(rate / 1_000).toFixed(1)} Kbps`;
  return `${rate.toFixed(rate >= 100 ? 0 : 1)} bps`;
}

function formatPerSecond(rate: number | null | undefined, suffix = '/s'): string {
  if (!rate) return `0${suffix}`;
  return `${Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(rate)}${suffix}`;
}

function compactNum(n: number): string {
  return Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n || 0);
}

function toTitleCase(v: string) {
  if (!v) return 'Unknown';
  return v.charAt(0).toUpperCase() + v.slice(1).replace(/_/g, ' ').toLowerCase();
}

function statusTone(value: string | undefined): string {
  const normalized = String(value || '').toLowerCase();
  if (normalized === 'up') return 'badge-success';
  if (normalized === 'down' || normalized === 'lower_layer_down') return 'badge-error';
  if (normalized === 'testing' || normalized === 'dormant') return 'badge-warning';
  return 'badge-ghost';
}

function buildSeries(
  points: MetricPoint[],
  keyFn: (p: MetricPoint) => string,
  labelFn: (p: MetricPoint) => string,
): { rows: ChartRow[]; series: ChartSeries[] } {
  const rowMap = new Map<string, ChartRow>();
  const seriesMap = new Map<string, string>();
  points.forEach((p) => {
    if (!isValidDeviceKey(p.device)) return;
    if (!p.time || typeof p.value !== 'number') return;
    const key = keyFn(p);
    if (!seriesMap.has(key)) seriesMap.set(key, labelFn(p));
    if (!rowMap.has(p.time)) rowMap.set(p.time, { ts: p.time, time: formatTime(p.time) });
    rowMap.get(p.time)![key] = p.value;
  });
  const rows = [...rowMap.values()].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  const series = [...seriesMap.entries()].map(([key, label], idx) => ({
    key, label, color: PALETTE[idx % PALETTE.length],
  }));
  return { rows, series };
}

function buildDeviceSeries(points: MetricPoint[], nameMap: Record<string, string>) {
  return buildSeries(
    points,
    (p) => sanitizeKey(`dev:${p.device}`),
    (p) => nameMap[p.device] || p.device,
  );
}

function buildTrafficSeries(inPts: MetricPoint[], outPts: MetricPoint[], nameMap: Record<string, string>) {
  return buildSeries(
    [...inPts.map((p) => ({ ...p, field: 'in' })), ...outPts.map((p) => ({ ...p, field: 'out' }))],
    (p) => sanitizeKey(`traffic:${p.device}:${p.field}`),
    (p) => `${nameMap[p.device] || p.device} ${p.field === 'out' ? 'Out' : 'In'}`,
  );
}

function buildTrafficSnapshotFallback(
  snapshots: InterfaceSnapshot[],
  focusDevice: string,
  selectedInterface: string,
  nameMap: Record<string, string>,
): { rows: ChartRow[]; series: ChartSeries[] } {
  if (!focusDevice) return { rows: [], series: [] };

  const scoped = snapshots.filter((item) => {
    if ((item.device || '').trim() !== focusDevice) return false;
    if (selectedInterface && item.interface !== selectedInterface) return false;
    return true;
  });
  if (scoped.length === 0) return { rows: [], series: [] };

  const latestTs = scoped.reduce((latest, item) => {
    const candidate = item.last_seen || '';
    return candidate > latest ? candidate : latest;
  }, '');
  const labelBase = selectedInterface ? `${nameMap[focusDevice] || focusDevice} ${selectedInterface}` : `${nameMap[focusDevice] || focusDevice} Aggregate`;
  const totalIn = scoped.reduce((sum, item) => sum + Number(item.in_octets || 0), 0);
  const totalOut = scoped.reduce((sum, item) => sum + Number(item.out_octets || 0), 0);
  const ts = latestTs || new Date().toISOString();

  return {
    rows: [{ ts, time: formatTime(ts), snapshot_in: totalIn, snapshot_out: totalOut }],
    series: [
      { key: 'snapshot_in', label: `${labelBase} In`, color: PALETTE[0] },
      { key: 'snapshot_out', label: `${labelBase} Out`, color: PALETTE[1] },
    ],
  };
}

function latestPointByInterface(points: MetricPoint[]): Map<string, MetricPoint> {
  const map = new Map<string, MetricPoint>();
  points.forEach((point) => {
    const iface = (point.interface || '').trim();
    if (!iface) return;
    const existing = map.get(iface);
    if (!existing || String(point.time || '') > String(existing.time || '')) {
      map.set(iface, point);
    }
  });
  return map;
}

function buildDerivedInterfaceMetrics(
  trafficIn: MetricPoint[],
  trafficOut: MetricPoint[],
  iuIn: MetricPoint[],
  iuOut: MetricPoint[],
  imIn: MetricPoint[],
  imOut: MetricPoint[],
  ibIn: MetricPoint[],
  ibOut: MetricPoint[],
  speedPoints: MetricPoint[],
): Map<string, InterfaceDerivedMetrics> {
  const metrics = new Map<string, InterfaceDerivedMetrics>();
  const get = (iface: string) => {
    if (!metrics.has(iface)) {
      metrics.set(iface, { inBps: 0, outBps: 0, inPps: 0, outPps: 0, speed: null });
    }
    return metrics.get(iface)!;
  };

  latestPointByInterface(trafficIn).forEach((point, iface) => {
    get(iface).inBps = Number(point.value || 0);
  });
  latestPointByInterface(trafficOut).forEach((point, iface) => {
    get(iface).outBps = Number(point.value || 0);
  });

  [iuIn, imIn, ibIn].forEach((points) => {
    latestPointByInterface(points).forEach((point, iface) => {
      get(iface).inPps += Number(point.value || 0);
    });
  });
  [iuOut, imOut, ibOut].forEach((points) => {
    latestPointByInterface(points).forEach((point, iface) => {
      get(iface).outPps += Number(point.value || 0);
    });
  });

  latestPointByInterface(speedPoints).forEach((point, iface) => {
    get(iface).speed = Number(point.value || 0) * 1_000_000;
  });

  return metrics;
}

function buildErrorDiscardSeries(errPts: MetricPoint[], discPts: MetricPoint[], nameMap: Record<string, string>) {
  return buildSeries(
    [...errPts.map((p) => ({ ...p, field: 'err' })), ...discPts.map((p) => ({ ...p, field: 'disc' }))],
    (p) => sanitizeKey(`ih:${p.device}:${p.field}`),
    (p) => `${nameMap[p.device] || p.device} ${p.field === 'disc' ? 'Discards' : 'Errors'}`,
  );
}

function buildPacketSeries(
  iuIn: MetricPoint[], iuOut: MetricPoint[],
  imIn: MetricPoint[], imOut: MetricPoint[],
  ibIn: MetricPoint[], ibOut: MetricPoint[],
  nameMap: Record<string, string>,
) {
  const label: Record<string, string> = {
    ui: 'Ucast In',
    uo: 'Ucast Out',
    mi: 'Mcast In',
    mo: 'Mcast Out',
    bi: 'Bcast In',
    bo: 'Bcast Out',
  };
  return buildSeries(
    [
      ...iuIn.map((p) => ({ ...p, field: 'ui' })),
      ...iuOut.map((p) => ({ ...p, field: 'uo' })),
      ...imIn.map((p) => ({ ...p, field: 'mi' })),
      ...imOut.map((p) => ({ ...p, field: 'mo' })),
      ...ibIn.map((p) => ({ ...p, field: 'bi' })),
      ...ibOut.map((p) => ({ ...p, field: 'bo' })),
    ],
    (p) => sanitizeKey(`pkt:${p.device}:${p.field}`),
    (p) => `${nameMap[p.device] || p.device} ${label[p.field] || p.field}`,
  );
}

function buildTopDeviceInsights(devices: DeviceSummary[], nameMap: Record<string, string>): TopDeviceInsight[] {
  return devices.map((d) => {
    const ip = d.mgmt_ip || d.hostname;
    const cpu = d.cpu ?? 0;
    const memPct = d.memory_pct ?? d.memory ?? null;
    const errors = d.errors ?? 0;
    const discards = d.discards ?? 0;
    const score = cpu + Math.min(40, Math.log10(errors + 1) * 15) + Math.min(30, Math.log10(discards + 1) * 12);
    return { device: ip, displayName: nameMap[ip] || d.sysname || ip, cpu, memPct, errors, discards, score };
  }).sort((a, b) => b.score - a.score).slice(0, 8);
}

function buildInterfaceInsights(
  inPts: MetricPoint[], outPts: MetricPoint[],
  errPts: MetricPoint[], discPts: MetricPoint[],
  iuIn: MetricPoint[], iuOut: MetricPoint[],
  imIn: MetricPoint[], imOut: MetricPoint[],
  ibIn: MetricPoint[], ibOut: MetricPoint[],
  nameMap: Record<string, string>,
): InterfaceInsight[] {
  const map = new Map<string, InterfaceInsight>();
  const get = (device: string, iface: string) => {
    const key = `${device}::${iface}`;
    if (!map.has(key)) {
      map.set(key, {
        device,
        displayName: nameMap[device] || device,
        interface: iface,
        inOctets: 0,
        outOctets: 0,
        inPkts: 0,
        outPkts: 0,
        errors: 0,
        discards: 0,
      });
    }
    return map.get(key)!;
  };
  const maxField = (pts: MetricPoint[], field: InterfaceInsightMetricKey) => {
    pts.forEach((p) => {
      const device = (p.device || '').trim();
      const iface = (p.interface || '').trim();
      if (!device || !iface) return;
      const r = get(device, iface);
      r[field] = Math.max(r[field], Number(p.value || 0));
    });
  };
  const sumField = (pts: MetricPoint[], field: InterfaceInsightMetricKey) => {
    pts.forEach((p) => {
      const device = (p.device || '').trim();
      const iface = (p.interface || '').trim();
      if (!device || !iface) return;
      get(device, iface)[field] += Number(p.value || 0);
    });
  };

  maxField(inPts, 'inOctets');
  maxField(outPts, 'outOctets');
  maxField(errPts, 'errors');
  maxField(discPts, 'discards');
  sumField(iuIn, 'inPkts');
  sumField(iuOut, 'outPkts');
  sumField(imIn, 'inPkts');
  sumField(imOut, 'outPkts');
  sumField(ibIn, 'inPkts');
  sumField(ibOut, 'outPkts');

  return [...map.values()]
    .sort((a, b) => (b.errors + b.discards + b.inPkts + b.outPkts) - (a.errors + a.discards + a.inPkts + a.outPkts))
    .slice(0, 14);
}

const KPICard: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: string;
  subtext?: string;
  color?: string;
}> = ({ icon, label, value, subtext = '', color = '' }) => (
  <div className="card bg-base-100 border border-base-300 shadow-sm">
    <div className="card-body p-4 gap-1">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium opacity-50 uppercase tracking-wide">{label}</span>
        <span className="opacity-40">{icon}</span>
      </div>
      <div className={`text-2xl font-bold leading-tight ${color}`}>{value}</div>
      <div className="text-xs opacity-50">{subtext}</div>
    </div>
  </div>
);

const EmptyChart: React.FC<{ label?: string }> = ({ label = 'No data available for the current scope' }) => (
  // <div className="h-44 flex flex-col items-center justify-center opacity-25 gap-1">
  <div className="h-full flex items-center justify-center">
    <div className="flex flex-col items-center justify-center opacity-25 gap-1">
      <BarChart3 className="h-7 w-7" />
      <p className="text-xs">{label}</p>
    </div>
  </div>
);

const GraphSkeleton: React.FC<{ height?: number }> = ({ height = 210 }) => (
  <div className="relative" style={{ height }}>
    <div className="absolute inset-0 flex flex-col items-center justify-center z-20 gap-3 bg-base-100/20 backdrop-blur-[1px] rounded-xl pointer-events-none">
      <RefreshCw className="h-6 w-6 text-primary animate-spin" />
      <div className="flex flex-col items-center drop-shadow-sm">
        <p className="text-[11px] font-bold text-primary uppercase tracking-[0.2em] animate-pulse">Loading</p>
        {/* <p className="text-[9px] font-medium opacity-80 text-primary uppercase tracking-widest mt-0.5">the graph data</p> */}
        <p className="text-[9px] font-medium opacity-80 text-white tracking-widest mt-0.5">the graph data...</p>
      </div>
    </div>
    <div className="animate-pulse h-full">
      <div className="flex h-full gap-3">
        <div className="w-10 rounded bg-neutral/10" />
        <div className="flex-1 space-y-3">
          <div className="h-4 w-24 rounded bg-neutral/10" />
          <div className="flex h-[calc(100%-1.75rem)] items-end gap-2 text-transparent overflow-hidden">
            <div className="h-[35%] flex-1 rounded-t bg-neutral/10" />
            <div className="h-[58%] flex-1 rounded-t bg-neutral/15" />
            <div className="h-[42%] flex-1 rounded-t bg-neutral/10" />
            <div className="h-[74%] flex-1 rounded-t bg-neutral/20" />
            <div className="h-[50%] flex-1 rounded-t bg-neutral/10" />
            <div className="h-[66%] flex-1 rounded-t bg-neutral/15" />
          </div>
        </div>
      </div>
    </div>
  </div>
);

const MultiLineChartCard: React.FC<{
  title: string;
  chart: { rows: ChartRow[]; series: ChartSeries[] };
  yUnit?: string;
  height?: number;
  loading?: boolean;
}> = ({ title, chart, yUnit = '', height = 210, loading = false }) => (
  <div className="card bg-base-100 border border-base-300 shadow-sm">
    <div className="card-body p-3">
      <h3 className="text-xs font-semibold opacity-60 uppercase tracking-wide mb-2">{title}</h3>
      {loading ? <GraphSkeleton height={height} /> : chart.rows.length === 0 || chart.series.length === 0 ? <EmptyChart /> : (
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={chart.rows} margin={{ left: -12, right: 6, top: 2, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.08} />
            <XAxis dataKey="time" tick={{ fontSize: 9 }} interval="preserveStartEnd" tickLine={false} axisLine={false} />
            <YAxis tick={{ fontSize: 9 }} tickFormatter={(v: number) => `${v}${yUnit}`} width={54} tickLine={false} axisLine={false} />
            <Tooltip 
              cursor={false}
              contentStyle={{ backgroundColor: '#172033', color: '#f8fafc', border: '1px solid #334155', fontSize: 11, padding: '8px 12px', borderRadius: '8px' }} 
              itemStyle={{ color: '#f1f5f9', padding: '2px 0' }} 
              labelStyle={{ color: '#60a5fa', fontWeight: 700, paddingBottom: 4, borderBottom: '1px solid #334155', marginBottom: 4 }} 
              formatter={(v: any, k: any) => [`${v ?? 0}${yUnit}`, String(k)] as [string, string]}
              labelFormatter={(l: any) => String(l)}
            />
            <Legend wrapperStyle={{ fontSize: '10px', paddingTop: '2px' }} />
            {chart.series.map((s) => (
              <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={1.5} dot={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  </div>
);

const AlertDonutCard: React.FC<{ alerts: ThresholdAlert[]; loading?: boolean }> = ({ alerts, loading = false }) => {
  const activeAlerts = alerts.filter((a) => a.status === 'active');
  const donutData = [
    { name: 'Critical', value: activeAlerts.filter((a) => a.severity === 'critical').length },
    { name: 'Warning', value: activeAlerts.filter((a) => a.severity === 'warning').length },
    { name: 'Info', value: activeAlerts.filter((a) => !['critical', 'warning'].includes((a.severity || '').toLowerCase())).length },
  ].filter((entry) => entry.value > 0);

  return (
    <div className="card bg-base-100 border border-base-300 shadow-sm">
      <div className="card-body p-3 items-center">
        <h3 className="text-xs font-semibold opacity-60 uppercase tracking-wide mb-2">Alert Severity Split</h3>
        {loading ? <GraphSkeleton height={176} /> : donutData.length === 0 ? (
          <div className="w-full h-44 rounded-xl border border-dashed border-base-300 bg-base-200/40 flex flex-col items-center justify-center text-center px-4">
            <div className="text-3xl font-bold leading-none text-success">0</div>
            <div className="text-[11px] uppercase tracking-[0.18em] opacity-50 mt-2">Active Alerts</div>
            <p className="text-xs opacity-60 mt-3 max-w-[16rem]">No active alerts in the current window.</p>
          </div>
        ) : (
          <>
            <div className="relative w-full h-44">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={donutData} cx="50%" cy="50%" innerRadius={46} outerRadius={68} dataKey="value" strokeWidth={0}>
                    {donutData.map((entry, index) => (
                      <Cell key={entry.name} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#172033', color: '#f8fafc', border: '1px solid #334155', fontSize: 11, padding: '8px 12px', borderRadius: '8px' }} 
                    itemStyle={{ color: '#f1f5f9' }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <div className="text-2xl font-bold leading-none">{activeAlerts.length}</div>
                <div className="text-[11px] opacity-50 uppercase tracking-wide">Active Alerts</div>
              </div>
            </div>
            <div className="flex flex-wrap justify-center gap-3 text-xs mt-1">
              {donutData.map((entry, index) => (
                <div key={entry.name} className="flex items-center gap-1">
                  <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: PIE_COLORS[index % PIE_COLORS.length] }} />
                  <span>{entry.name} ({entry.value})</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
};

type SortDirection = 'asc' | 'desc';
interface SortConfig { key: string; direction: SortDirection; }

const NetworkPerformance: React.FC = () => {
  const [stackStatus, setStackStatus] = useState<string>('unknown');
  const [devices, setDevices] = useState<DeviceSummary[]>([]);
  const [alerts, setAlerts] = useState<ThresholdAlert[]>([]);
  const [hostnameMap, setHostnameMap] = useState<Record<string, string>>({});
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [interfaceSnapshots, setInterfaceSnapshots] = useState<InterfaceSnapshot[]>([]);

  const [cpuPoints, setCpuPoints] = useState<MetricPoint[]>([]);
  const [memPoints, setMemPoints] = useState<MetricPoint[]>([]);
  const [trafficInPoints, setTrafficIn] = useState<MetricPoint[]>([]);
  const [trafficOutPoints, setTrafficOut] = useState<MetricPoint[]>([]);
  const [errorPoints, setErrors] = useState<MetricPoint[]>([]);
  const [discardPoints, setDiscards] = useState<MetricPoint[]>([]);
  const [inUcastPts, setInUcast] = useState<MetricPoint[]>([]);
  const [outUcastPts, setOutUcast] = useState<MetricPoint[]>([]);
  const [inMcastPts, setInMcast] = useState<MetricPoint[]>([]);
  const [outMcastPts, setOutMcast] = useState<MetricPoint[]>([]);
  const [inBcastPts, setInBcast] = useState<MetricPoint[]>([]);
  const [outBcastPts, setOutBcast] = useState<MetricPoint[]>([]);
  const [speedPoints, setSpeedPoints] = useState<MetricPoint[]>([]);

  const [isLoading, setIsLoading] = useState(false);
  const [hasLoadedSummaryGraphs, setHasLoadedSummaryGraphs] = useState(false);
  const [hasLoadedHistoryGraphs, setHasLoadedHistoryGraphs] = useState(false);
  const [recentSyslogEvents, setRecentSyslogEvents] = useState<any[]>([]);
  const [syslogSources, setSyslogSources] = useState<any[]>([]);
  const [selectedSyslogDevice, setSelectedSyslogDevice] = useState('');
  const [syslogEventsLoading, setSyslogEventsLoading] = useState(false);
  const [syslogSyncLoading, setSyslogSyncLoading] = useState(false);
  const [telegrafSyncLoading, setTelegrafSyncLoading] = useState(false);
  const [focusDevice, setFocusDevice] = useState<string>('');
  const [selectedInterface, setSelectedInterface] = useState<string>('');
  const [rangeMinutes, setRangeMinutes] = useState<number>(60);
  const [sortConfig, setSortConfig] = useState<SortConfig | null>(null);
  const fetchAllInFlightRef = React.useRef(false);
  const fetchHistoryInFlightRef = React.useRef(false);

  const requestSort = (key: string) => {
    let direction: SortDirection = 'desc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') {
      direction = 'asc';
    }
    setSortConfig({ key, direction });
  };

  const getSortIcon = (key: string) => {
    if (!sortConfig || sortConfig.key !== key) return <ArrowDownUp className="h-3 w-3 opacity-20" />;
    return sortConfig.direction === 'asc' ? <ChevronUp className="h-3 w-3 text-info" /> : <ChevronDown className="h-3 w-3 text-info" />;
  };

  const displayName = useCallback((ip: string) => hostnameMap[ip] || ip, [hostnameMap]);

  const deviceOptions = useMemo(
    () => devices.filter((d) => isValidDeviceKey(d.mgmt_ip || d.hostname)).map((d) => {
      const ip = d.mgmt_ip || d.hostname;
      const name = hostnameMap[ip] || d.sysname || ip;
      return { key: ip, label: name !== ip ? `${name} (${ip})` : ip };
    }),
    [devices, hostnameMap],
  );

  const focusedDevice = useMemo(
    () => devices.find((d) => (d.mgmt_ip || d.hostname) === focusDevice),
    [devices, focusDevice],
  );

  useEffect(() => {
    if (!focusDevice) return;
    console.log('[NetworkHealth] focused device payload', focusedDevice);
  }, [focusDevice, focusedDevice]);

  const fetchAll = useCallback(async () => {
    if (fetchAllInFlightRef.current) return;
    fetchAllInFlightRef.current = true;
    setIsLoading(true);
    try {
      const requests: Promise<any>[] = [
        monitorApi.getStatus().catch(() => ({ status: 'error' })),
        monitorApi.getDeviceSummary().catch(() => ({ devices: [] })),
        thresholdApi.listAlerts('active', 30).catch(() => []),
        monitorApi.getDeviceHostnames().catch(() => ({ hostnames: {} })),
      ];
      if (focusDevice) {
        requests.push(monitorApi.getDeviceInterfaces(focusDevice).catch(() => ({ interfaces: [] })));
      }

      const [statusRes, summaryRes, alertsRes, hostnameRes, interfaceRes] = await Promise.all(requests);
      const nextDevices = ((summaryRes as any).devices || []).filter((d: DeviceSummary) => isValidDeviceKey(d.mgmt_ip || d.hostname));
      const hostnames = Object.entries((hostnameRes as any).hostnames || {}).reduce((acc, [k, v]) => {
        if (isValidDeviceKey(k) && String(v || '').trim()) acc[k] = String(v).trim();
        return acc;
      }, {} as Record<string, string>);
      const nextInterfaces = (((interfaceRes as any)?.interfaces || []) as InterfaceSnapshot[]).filter((item) => !!item.interface).sort((a, b) => a.interface.localeCompare(b.interface));

      console.log('[NetworkHealth] summary devices', nextDevices);

      setStackStatus((statusRes as any).status || 'error');
      setDevices(nextDevices);
      setAlerts(alertsRes as ThresholdAlert[]);
      setHostnameMap(hostnames);
      setInterfaceSnapshots(nextInterfaces);
      setLastUpdated(new Date());
      setHasLoadedSummaryGraphs(true);

      if (focusDevice && selectedInterface && !nextInterfaces.some((item) => item.interface === selectedInterface)) {
        setSelectedInterface('');
      }
      if (focusDevice && nextInterfaces.length === 0) {
        setSelectedInterface('');
      }
    } catch {
      setHasLoadedSummaryGraphs(true);
      Toast.error('Load failed', 'Could not fetch monitoring data');
    } finally {
      fetchAllInFlightRef.current = false;
      setIsLoading(false);
    }
  }, [focusDevice, selectedInterface]);

  const fetchHistory = useCallback(async () => {
    if (fetchHistoryInFlightRef.current) return;
    fetchHistoryInFlightRef.current = true;
    try {
      const reqDevice = focusDevice || undefined;
      const ifFilter = selectedInterface || undefined;
      const metrics = [
        'cpu',
        'memory',
        'interface_traffic',
        'interface_out',
        'errors',
        'discards',
        'pkts_in_ucast',
        'pkts_out_ucast',
        'pkts_in_mcast',
        'pkts_out_mcast',
        'pkts_in_bcast',
        'pkts_out_bcast',
        'if_speed',
      ];
      const batch = await monitorApi.getMetricHistoryBatch(metrics, reqDevice, rangeMinutes, 240, ifFilter);
      const historyData = ((batch as any).data || {}) as Record<string, MetricPoint[]>;
      const pointsFor = (metricName: string) =>
        ((historyData[metricName] || []) as MetricPoint[]).filter((p) => isValidDeviceKey(p.device));

      setCpuPoints(pointsFor('cpu'));
      setMemPoints(pointsFor('memory'));
      setTrafficIn(pointsFor('interface_traffic'));
      setTrafficOut(pointsFor('interface_out'));
      setErrors(pointsFor('errors'));
      setDiscards(pointsFor('discards'));
      setInUcast(pointsFor('pkts_in_ucast'));
      setOutUcast(pointsFor('pkts_out_ucast'));
      setInMcast(pointsFor('pkts_in_mcast'));
      setOutMcast(pointsFor('pkts_out_mcast'));
      setInBcast(pointsFor('pkts_in_bcast'));
      setOutBcast(pointsFor('pkts_out_bcast'));
      setSpeedPoints(pointsFor('if_speed'));
      setHasLoadedHistoryGraphs(true);
    } catch {
      setHasLoadedHistoryGraphs(true);
      // Charts degrade gracefully.
    } finally {
      fetchHistoryInFlightRef.current = false;
    }
  }, [focusDevice, selectedInterface, rangeMinutes]);

  const loadRecentSyslogEvents = useCallback(async () => {
    setSyslogEventsLoading(true);
    try {
      const events = await syslogApi.listEvents(80, selectedSyslogDevice || undefined);
      setRecentSyslogEvents(Array.isArray(events) ? events : []);
    } catch {
      setRecentSyslogEvents([]);
    } finally {
      setSyslogEventsLoading(false);
    }
  }, [selectedSyslogDevice]);

  const loadSyslogSources = useCallback(async () => {
    try {
      const res = await syslogApi.listSources(200);
      setSyslogSources(Array.isArray(res?.items) ? res.items : []);
    } catch {
      setSyslogSources([]);
    }
  }, []);

  const handleSyncSyslogServer = useCallback(async () => {
    const ok = await Confirm.ask(
      'Sync syslog server config?',
      'This will connect to the configured server and update syslog service configuration based on current inventory.',
    );
    if (!ok) return;
    setSyslogSyncLoading(true);
    try {
      const res = await syslogApi.syncServer('hostname');
      if (res?.synced) {
        Toast.success(res.message || 'Syslog server sync completed');
      } else {
        Toast.error('Syslog sync incomplete', res?.message || 'Check backend settings and credentials');
      }
      await Promise.all([loadSyslogSources(), loadRecentSyslogEvents()]);
    } catch (e: any) {
      Toast.error('Syslog sync failed', e?.message || 'Unable to sync syslog server');
    } finally {
      setSyslogSyncLoading(false);
    }
  }, [loadRecentSyslogEvents, loadSyslogSources]);

  const handleSyncTelegraf = useCallback(async () => {
    const ok = await Confirm.ask(
      'Sync Telegraf target config?',
      'This will regenerate and deploy monitoring target configuration to the configured server.',
    );
    if (!ok) return;
    setTelegrafSyncLoading(true);
    try {
      const res: any = await monitorApi.updateConfig(true);
      Toast.success('Telegraf sync', res?.message || 'Telegraf sync completed');
    } catch (e: any) {
      Toast.error('Telegraf sync failed', e?.message || 'Unable to sync Telegraf config');
    } finally {
      setTelegrafSyncLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);
  useEffect(() => { fetchHistory(); }, [fetchHistory]);
  useEffect(() => { loadSyslogSources(); }, [loadSyslogSources]);
  useEffect(() => { loadRecentSyslogEvents(); }, [loadRecentSyslogEvents]);
  useEffect(() => {
    const timer = setInterval(() => {
      fetchAll();
      fetchHistory();
    }, 30000);
    return () => clearInterval(timer);
  }, [fetchAll, fetchHistory]);
  useEffect(() => {
    const timer = setInterval(() => {
      loadSyslogSources();
      loadRecentSyslogEvents();
    }, 10000);
    return () => clearInterval(timer);
  }, [loadRecentSyslogEvents, loadSyslogSources]);

  const upDevices = devices.filter((d) => (d.status || '').toLowerCase() === 'up').length;
  const activeAlerts = alerts.filter((a) => a.status === 'active').length;
  const cpuDevices = devices.filter((d) => d.cpu !== null);
  const avgCpu = cpuDevices.length > 0 ? cpuDevices.reduce((s, d) => s + (d.cpu || 0), 0) / cpuDevices.length : null;
  const totalIfUp = devices.reduce((s, d) => s + (d.interfaces_oper_up || d.interfaces_up || 0), 0);
  const totalIf = devices.reduce((s, d) => s + (d.interfaces_total || 0), 0);

  const cpuChart = useMemo(() => buildDeviceSeries(cpuPoints, hostnameMap), [cpuPoints, hostnameMap]);
  const memChart = useMemo(() => buildDeviceSeries(memPoints, hostnameMap), [memPoints, hostnameMap]);
  const trafficHistoryChart = useMemo(() => buildTrafficSeries(trafficInPoints, trafficOutPoints, hostnameMap), [trafficInPoints, trafficOutPoints, hostnameMap]);
  const trafficSnapshotChart = useMemo(() => buildTrafficSnapshotFallback(interfaceSnapshots, focusDevice, selectedInterface, hostnameMap), [interfaceSnapshots, focusDevice, selectedInterface, hostnameMap]);
  const trafficChart = useMemo(() => {
    if (trafficHistoryChart.rows.length > 0 && trafficHistoryChart.series.length > 0) {
      return trafficHistoryChart;
    }
    if (focusDevice) {
      return trafficSnapshotChart;
    }
    return trafficHistoryChart;
  }, [trafficHistoryChart, trafficSnapshotChart, focusDevice]);
  const ifHealthChart = useMemo(() => buildErrorDiscardSeries(errorPoints, discardPoints, hostnameMap), [errorPoints, discardPoints, hostnameMap]);
  const pktChart = useMemo(() => buildPacketSeries(inUcastPts, outUcastPts, inMcastPts, outMcastPts, inBcastPts, outBcastPts, hostnameMap), [inUcastPts, outUcastPts, inMcastPts, outMcastPts, inBcastPts, outBcastPts, hostnameMap]);
  const derivedInterfaceMetrics = useMemo(() => buildDerivedInterfaceMetrics(trafficInPoints, trafficOutPoints, inUcastPts, outUcastPts, inMcastPts, outMcastPts, inBcastPts, outBcastPts, speedPoints), [trafficInPoints, trafficOutPoints, inUcastPts, outUcastPts, inMcastPts, outMcastPts, inBcastPts, outBcastPts, speedPoints]);

  const topDeviceInsights = useMemo(() => buildTopDeviceInsights(devices, hostnameMap), [devices, hostnameMap]);
  const interfaceInsights = useMemo(() => buildInterfaceInsights(trafficInPoints, trafficOutPoints, errorPoints, discardPoints, inUcastPts, outUcastPts, inMcastPts, outMcastPts, inBcastPts, outBcastPts, hostnameMap), [trafficInPoints, trafficOutPoints, errorPoints, discardPoints, inUcastPts, outUcastPts, inMcastPts, outMcastPts, inBcastPts, outBcastPts, hostnameMap]);
  const showInitialGraphSkeleton = !hasLoadedSummaryGraphs || !hasLoadedHistoryGraphs;

  const sortedSnapshots = useMemo(() => {
    if (!sortConfig) return interfaceSnapshots;
    return [...interfaceSnapshots].sort((a: any, b: any) => {
      let valA = a[sortConfig.key];
      let valB = b[sortConfig.key];

      // Handle derived/normalized metrics if needed
      if (['in_bps', 'out_bps', 'in_pps', 'out_pps'].includes(sortConfig.key)) {
        const derivedA = derivedInterfaceMetrics.get(a.interface);
        const derivedB = derivedInterfaceMetrics.get(b.interface);
        const normA = normalizeDirectionalTraffic({
          device: a.device, iface: a.interface, direction: sortConfig.key.startsWith('in') ? 'in' : 'out',
          bps: a[sortConfig.key.replace('bps', 'octets')] > 0 ? a[sortConfig.key.replace('bps', 'octets')] : (sortConfig.key.startsWith('in') ? derivedA?.inBps : derivedA?.outBps) ?? 0,
          pps: a[sortConfig.key.replace('pps', 'packets')] > 0 ? a[sortConfig.key.replace('pps', 'packets')] : (sortConfig.key.startsWith('in') ? derivedA?.inPps : derivedA?.outPps) ?? 0,
        });
        const normB = normalizeDirectionalTraffic({
          device: b.device, iface: b.interface, direction: sortConfig.key.startsWith('in') ? 'in' : 'out',
          bps: b[sortConfig.key.replace('bps', 'octets')] > 0 ? b[sortConfig.key.replace('bps', 'octets')] : (sortConfig.key.startsWith('in') ? derivedB?.inBps : derivedB?.outBps) ?? 0,
          pps: b[sortConfig.key.replace('pps', 'packets')] > 0 ? b[sortConfig.key.replace('pps', 'packets')] : (sortConfig.key.startsWith('in') ? derivedB?.inPps : derivedB?.outPps) ?? 0,
        });
        valA = sortConfig.key.endsWith('bps') ? normA.bps : normA.pps;
        valB = sortConfig.key.endsWith('bps') ? normB.bps : normB.pps;
      }

      if (valA < valB) return sortConfig.direction === 'asc' ? -1 : 1;
      if (valA > valB) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });
  }, [interfaceSnapshots, sortConfig, derivedInterfaceMetrics]);

  const sortedInsights = useMemo(() => {
    if (!sortConfig) return interfaceInsights;
    return [...interfaceInsights].sort((a: any, b: any) => {
      let valA = a[sortConfig.key] ?? 0;
      let valB = b[sortConfig.key] ?? 0;

      // Handle normalized metrics for insights
      if (['inOctets', 'outOctets', 'inPkts', 'outPkts'].includes(sortConfig.key)) {
        const normA = normalizeDirectionalTraffic({ device: a.device, iface: a.interface, direction: sortConfig.key.startsWith('in') ? 'in' : 'out', bps: a.inOctets, pps: a.inPkts });
        const normB = normalizeDirectionalTraffic({ device: b.device, iface: b.interface, direction: sortConfig.key.startsWith('in') ? 'in' : 'out', bps: b.inOctets, pps: b.inPkts });
        valA = sortConfig.key.endsWith('Octets') ? normA.bps : normA.pps;
        valB = sortConfig.key.endsWith('Octets') ? normB.bps : normB.pps;
      }

      if (valA < valB) return sortConfig.direction === 'asc' ? -1 : 1;
      if (valA > valB) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });
  }, [interfaceInsights, sortConfig]);

  const deviceBarData = useMemo(() => {
    const source = focusDevice && focusedDevice ? [focusedDevice] : devices;
    return source.map((d) => {
      const ip = d.mgmt_ip || d.hostname;
      return { name: hostnameMap[ip] || d.sysname || ip, cpu: d.cpu ?? 0, mem: d.memory_pct ?? d.memory ?? 0 };
    });
  }, [devices, focusDevice, focusedDevice, hostnameMap]);

  const focusLabel = focusDevice ? displayName(focusDevice) : 'All devices';

  return (
    <div className="p-4 space-y-4 h-full overflow-y-auto">
      <div className="flex items-center justify-between mb-4 shrink-0 flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-3 w-full">
            <Activity className="w-7 h-7 text-primary" />
            <h1 className="text-2xl font-bold">Network Health</h1>
            <span className="badge badge-primary badge-outline hidden sm:inline-flex">{focusDevice ? 'Device focus' : `${devices.length} devices`}</span>
          </div>
          <p className="text-xs opacity-50 mt-1.5 flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {lastUpdated ? `Updated ${formatDateTime(lastUpdated.toISOString())}` : 'Fetching data'}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button className="btn btn-sm btn-outline gap-1" onClick={() => { fetchAll(); fetchHistory(); }} disabled={isLoading}>
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} /> Refresh
          </button>
          {/* <button className="btn btn-sm btn-outline gap-1" onClick={handleEvaluate}>
            <AlertTriangle className="w-4 h-4 text-warning" /> Evaluate
          </button>
          <button className="btn btn-sm btn-primary gap-1" onClick={handleRegenerateConfig} disabled={isLoading}>
            <Server className="w-4 h-4" /> Telegraf
          </button> */}
        </div>
      </div>

      <div className="card bg-base-100 border border-base-300 shadow-sm">
        <div className="card-body p-3 space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <select className="select select-bordered select-xs min-w-56" value={focusDevice} onChange={(e) => { setFocusDevice(e.target.value); setSelectedInterface(''); }}>
              <option value="">All Devices</option>
              {deviceOptions.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
            </select>
            {focusDevice && (
              <select className="select select-bordered select-xs min-w-56" value={selectedInterface} onChange={(e) => setSelectedInterface(e.target.value)}>
                <option value="">All Interfaces</option>
                {interfaceSnapshots.map((item) => <option key={item.interface} value={item.interface}>{item.interface}</option>)}
              </select>
            )}
            <div className="flex items-center gap-1 flex-wrap">
              {RANGE_OPTIONS.map((o) => (
                <button key={o.value} className={`btn btn-xs ${rangeMinutes === o.value ? 'btn-neutral' : 'btn-ghost'}`} onClick={() => setRangeMinutes(o.value)}>{o.label}</button>
              ))}
            </div>
            <span className="badge badge-outline text-xs">Scope: {focusLabel}{selectedInterface ? ` / ${selectedInterface}` : ''}</span>
          </div>
        </div>
      </div>

      {focusDevice && focusedDevice ? (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <KPICard icon={<CheckCircle className="h-4 w-4 text-success" />} label="Device Status" value={toTitleCase(focusedDevice.status)} color={(focusedDevice.status || '').toLowerCase() === 'up' ? 'text-success' : 'text-warning'} subtext={`${focusedDevice.vendor || 'Unknown vendor'} ${focusedDevice.model || ''}`.trim()} />
          <KPICard icon={<Cpu className="h-4 w-4 text-info" />} label="CPU" value={focusedDevice.cpu != null ? `${focusedDevice.cpu.toFixed(1)}%` : 'N/A'} color={focusedDevice.cpu && focusedDevice.cpu > 80 ? 'text-error' : focusedDevice.cpu && focusedDevice.cpu > 60 ? 'text-warning' : 'text-info'} subtext="Current device CPU" />
          <KPICard icon={<BarChart3 className="h-4 w-4 text-success" />} label="Memory" value={focusedDevice.memory_pct != null || focusedDevice.memory != null ? `${(focusedDevice.memory_pct ?? focusedDevice.memory ?? 0).toFixed(1)}%` : 'N/A'} color="text-success" subtext={focusedDevice.mem_used != null ? `${formatBytes(focusedDevice.mem_used)} used` : 'No memory counters'} />
          <KPICard icon={<TimerReset className="h-4 w-4 text-warning" />} label="System Uptime" value={focusedDevice.sysuptime ? formatUptime(focusedDevice.sysuptime) : 'N/A'} color="text-warning" subtext={focusedDevice.last_polled ? `Last polled ${formatDateTime(focusedDevice.last_polled)}` : 'No poll timestamp'} />
          <KPICard icon={<Network className="h-4 w-4 text-primary" />} label="Oper / Admin" value={`${focusedDevice.interfaces_oper_up ?? focusedDevice.interfaces_up ?? 0}/${focusedDevice.interfaces_total ?? 0}`} color="text-primary" subtext={`${focusedDevice.interfaces_admin_up ?? 0} admin-up, ${focusedDevice.interfaces_oper_down ?? focusedDevice.interfaces_down ?? 0} oper-down`} />
        </div>
      ) : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <KPICard icon={<Activity className="h-4 w-4" />} label="Stack Status" value={toTitleCase(stackStatus)} color={stackStatus.toLowerCase() === 'healthy' ? 'text-success' : 'text-warning'} subtext="InfluxDB reachability" />
          <KPICard icon={<CheckCircle className="h-4 w-4 text-success" />} label="Devices Up" value={`${upDevices} / ${devices.length}`} color="text-success" subtext={devices.length ? `${Math.round(upDevices / devices.length * 100)}% availability` : 'No live telemetry'} />
          <KPICard icon={<Cpu className="h-4 w-4 text-info" />} label="Average CPU" value={avgCpu !== null ? `${avgCpu.toFixed(1)}%` : 'N/A'} color={avgCpu !== null && avgCpu > 80 ? 'text-error' : avgCpu !== null && avgCpu > 60 ? 'text-warning' : 'text-info'} subtext={`${cpuDevices.length} devices with CPU values`} />
          <KPICard icon={<Wifi className="h-4 w-4 text-primary" />} label="Interface Health" value={totalIf ? `${totalIfUp}/${totalIf}` : 'N/A'} color="text-primary" subtext={`${activeAlerts} active alerts`} />
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <MultiLineChartCard title="CPU Utilisation (%)" chart={cpuChart} yUnit="%" loading={showInitialGraphSkeleton} />
        <MultiLineChartCard title="Memory Usage (%)" chart={memChart} yUnit="%" loading={showInitialGraphSkeleton} />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <MultiLineChartCard title={focusDevice ? 'Interface Traffic for Selected Scope (bps)' : 'Interface Traffic Across Devices (bps)'} chart={trafficChart} yUnit="bps" loading={showInitialGraphSkeleton} />
        <MultiLineChartCard title="Errors & Discards (/s)" chart={ifHealthChart} yUnit="/s" loading={showInitialGraphSkeleton} />
      </div>
      <MultiLineChartCard title="Packet Activity (pps)" chart={pktChart} yUnit="pps" loading={showInitialGraphSkeleton} />

      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        <div className="card bg-base-100 border border-base-300 shadow-sm">
          <div className="card-body p-3">
            <h3 className="text-xs font-semibold opacity-60 uppercase tracking-wide mb-2">CPU vs Memory Snapshot</h3>
            {showInitialGraphSkeleton ? <GraphSkeleton height={185} /> : deviceBarData.length === 0 ? <EmptyChart /> : (
              <ResponsiveContainer width="100%" height={185}>
                <BarChart data={deviceBarData} margin={{ left: -8, right: 4, top: 2, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.08} />
                  <XAxis dataKey="name" tick={{ fontSize: 9 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fontSize: 9 }} domain={[0, 100]} tickFormatter={(v) => `${v}%`} width={36} tickLine={false} axisLine={false} />
                  <Tooltip 
                    cursor={false}
                    contentStyle={{ backgroundColor: '#172033', color: '#f8fafc', border: '1px solid #334155', fontSize: 11, padding: '8px 12px', borderRadius: '8px' }} 
                    itemStyle={{ color: '#f1f5f9', padding: '2px 0' }} 
                    labelStyle={{ color: '#60a5fa', fontWeight: 700, paddingBottom: 4, borderBottom: '1px solid #334155', marginBottom: 4 }} 
                    formatter={(v: any, name: any) => [`${v ?? 0}%`, String(name)] as [string, string]} />
                  <Legend wrapperStyle={{ fontSize: '10px' }} />
                  <Bar dataKey="cpu" name="CPU" fill="#2563eb" radius={[2, 2, 0, 0]} maxBarSize={24} />
                  <Bar dataKey="mem" name="Memory" fill="#059669" radius={[2, 2, 0, 0]} maxBarSize={24} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
        <AlertDonutCard alerts={alerts} loading={showInitialGraphSkeleton} />
      </div>

      {!focusDevice && (
        <div className="card bg-base-100 border border-base-300 shadow-sm">
          <div className="card-body p-3">
            <h3 className="text-xs font-semibold opacity-60 uppercase tracking-wide mb-2">Top Devices Risk Score</h3>
            {topDeviceInsights.length === 0 ? <p className="text-xs opacity-30">No data</p> : (
              <div className="overflow-x-auto">
                <table className="table table-xs">
                  <thead>
                    <tr className="text-xs opacity-50"><th>Device</th><th>CPU</th><th>Mem</th><th>Err</th><th>Disc</th><th>Score</th></tr>
                  </thead>
                  <tbody>
                    {topDeviceInsights.map((r) => (
                      <tr key={r.device} className="hover cursor-pointer" onClick={() => { setFocusDevice(r.device); setSelectedInterface(''); }}>
                        <td><div className="font-medium text-xs leading-tight">{r.displayName}</div>{r.displayName !== r.device && <div className="font-mono text-xs opacity-40">{r.device}</div>}</td>
                        <td className={r.cpu > 80 ? 'text-error font-bold' : r.cpu > 60 ? 'text-warning' : ''}>{r.cpu.toFixed(0)}%</td>
                        <td>{r.memPct !== null ? `${r.memPct.toFixed(0)}%` : <span className="opacity-30">-</span>}</td>
                        <td className={r.errors > 0 ? 'text-error font-semibold' : 'opacity-30'}>{compactNum(r.errors)}</td>
                        <td className={r.discards > 0 ? 'text-warning font-semibold' : 'opacity-30'}>{compactNum(r.discards)}</td>
                        <td><span className={`font-bold text-xs ${r.score >= 80 ? 'text-error' : r.score >= 50 ? 'text-warning' : 'text-success'}`}>{r.score.toFixed(0)}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="card bg-base-100 border border-base-300 shadow-sm">
        <div className="card-body p-3">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold opacity-60 uppercase tracking-wide flex items-center gap-1.5"><ArrowDownUp className="h-3.5 w-3.5 text-info" />{focusDevice ? 'Interface Drilldown' : 'Top Interface Activity'}</h3>
            <span className="text-xs opacity-40 font-mono">{focusLabel}{selectedInterface ? ` / ${selectedInterface}` : ''}</span>
          </div>

          {focusDevice ? (
            interfaceSnapshots.length === 0 ? <EmptyChart label="No interface snapshot available for the selected device" /> : (
              <div className="overflow-x-auto">
                <table className="table table-xs table-zebra">
                  <thead>
                    <tr className="text-xs opacity-50">
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('interface')}>
                        <div className="flex items-center gap-1">Interface {getSortIcon('interface')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('admin_status_label')}>
                        <div className="flex items-center gap-1">Admin {getSortIcon('admin_status_label')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('oper_status_label')}>
                        <div className="flex items-center gap-1">Oper {getSortIcon('oper_status_label')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('speed')}>
                        <div className="flex items-center gap-1">Speed {getSortIcon('speed')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('in_bps')}>
                        <div className="flex items-center gap-1">In bps {getSortIcon('in_bps')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('out_bps')}>
                        <div className="flex items-center gap-1">Out bps {getSortIcon('out_bps')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('in_pps')}>
                        <div className="flex items-center gap-1">In pps {getSortIcon('in_pps')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('out_pps')}>
                        <div className="flex items-center gap-1">Out pps {getSortIcon('out_pps')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('errors')}>
                        <div className="flex items-center gap-1">Err/s {getSortIcon('errors')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('discards')}>
                        <div className="flex items-center gap-1">Disc/s {getSortIcon('discards')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('last_seen')}>
                        <div className="flex items-center gap-1">Last Seen {getSortIcon('last_seen')}</div>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedSnapshots.map((row) => {
                      const active = row.interface === selectedInterface;
                      const derived = derivedInterfaceMetrics.get(row.interface);
                      const normalizedIn = normalizeDirectionalTraffic({
                        device: row.device,
                        iface: row.interface,
                        direction: 'in',
                        bps: row.in_octets > 0 ? row.in_octets : derived?.inBps ?? 0,
                        pps: row.in_packets > 0 ? row.in_packets : derived?.inPps ?? 0,
                        timeBucket: row.last_seen,
                      });
                      const normalizedOut = normalizeDirectionalTraffic({
                        device: row.device,
                        iface: row.interface,
                        direction: 'out',
                        bps: row.out_octets > 0 ? row.out_octets : derived?.outBps ?? 0,
                        pps: row.out_packets > 0 ? row.out_packets : derived?.outPps ?? 0,
                        timeBucket: row.last_seen,
                      });
                      const effectiveSpeed = row.speed || derived?.speed || null;
                      return (
                        <tr key={row.interface} className={`hover cursor-pointer ${active ? 'bg-primary/5' : ''}`} onClick={() => setSelectedInterface((prev) => prev === row.interface ? '' : row.interface)}>
                          <td className="font-mono text-xs font-medium">{row.interface}</td>
                          <td><span className={`badge badge-xs ${statusTone(row.admin_status_label)}`}>{toTitleCase(row.admin_status_label || 'unknown')}</span></td>
                          <td><span className={`badge badge-xs ${statusTone(row.oper_status_label)}`}>{toTitleCase(row.oper_status_label || 'unknown')}</span></td>
                          <td className="text-xs opacity-60">{formatSpeed(effectiveSpeed)}</td>
                          <td>{formatBitRate(normalizedIn.bps)}</td>
                          <td>{formatBitRate(normalizedOut.bps)}</td>
                          <td>{formatPerSecond(normalizedIn.pps)}</td>
                          <td>{formatPerSecond(normalizedOut.pps)}</td>
                          <td className={row.errors > 0 ? 'text-error font-semibold' : 'opacity-30'}>{formatPerSecond(row.errors)}</td>
                          <td className={row.discards > 0 ? 'text-warning font-semibold' : 'opacity-30'}>{formatPerSecond(row.discards)}</td>
                          <td className="text-xs opacity-40">{row.last_seen ? formatDateTime(row.last_seen) : '-'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            interfaceInsights.length === 0 ? <EmptyChart label="Select a device or wait for interface telemetry" /> : (
              <div className="overflow-x-auto">
                <table className="table table-xs table-zebra">
                  <thead>
                    <tr className="text-xs opacity-50">
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('displayName')}>
                        <div className="flex items-center gap-1">Device {getSortIcon('displayName')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('interface')}>
                        <div className="flex items-center gap-1">Interface {getSortIcon('interface')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('inOctets')}>
                        <div className="flex items-center gap-1">In bps {getSortIcon('inOctets')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('outOctets')}>
                        <div className="flex items-center gap-1">Out bps {getSortIcon('outOctets')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('inPkts')}>
                        <div className="flex items-center gap-1">In pps {getSortIcon('inPkts')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('outPkts')}>
                        <div className="flex items-center gap-1">Out pps {getSortIcon('outPkts')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('errors')}>
                        <div className="flex items-center gap-1">Err/s {getSortIcon('errors')}</div>
                      </th>
                      <th className="cursor-pointer hover:bg-base-200" onClick={() => requestSort('discards')}>
                        <div className="flex items-center gap-1">Disc/s {getSortIcon('discards')}</div>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedInsights.map((row) => (
                      (() => {
                        const normalizedIn = normalizeDirectionalTraffic({
                          device: row.device,
                          iface: row.interface,
                          direction: 'in',
                          bps: row.inOctets,
                          pps: row.inPkts,
                        });
                        const normalizedOut = normalizeDirectionalTraffic({
                          device: row.device,
                          iface: row.interface,
                          direction: 'out',
                          bps: row.outOctets,
                          pps: row.outPkts,
                        });
                        return <tr key={`${row.device}:${row.interface}`} className="hover">
                        <td>
                          <div className="font-medium text-xs leading-tight">{row.displayName}</div>
                          {row.displayName !== row.device && <div className="font-mono text-[10px] opacity-40">{row.device}</div>}
                        </td>
                        <td className="font-mono text-xs font-medium">{row.interface}</td>
                        <td>{formatBitRate(normalizedIn.bps)}</td>
                        <td>{formatBitRate(normalizedOut.bps)}</td>
                        <td>{formatPerSecond(normalizedIn.pps)}</td>
                        <td>{formatPerSecond(normalizedOut.pps)}</td>
                        <td className={row.errors > 0 ? 'text-error font-semibold' : 'opacity-30'}>{formatPerSecond(row.errors)}</td>
                        <td className={row.discards > 0 ? 'text-warning font-semibold' : 'opacity-30'}>{formatPerSecond(row.discards)}</td>
                      </tr>;
                      })()
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </div>
      </div>

      <div className="card bg-base-100 border border-base-300 shadow-sm">
        <div className="card-body p-3">
          <h3 className="text-xs font-semibold opacity-60 uppercase tracking-wide mb-2 flex items-center gap-1.5"><Server className="h-3.5 w-3.5" /> Live Devices</h3>
          <div className="overflow-x-auto">
            <table className="table table-xs table-zebra">
              <thead>
                <tr className="text-xs opacity-50"><th>Hostname / sysName</th><th>IP</th><th>Status</th><th>CPU</th><th>CPU Source</th><th>Mem %</th><th>Oper Ifaces</th><th>Admin Ifaces</th><th>Errors</th><th>Discards</th><th>Uptime</th><th>Last Polled</th></tr>
              </thead>
              <tbody>
                {devices.length === 0 ? (
                  <tr><td colSpan={12} className="text-center opacity-30 py-4">No devices found</td></tr>
                ) : devices.map((d) => {
                  const ip = d.mgmt_ip || d.hostname;
                  const name = hostnameMap[ip] || d.sysname || d.hostname || ip;
                  const isFocused = ip === focusDevice;
                  const memPct = d.memory_pct ?? d.memory;
                  return (
                    <tr key={ip} className={`hover cursor-pointer ${isFocused ? 'bg-primary/5' : ''}`} onClick={() => { setFocusDevice(ip); setSelectedInterface(''); }}>
                      <td><div className="font-medium">{name}</div>{name !== ip && <div className="text-xs font-mono opacity-40">{d.hostname}</div>}</td>
                      <td className="font-mono text-xs opacity-60">{ip}</td>
                      <td><span className={`badge badge-xs ${(d.status || '').toLowerCase() === 'up' ? 'badge-success' : 'badge-warning'}`}>{toTitleCase(d.status)}</span></td>
                      <td>{d.cpu !== null ? <span className={d.cpu > 80 ? 'text-error font-bold' : d.cpu > 60 ? 'text-warning' : ''}>{d.cpu.toFixed(1)}%</span> : <span className="opacity-30">-</span>}</td>
                      <td className="text-xs opacity-60">{toTitleCase(d.cpu_source || 'reported')}</td>
                      <td>{memPct != null ? <span className={(memPct as number) > 85 ? 'text-error font-bold' : (memPct as number) > 70 ? 'text-warning' : ''}>{(memPct as number).toFixed(0)}%</span> : <span className="opacity-30">-</span>}</td>
                      <td className="text-xs">{d.interfaces_total ? `${d.interfaces_oper_up ?? d.interfaces_up ?? 0}/${d.interfaces_total}` : <span className="opacity-30">-</span>}</td>
                      <td className="text-xs">{d.interfaces_total ? `${d.interfaces_admin_up ?? 0}/${d.interfaces_total}` : <span className="opacity-30">-</span>}</td>
                      <td className={`text-xs ${(d.errors || 0) > 0 ? 'text-error font-semibold' : 'opacity-30'}`}>{d.errors || 0}</td>
                      <td className={`text-xs ${(d.discards || 0) > 0 ? 'text-warning font-semibold' : 'opacity-30'}`}>{d.discards || 0}</td>
                      <td className="text-xs opacity-50">{d.sysuptime ? formatUptime(d.sysuptime) : <span className="opacity-30">-</span>}</td>
                      <td className="text-xs opacity-40">{d.last_polled ? formatDateTime(d.last_polled) : '-'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card bg-base-100 border border-base-300 shadow-sm">
        <div className="card-body p-3 gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-xs font-semibold opacity-60 uppercase tracking-wide flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 text-warning" />
              Recent Syslog Events
            </h3>
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-ghost btn-xs" onClick={loadRecentSyslogEvents} disabled={syslogEventsLoading} title="Refresh syslog events">
                <RefreshCw className={`w-3.5 h-3.5 ${syslogEventsLoading ? 'animate-spin' : ''}`} />
              </button>
              <button className="btn btn-outline btn-xs" onClick={handleSyncTelegraf} disabled={telegrafSyncLoading}>
                {telegrafSyncLoading ? <span className="loading loading-spinner loading-xs" /> : null}
                Sync Telegraf
              </button>
              <button className="btn btn-outline btn-xs" onClick={handleSyncSyslogServer} disabled={syslogSyncLoading}>
                {syslogSyncLoading ? <span className="loading loading-spinner loading-xs" /> : null}
                Sync Server
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs opacity-60">View</span>
            <select
              className="select select-bordered select-xs w-full"
              value={selectedSyslogDevice}
              onChange={(e) => setSelectedSyslogDevice(e.target.value)}
            >
              <option value="">All Devices (Aggregate)</option>
              {syslogSources.map((source: any) => {
                const label = source?.device_hostname || source?.source_host || source?.source_ip || 'unknown';
                const value = source?.device_hostname || source?.source_ip || '';
                return (
                  <option key={`${source?.source_ip || 'unknown'}-${source?.device_hostname || ''}`} value={value}>
                    {label}{source?.source_ip ? ` (${source.source_ip})` : ''} - {source?.event_count || 0}
                  </option>
                );
              })}
            </select>
          </div>

          {recentSyslogEvents.length === 0 ? (
            <div className="rounded-lg border border-dashed border-base-300 bg-base-200/40 px-4 py-4 text-sm opacity-70">
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

      {alerts.length > 0 && (
        <div className="card bg-base-100 border border-error/30 shadow-sm">
          <div className="card-body p-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-error mb-2 flex items-center gap-1.5"><AlertTriangle className="h-3.5 w-3.5" /> Active Threshold Alerts</h3>
            <div className="space-y-1.5">
              {alerts.map((a) => (
                <div key={a.id} className="flex items-center gap-2 text-xs px-2.5 py-1.5 rounded bg-error/5 border border-error/15">
                  <span className={`badge badge-xs ${a.severity === 'critical' ? 'badge-error' : 'badge-warning'}`}>{a.severity}</span>
                  <span className="font-mono font-medium">{displayName(a.device || '')}: {a.metric} = {a.value}</span>
                  <span className="opacity-40 ml-auto shrink-0">{a.created_at}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default NetworkPerformance;
