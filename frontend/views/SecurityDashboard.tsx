import React, { useEffect, useState, useCallback } from 'react';
import {
    Shield, ShieldAlert, ShieldCheck, AlertTriangle, AlertOctagon,
    Bug, Activity, RefreshCw, TrendingUp, Server
} from 'lucide-react';
import {
    PieChart, Pie, Cell, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip
} from 'recharts';
import { securityApi } from '../src/services/api';
import { Toast } from '../src/utils/swal';

/* ─── Types ────────────────────────────────────────────── */
interface SecurityEvent {
    id: number;
    event_type: string;
    severity: string;
    source_ip: string;
    dest_ip: string;
    device_id: number | null;
    description: string;
    status: string;
    created_at: string;
}

interface AuditSummary {
    device_id: number;
    score: number;
    total_checks: number;
    passed: number;
    failed: number;
    critical: number;
    high: number;
    medium: number;
    low: number;
}

interface CveStats {
    total_cves: number;
    by_severity: Record<string, number>;
    by_vendor: Record<string, number>;
}

/* ─── Constants ────────────────────────────────────────── */
const SEVERITY_COLORS: Record<string, string> = {
    critical: '#ef4444',
    high: '#f97316',
    medium: '#eab308',
    low: '#22c55e',
    info: '#6b7280',
};
const PIE_COLORS = ['#22c55e', '#ef4444', '#f97316', '#eab308'];

/* ─── Component ────────────────────────────────────────── */
const SecurityDashboard: React.FC = () => {
    const [events, setEvents] = useState<SecurityEvent[]>([]);
    const [audits, setAudits] = useState<AuditSummary[]>([]);
    const [cveStats, setCveStats] = useState<CveStats | null>(null);
    const [loading, setLoading] = useState(true);

    const fetchAll = useCallback(async () => {
        setLoading(true);
        try {
            const [evts, auds, cves] = await Promise.all([
                securityApi.listEvents(20),
                securityApi.listAudits(),
                securityApi.getCveStats().catch(() => null),
            ]);
            setEvents(evts);
            setAudits(auds);
            setCveStats(cves);
        } catch (e) {
            console.error('SecurityDashboard: fetch failed', e);
            Toast.error('Failed to load security dashboard');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { fetchAll(); }, [fetchAll]);

    /* ─── Computed Stats ─── */
    const avgScore = audits.length > 0
        ? Math.round(audits.reduce((s, a) => s + a.score, 0) / audits.length)
        : 0;
    const totalPassed = audits.reduce((s, a) => s + a.passed, 0);
    const totalChecks = audits.reduce((s, a) => s + a.total_checks, 0);
    const compliancePct = totalChecks > 0 ? Math.round((totalPassed / totalChecks) * 100) : 0;
    const criticalEvents = events.filter(e => e.severity === 'critical').length;
    const totalCves = cveStats?.total_cves ?? 0;

    /* ─── Audit Donut Data ─── */
    const auditDonut = [
        { name: 'Passed', value: totalPassed },
        { name: 'Failed', value: totalChecks - totalPassed },
    ];

    /* ─── CVE Bar Data ─── */
    const cveBarData = cveStats
        ? Object.entries(cveStats.by_severity).map(([sev, cnt]) => ({ severity: sev, count: cnt }))
        : [];

    /* ─── Hardening sorted ─── */
    const sortedAudits = [...audits].sort((a, b) => a.score - b.score);

    /* ─── Severity icon ─── */
    const sevIcon = (sev: string) => {
        switch (sev) {
            case 'critical': return <AlertOctagon className="w-4 h-4 text-error" />;
            case 'high': return <AlertTriangle className="w-4 h-4 text-warning" />;
            case 'medium': return <Bug className="w-4 h-4 text-info" />;
            default: return <ShieldCheck className="w-4 h-4 text-success" />;
        }
    };

    const scoreColor = (score: number) => {
        if (score >= 80) return 'text-success';
        if (score >= 60) return 'text-warning';
        return 'text-error';
    };

    const barColor = (score: number) => {
        if (score >= 80) return 'bg-success';
        if (score >= 60) return 'bg-warning';
        return 'bg-error';
    };

    const timeAgo = (iso: string) => {
        const diff = Date.now() - new Date(iso).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        return `${Math.floor(hrs / 24)}d ago`;
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center h-full">
                <span className="loading loading-spinner loading-lg text-primary"></span>
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto p-6 space-y-6">
            {/* ─── Header ─── */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-gradient-to-tr from-secondary to-primary rounded-lg text-white">
                        <Shield className="w-6 h-6" />
                    </div>
                    <div>
                        <h1 className="text-2xl font-bold">Security Dashboard</h1>
                        <p className="text-xs text-base-content/50">Centralized security posture overview</p>
                    </div>
                </div>
                <button onClick={fetchAll} className="btn btn-sm btn-outline gap-2">
                    <RefreshCw className="w-4 h-4" /> Refresh
                </button>
            </div>

            {/* ─── Score Cards ─── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                {/* Security Score */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4 flex-row items-center gap-4">
                        <div className={`radial-progress ${scoreColor(avgScore)}`} style={{ '--value': avgScore, '--size': '3.5rem', '--thickness': '4px' } as any} role="progressbar">
                            <span className="text-xs font-bold">{avgScore}%</span>
                        </div>
                        <div>
                            <div className="text-xs text-base-content/50 uppercase tracking-wider">Security Score</div>
                            <div className="text-xl font-bold">{avgScore}<span className="text-sm font-normal text-base-content/50"> / 100</span></div>
                        </div>
                    </div>
                </div>

                {/* Compliance */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4 flex-row items-center gap-4">
                        <div className="p-3 rounded-lg bg-success/10 text-success">
                            <ShieldCheck className="w-6 h-6" />
                        </div>
                        <div>
                            <div className="text-xs text-base-content/50 uppercase tracking-wider">Compliance</div>
                            <div className="text-xl font-bold">{compliancePct}%<span className="text-sm font-normal text-base-content/50"> ({totalPassed}/{totalChecks})</span></div>
                        </div>
                    </div>
                </div>

                {/* Critical Threats */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4 flex-row items-center gap-4">
                        <div className="p-3 rounded-lg bg-error/10 text-error">
                            <ShieldAlert className="w-6 h-6" />
                        </div>
                        <div>
                            <div className="text-xs text-base-content/50 uppercase tracking-wider">Critical Threats</div>
                            <div className="text-xl font-bold">{criticalEvents}<span className="text-sm font-normal text-base-content/50"> active</span></div>
                        </div>
                    </div>
                </div>

                {/* CVE Count */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4 flex-row items-center gap-4">
                        <div className="p-3 rounded-lg bg-warning/10 text-warning">
                            <Bug className="w-6 h-6" />
                        </div>
                        <div>
                            <div className="text-xs text-base-content/50 uppercase tracking-wider">Known CVEs</div>
                            <div className="text-xl font-bold">{totalCves}<span className="text-sm font-normal text-base-content/50"> matched</span></div>
                        </div>
                    </div>
                </div>
            </div>

            {/* ─── Middle Row: Hardening + Audit Donut ─── */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* Hardening Progress */}
                <div className="card bg-base-100 shadow lg:col-span-2">
                    <div className="card-body p-4">
                        <div className="flex items-center gap-2 mb-3">
                            <TrendingUp className="w-4 h-4 text-primary" />
                            <h2 className="font-semibold text-sm">Device Hardening Progress</h2>
                            <span className="badge badge-ghost badge-sm">{audits.length} devices</span>
                        </div>
                        {sortedAudits.length === 0 ? (
                            <div className="text-center text-base-content/40 py-8">No audit data yet. Run a security audit to populate.</div>
                        ) : (
                            <div className="space-y-2 max-h-60 overflow-y-auto pr-2">
                                {sortedAudits.map((a) => (
                                    <div key={a.device_id} className="flex items-center gap-3">
                                        <Server className="w-3.5 h-3.5 text-base-content/40 flex-shrink-0" />
                                        <span className="text-xs w-20 truncate font-mono">Device {a.device_id}</span>
                                        <div className="flex-grow h-3 bg-base-200 rounded-full overflow-hidden">
                                            <div
                                                className={`h-full rounded-full transition-all ${barColor(a.score)}`}
                                                style={{ width: `${a.score}%` }}
                                            />
                                        </div>
                                        <span className={`text-xs font-bold w-10 text-right ${scoreColor(a.score)}`}>{Math.round(a.score)}%</span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {/* Audit Summary Donut */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4 items-center">
                        <h2 className="font-semibold text-sm mb-2">Audit Findings</h2>
                        {totalChecks === 0 ? (
                            <div className="text-center text-base-content/40 py-8 text-xs">No findings</div>
                        ) : (
                            <>
                                <ResponsiveContainer width="100%" height={160}>
                                    <PieChart>
                                        <Pie
                                            data={auditDonut}
                                            cx="50%" cy="50%"
                                            innerRadius={45} outerRadius={65}
                                            dataKey="value"
                                            strokeWidth={0}
                                        >
                                            {auditDonut.map((entry, i) => (
                                                <Cell key={entry.name} fill={PIE_COLORS[i]} />
                                            ))}
                                        </Pie>
                                        <Tooltip />
                                    </PieChart>
                                </ResponsiveContainer>
                                <div className="flex gap-4 text-xs">
                                    <div className="flex items-center gap-1"><div className="w-2.5 h-2.5 rounded-full bg-success" /> Passed ({totalPassed})</div>
                                    <div className="flex items-center gap-1"><div className="w-2.5 h-2.5 rounded-full bg-error" /> Failed ({totalChecks - totalPassed})</div>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* ─── Bottom Row: CVE Chart + Recent Threats + Timeline ─── */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* CVE Overview */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4">
                        <div className="flex items-center gap-2 mb-3">
                            <Bug className="w-4 h-4 text-warning" />
                            <h2 className="font-semibold text-sm">CVE by Severity</h2>
                        </div>
                        {cveBarData.length === 0 ? (
                            <div className="text-center text-base-content/40 py-8 text-xs">No CVE data</div>
                        ) : (
                            <ResponsiveContainer width="100%" height={140}>
                                <BarChart data={cveBarData}>
                                    <XAxis dataKey="severity" tick={{ fontSize: 10 }} />
                                    <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                                    <Tooltip />
                                    <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                                        {cveBarData.map((entry) => (
                                            <Cell key={entry.severity} fill={SEVERITY_COLORS[entry.severity] || '#6b7280'} />
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        )}
                    </div>
                </div>

                {/* Recent Threats */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4">
                        <div className="flex items-center gap-2 mb-3">
                            <ShieldAlert className="w-4 h-4 text-error" />
                            <h2 className="font-semibold text-sm">Recent Threats</h2>
                        </div>
                        {events.length === 0 ? (
                            <div className="text-center text-base-content/40 py-8 text-xs">No active threats</div>
                        ) : (
                            <div className="space-y-2 max-h-48 overflow-y-auto">
                                {events.filter(e => e.severity === 'critical' || e.severity === 'high').slice(0, 6).map((ev) => (
                                    <div key={ev.id} className="flex items-start gap-2 p-2 bg-base-200/50 rounded-lg">
                                        {sevIcon(ev.severity)}
                                        <div className="flex-grow min-w-0">
                                            <div className="text-xs font-medium truncate">{ev.description || ev.event_type}</div>
                                            <div className="text-[10px] text-base-content/40">{ev.source_ip} → {ev.dest_ip}</div>
                                        </div>
                                        <span className={`badge badge-xs ${ev.severity === 'critical' ? 'badge-error' : 'badge-warning'}`}>
                                            {ev.severity}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {/* Incident Timeline */}
                <div className="card bg-base-100 shadow">
                    <div className="card-body p-4">
                        <div className="flex items-center gap-2 mb-3">
                            <Activity className="w-4 h-4 text-primary" />
                            <h2 className="font-semibold text-sm">Incident Timeline</h2>
                        </div>
                        {events.length === 0 ? (
                            <div className="text-center text-base-content/40 py-8 text-xs">No incidents recorded</div>
                        ) : (
                            <div className="relative max-h-48 overflow-y-auto">
                                <div className="absolute left-3 top-0 bottom-0 w-px bg-base-300" />
                                <div className="space-y-3 pl-8">
                                    {events.slice(0, 8).map((ev) => (
                                        <div key={ev.id} className="relative">
                                            <div className="absolute -left-5 top-1 w-2.5 h-2.5 rounded-full border-2 border-base-100"
                                                style={{ backgroundColor: SEVERITY_COLORS[ev.severity] || '#6b7280' }} />
                                            <div className="text-xs font-medium truncate">{ev.event_type.replace(/_/g, ' ')}</div>
                                            <div className="text-[10px] text-base-content/40">{timeAgo(ev.created_at)} · {ev.status}</div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SecurityDashboard;
