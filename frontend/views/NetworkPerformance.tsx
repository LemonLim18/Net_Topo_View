import React from 'react';
import { 
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, Legend, Cell 
} from 'recharts';
import { Activity, Wifi, Globe, ArrowUpRight, ArrowDownRight } from 'lucide-react';

const generateData = (count: number) => {
    return Array.from({ length: count }, (_, i) => ({
        time: `${String(i).padStart(2, '0')}:00`,
        trafficIn: Math.floor(Math.random() * 500) + 100,
        trafficOut: Math.floor(Math.random() * 400) + 50,
        latency: Math.floor(Math.random() * 40) + 10,
        loss: Math.random() > 0.8 ? Math.floor(Math.random() * 5) : 0
    }));
};

const APP_DATA = [
    { name: 'YouTube/Video', usage: 450, color: 'hsl(var(--p))' },
    { name: 'MS Teams/Zoom', usage: 320, color: 'hsl(var(--s))' },
    { name: 'AWS Internal', usage: 280, color: 'hsl(var(--a))' },
    { name: 'HTTPS (Web)', usage: 210, color: 'hsl(var(--in))' },
    { name: 'SQL Database', usage: 150, color: 'hsl(var(--wa))' },
    { name: 'Social Media', usage: 80, color: 'hsl(var(--er))' },
];

const data = generateData(24);

const NetworkPerformance: React.FC = () => {
  // Common style for chart text to ensure visibility in dark mode
  const axisStyle = { 
      fontSize: 12, 
      fill: 'hsl(var(--bc))', // Uses the theme's text color
      opacity: 0.7
  };

  const axisLineStyle = {
      stroke: 'hsl(var(--bc))',
      strokeOpacity: 0.2
  };

  const gridStyle = {
      stroke: 'hsl(var(--bc))',
      strokeOpacity: 0.1
  };

  const tooltipStyle = {
      backgroundColor: 'hsl(var(--b1))',
      borderColor: 'hsl(var(--b3))',
      color: 'hsl(var(--bc))',
      borderRadius: '8px',
      boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)'
  };

  return (
    <div className="p-6 h-full overflow-y-auto bg-base-200 text-base-content">
        <div className="flex justify-between items-center mb-6">
            <div>
                <h1 className="text-2xl font-bold flex items-center gap-2">
                    <Activity className="w-6 h-6 text-primary" />
                    Network Performance
                </h1>
                <p className="text-sm opacity-60">Real-time metrics & Application Traffic Analysis</p>
            </div>
            <div className="flex gap-2">
                <select className="select select-sm select-bordered">
                    <option>Last 24 Hours</option>
                    <option>Last 7 Days</option>
                    <option>Last 30 Days</option>
                </select>
                <button className="btn btn-sm btn-ghost">Refresh</button>
            </div>
        </div>

        {/* Key Metrics Rows */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
             <div className="stats shadow bg-base-100 text-base-content">
                <div className="stat">
                    <div className="stat-figure text-primary">
                        <Activity className="w-8 h-8" />
                    </div>
                    <div className="stat-title opacity-70">Total Throughput</div>
                    <div className="stat-value text-primary">8.4 Gbps</div>
                    <div className="stat-desc text-success flex items-center gap-1">
                        <ArrowUpRight className="w-3 h-3"/> 12% vs last month
                    </div>
                </div>
            </div>
            <div className="stats shadow bg-base-100 text-base-content">
                <div className="stat">
                    <div className="stat-figure text-secondary">
                        <Wifi className="w-8 h-8" />
                    </div>
                    <div className="stat-title opacity-70">Avg Latency</div>
                    <div className="stat-value text-secondary">24ms</div>
                    <div className="stat-desc text-success flex items-center gap-1">
                         <ArrowDownRight className="w-3 h-3"/> 2ms improvement
                    </div>
                </div>
            </div>
            <div className="stats shadow bg-base-100 text-base-content">
                <div className="stat">
                     <div className="stat-figure text-accent">
                        <Globe className="w-8 h-8" />
                    </div>
                    <div className="stat-title opacity-70">Top App Category</div>
                    <div className="stat-value text-accent">Streaming</div>
                    <div className="stat-desc opacity-70">45% of total bandwidth</div>
                </div>
            </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Traffic Chart */}
            <div className="card bg-base-100 shadow-xl col-span-2 lg:col-span-1">
                <div className="card-body h-80">
                    <h2 className="card-title text-sm opacity-70 mb-4">Interface Traffic (Mbps)</h2>
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={data}>
                            <defs>
                                <linearGradient id="colorIn" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="hsl(var(--p))" stopOpacity={0.8}/>
                                    <stop offset="95%" stopColor="hsl(var(--p))" stopOpacity={0}/>
                                </linearGradient>
                                <linearGradient id="colorOut" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="hsl(var(--s))" stopOpacity={0.8}/>
                                    <stop offset="95%" stopColor="hsl(var(--s))" stopOpacity={0}/>
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" {...gridStyle} />
                            <XAxis dataKey="time" tick={axisStyle} axisLine={axisLineStyle} />
                            <YAxis tick={axisStyle} axisLine={axisLineStyle} />
                            <Tooltip contentStyle={tooltipStyle} />
                            <Area type="monotone" dataKey="trafficIn" stroke="hsl(var(--p))" fillOpacity={1} fill="url(#colorIn)" name="Inbound" />
                            <Area type="monotone" dataKey="trafficOut" stroke="hsl(var(--s))" fillOpacity={1} fill="url(#colorOut)" name="Outbound" />
                            <Legend wrapperStyle={{ paddingTop: '10px' }} />
                        </AreaChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Latency Chart */}
             <div className="card bg-base-100 shadow-xl col-span-2 lg:col-span-1">
                <div className="card-body h-80">
                    <h2 className="card-title text-sm opacity-70 mb-4">Edge Latency (ms)</h2>
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" {...gridStyle} />
                            <XAxis dataKey="time" tick={axisStyle} axisLine={axisLineStyle} />
                            <YAxis tick={axisStyle} axisLine={axisLineStyle} />
                            <Tooltip contentStyle={tooltipStyle} />
                            <Line type="monotone" dataKey="latency" stroke="hsl(var(--wa))" strokeWidth={3} dot={false} name="Latency" />
                            <Legend wrapperStyle={{ paddingTop: '10px' }} />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </div>

             {/* Application/Website Usage Chart (Horizontal Bar) */}
             <div className="card bg-base-100 shadow-xl col-span-2 lg:col-span-1">
                <div className="card-body h-72">
                    <div className="flex justify-between items-center mb-2">
                        <h2 className="card-title text-sm opacity-70">Top Application Usage (GB)</h2>
                        <div className="badge badge-outline text-xs">Layer 7 Analysis</div>
                    </div>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart layout="vertical" data={APP_DATA} margin={{ left: 20, right: 20 }}>
                            <CartesianGrid strokeDasharray="3 3" {...gridStyle} horizontal={false} />
                            <XAxis type="number" hide />
                            <YAxis 
                                dataKey="name" 
                                type="category" 
                                width={100} 
                                tick={{...axisStyle, fontSize: 11}} 
                                axisLine={axisLineStyle}
                                interval={0}
                            />
                            <Tooltip cursor={{fill: 'hsl(var(--bc))', opacity: 0.1}} contentStyle={tooltipStyle} />
                            <Bar dataKey="usage" radius={[0, 4, 4, 0]} barSize={20} name="Bandwidth (GB)">
                                {APP_DATA.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={entry.color} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>

             {/* Packet Loss Chart */}
             <div className="card bg-base-100 shadow-xl col-span-2 lg:col-span-1">
                <div className="card-body h-72">
                    <h2 className="card-title text-sm opacity-70 mb-4">Packet Loss Events</h2>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" {...gridStyle} />
                            <XAxis dataKey="time" tick={axisStyle} axisLine={axisLineStyle} />
                            <YAxis tick={axisStyle} axisLine={axisLineStyle} />
                            <Tooltip contentStyle={tooltipStyle} cursor={{fill: 'hsl(var(--bc))', opacity: 0.1}} />
                            <Bar dataKey="loss" fill="hsl(var(--er))" name="Dropped Packets" radius={[4, 4, 0, 0]} />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>
    </div>
  );
};

export default NetworkPerformance;