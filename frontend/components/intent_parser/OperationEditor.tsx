import React from 'react';
import { Save, Link, Plus, ShieldAlert } from 'lucide-react';
import type { IntentSchemaOperation } from '../../types/intentSchema';

interface Props {
  operation: IntentSchemaOperation | undefined;
}

const OperationEditor: React.FC<Props> = ({ operation }) => {
  if (!operation) return (
    <div className="flex flex-col items-center justify-center p-8 opacity-50">
      <ShieldAlert className="w-12 h-12 mb-4" />
      <p>Select an Operation</p>
    </div>
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">Operation Rule Set</h2>
        <button className="btn btn-sm btn-primary">
          <Save className="w-4 h-4" /> Save
        </button>
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold">Name</span></label>
        <input type="text" className="input input-bordered" defaultValue={operation.name} />
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold text-error">Required Fields</span></label>
        <div className="flex flex-wrap gap-2 p-3 bg-error/10 border border-error/20 rounded-lg min-h-16">
           {operation.required_fields.map(f => (
             <span key={f} className="badge badge-error gap-2">{f} <button className="hover:text-black">x</button></span>
           ))}
        </div>
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold opacity-70">Optional Fields</span></label>
        <div className="flex flex-wrap gap-2 p-3 bg-base-200 border border-base-300 rounded-lg min-h-16">
           {(operation.optional_fields || []).map(f => (
             <span key={f} className="badge gap-2">{f} <button className="hover:text-black">x</button></span>
           ))}
        </div>
      </div>

      <div className="divider">Dependencies</div>

      {operation.dependencies && operation.dependencies.length > 0 ? (
        <div className="flex flex-col gap-3">
          {operation.dependencies.map((d, i) => (
            <div key={i} className="p-3 bg-warning/10 text-warning-content text-sm rounded-lg border border-warning/30 flex justify-between items-center">
               <div className="flex flex-col">
                  <span className="font-bold flex gap-1 items-center"><Link className="w-3 h-3"/> IF: {d.condition}</span>
                  <span className="opacity-80 ml-4 border-l pl-2 mt-1">THEN: {d.action}</span>
               </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm opacity-50 px-2 italic">No dependencies configured.</p>
      )}

      <button className="btn btn-outline btn-sm btn-block mt-2">
        <Plus className="w-4 h-4" /> Add Dependency Rule
      </button>

    </div>
  );
};

export default OperationEditor;
