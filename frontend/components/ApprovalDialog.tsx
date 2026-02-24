import React, { useState } from 'react';
import { 
  ShieldCheck, 
  AlertTriangle, 
  Loader2, 
  Server, 
  Key,
  Rocket,
  X
} from 'lucide-react';
import { deployApi } from '../src/services/api';
import { Toast } from '../src/utils/swal';

interface ApprovalDialogProps {
  isOpen: boolean;
  onClose: () => void;
  vendor: string;
  feature: string;
  generatedConfig: string;
  undoConfig: string | null;
  diffSummary: string;
  onDeploySuccess: (jobId: number) => void;
}

const ApprovalDialog: React.FC<ApprovalDialogProps> = ({
  isOpen,
  onClose,
  vendor,
  feature,
  generatedConfig,
  undoConfig,
  diffSummary,
  onDeploySuccess,
}) => {
  const [host, setHost] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [driverType, setDriverType] = useState(vendor || 'cisco_ios');
  const [isDeploying, setIsDeploying] = useState(false);
  const [deployStatus, setDeployStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [currentStep, setCurrentStep] = useState('');

  if (!isOpen) return null;

  const handleDeploy = async () => {
    if (!host || !username || !password) {
      Toast.warning('Missing Fields', 'All device connection fields are required.');
      return;
    }

    setIsDeploying(true);
    setDeployStatus('idle');

    try {
      // Step 1: Create Change Request
      setCurrentStep('Creating change request...');
      const { request_id } = await deployApi.createRequest(
        `M11: ${feature} on ${host}`,
        `Auto-generated ${vendor}/${feature} config deployment`
      );

      // Step 2: Add Job (using device_id = 1 as placeholder; in production this would be resolved)
      setCurrentStep('Creating deployment job...');
      const { job_id } = await deployApi.addJob(request_id, 1);

      // Step 3: Plan (generate config within pipeline)
      setCurrentStep('Planning deployment...');
      await deployApi.planJob(job_id, vendor, feature, {});

      // Step 4: Execute
      setCurrentStep('Executing deployment...');
      const result = await deployApi.executeJob(job_id, {
        host,
        username,
        password,
        driver_type: driverType,
      });

      if (result.status === 'success') {
        setDeployStatus('success');
        setCurrentStep('Deployment completed successfully!');
        Toast.success('Deployment Successful');
        onDeploySuccess(job_id);
      } else {
        setDeployStatus('error');
        Toast.error('Deployment Failed', result.message || 'Deployment failed');
      }
    } catch (err: any) {
      setDeployStatus('error');
      Toast.error('Pipeline Error', err.message || 'Deployment pipeline error');
    } finally {
      setIsDeploying(false);
    }
  };

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-2xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-lg flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-warning" />
            Deployment Approval
          </h3>
          <button 
            className="btn btn-ghost btn-sm btn-circle" 
            onClick={onClose}
            disabled={isDeploying}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Summary Card */}
        <div className="bg-base-200 rounded-lg p-4 mb-4">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span className="opacity-60">Vendor:</span>
              <span className="ml-2 font-mono badge badge-sm">{vendor}</span>
            </div>
            <div>
              <span className="opacity-60">Feature:</span>
              <span className="ml-2 font-mono badge badge-sm">{feature}</span>
            </div>
            <div className="col-span-2">
              <span className="opacity-60">Changes:</span>
              <span className="ml-2">{diffSummary || 'New configuration'}</span>
            </div>
          </div>
        </div>

        {/* Generated Config Preview */}
        <div className="collapse collapse-arrow bg-base-200 mb-4">
          <input type="checkbox" />
          <div className="collapse-title text-sm font-medium">
            View Generated Commands ({generatedConfig.split('\n').length} lines)
          </div>
          <div className="collapse-content">
            <pre className="text-xs font-mono bg-base-300 p-3 rounded max-h-40 overflow-y-auto whitespace-pre-wrap">
              {generatedConfig}
            </pre>
          </div>
        </div>

        {undoConfig && (
          <div className="collapse collapse-arrow bg-base-200 mb-4">
            <input type="checkbox" />
            <div className="collapse-title text-sm font-medium">
              View Rollback Commands
            </div>
            <div className="collapse-content">
              <pre className="text-xs font-mono bg-base-300 p-3 rounded max-h-40 overflow-y-auto whitespace-pre-wrap">
                {undoConfig}
              </pre>
            </div>
          </div>
        )}

        {/* Warning */}
        <div className="alert alert-warning mb-4 text-sm">
          <AlertTriangle className="h-4 w-4" />
          <span>This will apply configuration changes to a live device. Ensure you have verified the commands above.</span>
        </div>

        {/* Device Connection Form */}
        <div className="space-y-3 mb-4">
          <h4 className="text-sm font-semibold flex items-center gap-2">
            <Server className="h-4 w-4" />
            Device Connection
          </h4>
          <div className="grid grid-cols-2 gap-3">
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs">Host / IP</span></label>
              <input
                type="text"
                placeholder="192.168.1.1"
                className="input input-bordered input-sm"
                value={host}
                onChange={e => setHost(e.target.value)}
                disabled={isDeploying}
              />
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs">Driver Type</span></label>
              <select
                className="select select-bordered select-sm"
                value={driverType}
                onChange={e => setDriverType(e.target.value)}
                disabled={isDeploying}
              >
                <option value="cisco_ios">Cisco IOS</option>
                <option value="huawei_vrp">Huawei VRP</option>
                <option value="juniper_junos">Juniper Junos</option>
              </select>
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs">Username</span></label>
              <input
                type="text"
                placeholder="admin"
                className="input input-bordered input-sm"
                value={username}
                onChange={e => setUsername(e.target.value)}
                disabled={isDeploying}
              />
            </div>
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text text-xs flex items-center gap-1">
                  <Key className="h-3 w-3" /> Password
                </span>
              </label>
              <input
                type="password"
                placeholder="••••••••"
                className="input input-bordered input-sm"
                value={password}
                onChange={e => setPassword(e.target.value)}
                disabled={isDeploying}
              />
            </div>
          </div>
        </div>

        {/* Status Messages */}
        {isDeploying && (
          <div className="alert alert-info mb-4 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>{currentStep}</span>
          </div>
        )}
        {deployStatus === 'success' && (
          <div className="alert alert-success mb-4 text-sm">
            <ShieldCheck className="h-4 w-4" />
            <span>{currentStep}</span>
          </div>
        )}

        {/* Actions */}
        <div className="modal-action">
          <button className="btn btn-ghost" onClick={onClose} disabled={isDeploying}>
            Cancel
          </button>
          <button
            className={`btn btn-warning gap-2 ${isDeploying ? 'loading' : ''}`}
            onClick={handleDeploy}
            disabled={isDeploying || deployStatus === 'success'}
          >
            {!isDeploying && <Rocket className="h-4 w-4" />}
            {deployStatus === 'success' ? 'Deployed ✓' : 'Approve & Deploy'}
          </button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={!isDeploying ? onClose : undefined}></div>
    </div>
  );
};

export default ApprovalDialog;
