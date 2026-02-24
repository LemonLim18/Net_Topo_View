import React, { useEffect, useState, useCallback } from 'react';
import { ShieldAlert, Search, AlertTriangle, AlertOctagon, CheckCircle, RefreshCw, Play, BookOpen } from 'lucide-react';
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell
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

const SEVERITY_COLORS: Record<string, string> = {
    critical: 'hsl(var(--er))',
    high: 'hsl(var(--wa))',
    medium: 'hsl(var(--in))',
    low: 'hsl(var(--su))',
};

const SecurityEvents: React.FC = () => {
    const [events, setEvents] = useState<SecurityEvent[]>([]);
    const [audits, setAudits] = useState<AuditSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState('');
    const [severityFilter, setSeverityFilter] = useState('');
    const [respondingId, setRespondingId] = useState<number | null>(null);
    const [responseModal, setResponseModal] = useState<{ action_type: string; description: string; config_snippet: string; requires_approval: boolean } | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        try {
            const [evts, auds] = await Promise.all([
                securityApi.listEvents(50, severityFilter || undefined),
                securityApi.listAudits(),
            ]);
            setEvents(evts);
            setAudits(auds);
        } catch (e) {
            console.error('Failed to fetch security data:', e);
            Toast.error('Failed to load security data');
        } finally {
            setLoading(false);
        }
    }, [severityFilter]);

    useEffect(() => { fetchData(); }, [fetchData]);

    /* Severity distribution for pie chart */
    const severityData = ['critical', 'high', 'medium', 'low'].map(sev => ({
        name: sev.charAt(0).toUpperCase() + sev.slice(1),
        value: events.filter(e => e.severity === sev).length,
        color: SEVERITY_COLORS[sev],
    })).filter(d => d.value > 0);

    /* Time-series bar data (group events by hour) */
    const timeData: { t: string; count: number }[] = [];
    const hourBuckets: Record<string, number> = {};
    events.forEach(evt => {
        if (evt.created_at) {
            const hour = evt.created_at.substring(11, 16) || 'N/A';
            hourBuckets[hour] = (hourBuckets[hour] || 0) + 1;
        }
    });
    Object.entries(hourBuckets).sort().slice(-8).forEach(([t, count]) => timeData.push({ t, count }));

    /* Filtered events */
    const filteredEvents = events.filter(evt => {
        if (filter) {
            const q = filter.toLowerCase();
            return (
                evt.event_type.toLowerCase().includes(q) ||
                evt.source_ip.toLowerCase().includes(q) ||
                evt.dest_ip.toLowerCase().includes(q) ||
                evt.description.toLowerCase().includes(q)
            );
        }
        return true;
    });

    /* Respond to event */
    const handleRespond = async (eventId: number) => {
        setRespondingId(eventId);
        try {
            const resp = await securityApi.respondToEvent(eventId);
            setResponseModal(resp);
            Toast.info('Response generated');
        } catch (e) {
            console.error('Respond failed:', e);
            Toast.error('Incident response failed');
        } finally {
            setRespondingId(null);
        }
    };

    /* Run network-wide audit */
    const handleAuditAll = async () => {
        try {
            await securityApi.runAuditAll();
            Toast.success('Audit triggered', 'Network-wide audit started');
        } catch (e) {
            console.error('Audit all failed:', e);
            Toast.error('Network audit failed');
        }
    };

    /* Analyze threats */
    const handleAnalyze = async () => {
        try {
            await fetch(`${(window as any).__API_BASE || ''}/api/v1/security/threats/analyze`, { method: 'POST' });
            Toast.success('Analysis complete', 'Threat analysis finished');
            fetchData();
        } catch (e) {
            console.error('Analyze failed:', e);
            Toast.error('Threat analysis failed');
        }
    };

    return (
        <div className="p-6 h-full overflow-y-auto bg-base-200">
            {/* Header */}
            <div className="flex justify-between items-center mb-6">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <ShieldAlert className="w-6 h-6 text-error" />
                        Security Operations Center
                    </h1>
                    <p className="text-sm opacity-60">M13 Security Module — Live Events &amp; Audits</p>
                </div>
                <div className="flex gap-2">
                    <button className="btn btn-sm btn-outline gap-1" onClick={handleAnalyze}>
                        <Search className="w-4 h-4" /> Analyze
                    </button>
                    <button className="btn btn-sm btn-primary gap-1" onClick={handleAuditAll}>
                        <Play className="w-4 h-4" /> Audit All
                    </button>
                    <button className="btn btn-sm btn-ghost" onClick={fetchData}>
                        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    </button>
                </div>
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                <div className="stat bg-base-100 rounded-lg shadow p-4">
                    <div className="stat-title text-xs">Total Events</div>
                    <div className="stat-value text-2xl">{events.length}</div>
                </div>
                <div className="stat bg-base-100 rounded-lg shadow p-4">
                    <div className="stat-title text-xs">Critical</div>
                    <div className="stat-value text-2xl text-error">{events.filter(e => e.severity === 'critical').length}</div>
                </div>
                <div className="stat bg-base-100 rounded-lg shadow p-4">
                    <div className="stat-title text-xs">Devices Audited</div>
                    <div className="stat-value text-2xl">{audits.length}</div>
                </div>
                <div className="stat bg-base-100 rounded-lg shadow p-4">
                    <div className="stat-title text-xs">Avg Score</div>
                    <div className="stat-value text-2xl">{audits.length ? Math.round(audits.reduce((s, a) => s + a.score, 0) / audits.length) : '—'}</div>
                </div>
            </div>

            {/* Charts */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 mb-8">
                <div className="lg:col-span-3 card bg-base-100 shadow-lg">
                    <div className="card-body p-4 h-64">
                        <h3 className="font-bold text-sm opacity-70 mb-2">Threats over Time</h3>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={timeData.length ? timeData : [{ t: 'No data', count: 0 }]}>
                                <XAxis dataKey="t" tick={{ fontSize: 12 }} />
                                <YAxis tick={{ fontSize: 12 }} />
                                <Tooltip cursor={{ fill: 'transparent' }} contentStyle={{ backgroundColor: 'hsl(var(--b1))', borderColor: 'hsl(var(--b3))', borderRadius: '8px' }} />
                                <Bar dataKey="count" fill="hsl(var(--er))" radius={[4, 4, 0, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
                <div className="card bg-base-100 shadow-lg">
                    <div className="card-body p-4 h-64 flex flex-col items-center">
                        <h3 className="font-bold text-sm opacity-70 mb-2 w-full text-left">Severity Distribution</h3>
                        <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                                <Pie data={severityData.length ? severityData : [{ name: 'None', value: 1, color: '#555' }]} innerRadius={50} outerRadius={70} paddingAngle={5} dataKey="value">
                                    {(severityData.length ? severityData : [{ color: '#555' }]).map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={entry.color} />
                                    ))}
                                </Pie>
                                <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--b1))', borderRadius: '8px' }} />
                            </PieChart>
                        </ResponsiveContainer>
                        <div className="flex flex-wrap gap-2 text-[10px] justify-center">
                            {severityData.map(d => (
                                <span key={d.name} className="flex items-center gap-1">
                                    <div className="w-2 h-2 rounded-full" style={{ backgroundColor: d.color }}></div>
                                    {d.name}
                                </span>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            {/* Filters */}
            <div className="flex gap-2 mb-4">
                <input
                    className="input input-bordered input-sm w-64"
                    placeholder="Search events..."
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                />
                <select className="select select-bordered select-sm" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
                    <option value="">All Severities</option>
                    <option value="critical">Critical</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                </select>
            </div>

            {/* Event table */}
            <div className="card bg-base-100 shadow-xl overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="table table-zebra w-full">
                        <thead>
                            <tr className="bg-base-200">
                                <th>Severity</th>
                                <th>Time</th>
                                <th>Type</th>
                                <th>Source</th>
                                <th>Destination</th>
                                <th>Status</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading ? (
                                <tr><td colSpan={7} className="text-center py-8"><span className="loading loading-spinner loading-md"></span></td></tr>
                            ) : filteredEvents.length === 0 ? (
                                <tr><td colSpan={7} className="text-center py-8 opacity-50">No security events found</td></tr>
                            ) : (
                                filteredEvents.map(evt => (
                                    <tr key={evt.id} className="hover">
                                        <td>
                                            <div className={`badge badge-sm font-bold ${evt.severity === 'critical' ? 'badge-error' :
                                                evt.severity === 'high' ? 'badge-warning' :
                                                    evt.severity === 'medium' ? 'badge-info' : 'badge-ghost'
                                                }`}>
                                                {evt.severity}
                                            </div>
                                        </td>
                                        <td className="font-mono text-xs">{evt.created_at ? evt.created_at.substring(11, 19) : '—'}</td>
                                        <td className="font-bold text-sm">{evt.event_type.replace(/_/g, ' ')}</td>
                                        <td className="font-mono text-xs text-base-content/70">{evt.source_ip || '—'}</td>
                                        <td className="font-mono text-xs text-base-content/70">{evt.dest_ip || '—'}</td>
                                        <td>
                                            {evt.status === 'blocked' && <span className="text-success flex items-center gap-1 text-xs"><CheckCircle className="w-3 h-3" /> Blocked</span>}
                                            {evt.status === 'new' && <span className="text-warning flex items-center gap-1 text-xs"><AlertTriangle className="w-3 h-3" /> New</span>}
                                            {evt.status === 'resolved' && <span className="text-success flex items-center gap-1 text-xs"><CheckCircle className="w-3 h-3" /> Resolved</span>}
                                            {!['blocked', 'new', 'resolved'].includes(evt.status) && <span className="text-base-content/50 text-xs">{evt.status}</span>}
                                        </td>
                                        <td>
                                            <button
                                                className="btn btn-xs btn-ghost"
                                                onClick={() => handleRespond(evt.id)}
                                                disabled={respondingId === evt.id}
                                            >
                                                {respondingId === evt.id ? <span className="loading loading-spinner loading-xs"></span> : <BookOpen className="w-3 h-3" />}
                                                Respond
                                            </button>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
                <div className="p-2 border-t border-base-200 bg-base-100 flex justify-between items-center">
                    <div className="text-xs text-base-content/50">Showing {filteredEvents.length} of {events.length} events</div>
                </div>
            </div>

            {/* Response Modal */}
            {responseModal && (
                <dialog className="modal modal-open">
                    <div className="modal-box max-w-2xl">
                        <h3 className="font-bold text-lg flex items-center gap-2">
                            <AlertOctagon className="w-5 h-5 text-warning" />
                            Incident Response
                        </h3>
                        <div className="mt-4 space-y-3">
                            <div>
                                <span className="badge badge-outline mr-2">{responseModal.action_type}</span>
                                {responseModal.requires_approval && <span className="badge badge-warning badge-sm">Requires Approval</span>}
                            </div>
                            <p className="text-sm">{responseModal.description}</p>
                            <div className="bg-base-200 p-3 rounded-lg">
                                <h4 className="text-xs font-bold opacity-70 mb-1">Remediation Config</h4>
                                <pre className="text-xs font-mono whitespace-pre-wrap">{responseModal.config_snippet}</pre>
                            </div>
                        </div>
                        <div className="modal-action">
                            <button className="btn btn-sm" onClick={() => setResponseModal(null)}>Close</button>
                        </div>
                    </div>
                    <form method="dialog" className="modal-backdrop" onClick={() => setResponseModal(null)}><button>close</button></form>
                </dialog>
            )}
        </div>
    );
};

export default SecurityEvents;