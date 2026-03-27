import React, { useEffect, useState } from 'react';
import { Cpu, Globe, Layers, Database } from 'lucide-react';
import { complianceApi } from '../../src/services/api';

type Scope = {
  vendors?: string[];
  device_roles?: string[];
  device_groups?: string[];
  devices?: string[];
};

interface Props {
  scope: Scope;
  onChange: (scope: Scope) => void;
}

const PolicyScopeSelector: React.FC<Props> = ({ scope, onChange }) => {
  const [opts, setOpts] = useState<{ available: boolean; roles: string[]; tags: string[]; sites: string[]; tenants: string[] } | null>(null);

  useEffect(() => {
    complianceApi.scopeOptions()
      .then((r: any) => setOpts(r))
      .catch(() => setOpts({ available: false, roles: [], tags: [], sites: [], tenants: [] }));
  }, []);

  const toggle = (k: keyof Scope, v: string) => {
    const cur = new Set((scope[k] || []) as string[]);
    if (cur.has(v)) cur.delete(v);
    else cur.add(v);
    onChange({ ...scope, [k]: Array.from(cur) });
  };

  const setCsv = (k: keyof Scope, v: string) =>
    onChange({
      ...scope,
      [k]: v
        .split(',')
        .map(s => s.trim())
        .filter(Boolean),
    });

  return (
    <div className="space-y-4">
      {/* CSV Inputs */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="form-control w-full">
          <div className="label pt-0 pb-1">
            <span className="label-text text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5" /> Vendors
            </span>
          </div>
          <input 
            className="input input-sm input-bordered h-9 bg-base-100/30" 
            placeholder="Cisco, Huawei, Juniper..."
            value={(scope.vendors || []).join(', ')} 
            onChange={(e) => setCsv('vendors', e.target.value)} 
          />
        </label>

        <label className="form-control w-full">
           <div className="label pt-0 pb-1">
            <span className="label-text text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5">
              <Globe className="w-3.5 h-3.5" /> Specific Devices
            </span>
          </div>
          <input 
            className="input input-sm input-bordered h-9 bg-base-100/30" 
            placeholder="router-01, switch-05..."
            value={(scope.devices || []).join(', ')} 
            onChange={(e) => setCsv('devices', e.target.value)} 
          />
        </label>
      </div>

      {/* NetBox Scope Section */}
      <div className="bg-base-100/40 backdrop-blur-sm border border-base-300 rounded-xl p-4 transition-all">
        <div className="flex items-center gap-2 mb-4">
          <Database className="w-4 h-4 text-primary" />
          <span className="text-xs font-bold uppercase tracking-widest opacity-80">NetBox Dynamic Scope</span>
        </div>

        {!opts?.available ? (
          <div className="text-xs opacity-60 italic bg-base-300/30 p-3 rounded-lg border border-dashed border-base-300 flex items-center justify-center">
            NetBox integration offline. Using manual scope definitions.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6">
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase opacity-40">
                <Layers className="w-3 h-3" /> Device Roles
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto p-1">
                {(opts.roles || []).map((r) => (
                  <button
                    type="button"
                    key={r}
                    className={`btn btn-xs rounded-lg transition-all ${((scope.device_roles || []) as string[]).includes(r) 
                      ? 'btn-primary shadow-sm scale-105' 
                      : 'btn-ghost hover:bg-base-300/50 opacity-60 hover:opacity-100'}`}
                    onClick={() => toggle('device_roles', r)}
                  >
                    {r}
                  </button>
                ))}
              </div>
            </div>
            {/* Sites / Tenants / Tags section removed as requested */}
          </div>
        )}
      </div>
    </div>
  );
};

export default PolicyScopeSelector;
