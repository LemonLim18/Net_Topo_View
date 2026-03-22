import React from 'react';
import { PlaySquare, Bug } from 'lucide-react';

const PromptPreviewPanel: React.FC = () => {
    return (
        <div className="h-full flex gap-4 p-4 items-stretch justify-stretch bg-base-100">
            <div className="w-1/2 flex flex-col gap-4">
                <div className="flex justify-between items-center">
                    <h2 className="text-xl font-bold font-mono">Input Prompt</h2>
                    <button className="btn btn-sm btn-accent shadow border-0">
                        <PlaySquare className="w-4 h-4" /> Parse Intent
                    </button>
                </div>
                <textarea 
                    className="textarea textarea-bordered flex-1 font-mono text-lg bg-base-200" 
                    placeholder="E.g. Assign VLAN 100 on interface GigabitEthernet1/0/1 on device switch-core-01..."
                ></textarea>
                <div className="alert alert-info shadow-lg p-3">
                    <Bug className="w-4 h-4" />
                    <span className="text-xs">This sends a request to the NetBox topology context rules engine to validate parameter extraction.</span>
                </div>
            </div>
            
            <div className="w-1/2 flex flex-col gap-4">
               <div className="flex justify-between items-center">
                    <h2 className="text-xl font-bold font-mono text-success">Parser Output</h2>
               </div>
               <div className="bg-neutral text-neutral-content p-4 rounded-xl flex-1 font-mono text-sm overflow-y-auto whitespace-pre shadow-inner select-text">
{`{
  "status": "waiting_for_input",
  "extracted": {},
  "missing": [],
  "invalid": []
}`}
               </div>
            </div>
        </div>
    );
};

export default PromptPreviewPanel;
