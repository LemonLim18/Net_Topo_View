import React from 'react';
import { Save, Plus, Trash2, ShieldAlert } from 'lucide-react';
import type { IntentSchemaCategory } from '../../types/intentSchema';

interface Props {
  category: IntentSchemaCategory | undefined;
}

const CategoryEditor: React.FC<Props> = ({ category }) => {
  if (!category) return (
    <div className="flex flex-col items-center justify-center p-8 opacity-50">
      <ShieldAlert className="w-12 h-12 mb-4" />
      <p>Select a Category</p>
    </div>
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">Category Details</h2>
        <button className="btn btn-sm btn-primary">
          <Save className="w-4 h-4" /> Save
        </button>
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold">Name</span></label>
        <input type="text" className="input input-bordered" defaultValue={category.name} />
      </div>

      <div className="form-control">
        <label className="label"><span className="label-text font-bold">Description</span></label>
        <textarea className="textarea textarea-bordered h-24" defaultValue={category.description} />
      </div>

      <div className="divider">Shared Parameters</div>
      
      <div className="flex flex-col gap-2">
        {category.fields.map(f => (
          <div key={f.name} className="flex items-center justify-between p-3 bg-base-200 rounded-lg border border-base-300">
             <div className="flex flex-col">
               <span className="font-bold font-mono text-sm">{f.name}</span>
               <span className="text-xs opacity-60">Type: {f.type} | {f.required ? 'Required' : 'Optional'}</span>
             </div>
             <button className="btn btn-ghost btn-xs btn-circle text-error"><Trash2 className="w-3 h-3" /></button>
          </div>
        ))}
      </div>

      <button className="btn btn-outline btn-sm btn-block mt-2">
        <Plus className="w-4 h-4" /> Add Field
      </button>

    </div>
  );
};

export default CategoryEditor;
