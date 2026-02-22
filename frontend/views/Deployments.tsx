import React, { useState, useEffect } from 'react';
import { 
  Play, 
  RotateCcw, 
  Clock, 
  Server,
  Terminal
} from 'lucide-react';
import { deployApi } from '../src/services/api';

interface Job {
  id: number;
  request_id: number;
  device_id: number;
  stage: string;
  status: string;
  created_at: string;
  error_message?: string;
}


const Deployments: React.FC = () => {
  // In a real app, we'd fetch requests. For this MVP, we might need a "list requests" endpoint 
  // or we just show a hardcoded list or fetch a specific known request for demo.
  // Since we haven't implemented "list requests" in API yet, let's assume we can fetch job logs given an ID
  // or we need to add a "list jobs" endpoint to api.py.
  // For now, I'll mock the list state but implement the interaction (execute/rollback) using real API calls.
  
  // WAIT: implementing a view without the backend list endpoint is bad.
  // api.py has: create_request, add_job, plan_job, execute_job, get_job_logs.
  // It MISSES "get_requests" or "get_jobs".
  // I will add a quick "get_jobs" endpoint to backend/deploy/api.py in the next step.
  // For now, I'll build the UI to expect it.

  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // Poll for jobs (mocking the fetch for now until endpoint exists)
  useEffect(() => {
    // mock data for UI dev
    setJobs([
      { id: 1, request_id: 101, device_id: 1, stage: 'PLAN', status: 'PENDING', created_at: new Date().toISOString() }
    ]);
  }, []);

  const handleExecute = async (job: Job) => {
    setIsLoading(true);
    try {
      await deployApi.executeJob(job.id, {
        host: "192.168.1.123", // should come from device inventory
        username: "admin",
        password: "password"
      });
      alert("Execution started!");
      // Poll logs...
    } catch (e: any) {
      alert("Execution failed: " + e.message);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchLogs = async (jobId: number) => {
    try {
      const res = await deployApi.getLogs(jobId);
      setLogs(res.logs);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    if (selectedJob) {
      fetchLogs(selectedJob.id);
      const interval = setInterval(() => fetchLogs(selectedJob.id), 2000);
      return () => clearInterval(interval);
    }
  }, [selectedJob]);

  return (
    <div className="p-6 h-full flex flex-col">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <Server className="text-primary" /> Deployment Pipeline
      </h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-grow overflow-hidden">
        {/* Job List */}
        <div className="bg-base-100 rounded-xl shadow-sm border border-base-300 flex flex-col">
          <div className="p-4 border-b border-base-300 font-semibold">Active Jobs</div>
          <div className="overflow-y-auto flex-1">
            {jobs.map(job => (
              <div 
                key={job.id}
                onClick={() => setSelectedJob(job)}
                className={`p-4 border-b border-base-200 cursor-pointer hover:bg-base-200 transition-colors ${selectedJob?.id === job.id ? 'bg-primary/10 border-l-4 border-l-primary' : ''}`}
              >
                <div className="flex justify-between items-start mb-2">
                  <span className="font-mono text-xs opacity-50">JOB-{job.id}</span>
                  <span className={`badge badge-sm ${
                    job.status === 'COMPLETED' ? 'badge-success' : 
                    job.status === 'FAILED' ? 'badge-error' : 'badge-warning'
                  }`}>
                    {job.status}
                  </span>
                </div>
                <div className="font-semibold text-sm">Deploy Request #{job.request_id}</div>
                <div className="text-xs mt-1 flex items-center gap-1 opacity-70">
                    <Clock size={12} /> {new Date(job.created_at).toLocaleTimeString()}
                </div>
                
                <div className="mt-3 flex gap-2">
                    <button 
                        onClick={(e) => { e.stopPropagation(); handleExecute(job); }}
                        className="btn btn-xs btn-primary"
                        disabled={job.status !== 'PENDING' || isLoading}
                    >
                        <Play size={12} /> Approve
                    </button>
                    <button 
                        className="btn btn-xs btn-outline btn-error"
                        disabled={job.status === 'PENDING'}
                    >
                        <RotateCcw size={12} /> Rollback
                    </button>
                </div>
              </div>
            ))}
             {jobs.length === 0 && (
                <div className="p-8 text-center opacity-50">No active jobs</div>
            )}
          </div>
        </div>

        {/* Job Details / Logs */}
        <div className="lg:col-span-2 bg-base-100 rounded-xl shadow-sm border border-base-300 flex flex-col overflow-hidden">
           <div className="p-4 border-b border-base-300 flex justify-between items-center bg-base-200/50">
               <div className="font-semibold flex items-center gap-2">
                   <Terminal size={18} /> 
                   {selectedJob ? `Execution Logs: Job-${selectedJob.id}` : 'Console Output'}
               </div>
           </div>
           
           <div className="flex-1 bg-[#1e1e1e] text-gray-300 font-mono text-sm p-4 overflow-y-auto">
               {!selectedJob ? (
                   <div className="h-full flex items-center justify-center opacity-30">
                       Select a job to view execution logs
                   </div>
               ) : (
                   <div className="space-y-1">
                       {logs.length === 0 && <div className="opacity-50">Waiting for logs...</div>}
                       {logs.map((log, i) => (
                           <div key={i} className="flex gap-3">
                               <span className="opacity-40 select-none w-20 shrink-0">{new Date(log.timestamp).toLocaleTimeString()}</span>
                               <span className={`
                                   ${log.level === 'ERROR' ? 'text-red-400' : 
                                     log.level === 'WARNING' ? 'text-yellow-400' : 'text-green-400'}
                               `}>
                                   [{log.step}]
                               </span>
                               <span>{log.message}</span>
                           </div>
                       ))}
                   </div>
               )}
           </div>
        </div>
      </div>
    </div>
  );
};

export default Deployments;
