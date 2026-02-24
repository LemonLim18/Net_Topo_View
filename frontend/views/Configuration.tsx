import React, { useState, useEffect } from 'react';
import {
  Settings,
  Play,
  Eye,
  History,
  Loader2,
  FileCode,
  AlertTriangle,
  CheckCircle,
  Rocket,
  Copy,
  Check,
  Code,
  BookOpen,
} from 'lucide-react';
import { configApi, deployApi } from '../src/services/api';
import ConfigDiff from '../components/ConfigDiff';
import ApprovalDialog from '../components/ApprovalDialog';
import { Toast } from '../src/utils/swal';

// ---------- Types ----------
interface TemplateInfo {
  vendor: string;
  feature: string;
  has_undo: boolean;
  schema_exists: boolean;
}

interface SchemaProperty {
  type: string;
  description?: string;
  default?: any;
  enum?: string[];
  items?: { type: string };
}

// ---------- Component ----------
const Configuration: React.FC = () => {
  // Tab state
  const [activeTab, setActiveTab] = useState<'generate' | 'templates' | 'history'>('generate');

  // Template data
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [isLoadingTemplates, setIsLoadingTemplates] = useState(false);

  // Generate tab state
  const [selectedVendor, setSelectedVendor] = useState('cisco_ios');
  const [selectedFeature, setSelectedFeature] = useState('');
  const [schema, setSchema] = useState<Record<string, any> | null>(null);
  const [params, setParams] = useState<Record<string, any>>({});
  const [generatedConfig, setGeneratedConfig] = useState('');
  const [undoConfig, setUndoConfig] = useState<string | null>(null);
  const [isRendering, setIsRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  // Diff state
  const [existingConfig, setExistingConfig] = useState('');
  const [showDiff, setShowDiff] = useState(false);

  // Approval dialog
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [diffSummary, setDiffSummary] = useState('');

  // Template browser state
  const [selectedSchemaSource, setSelectedSchemaSource] = useState<Record<string, any> | null>(null);
  const [browserVendor, setBrowserVendor] = useState('cisco_ios');
  const [browserFeature, setBrowserFeature] = useState('');

  // History state
  const [jobs, setJobs] = useState<any[]>([]);
  const [isLoadingJobs, setIsLoadingJobs] = useState(false);

  // Copy state
  const [copied, setCopied] = useState(false);

  // ---------- Load templates ----------
  useEffect(() => {
    loadTemplates();
  }, []);

  const loadTemplates = async () => {
    setIsLoadingTemplates(true);
    try {
      const result = await configApi.listTemplates();
      setTemplates(result);
    } catch (err) {
      console.error('Failed to load templates:', err);
      Toast.error('Failed to load templates');
    } finally {
      setIsLoadingTemplates(false);
    }
  };

  // ---------- Filter templates by vendor ----------
  const filteredTemplates = templates.filter(t => t.vendor === selectedVendor);
  const browserTemplates = templates.filter(t => t.vendor === browserVendor);

  // ---------- Load schema when feature changes ----------
  useEffect(() => {
    if (!selectedFeature) {
      setSchema(null);
      setParams({});
      return;
    }
    loadSchema(selectedFeature);
  }, [selectedFeature]);

  const loadSchema = async (feature: string) => {
    try {
      const s = await configApi.getSchema(feature);
      setSchema(s);
      // Initialize params with defaults
      const initial: Record<string, any> = {};
      if (s.properties) {
        Object.entries(s.properties).forEach(([key, prop]: [string, any]) => {
          if (prop.default !== undefined) {
            initial[key] = prop.default;
          } else if (prop.type === 'boolean') {
            initial[key] = false;
          } else if (prop.type === 'integer' || prop.type === 'number') {
            initial[key] = 0;
          } else if (prop.type === 'array') {
            initial[key] = [];
          } else {
            initial[key] = '';
          }
        });
      }
      setParams(initial);
    } catch (err) {
      console.error('Failed to load schema:', err);
      Toast.error('Failed to load schema', 'Could not load parameter schema for this feature');
      setSchema(null);
    }
  };

  // ---------- Render config ----------
  const handleRender = async () => {
    if (!selectedVendor || !selectedFeature) return;
    setIsRendering(true);
    try {
      setRenderError(null);
      const result = await configApi.render(selectedVendor, selectedFeature, params);
      setGeneratedConfig(result.config);
      setUndoConfig(result.undo_config);
      Toast.success('Config rendered');
    } catch (err: any) {
      const msg = err.message || 'Rendering failed';
      setRenderError(msg);
      Toast.error('Render Failed', msg);
      setGeneratedConfig('');
      setUndoConfig(null);
    } finally {
      setIsRendering(false);
    }
  };

  // ---------- Copy config ----------
  const handleCopy = () => {
    navigator.clipboard.writeText(generatedConfig);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // ---------- Handle deploy ----------
  const handleDeployClick = () => {
    if (existingConfig && generatedConfig) {
      setDiffSummary(`Changes to ${selectedFeature} on ${selectedVendor}`);
    } else {
      setDiffSummary(`New ${selectedFeature} configuration for ${selectedVendor}`);
    }
    setApprovalOpen(true);
  };

  // ---------- Load deployment history ----------
  useEffect(() => {
    if (activeTab === 'history') {
      loadJobs();
    }
  }, [activeTab]);

  const loadJobs = async () => {
    setIsLoadingJobs(true);
    try {
      const result = await deployApi.getLogs(0); // Get all logs; in production filter
      setJobs(Array.isArray(result.logs) ? result.logs : []);
    } catch (err) {
      console.error('Failed to load jobs:', err);
      Toast.error('Failed to load deployment history');
      setJobs([]);
    } finally {
      setIsLoadingJobs(false);
    }
  };

  // ---------- Param input field renderer ----------
  const renderParamField = (key: string, prop: SchemaProperty) => {
    const required = schema?.required?.includes(key);
    const value = params[key];

    if (prop.enum) {
      return (
        <div key={key} className="form-control">
          <label className="label py-1">
            <span className="label-text text-xs">
              {key} {required && <span className="text-error">*</span>}
            </span>
          </label>
          <select
            className="select select-bordered select-sm"
            value={value || ''}
            onChange={e => setParams({ ...params, [key]: e.target.value })}
          >
            <option value="">Select...</option>
            {prop.enum.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          {prop.description && <label className="label py-0"><span className="label-text-alt opacity-50">{prop.description}</span></label>}
        </div>
      );
    }

    if (prop.type === 'boolean') {
      return (
        <div key={key} className="form-control">
          <label className="label cursor-pointer py-1 justify-start gap-3">
            <input
              type="checkbox"
              className="toggle toggle-primary toggle-sm"
              checked={!!value}
              onChange={e => setParams({ ...params, [key]: e.target.checked })}
            />
            <span className="label-text text-xs">
              {key} {required && <span className="text-error">*</span>}
            </span>
          </label>
          {prop.description && <label className="label py-0"><span className="label-text-alt opacity-50">{prop.description}</span></label>}
        </div>
      );
    }

    if (prop.type === 'integer' || prop.type === 'number') {
      return (
        <div key={key} className="form-control">
          <label className="label py-1">
            <span className="label-text text-xs">
              {key} {required && <span className="text-error">*</span>}
            </span>
          </label>
          <input
            type="number"
            className="input input-bordered input-sm"
            value={value ?? ''}
            onChange={e => setParams({ ...params, [key]: parseInt(e.target.value) || 0 })}
            placeholder={prop.description || key}
          />
          {prop.description && <label className="label py-0"><span className="label-text-alt opacity-50">{prop.description}</span></label>}
        </div>
      );
    }

    if (prop.type === 'array') {
      const arrValue = Array.isArray(value) ? value : [];
      return (
        <div key={key} className="form-control">
          <label className="label py-1">
            <span className="label-text text-xs">
              {key} {required && <span className="text-error">*</span>}
            </span>
          </label>
          <textarea
            className="textarea textarea-bordered textarea-sm"
            value={arrValue.join('\n')}
            onChange={e => setParams({ ...params, [key]: e.target.value.split('\n').filter(Boolean) })}
            placeholder="One item per line"
            rows={3}
          />
          {prop.description && <label className="label py-0"><span className="label-text-alt opacity-50">{prop.description}</span></label>}
        </div>
      );
    }

    // Default: string
    return (
      <div key={key} className="form-control">
        <label className="label py-1">
          <span className="label-text text-xs">
            {key} {required && <span className="text-error">*</span>}
          </span>
        </label>
        <input
          type="text"
          className="input input-bordered input-sm"
          value={value ?? ''}
          onChange={e => setParams({ ...params, [key]: e.target.value })}
          placeholder={prop.description || key}
        />
        {prop.description && <label className="label py-0"><span className="label-text-alt opacity-50">{prop.description}</span></label>}
      </div>
    );
  };

  // ========================================
  // RENDER
  // ========================================
  return (
    <div className="p-6 h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto">
        {/* Page Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Settings className="h-6 w-6 text-primary" />
              Configuration Engine
            </h1>
            <p className="text-base-content/60 text-sm mt-1">
              Generate, preview, diff, and deploy network configurations using Jinja2 templates.
            </p>
          </div>
        </div>

        {/* Tabs */}
        <div className="tabs tabs-boxed bg-base-200 mb-6 p-1">
          <a
            className={`tab gap-2 ${activeTab === 'generate' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('generate')}
          >
            <Play className="h-4 w-4" />
            Generate Config
          </a>
          <a
            className={`tab gap-2 ${activeTab === 'templates' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('templates')}
          >
            <BookOpen className="h-4 w-4" />
            Template Browser
          </a>
          <a
            className={`tab gap-2 ${activeTab === 'history' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('history')}
          >
            <History className="h-4 w-4" />
            Change History
          </a>
        </div>

        {/* ====== TAB: Generate Config ====== */}
        {activeTab === 'generate' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left: Selector + Form */}
            <div className="space-y-4">
              {/* Vendor/Feature Selector Card */}
              <div className="card bg-base-100 shadow">
                <div className="card-body p-4">
                  <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                    <FileCode className="h-4 w-4 text-primary" />
                    Template Selection
                  </h3>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="form-control">
                      <label className="label py-1"><span className="label-text text-xs">Vendor</span></label>
                      <select
                        className="select select-bordered select-sm"
                        value={selectedVendor}
                        onChange={e => {
                          setSelectedVendor(e.target.value);
                          setSelectedFeature('');
                          setGeneratedConfig('');
                        }}
                      >
                        <option value="cisco_ios">Cisco IOS</option>
                        <option value="huawei_vrp">Huawei VRP</option>
                        <option value="juniper_junos">Juniper Junos</option>
                      </select>
                    </div>
                    <div className="form-control">
                      <label className="label py-1"><span className="label-text text-xs">Feature</span></label>
                      <select
                        className="select select-bordered select-sm"
                        value={selectedFeature}
                        onChange={e => {
                          setSelectedFeature(e.target.value);
                          setGeneratedConfig('');
                        }}
                      >
                        <option value="">Select feature...</option>
                        {filteredTemplates.map(t => (
                          <option key={t.feature} value={t.feature}>
                            {t.feature} {t.has_undo ? '↩' : ''} {t.schema_exists ? '📋' : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>
              </div>

              {/* Parameter Form */}
              {schema && schema.properties && (
                <div className="card bg-base-100 shadow">
                  <div className="card-body p-4">
                    <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                      <Settings className="h-4 w-4 text-secondary" />
                      Parameters
                      <span className="badge badge-ghost badge-sm">
                        {Object.keys(schema.properties).length} fields
                      </span>
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {Object.entries(schema.properties).map(([key, prop]) =>
                        renderParamField(key, prop as SchemaProperty)
                      )}
                    </div>
                    <div className="card-actions justify-end mt-4">
                      <button
                        className={`btn btn-primary btn-sm gap-2 ${isRendering ? 'loading' : ''}`}
                        onClick={handleRender}
                        disabled={isRendering || !selectedFeature}
                      >
                        {!isRendering && <Play className="h-4 w-4" />}
                        Render Config
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Existing Config (for diff) */}
              <div className="card bg-base-100 shadow">
                <div className="card-body p-4">
                  <h3 className="font-semibold text-sm mb-2 flex items-center gap-2">
                    <Eye className="h-4 w-4 text-info" />
                    Current Running Config
                    <span className="badge badge-ghost badge-sm">Optional</span>
                  </h3>
                  <p className="text-xs text-base-content/50 mb-2">
                    Paste the device's current running configuration to see a side-by-side diff.
                  </p>
                  <textarea
                    className="textarea textarea-bordered text-xs font-mono w-full"
                    rows={5}
                    placeholder="Paste current running-config here (optional)..."
                    value={existingConfig}
                    onChange={e => setExistingConfig(e.target.value)}
                  />
                  {existingConfig && generatedConfig && (
                    <button
                      className="btn btn-outline btn-sm btn-info mt-2 gap-2"
                      onClick={() => setShowDiff(!showDiff)}
                    >
                      <Eye className="h-3 w-3" />
                      {showDiff ? 'Hide' : 'Show'} Diff
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Right: Output */}
            <div className="space-y-4">
              {/* Generated Config */}
              {generatedConfig && (
                <div className="card bg-base-100 shadow">
                  <div className="card-body p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h3 className="font-semibold text-sm flex items-center gap-2">
                        <Code className="h-4 w-4 text-success" />
                        Generated Configuration
                      </h3>
                      <div className="flex gap-2">
                        <button className="btn btn-ghost btn-xs gap-1" onClick={handleCopy}>
                          {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
                          {copied ? 'Copied' : 'Copy'}
                        </button>
                      </div>
                    </div>
                    <pre className="bg-base-300 rounded-lg p-4 text-xs font-mono overflow-x-auto max-h-80 overflow-y-auto whitespace-pre-wrap">
                      {generatedConfig}
                    </pre>

                    {undoConfig && (
                      <div className="collapse collapse-arrow bg-base-200 mt-3">
                        <input type="checkbox" />
                        <div className="collapse-title text-xs font-medium py-2">
                          Rollback Commands Available ↩
                        </div>
                        <div className="collapse-content">
                          <pre className="text-xs font-mono whitespace-pre-wrap">
                            {undoConfig}
                          </pre>
                        </div>
                      </div>
                    )}

                    <div className="card-actions justify-end mt-4">
                      <button
                        className="btn btn-warning btn-sm gap-2"
                        onClick={handleDeployClick}
                      >
                        <Rocket className="h-4 w-4" />
                        Deploy to Device
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Diff View */}
              {showDiff && existingConfig && generatedConfig && (
                <div className="card bg-base-100 shadow">
                  <div className="card-body p-4">
                    <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                      <Eye className="h-4 w-4 text-info" />
                      Configuration Diff
                    </h3>
                    <ConfigDiff
                      oldConfig={existingConfig}
                      newConfig={generatedConfig}
                      fromLabel="Running Config"
                      toLabel="Proposed Config"
                    />
                  </div>
                </div>
              )}

              {/* Empty state */}
              {!generatedConfig && !renderError && (
                <div className="card bg-base-100 shadow">
                  <div className="card-body items-center text-center py-16">
                    <FileCode className="h-12 w-12 text-base-content/20 mb-4" />
                    <h3 className="font-medium text-base-content/50">No configuration generated yet</h3>
                    <p className="text-sm text-base-content/40 mt-1">
                      Select a vendor and feature, fill in the parameters, then click "Render Config".
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ====== TAB: Template Browser ====== */}
        {activeTab === 'templates' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Template List */}
            <div className="card bg-base-100 shadow">
              <div className="card-body p-4">
                <h3 className="font-semibold text-sm mb-3">Template Library</h3>
                <div className="form-control mb-3">
                  <select
                    className="select select-bordered select-sm"
                    value={browserVendor}
                    onChange={e => {
                      setBrowserVendor(e.target.value);
                      setBrowserFeature('');
                      setSelectedSchemaSource(null);
                    }}
                  >
                    <option value="cisco_ios">Cisco IOS</option>
                    <option value="huawei_vrp">Huawei VRP</option>
                    <option value="juniper_junos">Juniper Junos</option>
                  </select>
                </div>
                <ul className="menu menu-sm bg-base-200 rounded-lg max-h-96 overflow-y-auto">
                  {isLoadingTemplates ? (
                    <li className="text-center py-4">
                      <Loader2 className="h-4 w-4 animate-spin mx-auto" />
                    </li>
                  ) : browserTemplates.length === 0 ? (
                    <li className="text-center py-4 text-sm opacity-50">No templates found</li>
                  ) : (
                    browserTemplates.map(t => (
                      <li key={t.feature}>
                        <a
                          className={browserFeature === t.feature ? 'active' : ''}
                          onClick={async () => {
                            setBrowserFeature(t.feature);
                            // Load schema for browser view
                            try {
                              const s = await configApi.getSchema(t.feature);
                              setSelectedSchemaSource(s);
                            } catch {
                              setSelectedSchemaSource(null);
                            }
                          }}
                        >
                          <FileCode className="h-3 w-3" />
                          {t.feature}
                          <div className="flex gap-1 ml-auto">
                            {t.has_undo && <span className="badge badge-xs badge-ghost">undo</span>}
                            {t.schema_exists && <span className="badge badge-xs badge-info">schema</span>}
                          </div>
                        </a>
                      </li>
                    ))
                  )}
                </ul>
              </div>
            </div>

            {/* Schema Viewer */}
            <div className="lg:col-span-2 space-y-4">
              {selectedSchemaSource ? (
                <>
                  <div className="card bg-base-100 shadow">
                    <div className="card-body p-4">
                      <h3 className="font-semibold text-sm mb-3">
                        Schema: {browserFeature}
                      </h3>
                      <pre className="bg-base-300 rounded-lg p-4 text-xs font-mono overflow-x-auto max-h-96 overflow-y-auto">
                        {JSON.stringify(selectedSchemaSource, null, 2)}
                      </pre>
                    </div>
                  </div>
                  {selectedSchemaSource.properties && (
                    <div className="card bg-base-100 shadow">
                      <div className="card-body p-4">
                        <h3 className="font-semibold text-sm mb-3">Parameter Reference</h3>
                        <div className="overflow-x-auto">
                          <table className="table table-sm table-zebra">
                            <thead>
                              <tr>
                                <th>Parameter</th>
                                <th>Type</th>
                                <th>Required</th>
                                <th>Description</th>
                              </tr>
                            </thead>
                            <tbody>
                              {Object.entries(selectedSchemaSource.properties).map(([key, prop]: [string, any]) => (
                                <tr key={key}>
                                  <td className="font-mono text-xs">{key}</td>
                                  <td><span className="badge badge-sm badge-ghost">{prop.type}</span></td>
                                  <td>{selectedSchemaSource.required?.includes(key) ? '✓' : ''}</td>
                                  <td className="text-xs opacity-70">{prop.description || '—'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <div className="card bg-base-100 shadow">
                  <div className="card-body items-center text-center py-16">
                    <BookOpen className="h-12 w-12 text-base-content/20 mb-4" />
                    <h3 className="font-medium text-base-content/50">Select a template</h3>
                    <p className="text-sm text-base-content/40 mt-1">
                      Click a template from the list to view its JSON schema and parameter reference.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ====== TAB: Change History ====== */}
        {activeTab === 'history' && (
          <div className="card bg-base-100 shadow">
            <div className="card-body p-4">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold text-sm flex items-center gap-2">
                  <History className="h-4 w-4 text-accent" />
                  Deployment History
                </h3>
                <button className="btn btn-ghost btn-xs" onClick={loadJobs}>
                  Refresh
                </button>
              </div>

              {isLoadingJobs ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-primary" />
                </div>
              ) : jobs.length === 0 ? (
                <div className="text-center py-12">
                  <History className="h-12 w-12 text-base-content/20 mx-auto mb-4" />
                  <p className="text-base-content/50">No deployment history found.</p>
                  <p className="text-sm text-base-content/40 mt-1">
                    Deploy a configuration from the "Generate Config" tab to see it here.
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="table table-sm">
                    <thead>
                      <tr>
                        <th>ID</th>
                        <th>Stage</th>
                        <th>Status</th>
                        <th>Timestamp</th>
                        <th>Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {jobs.map((job, i) => (
                        <tr key={i}>
                          <td className="font-mono text-xs">{job.id || job.job_id || i}</td>
                          <td>
                            <span className="badge badge-sm badge-outline">
                              {job.stage || job.step_name || '—'}
                            </span>
                          </td>
                          <td>
                            {job.status === 'success' || job.status === 'completed' ? (
                              <span className="badge badge-sm badge-success gap-1">
                                <CheckCircle className="h-3 w-3" />
                                {job.status}
                              </span>
                            ) : job.status === 'failed' ? (
                              <span className="badge badge-sm badge-error gap-1">
                                <AlertTriangle className="h-3 w-3" />
                                {job.status}
                              </span>
                            ) : (
                              <span className="badge badge-sm badge-ghost">{job.status || '—'}</span>
                            )}
                          </td>
                          <td className="text-xs opacity-60">{job.created_at || job.timestamp || '—'}</td>
                          <td className="text-xs max-w-xs truncate">{job.detail || job.message || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Approval Dialog */}
      <ApprovalDialog
        isOpen={approvalOpen}
        onClose={() => setApprovalOpen(false)}
        vendor={selectedVendor}
        feature={selectedFeature}
        generatedConfig={generatedConfig}
        undoConfig={undoConfig}
        diffSummary={diffSummary}
        onDeploySuccess={(jobId) => {
          Toast.success('Deployment Successful', `Job #${jobId} completed`);
          setApprovalOpen(false);
        }}
      />
    </div>
  );
};

export default Configuration;
