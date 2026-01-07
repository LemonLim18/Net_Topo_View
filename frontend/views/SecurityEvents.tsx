import React from 'react';
import { ShieldAlert, Search, AlertTriangle, AlertOctagon, CheckCircle } from 'lucide-react';
import { 
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell 
} from 'recharts';

const EVENTS = [
    { id: 'EVT-001', time: '10:42:01', source: '192.168.1.105', dest: '10.0.0.5', type: 'SSH Brute Force', severity: 'Critical', status: 'Blocked' },
    { id: 'EVT-002', time: '10:41:55', source: '45.33.22.11', dest: '10.0.0.1', type: 'Port Scan', severity: 'High', status: 'Blocked' },
    { id: 'EVT-003', time: '10:38:12', source: 'Internal', dest: 'Gateway', type: 'Config Change', severity: 'Info', status: 'Logged' },
    { id: 'EVT-004', time: '10:35:00', source: '192.168.5.3', dest: 'External', type: 'Malware Callback', severity: 'Critical', status: 'Quarantined' },
    { id: 'EVT-005', time: '10:30:22', source: '192.168.2.2', dest: '10.0.0.2', type: 'Failed Login', severity: 'Medium', status: 'Alerted' },
];

const SEVERITY_DATA = [
    { name: 'Critical', value: 12, color: 'hsl(var(--er))' },
    { name: 'High', value: 25, color: 'hsl(var(--wa))' },
    { name: 'Medium', value: 45, color: 'hsl(var(--in))' },
    { name: 'Low', value: 80, color: 'hsl(var(--su))' },
];

const SecurityEvents: React.FC = () => {
    return (
        <div className="p-6 h-full overflow-y-auto bg-base-200">
             <div className="flex justify-between items-center mb-6">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <ShieldAlert className="w-6 h-6 text-error" />
                        Security Operations Center
                    </h1>
                    <p className="text-sm opacity-60">Elasticsearch / SIEM Integration</p>
                </div>
                <div className="join">
                    <input className="input input-bordered input-sm join-item w-64" placeholder="Search logs (Lucene syntax)..." />
                    <button className="btn btn-sm btn-primary join-item"><Search className="w-4 h-4" /></button>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 mb-8">
                <div className="lg:col-span-3 card bg-base-100 shadow-lg">
                    <div className="card-body p-4 h-64">
                         <h3 className="font-bold text-sm opacity-70 mb-2">Threats over Time</h3>
                         <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={[
                                { t: '09:00', count: 12 }, { t: '09:30', count: 19 }, { t: '10:00', count: 8 }, { t: '10:30', count: 24 }, { t: '11:00', count: 15 }
                            ]}>
                                <XAxis dataKey="t" tick={{fontSize: 12}} />
                                <YAxis tick={{fontSize: 12}} />
                                <Tooltip cursor={{fill: 'transparent'}} contentStyle={{backgroundColor: 'hsl(var(--b1))', borderColor: 'hsl(var(--b3))', borderRadius: '8px'}} />
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
                                <Pie 
                                    data={SEVERITY_DATA} 
                                    innerRadius={50} 
                                    outerRadius={70} 
                                    paddingAngle={5} 
                                    dataKey="value"
                                >
                                    {SEVERITY_DATA.map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={entry.color} />
                                    ))}
                                </Pie>
                                <Tooltip contentStyle={{backgroundColor: 'hsl(var(--b1))', borderRadius: '8px'}}/>
                            </PieChart>
                        </ResponsiveContainer>
                        <div className="flex flex-wrap gap-2 text-[10px] justify-center">
                            {SEVERITY_DATA.map(d => (
                                <span key={d.name} className="flex items-center gap-1">
                                    <div className="w-2 h-2 rounded-full" style={{backgroundColor: d.color}}></div>
                                    {d.name}
                                </span>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

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
                            {EVENTS.map(evt => (
                                <tr key={evt.id} className="hover">
                                    <td>
                                        <div className={`badge badge-sm font-bold ${
                                            evt.severity === 'Critical' ? 'badge-error' :
                                            evt.severity === 'High' ? 'badge-warning' :
                                            evt.severity === 'Medium' ? 'badge-info' : 'badge-ghost'
                                        }`}>
                                            {evt.severity}
                                        </div>
                                    </td>
                                    <td className="font-mono text-xs">{evt.time}</td>
                                    <td className="font-bold">{evt.type}</td>
                                    <td className="font-mono text-xs text-base-content/70">{evt.source}</td>
                                    <td className="font-mono text-xs text-base-content/70">{evt.dest}</td>
                                    <td>
                                        {evt.status === 'Blocked' && <span className="text-success flex items-center gap-1 text-xs"><CheckCircle className="w-3 h-3"/> Blocked</span>}
                                        {evt.status === 'Quarantined' && <span className="text-warning flex items-center gap-1 text-xs"><AlertOctagon className="w-3 h-3"/> Quarantined</span>}
                                        {evt.status === 'Alerted' && <span className="text-info flex items-center gap-1 text-xs"><AlertTriangle className="w-3 h-3"/> Alerted</span>}
                                        {evt.status === 'Logged' && <span className="text-base-content/50 text-xs">Logged</span>}
                                    </td>
                                    <td>
                                        <button className="btn btn-xs btn-ghost">Details</button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <div className="p-2 border-t border-base-200 bg-base-100 flex justify-between items-center">
                    <div className="text-xs text-base-content/50">Showing 5 of 1243 events</div>
                    <div className="join">
                        <button className="join-item btn btn-xs">«</button>
                        <button className="join-item btn btn-xs btn-active">1</button>
                        <button className="join-item btn btn-xs">2</button>
                        <button className="join-item btn btn-xs">»</button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SecurityEvents;