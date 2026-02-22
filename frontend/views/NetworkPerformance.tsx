import React, { useState, useEffect } from 'react';
import { RefreshCw, Activity, Server } from 'lucide-react';
import { monitorApi } from '../src/services/api';

// M17: Network Performance Monitoring Dashboard
// Embeds Grafana panels and provides controls for Telegraf config generation.

const NetworkPerformance: React.FC = () => {
  const [status, setStatus] = useState<string>("unknown");
  const [isLoading, setIsLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const checkStatus = async () => {
    try {
      const res = await monitorApi.getStatus();
      setStatus(res.status);
    } catch (e) {
      console.error("Failed to check status", e);
      setStatus("error");
    }
  };

  const handleRegenerateConfig = async () => {
    setIsLoading(true);
    try {
      const res = await monitorApi.updateConfig();
      setLastUpdated(new Date().toLocaleTimeString());
      alert(res.message);
    } catch (e: any) {
      alert("Failed to update config: " + e.message);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    checkStatus();
  }, []);

  return (
    <div className="p-6 space-y-6 h-full overflow-y-auto">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Activity className="h-8 w-8 text-primary" />
            Network Performance
          </h1>
          <p className="text-muted-foreground mt-2 opacity-70">
            Real-time telemetry from SNMP/Telegraf stack.
          </p>
        </div>
        <div className="flex gap-2">
          <button 
            className="btn btn-outline"
            onClick={checkStatus}
            title="Check Monitoring Stack Status"
          >
            <Server className="mr-2 h-4 w-4" />
            Status: {status}
          </button>
          <button 
            className="btn btn-primary"
            onClick={handleRegenerateConfig} 
            disabled={isLoading}
            title="Regenerate Telegraf Config from Inventory"
          >
            <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            Update Config
          </button>
        </div>
      </div>

      {/* Info Cards */}
      <div className="grid gap-4 md:grid-cols-3">
        <div className="card bg-base-100 shadow-xl border border-base-300">
          <div className="card-body p-6">
            <div className="flex flex-row items-center justify-between space-y-0 pb-2">
              <h3 className="tracking-tight text-sm font-medium">Monitoring Stack</h3>
              <Server className="h-4 w-4 text-muted-foreground opacity-50" />
            </div>
            <div className="text-2xl font-bold">Telegraf + InfluxDB</div>
            <p className="text-xs text-muted-foreground opacity-70">Standard TIG Stack</p>
          </div>
        </div>

        <div className="card bg-base-100 shadow-xl border border-base-300">
          <div className="card-body p-6">
            <div className="flex flex-row items-center justify-between space-y-0 pb-2">
              <h3 className="tracking-tight text-sm font-medium">Last Config Update</h3>
              <RefreshCw className="h-4 w-4 text-muted-foreground opacity-50" />
            </div>
            <div className="text-2xl font-bold">{lastUpdated || "Never"}</div>
            <p className="text-xs text-muted-foreground opacity-70">Local Session</p>
          </div>
        </div>
      </div>

      {/* Grafana Embeds */}
      <div className="card bg-base-100 shadow-xl border border-base-300 min-h-[600px]">
        <div className="card-body">
          <h2 className="card-title">Live Dashboard (Grafana)</h2>
          
          <div className="h-[500px] flex items-center justify-center border-2 border-dashed rounded-md bg-base-200/50 mt-4">
            {/* 
                In a real deployment, this would be an IFrame to Grafana:
                <iframe 
                    src="http://localhost:3000/d-solo/..." 
                    width="100%" 
                    height="100%" 
                    frameBorder="0"
                />
            */}
            <div className="text-center space-y-4 p-6">
                <Activity className="h-16 w-16 mx-auto text-base-content opacity-20" />
                <h3 className="text-lg font-semibold">Grafana Dashboard Placeholder</h3>
                <p className="text-sm opacity-70 max-w-sm mx-auto">
                    To view live metrics, please ensure Grafana is running on port 3000 and 
                    configure the dashboard URL in <code>frontend/views/NetworkPerformance.tsx</code>.
                </p>
                <a 
                    href="http://localhost:3000" 
                    target="_blank" 
                    rel="noreferrer"
                    className="btn btn-link"
                >
                    Open Grafana Locally
                </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default NetworkPerformance;