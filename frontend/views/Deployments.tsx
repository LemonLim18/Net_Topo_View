import React, { useState, useEffect } from 'react';
import { 
  Play, 
  RotateCcw, 
  Clock, 
  Server,
  Terminal,
  Bot,
  CheckCircle,
  XCircle,
  AlertTriangle,
  FileCode,
  Loader2
} from 'lucide-react';
import { deployApi, proposalApi } from '../src/services/api';
import { Toast, Confirm } from '../src/utils/swal';

interface Job {
  id: number;
  request_id: number;
  device_id: number;
  stage: string;
  status: string;
  created_at: string;
  error_message?: string;
}

interface Proposal {
  id: string;
  status: string;
  user_prompt: string;
  target_device: string;
  vendor: string;
  feature: string;
  generated_config: string;
  rollback_config: string;
  risk_level: string;
  ai_explanation: string;
  job_id?: number;
  created_at?: string;
  updated_at?: string;
}

type TabId = 'jobs' | 'proposals';

const statusBadge = (status: string) => {
  const map: Record<string, string> = {
    'COMPLETED': 'badge-success', 'deployed': 'badge-success',
    'FAILED': 'badge-error', 'rejected': 'badge-error', 'rolled_back': 'badge-warning',
    'PENDING': 'badge-warning', 'pending': 'badge-info',
    'approved': 'badge-accent',
  };
  return map[status] || 'badge-ghost';
};


const Deployments: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabId>('proposals');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [selectedProposal, setSelectedProposal] = useState<Proposal | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // Fetch proposals on mount and tab switch
  useEffect(() => {
    if (activeTab === 'proposals') {
      fetchProposals();
    }
  }, [activeTab]);

  const fetchProposals = async () => {
    try {
      const res = await proposalApi.list();
      setProposals(res.proposals);
    } catch (e) {
      console.error('Failed to fetch proposals:', e);
    }
  };

  // --- Jobs Tab Logic (existing) ---
  useEffect(() => {
    if (activeTab === 'jobs') {
      setJobs([
        { id: 1, request_id: 101, device_id: 1, stage: 'PLAN', status: 'PENDING', created_at: new Date().toISOString() }
      ]);
    }
  }, [activeTab]);

  const handleExecute = async (job: Job) => {
    setIsLoading(true);
    try {
      await deployApi.executeJob(job.id, {
        host: "192.168.1.123",
        username: "admin",
        password: "password"
      });
      Toast.success('Execution started!');
    } catch (e: any) {
      Toast.error('Execution failed', e.message);
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

  // --- Proposal Actions ---
  const handleRollbackProposal = async (proposal: Proposal) => {
    const ok = await Confirm.ask(
      'Rollback Configuration',
      `Rollback configuration on ${proposal.target_device}?`
    );
    if (!ok) return;
    setIsLoading(true);
    try {
      await proposalApi.rollback(proposal.id);
      await fetchProposals();
      Toast.success('Rollback complete');
    } catch (e: any) {
      Toast.error('Rollback failed', e.message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="p-6 h-full flex flex-col">
      <h1 className="text-2xl font-bold mb-4 flex items-center gap-2">
        <Server className="text-primary" /> Deployment Pipeline
      </h1>

      {/* Tab Bar */}
      <div className="tabs tabs-boxed mb-4 w-fit">
        <button
          className={`tab ${activeTab === 'proposals' ? 'tab-active' : ''}`}
          onClick={() => setActiveTab('proposals')}
        >
          <Bot className="w-4 h-4 mr-1" /> AI Proposals
        </button>
        <button
          className={`tab ${activeTab === 'jobs' ? 'tab-active' : ''}`}
          onClick={() => setActiveTab('jobs')}
        >
          <Terminal className="w-4 h-4 mr-1" /> Pipeline Jobs
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-grow overflow-hidden">
        
        {/* ========== PROPOSALS TAB ========== */}
        {activeTab === 'proposals' && (
          <>
            {/* Proposal List */}
            <div className="bg-base-100 rounded-xl shadow-sm border border-base-300 flex flex-col">
              <div className="p-4 border-b border-base-300 font-semibold flex items-center justify-between">
                <span>Proposals</span>
                <button onClick={fetchProposals} className="btn btn-ghost btn-xs">
                  <RotateCcw className="w-3 h-3" />
                </button>
              </div>
              <div className="overflow-y-auto flex-1">
                {proposals.map(p => (
                  <div 
                    key={p.id}
                    onClick={() => setSelectedProposal(p)}
                    className={`p-4 border-b border-base-200 cursor-pointer hover:bg-base-200 transition-colors ${selectedProposal?.id === p.id ? 'bg-primary/10 border-l-4 border-l-primary' : ''}`}
                  >
                    <div className="flex justify-between items-start mb-1">
                      <span className="font-mono text-xs opacity-50">{p.id.substring(0, 8)}</span>
                      <span className={`badge badge-sm ${statusBadge(p.status)}`}>
                        {p.status}
                      </span>
                    </div>
                    <div className="font-semibold text-sm truncate">{p.feature} on {p.target_device}</div>
                    <div className="text-xs mt-1 opacity-70 truncate">{p.user_prompt}</div>
                    <div className="text-xs mt-1 flex items-center gap-1 opacity-50">
                      <Clock size={10} /> {p.created_at ? new Date(p.created_at).toLocaleString() : '—'}
                    </div>

                    {p.status === 'deployed' && p.rollback_config && (
                      <button 
                        onClick={(e) => { e.stopPropagation(); handleRollbackProposal(p); }}
                        className="btn btn-xs btn-warning mt-2 gap-1"
                        disabled={isLoading}
                      >
                        <RotateCcw size={10} /> Rollback
                      </button>
                    )}
                  </div>
                ))}
                {proposals.length === 0 && (
                  <div className="p-8 text-center opacity-50">
                    <Bot className="w-8 h-8 mx-auto mb-2 opacity-30" />
                    <p className="text-sm">No proposals yet</p>
                    <p className="text-xs mt-1">Use the AI Agent panel to generate config proposals</p>
                  </div>
                )}
              </div>
            </div>

            {/* Proposal Detail */}
            <div className="lg:col-span-2 bg-base-100 rounded-xl shadow-sm border border-base-300 flex flex-col overflow-hidden">
              <div className="p-4 border-b border-base-300 flex justify-between items-center bg-base-200/50">
                <div className="font-semibold flex items-center gap-2">
                  <FileCode size={18} /> 
                  {selectedProposal ? `Proposal: ${selectedProposal.feature} on ${selectedProposal.target_device}` : 'Proposal Details'}
                </div>
              </div>
              
              <div className="flex-1 overflow-y-auto p-4">
                {!selectedProposal ? (
                  <div className="h-full flex items-center justify-center opacity-30">
                    Select a proposal to view details
                  </div>
                ) : (
                  <div className="space-y-4">
                    {/* Status Banner */}
                    <div className={`alert ${
                      selectedProposal.status === 'deployed' ? 'alert-success' :
                      selectedProposal.status === 'rejected' ? 'alert-error' :
                      selectedProposal.status === 'rolled_back' ? 'alert-warning' :
                      'alert-info'
                    }`}>
                      <span className="font-semibold">{selectedProposal.status.toUpperCase()}</span>
                      {selectedProposal.job_id && <span className="text-sm ml-2">Job #{selectedProposal.job_id}</span>}
                    </div>

                    {/* Metadata */}
                    <div className="bg-base-200 rounded-lg p-4 text-sm space-y-1">
                      <div><span className="opacity-60 min-w-[100px] inline-block">User Request:</span> {selectedProposal.user_prompt}</div>
                      <div><span className="opacity-60 min-w-[100px] inline-block">Target:</span> {selectedProposal.target_device} ({selectedProposal.vendor})</div>
                      <div><span className="opacity-60 min-w-[100px] inline-block">Feature:</span> {selectedProposal.feature}</div>
                      <div><span className="opacity-60 min-w-[100px] inline-block">Risk:</span> 
                        <span className={`badge badge-sm ${
                          selectedProposal.risk_level === 'high' ? 'badge-error' :
                          selectedProposal.risk_level === 'medium' ? 'badge-warning' : 'badge-success'
                        } ml-1`}>{selectedProposal.risk_level}</span>
                      </div>
                    </div>

                    {/* Generated Config */}
                    <div>
                      <h3 className="font-semibold text-sm mb-2">Generated Configuration</h3>
                      <pre className="bg-[#1e1e1e] text-green-400 p-4 rounded-lg text-xs overflow-auto max-h-64 font-mono">
                        {selectedProposal.generated_config}
                      </pre>
                    </div>

                    {/* Rollback Config */}
                    {selectedProposal.rollback_config && (
                      <div>
                        <h3 className="font-semibold text-sm mb-2">Rollback Commands</h3>
                        <pre className="bg-[#1e1e1e] text-yellow-400 p-4 rounded-lg text-xs overflow-auto max-h-48 font-mono">
                          {selectedProposal.rollback_config}
                        </pre>
                      </div>
                    )}

                    {/* AI Explanation */}
                    {selectedProposal.ai_explanation && (
                      <div>
                        <h3 className="font-semibold text-sm mb-2">AI Explanation</h3>
                        <div className="bg-base-200 rounded-lg p-4 text-sm whitespace-pre-wrap">
                          {selectedProposal.ai_explanation}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {/* ========== JOBS TAB (existing) ========== */}
        {activeTab === 'jobs' && (
          <>
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
                      <span className={`badge badge-sm ${statusBadge(job.status)}`}>
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
          </>
        )}
      </div>
    </div>
  );
};

export default Deployments;
