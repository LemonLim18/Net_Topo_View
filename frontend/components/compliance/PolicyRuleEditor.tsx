import React from 'react';
import { Plus, Trash2, Info, LifeBuoy, ShieldAlert } from 'lucide-react';
import type { PolicyRule } from '../../src/services/api';

interface Props {
  rules: PolicyRule[];
  onChange: (rules: PolicyRule[]) => void;
}

const ACTIONS = [
  'must_exist',
  'must_not_exist',
  'must_equal',
  'must_contain',
  'must_match_regex',
  'must_be_in_range',
  'must_be_in_set',
  'must_be_absent',
  'must_be_on_device_role',
  'must_not_be_on_device_role',
];

const SEV = ['low', 'medium', 'high', 'critical'];

const PolicyRuleEditor: React.FC<Props> = ({ rules, onChange }) => {
  const setAt = (i: number, patch: Partial<PolicyRule>) =>
    onChange(rules.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  const addRule = () =>
    onChange([
      ...rules,
      { id: `r${rules.length + 1}`, action: 'must_exist', target: '', severity: 'medium', explanation: '', remediation: '' },
    ]);

  const delRule = (i: number) => onChange(rules.filter((_, idx) => idx !== i));

  const getSeverityColor = (sev: string) => {
    switch(sev) {
      case 'critical': return 'text-error';
      case 'high': return 'text-orange-500';
      case 'medium': return 'text-warning';
      default: return 'text-success';
    }
  };

  return (
    <div className="space-y-4">
      {rules.map((r, i) => (
        <div key={r.id || i} className="group relative bg-base-100/40 backdrop-blur-sm border border-base-300 rounded-xl p-4 transition-all hover:bg-base-100/60 hover:shadow-md">
           <div className="flex flex-col gap-4">
            {/* Top Row: Action, Target, Severity */}
            <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-end">
              <div className="md:col-span-3">
                <label className="label py-1"><span className="label-text-alt opacity-50 uppercase font-bold tracking-wider text-[10px]">Action</span></label>
                <select 
                  className="select select-sm select-bordered w-full h-9" 
                  value={r.action} 
                  onChange={(e) => setAt(i, { action: e.target.value })}
                >
                  {ACTIONS.map(a => <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>)}
                </select>
              </div>

              <div className="md:col-span-4">
                <label className="label py-1"><span className="label-text-alt opacity-50 uppercase font-bold tracking-wider text-[10px]">Object Path / Target</span></label>
                <input
                  className="input input-sm input-bordered w-full h-9"
                  placeholder="e.g. interfaces.GigabitEthernet1.description"
                  value={String(r.target || r.path || '')}
                  onChange={(e) => setAt(i, { target: e.target.value, path: e.target.value })}
                />
              </div>

              <div className="md:col-span-2">
                <label className="label py-1"><span className="label-text-alt opacity-50 uppercase font-bold tracking-wider text-[10px]">Expected</span></label>
                <input
                  className="input input-sm input-bordered w-full h-9"
                  placeholder="Value"
                  value={r.expected == null ? '' : String(r.expected)}
                  onChange={(e) => setAt(i, { expected: e.target.value })}
                />
              </div>

              <div className="md:col-span-3">
                <label className="label py-1"><span className="label-text-alt opacity-50 uppercase font-bold tracking-wider text-[10px]">Severity</span></label>
                <div className="relative">
                   <select 
                    className={`select select-sm select-bordered w-full h-9 pl-8 ${getSeverityColor(String(r.severity || 'medium'))}`}
                    value={String(r.severity || 'medium')} 
                    onChange={(e) => setAt(i, { severity: e.target.value })}
                  >
                    {SEV.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <ShieldAlert className={`absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 ${getSeverityColor(String(r.severity || 'medium'))}`} />
                </div>
              </div>
            </div>

            {/* Bottom Row: Explanation and Remediation */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="relative">
                <div className="absolute left-3 top-2.5 opacity-30"><Info className="w-4 h-4" /></div>
                <input
                  className="input input-sm input-bordered w-full pl-9 h-9"
                  placeholder="Compliance Explanation (Why this rule exists)"
                  value={String(r.explanation || '')}
                  onChange={(e) => setAt(i, { explanation: e.target.value })}
                />
              </div>
              <div className="flex gap-2 items-center">
                 <div className="relative grow">
                  <div className="absolute left-3 top-2.5 opacity-30"><LifeBuoy className="w-4 h-4" /></div>
                  <input
                    className="input input-sm input-bordered w-full pl-9 h-9"
                    placeholder="Remediation Steps (How to fix)"
                    value={String(r.remediation || '')}
                    onChange={(e) => setAt(i, { remediation: e.target.value })}
                  />
                </div>
                <button 
                  className="btn btn-sm btn-ghost text-error opacity-0 group-hover:opacity-100 transition-opacity" 
                  onClick={() => delRule(i)}
                  title="Remove Rule"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        </div>
      ))}
      <button 
        className="btn btn-sm btn-outline btn-primary gap-2 w-full border-dashed h-10" 
        onClick={addRule}
      >
        <Plus className="w-4 h-4" /> Add New Rule
      </button>
    </div>
  );
};

export default PolicyRuleEditor;
