import React, { useEffect, useState } from 'react';
import { History, User as UserIcon, Clock, Activity } from 'lucide-react';
import { policyApi } from '../../src/services/api';

interface Props {
  policyId: string | null;
}

const PolicyVersionHistory: React.FC<Props> = ({ policyId }) => {
  const [items, setItems] = useState<any[]>([]);

  useEffect(() => {
    if (!policyId) {
      setItems([]);
      return;
    }
    policyApi.audit(policyId, 20)
      .then((r) => setItems(r.items || []))
      .catch(() => setItems([]));
  }, [policyId]);

  return (
    <div className="bg-base-100/40 backdrop-blur-sm border border-base-300 rounded-xl p-4 transition-all">
      <div className="flex items-center gap-2 mb-4">
        <History className="w-4 h-4 text-primary" />
        <span className="text-xs font-bold uppercase tracking-widest opacity-80">Version History / Audit</span>
      </div>
      
      {items.length === 0 ? (
        <div className="text-xs opacity-50 italic py-8 flex flex-col items-center justify-center gap-2 bg-base-300/20 rounded-lg border border-dashed border-base-300">
          <Activity className="w-8 h-8 opacity-20" />
          No audit entries recorded for this policy.
        </div>
      ) : (
        <div className="max-h-56 overflow-auto custom-scrollbar">
          <table className="table table-sm w-full">
            <thead className="sticky top-0 bg-base-100 z-10">
              <tr className="border-b border-base-300">
                <th className="text-[10px] font-bold uppercase tracking-wider opacity-60">Action</th>
                <th className="text-[10px] font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5"><UserIcon className="w-3 h-3" /> Changed By</th>
                <th className="text-[10px] font-bold uppercase tracking-wider opacity-60 flex items-center gap-1.5"><Clock className="w-3 h-3" /> Timestamp</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-base-300/40">
              {items.map((x, i) => (
                <tr key={i} className="hover:bg-base-300/30 transition-colors">
                  <td className="py-2.5">
                    <span className={`badge badge-xs font-mono uppercase ${x.action === 'create' ? 'badge-success' : x.action === 'delete' ? 'badge-error' : 'badge-ghost border-base-300'}`}>
                      {x.action}
                    </span>
                  </td>
                  <td className="text-xs font-medium py-2.5">{x.changed_by || 'system'}</td>
                  <td className="text-[11px] opacity-60 py-2.5">
                     {x.timestamp ? new Date(x.timestamp).toLocaleString() : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default PolicyVersionHistory;
