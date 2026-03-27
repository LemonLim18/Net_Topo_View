import React, { useEffect, useState } from 'react';
import { 
  Shield, History, Camera, Info, Calendar, Smartphone, 
  Activity, GitCompareArrows, CheckCircle2,
  AlertCircle
} from 'lucide-react';
import { complianceApi, type ComplianceResultRecord } from '../src/services/api';
import { Toast } from '../src/utils/swal';

interface Props {
  deviceId: number | null;
}

const ComplianceDeviceDetail: React.FC<Props> = ({ deviceId }) => {
  const [baseline, setBaseline] = useState<any>(null);
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [history, setHistory] = useState<ComplianceResultRecord[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!deviceId) return;
    setLoading(true);
    try {
      const [b, s, h] = await Promise.all([
        complianceApi.getBaseline(deviceId),
        complianceApi.listSnapshots(deviceId, 50),
        complianceApi.deviceResults(deviceId, 20),
      ]);
      setBaseline(b.baseline);
      setSnapshots(s.items || []);
      setHistory(h.items || []);
    } catch (e: any) {
      Toast.error('Failed to load compliance device detail', e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [deviceId]);

  const onCollect = async () => {
    if (!deviceId) return;
    try {
      await complianceApi.collectSnapshot(deviceId);
      Toast.success('Snapshot collected');
      await load();
    } catch (e: any) {
      Toast.error('Collect failed', e.message);
    }
  };

  const onSetBaseline = async (snapshotId: number) => {
    if (!deviceId) return;
    try {
      await complianceApi.setBaseline(deviceId, snapshotId);
      Toast.success('Baseline updated');
      await load();
    } catch (e: any) {
      Toast.error('Set baseline failed', e.message);
    }
  };

  const latest = history[0];

  return (
    <div className="h-full flex flex-col overflow-hidden bg-base-200">
      {/* Header */}
      <div className="px-6 py-4 flex items-center justify-between bg-base-100/40 backdrop-blur-md border-b border-base-300 shrink-0">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center text-primary shadow-sm border border-primary/20">
            <Smartphone className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-xl font-black tracking-tight flex items-center gap-2">
              Device 360 {deviceId ? `#${deviceId}` : ''}
              {(latest as any)?.hostname && (
                <span className="text-xs font-bold uppercase tracking-widest opacity-40 ml-2">
                   · {String((latest as any).hostname)}
                </span>
              )}
            </h1>
            <p className="text-[10px] font-bold uppercase tracking-widest opacity-40 mt-0.5">Deep Compliance & Drift Analysis</p>
          </div>
        </div>
        <button 
          className="btn btn-sm btn-primary gap-2 shadow-lg shadow-primary/20" 
          onClick={onCollect} 
          disabled={!deviceId || loading}
        >
          <Camera className="w-4 h-4" />
          Capture New Snapshot
        </button>
      </div>

      <div className="grow overflow-y-auto p-6 space-y-6 custom-scrollbar">
        {!deviceId ? (
          <div className="flex flex-col items-center justify-center py-20 gap-4 opacity-40">
             <div className="w-16 h-16 rounded-full bg-base-300 flex items-center justify-center">
                <Smartphone className="w-8 h-8" />
             </div>
             <p className="text-sm font-bold uppercase tracking-widest">Select a device to begin analysis</p>
          </div>
        ) : loading ? (
          <div className="flex flex-col items-center justify-center py-20 gap-4 opacity-60">
             <span className="loading loading-spinner loading-lg text-primary" />
             <p className="text-[10px] font-bold uppercase tracking-widest animate-pulse">Analyzing Configuration History...</p>
          </div>
        ) : (
          <>
            {/* KPI Stats Grid */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-5 shadow-sm relative overflow-hidden group hover:bg-base-100/80 transition-all">
                <div className="flex justify-between items-start mb-2">
                  <span className="text-[10px] font-black uppercase tracking-widest opacity-30">Baseline Anchor</span>
                  <History className="w-4 h-4 opacity-20 group-hover:text-primary group-hover:opacity-60 transition-all" />
                </div>
                <div className="text-2xl font-black">{baseline?.snapshot_id || 'Not Set'}</div>
                <div className="flex items-center gap-1.5 mt-2 opacity-50 text-[10px] uppercase font-bold tracking-tight">
                  <Calendar className="w-3 h-3" />
                  {baseline?.captured_at ? new Date(baseline.captured_at).toLocaleDateString() : 'Baseline required'}
                </div>
              </div>

              <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-5 shadow-sm relative overflow-hidden group hover:bg-base-100/80 transition-all">
                <div className="flex justify-between items-start mb-2">
                  <span className="text-[10px] font-black uppercase tracking-widest opacity-30">Current State</span>
                  <Activity className="w-4 h-4 opacity-20 group-hover:text-info group-hover:opacity-60 transition-all" />
                </div>
                <div className={`text-2xl font-black uppercase tracking-tight ${latest?.status === 'compliant' ? 'text-success' : 'text-error'}`}>
                  {latest?.status || 'Unknown'}
                </div>
                <div className="flex items-center gap-1.5 mt-2 opacity-50 text-[10px] uppercase font-bold tracking-tight">
                  <Shield className="w-3 h-3" />
                  Severity: {latest?.severity || 'N/A'}
                </div>
              </div>

              <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-5 shadow-sm relative overflow-hidden group hover:bg-base-100/80 transition-all">
                <div className="flex justify-between items-start mb-2">
                  <span className="text-[10px] font-black uppercase tracking-widest opacity-30">Confidence Score</span>
                  <Info className="w-4 h-4 opacity-20 group-hover:text-warning group-hover:opacity-60 transition-all" />
                </div>
                <div className="text-2xl font-black">{latest?.compliance_score ?? '0'}%</div>
                <div className="flex items-center gap-1.5 mt-2 opacity-50 text-[10px] uppercase font-bold tracking-tight">
                  <GitCompareArrows className="w-3 h-3" />
                  Drift Rate: {latest?.drift_pct ?? '0'}%
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
              {/* Snapshot Timeline */}
              <div className="xl:col-span-5 space-y-4">
                 <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-4 shadow-sm h-full flex flex-col">
                    <div className="flex items-center justify-between mb-4 px-2">
                       <h3 className="text-[10px] font-black uppercase tracking-widest opacity-40 flex items-center gap-2">
                          <History className="w-3.5 h-3.5" /> Configuration Timeline
                       </h3>
                       <span className="badge badge-link border-base-300 opacity-40 text-[9px] font-bold uppercase">{snapshots.length} Points</span>
                    </div>

                    <div className="grow overflow-y-auto max-h-[500px] custom-scrollbar space-y-1">
                       {snapshots.map((s) => (
                         <div 
                          key={s.id} 
                          className={`flex items-center justify-between p-3 rounded-xl transition-all border ${baseline?.snapshot_id === s.id ? 'bg-primary/5 border-primary/20' : 'bg-transparent border-transparent hover:bg-base-200/50'}`}
                        >
                           <div className="flex items-center gap-3">
                              <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-mono text-xs font-bold ${baseline?.snapshot_id === s.id ? 'bg-primary text-primary-content shadow-sm' : 'bg-base-300/50 opacity-40'}`}>
                                 {s.id}
                              </div>
                              <div>
                                 <div className="text-xs font-bold flex items-center gap-1.5 uppercase tracking-wide">
                                    {s.snapshot_type}
                                    {baseline?.snapshot_id === s.id && <Shield className="w-2.5 h-2.5 text-primary" />}
                                 </div>
                                 <div className="text-[10px] opacity-40 font-bold uppercase tracking-tighter">
                                    {new Date(s.captured_at).toLocaleString()}
                                 </div>
                              </div>
                           </div>
                           <button 
                            className={`btn btn-xs rounded-lg ${baseline?.snapshot_id === s.id ? 'btn-disabled opacity-0' : 'btn-outline border-base-300 opacity-40 hover:opacity-100 hover:btn-primary'}`} 
                            onClick={() => onSetBaseline(s.id)}
                           >
                             Set Baseline
                           </button>
                         </div>
                       ))}
                    </div>
                 </div>
              </div>

              {/* Enhanced Diff Area */}
              <div className="xl:col-span-7 space-y-4">
                 <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-6 shadow-sm min-h-full space-y-4">
                    <div className="flex items-center justify-between mb-2">
                       <h3 className="text-[10px] font-black uppercase tracking-widest opacity-40 flex items-center gap-2">
                          <Activity className="w-3.5 h-3.5 text-warning" /> Forensic Diff & Violations
                       </h3>
                       {latest?.status === 'compliant' && (
                         <div className="flex items-center gap-1.5 text-success">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            <span className="text-[10px] font-bold uppercase tracking-widest">Verified Clean</span>
                         </div>
                       )}
                    </div>

                    {!latest ? (
                      <div className="flex flex-col items-center justify-center py-20 gap-3 opacity-20 border-2 border-dashed border-base-300 rounded-2xl">
                         <Activity className="w-12 h-12" />
                         <p className="text-xs font-bold uppercase tracking-widest">No compliance data available</p>
                      </div>
                    ) : (
                      <div className="space-y-4">
                         {latest.reason && (
                           <div className="p-4 bg-base-300/30 rounded-xl border border-base-300/50 flex gap-3">
                              <Info className="w-5 h-5 text-info shrink-0" />
                              <div className="text-xs leading-relaxed opacity-80">
                                 <b className="uppercase text-[9px] tracking-widest block mb-1 opacity-50">Structural Analysis</b>
                                 {latest.reason}
                              </div>
                           </div>
                         )}

                         <div className="relative group">
                            <div className="absolute top-2 right-4 text-[8px] font-black uppercase tracking-widest opacity-20 select-none">Config Logic Trace</div>
                            <pre className="text-xs font-mono bg-neutral-900 text-neutral-300 p-5 rounded-xl max-h-[400px] overflow-auto whitespace-pre border border-white/5 shadow-inner custom-scrollbar leading-relaxed">
                              {latest.status === 'compliant' ? (
                                <div className="text-success/50 italic opacity-80">Full compliance verified. No unauthorized configuration drift detected within baseline parameters.</div>
                              ) : (
                                <div dangerouslySetInnerHTML={{ __html: [
                                  `<span style="color: #60a5fa; opacity: 0.5;">[${new Date(latest.checked_at || '').toLocaleTimeString()}] Analysis triggered</span>`,
                                  `Status: <span style="color: ${latest.status === 'compliant' ? '#4ade80' : '#fb7185'}">${latest.status}</span>`,
                                  `Score:  <span style="color: #c084fc">${latest.compliance_score ?? '-'}%</span>`,
                                  '',
                                  '<span style="color: #fb7185; font-weight: bold;">[!] DETECTED DEVIATIONS</span>',
                                  ...((latest.missing_lines || []).map((x) => `<span style="color: #fb7185; opacity: 0.8;">- MISSING: ${x}</span>`)),
                                  ...((latest.extra_lines || []).map((x) => `<span style="color: #4ade80; opacity: 0.8;">+ EXTRA:   ${x}</span>`)),
                                  ...((latest.changed_lines || []).map((x: any) => `<span style="color: #facc15; opacity: 0.8;">~ CHANGE:  ${x?.baseline || ''} ➔ ${x?.actual || ''}</span>`)),
                                  '',
                                  '<span style="color: #60a5fa; font-weight: bold;">[i] POLICY VIOLATIONS</span>',
                                  ...((latest.violated_policies || []).map((v: any) => `<span style="color: #fb7185; opacity: 0.9;">! [${v?.severity || '-'}] ${v?.policy_name || v?.policy_id || '-'}: ${v?.message || ''}</span>`)),
                                ].join('\n') }} />
                              )}
                            </pre>
                         </div>
                         
                         {latest.violated_policies && latest.violated_policies.length > 0 && (
                            <div className="flex flex-wrap gap-2">
                               {latest.violated_policies.map((v: any, idx) => (
                                 <div key={idx} className="flex items-center gap-2 px-3 py-1.5 bg-error/5 border border-error/20 rounded-full text-[10px] font-bold text-error uppercase tracking-tight">
                                    <AlertCircle className="w-3 h-3" />
                                    {v.policy_id || 'Policy Violation'}
                                 </div>
                               ))}
                            </div>
                         )}
                      </div>
                    )}
                 </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default ComplianceDeviceDetail;
