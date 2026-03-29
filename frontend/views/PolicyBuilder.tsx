import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { 
  Save, Trash2, Copy, ToggleLeft, ToggleRight, 
  Play, Search, ShieldCheck, List, Settings, Info, 
  Database, Activity, FilePlus
} from 'lucide-react';
import PolicyRuleEditor from '../components/compliance/PolicyRuleEditor';
import PolicyScopeSelector from '../components/compliance/PolicyScopeSelector';
import PolicyVersionHistory from '../components/compliance/PolicyVersionHistory';
import { complianceApi, policyApi, type PolicyRecord } from '../src/services/api';
import { Toast, Confirm } from '../src/utils/swal';
import type { ViewId } from '../types';
import { COMPLIANCE_HUB_TAB_KEY, POLICY_FOCUS_IDS_KEY } from '../utils/complianceNavigationKeys';

const CATEGORIES = ['interface', 'vlan', 'ospf', 'static_route', 'acl', 'snmp', 'logging', 'ntp'];

const blankPolicy = (): PolicyRecord => ({
  policy_id: '',
  name: '',
  description: '',
  category: 'interface',
  enabled: true,
  scope: {},
  rules: [{ id: 'r1', action: 'must_exist', target: '', severity: 'medium' }],
  normalization: { ignore_patterns: [] },
  thresholds: { auto_alert: true, alert_severity_min: 'medium' },
});

export interface PolicyBuilderProps {
  setView?: (v: ViewId) => void;
  variant?: 'page' | 'embedded';
  onRequestScan?: () => void;
  onOpenResults?: () => void;
}

const PolicyBuilder: React.FC<PolicyBuilderProps> = ({
  setView,
  variant = 'page',
  onRequestScan,
  onOpenResults,
}) => {
  const [items, setItems] = useState<PolicyRecord[]>([]);
  const [selected, setSelected] = useState<PolicyRecord>(blankPolicy());
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testDeviceId, setTestDeviceId] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [isResizing, setIsResizing] = useState(false);
  const [focusPolicyIds, setFocusPolicyIds] = useState<string[]>([]);

  const selectedId = selected.policy_id || null;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await policyApi.list(true);
      setItems(r.items || []);
    } catch (e: any) {
      Toast.error('Failed to load policies', e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(POLICY_FOCUS_IDS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as unknown;
      if (!Array.isArray(parsed) || !parsed.every((x) => typeof x === 'string')) return;
      sessionStorage.removeItem(POLICY_FOCUS_IDS_KEY);
      setFocusPolicyIds(parsed);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!focusPolicyIds.length || loading) return;
    const hit = items.find((p) => focusPolicyIds.includes(p.policy_id));
    if (hit) setSelected(hit);
  }, [items, loading, focusPolicyIds]);

  const filteredItems = useMemo(() => {
    return items.filter(p => 
      p.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
      p.policy_id.toLowerCase().includes(searchTerm.toLowerCase())
    );
  }, [items, searchTerm]);

  const canSave = useMemo(() => Boolean(selected.name && selected.category && selected.rules.length > 0), [selected]);

  const onSave = async () => {
    try {
      if (!selected.policy_id) {
        const created = await policyApi.create(selected);
        setSelected(created.policy);
        Toast.success(
          'Policy created',
          onRequestScan ? undefined : 'Open Compliance → Overview to run a global scan when ready.',
        );
      } else {
        const updated = await policyApi.update(selected.policy_id, selected);
        setSelected(updated.policy);
        Toast.success(
          'Policy updated',
          onRequestScan ? undefined : 'Open Compliance → Overview to run a global scan when ready.',
        );
      }
      await reload();
      if (onRequestScan) {
        const run = await Confirm.ask(
          'Run global compliance scan now?',
          'Refreshes drift results against the updated policy.',
        );
        if (run) onRequestScan();
      }
    } catch (e: any) {
      Toast.error('Save failed', e.message);
    }
  };

  const onDelete = async () => {
    if (!selected.policy_id) return;
    try {
      await policyApi.remove(selected.policy_id);
      Toast.success('Policy deleted');
      setSelected(blankPolicy());
      await reload();
    } catch (e: any) {
      Toast.error('Delete failed', e.message);
    }
  };

  const onClone = async () => {
    if (!selected.policy_id) return;
    try {
      const out = await policyApi.clone(selected.policy_id, 'clone');
      setSelected(out.policy);
      Toast.success('Policy cloned');
      await reload();
    } catch (e: any) {
      Toast.error('Clone failed', e.message);
    }
  };

  const onToggle = async () => {
    if (!selected.policy_id) return;
    try {
      const out = await policyApi.update(selected.policy_id, { enabled: !selected.enabled });
      setSelected(out.policy);
      Toast.success(out.policy.enabled ? 'Policy enabled' : 'Policy disabled');
      await reload();
    } catch (e: any) {
      Toast.error('Toggle failed', e.message);
    }
  };

  const onTest = async () => {
    if (!selected.policy_id) return;
    if (!testDeviceId) {
      Toast.warning('Provide a device ID for test');
      return;
    }
    setTesting(true);
    try {
      const r = await policyApi.test(selected.policy_id, { device_id: Number(testDeviceId) });
      Toast.success(`Test finished: ${r.status}`, `${(r.violations || []).length} violation(s)`);
    } catch (e: any) {
      Toast.error('Test failed', e.message);
    } finally {
      setTesting(false);
    }
  };

  const startResizing = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  const stopResizing = useCallback(() => {
    setIsResizing(false);
  }, []);

  const resize = useCallback((e: MouseEvent) => {
    if (isResizing) {
      const newWidth = e.clientX;
      if (newWidth > 200 && newWidth < 800) {
        setSidebarWidth(newWidth);
      }
    }
  }, [isResizing]);

  useEffect(() => {
    if (isResizing) {
      window.addEventListener('mousemove', resize);
      window.addEventListener('mouseup', stopResizing);
    } else {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mouseup', stopResizing);
    }
    return () => {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mouseup', stopResizing);
    };
  }, [isResizing, resize, stopResizing]);

  const openResults = () => {
    if (onOpenResults) {
      onOpenResults();
      return;
    }
    try {
      sessionStorage.setItem(COMPLIANCE_HUB_TAB_KEY, 'results');
    } catch {
      /* ignore */
    }
    setView?.('compliance_hub');
  };

  const runScanFromHeader = async () => {
    if (onRequestScan) {
      onRequestScan();
      return;
    }
    try {
      const res = await complianceApi.scanAll();
      Toast.success('Compliance scan started', `job_id=${res.job_id}`);
    } catch (e: any) {
      Toast.error('Scan failed', e.message);
    }
  };

  return (
    <div className={`h-full flex overflow-hidden bg-base-200 ${isResizing ? 'cursor-col-resize select-none' : ''}`}>
      {/* Sidebar Navigation */}
      <aside 
        className="relative border-r border-base-300 flex flex-col bg-base-100 shadow-sm z-10 shrink-0"
        style={{ width: `${sidebarWidth}px`, minWidth: '200px' }}
      >
        <div className="p-4 border-b border-base-300 space-y-3">
          <div className="flex items-center justify-between h-10">
            <div className="flex items-center gap-3">
              <ShieldCheck className="w-6 h-6 text-primary" />
              <h2 className="text-xl font-bold tracking-tight">Policies</h2>
            </div>
            <button 
              className="btn btn-ghost btn-xs btn-square hover:bg-primary/10 hover:text-primary transition-all active:scale-95" 
              onClick={() => setSelected(blankPolicy())}
              title="Create New Policy"
            >
              <FilePlus className="w-4 h-4" />
            </button>
          </div>
          <div className="relative">
            <Search className="absolute left-3 top-2.5 w-4 h-4 opacity-50" />
            <input 
              className="input input-sm input-bordered w-full pl-9 h-10" 
              placeholder="Filter policies..." 
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
        </div>

        <div className="grow overflow-y-auto custom-scrollbar p-2 space-y-1">
          {loading ? (
             <div className="flex flex-col items-center justify-center p-8 opacity-40">
                <span className="loading loading-spinner loading-md text-primary" />
                <span className="text-[10px] mt-2 font-bold uppercase tracking-widest">Loading...</span>
             </div>
          ) : filteredItems.length === 0 ? (
            <div className="text-xs opacity-40 italic text-center py-12">No policies found</div>
          ) : (
            filteredItems.map(p => (
              <button
                key={p.policy_id}
                className={`w-full group flex flex-col p-3 rounded-xl transition-all ${
                  selected.policy_id === p.policy_id 
                    ? 'bg-primary/10 border border-primary/20 text-primary active:scale-95' 
                    : 'bg-transparent border border-transparent hover:bg-base-200/50 hover:border-base-300 opacity-70 hover:opacity-100'
                } ${focusPolicyIds.includes(p.policy_id) ? 'ring-2 ring-accent/50 ring-offset-2 ring-offset-base-200' : ''}`}
                onClick={() => setSelected(p)}
              >
                <div className="flex items-start justify-between w-full">
                  <span className="font-bold text-sm truncate pr-2">{p.name}</span>
                  {!p.enabled && <span className="badge badge-error badge-outline badge-xs opacity-60">Disabled</span>}
                </div>
                <div className="flex items-center gap-2 mt-1 text-[10px] uppercase font-bold tracking-tight opacity-50">
                   <span className="truncate">{p.policy_id}</span>
                   <span className="w-1 h-1 rounded-full bg-current opacity-30" />
                   <span>{p.category}</span>
                   <span className="w-1 h-1 rounded-full bg-current opacity-30" />
                   <span>v{p.version || 1}</span>
                </div>
              </button>
            ))
          )}
        </div>

        {/* Resize Handle */}
        <div 
          className={`absolute top-0 right-0 w-1 h-full cursor-col-resize transition-colors z-20 hover:bg-primary/30 ${isResizing ? 'bg-primary/50' : ''}`}
          onMouseDown={startResizing}
        />
      </aside>

      {/* Main Form Area */}
      <main
        className={`grow overflow-y-auto custom-scrollbar space-y-4 overflow-x-auto ${
          variant === 'embedded' ? 'p-3' : 'p-5'
        }`}
      >
        {/* View Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 mb-1">
          <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center text-primary border border-primary/20">
                <Settings className="w-6 h-6" />
              </div>
              <div>
                <h1 className="text-2xl font-bold">
                  {selected.policy_id ? selected.name : 'Create New Policy'}
                </h1>
                <p className="text-sm opacity-50 font-medium">Policy Configuration & Control</p>
              </div>
          </div>
          
          <div className="flex flex-wrap items-center gap-2 shrink-0">
             <button type="button" className="btn btn-sm btn-outline gap-2" onClick={openResults}>
                <Activity className="w-4 h-4" />
                View drift results
              </button>
              <button type="button" className="btn btn-sm btn-outline gap-2" onClick={runScanFromHeader}>
                <Play className="w-4 h-4" />
                Run scan
              </button>
             <button 
                className="btn btn-sm btn-outline gap-2" 
                onClick={onClone} 
                disabled={!selected.policy_id}
              >
                <Copy className="w-4 h-4" />
                Clone
              </button>
              
              <button 
                className="btn btn-sm btn-outline gap-2" 
                onClick={onToggle} 
                disabled={!selected.policy_id}
              >
                {selected.enabled ? <ToggleRight className="w-4 h-4" /> : <ToggleLeft className="w-4 h-4" />}
                {selected.enabled ? 'Disable' : 'Enable'}
              </button>

              <div className="divider divider-horizontal mx-1" />

              <button 
                className="btn btn-sm btn-outline btn-error gap-2" 
                onClick={onDelete} 
                disabled={!selected.policy_id}
              >
                <Trash2 className="w-4 h-4" />
                Delete
              </button>

              <button 
                className={`btn btn-sm gap-2 ${canSave ? 'btn-primary' : 'btn-disabled'}`} 
                onClick={onSave}
              >
                <Save className="w-4 h-4" />
                {selected.policy_id ? 'Save Changes' : 'Create Policy'}
              </button>
          </div>
        </div>

        {/* Form Content */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          
          <div className="xl:col-span-8 space-y-6">
            {/* Basic Info Card */}
            <div className="bg-base-100 rounded-xl border border-base-300 p-5 shadow-sm space-y-5">
               <div className="flex items-center gap-3 mb-1">
                  <List className="w-5 h-5 text-primary" />
                  <h3 className="text-lg font-bold">Identity & Details</h3>
               </div>

               <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <label className="form-control w-full">
                    <div className="label">
                      <span className="label-text text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5">Policy ID</span>
                    </div>
                    <input 
                      className="input input-bordered font-mono" 
                      placeholder="e.g. CORE_VLAN_10"
                      value={selected.policy_id || ''} 
                      onChange={(e) => setSelected({ ...selected, policy_id: e.target.value })} 
                    />
                  </label>
                  <label className="form-control w-full">
                    <div className="label">
                      <span className="label-text text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5">Policy Label</span>
                    </div>
                    <input 
                      className="input input-bordered" 
                      placeholder="e.g. Core Network VLAN 10 Baseline"
                      value={selected.name} 
                      onChange={(e) => setSelected({ ...selected, name: e.target.value })} 
                    />
                  </label>
                </div>

                <div className="form-control w-full">
                  <div className="label">
                    <span className="label-text text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5">Description & Intent</span>
                  </div>
                  <textarea 
                    className="textarea textarea-bordered min-h-24 w-full" 
                    placeholder="Describe the purpose of this policy and what it enforces..."
                    value={selected.description || ''} 
                    onChange={(e) => setSelected({ ...selected, description: e.target.value })} 
                  />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <label className="form-control w-full">
                    <div className="label">
                      <span className="label-text text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5">Operational Category</span>
                    </div>
                    <select 
                      className="select select-bordered" 
                      value={selected.category} 
                      onChange={(e) => setSelected({ ...selected, category: e.target.value })}
                    >
                      {CATEGORIES.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
                    </select>
                  </label>
                  
                  <div className="form-control w-full">
                    <div className="label">
                      <span className="label-text text-xs font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5">Active Status</span>
                    </div>
                    <div className="flex items-center gap-3 bg-base-200/50 p-3 rounded-lg border border-base-300">
                        <input 
                          type="checkbox" 
                          className="toggle toggle-primary" 
                          checked={selected.enabled} 
                          onChange={(e) => setSelected({ ...selected, enabled: e.target.checked })} 
                        />
                        <span className="text-sm opacity-70">Enabled for automated scans</span>
                    </div>
                  </div>
                </div>
            </div>

            {/* Scope Card */}
            <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-6 shadow-sm space-y-4">
              <div className="flex items-center gap-2 border-b border-base-300/40 pb-4 mb-4">
                  <div className="w-8 h-8 rounded-lg bg-secondary/10 flex items-center justify-center text-secondary">
                      <Database className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold uppercase tracking-wider">Scope Definition</h3>
                    <p className="text-[10px] opacity-40 uppercase tracking-widest font-bold">Select target devices and domains</p>
                  </div>
               </div>
              <PolicyScopeSelector scope={selected.scope || {}} onChange={(scope) => setSelected({ ...selected, scope })} />
            </div>

            {/* Rules Card */}
            <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-6 shadow-sm space-y-4">
              <div className="flex items-center justify-between border-b border-base-300/40 pb-4 mb-4">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg bg-warning/10 flex items-center justify-center text-warning">
                        <Activity className="w-4 h-4" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold uppercase tracking-wider">Enforcement Rules</h3>
                      <p className="text-[10px] opacity-40 uppercase tracking-widest font-bold">Logic for compliance verification</p>
                    </div>
                  </div>
                  <div className="badge badge-outline border-base-300 opacity-60 font-mono text-xs">{selected.rules.length} Rules</div>
               </div>
              <PolicyRuleEditor rules={selected.rules || []} onChange={(rules) => setSelected({ ...selected, rules })} />
            </div>
          </div>

          <div className="xl:col-span-4 space-y-6">
            {/* Test Policy Card */}
            <div className="bg-base-100/60 backdrop-blur-md border border-base-300 rounded-2xl p-6 shadow-sm">
              <div className="flex items-center gap-2 mb-6">
                  <div className="w-8 h-8 rounded-lg bg-info/10 flex items-center justify-center text-info">
                      <Play className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold uppercase tracking-wider">Verification Test</h3>
                  </div>
               </div>
               
              <div className="space-y-4">
                <div className="p-3 bg-info/5 rounded-xl border border-info/20">
                  <p className="text-[10px] text-info/80 font-medium leading-relaxed">
                    Verify this policy against a specific device without affecting global compliance reports.
                  </p>
                </div>
                
                <div className="form-control">
                  <input
                    className="input input-sm input-bordered w-full h-10 font-mono tracking-tight"
                    placeholder="Enter Device ID (e.g. 42)"
                    value={testDeviceId}
                    onChange={(e) => setTestDeviceId(e.target.value)}
                  />
                </div>
                
                <button 
                  className="btn btn-sm btn-outline btn-info w-full h-10 gap-2 border-2" 
                  onClick={onTest} 
                  disabled={!selected.policy_id || testing}
                >
                  {testing ? <span className="loading loading-spinner loading-xs" /> : <Play className="w-4 h-4" />}
                  {testing ? 'Verifying...' : 'Run Test Scan'}
                </button>
              </div>
            </div>

            {/* History Card */}
            <PolicyVersionHistory policyId={selectedId} />
            
            {/* Info Card */}
            <div className="p-5 bg-primary/5 rounded-2xl border border-primary/20 flex gap-3">
               <Info className="w-5 h-5 text-primary shrink-0" />
               <div className="space-y-1">
                  <span className="text-xs font-bold uppercase tracking-wider text-primary">Pro Tip</span>
                  <p className="text-[11px] opacity-60 leading-relaxed">
                    Policies are versioned automatically on save. Use the audit log below to track changes across team members.
                  </p>
               </div>
            </div>
          </div>

        </div>
      </main>
    </div>
  );
};

export default PolicyBuilder;
