import React from 'react';
import { Save, AlertTriangle, ShieldAlert } from 'lucide-react';

interface Props {
  parameter: string | undefined;
}

const ParameterEditor: React.FC<Props> = ({ parameter }) => {
  if (!parameter) return (
    <div className="flex flex-col items-center justify-center p-8 opacity-50">
      <ShieldAlert className="w-12 h-12 mb-4" />
      <p>Select a Parameter field</p>
    </div>
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">Field Validator</h2>
        <button className="btn btn-sm btn-primary">
          <Save className="w-4 h-4" /> Save
        </button>
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold">Field Key</span></label>
        <input type="text" className="input input-bordered font-mono" defaultValue={parameter} disabled />
        <span className="text-xs opacity-50 mt-1">Field keys cannot be renamed. Delete and recreate if needed.</span>
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold">Data Type</span></label>
        <select className="select select-bordered" defaultValue={parameter.includes('id') ? 'integer' : 'string'}>
           <option value="string">String (Text)</option>
           <option value="integer">Integer</option>
           <option value="boolean">Boolean</option>
           <option value="enum">List of Choices (Enum)</option>
        </select>
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold text-info flex gap-2"><AlertTriangle className="w-4 h-4"/> Cross-check Context</span></label>
        <select className="select select-bordered" defaultValue={parameter.includes('vlan') ? 'planned_topology' : 'none'}>
           <option value="none">No Context Required</option>
           <option value="inventory">NetBox Inventory (Active Network)</option>
           <option value="planned_topology">Graph Topology (Planned Network)</option>
        </select>
        <p className="text-xs opacity-70 mt-3 bg-info/10 p-2 rounded">
          If cross-check is enabled, the parser will flag this parameter as INVALID if the provided value does not match the active context source.
        </p>
      </div>
    </div>
  );
};

export default ParameterEditor;
