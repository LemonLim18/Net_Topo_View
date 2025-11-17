import React, { useState } from 'react';
import { Download, Copy, Check } from 'lucide-react';
import { mockTopologyData as INITIAL_CONFIG_DATA } from '../constants';

const Configuration: React.FC = () => {
  const [showConfig, setShowConfig] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    const configText = JSON.stringify(INITIAL_CONFIG_DATA, null, 2);
    navigator.clipboard.writeText(configText.trim());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="p-8 h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold mb-2">Device Configuration</h1>
        <p className="text-base-content/70 mb-8">
          Manage and deploy initial configurations to establish basic network connectivity.
        </p>

        <div className="card bg-base-100 shadow-xl">
          <div className="card-body">
            <h2 className="card-title">Initial Bootstrap Configuration</h2>
            <p>
              Fetch the baseline configuration from the provisioning server. This template is used for all new campus routers.
            </p>
            <div className="card-actions justify-start mt-4">
              <button className="btn btn-primary" onClick={() => setShowConfig(!showConfig)}>
                <Download className="h-4 w-4" />
                {showConfig ? 'Hide' : 'Fetch'} Configuration
              </button>
            </div>

            {showConfig && (
              <div className="mt-6">
                <div className="mockup-code">
                  <div className="absolute top-2 right-2">
                    <button className="btn btn-ghost btn-sm" onClick={handleCopy}>
                      {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
                    </button>
                  </div>
                  {
                    JSON.stringify(INITIAL_CONFIG_DATA, null, 2).trim().split('\n').map((line, i) => (
                      <pre data-prefix={i + 1} key={i}><code>{line}</code></pre>
                    ))
                  }
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Configuration;
