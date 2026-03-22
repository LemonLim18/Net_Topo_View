import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, RefreshCw, ServerCog } from 'lucide-react';

import { llmApi } from '../src/services/api';
import type { LlmRuntimeProviderDescriptor, LlmRuntimeState } from '../src/services/api';
import { Toast } from '../src/utils/swal';
import { DEFAULT_LLM_PROVIDER, DEFAULT_OLLAMA_BASE_URL } from '../src/config';

const OLLAMA_BASE_URL_STORAGE_KEY = 'llmRuntimeSelector.ollamaBaseUrl';

interface LLMRuntimeSelectorProps {
  compact?: boolean;
  className?: string;
}

interface CombinedModelOption {
  providerName: string;
  providerLabel: string;
  modelName: string;
  displayLabel: string;
}

const prettifyModelName = (providerName: string, modelName: string) => {
  if (providerName === 'gemini') {
    if (modelName === 'gemini-2.5-flash') return 'Gemini 2.5 Flash | Recommended';
    if (modelName === 'gemini-2.5-pro') return 'Gemini 2.5 Pro';
  }
  if (providerName === 'ollama') {
    if (modelName === 'qwen2.5-coder:3b') return 'Qwen 2.5 Coder 3B | Ollama (local)';
    return `${modelName} | Ollama (local)`;
  }
  return modelName;
};

export const LLMRuntimeSelector: React.FC<LLMRuntimeSelectorProps> = ({
  compact = false,
  className = '',
}) => {
  const [runtime, setRuntime] = useState<LlmRuntimeState | null>(null);
  const [selectedProvider, setSelectedProvider] = useState(DEFAULT_LLM_PROVIDER);
  const [selectedModel, setSelectedModel] = useState('');
  const [ollamaBaseUrl, setOllamaBaseUrl] = useState(() => localStorage.getItem(OLLAMA_BASE_URL_STORAGE_KEY) || DEFAULT_OLLAMA_BASE_URL);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isApplying, setIsApplying] = useState(false);

  const loadRuntime = async (requestedBaseUrl?: string) => {
    const browseUrl = (requestedBaseUrl || ollamaBaseUrl || DEFAULT_OLLAMA_BASE_URL).trim();
    setIsLoading(true);
    try {
      const nextRuntime = await llmApi.getRuntime(browseUrl);
      setRuntime(nextRuntime);
      setSelectedProvider(nextRuntime.active_provider || DEFAULT_LLM_PROVIDER);
      setSelectedModel(nextRuntime.active_model || '');
      const ollamaDescriptor = nextRuntime.providers.find((provider) => provider.name === 'ollama');
      const nextBaseUrl = ollamaDescriptor?.base_url || browseUrl;
      setOllamaBaseUrl(nextBaseUrl);
      localStorage.setItem(OLLAMA_BASE_URL_STORAGE_KEY, nextBaseUrl);
    } catch (error: any) {
      Toast.error('LLM Runtime', error.message || 'Failed to load model runtime');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadRuntime();
  }, []);

  const providerDescriptors = runtime?.providers || [];
  const activeProvider = runtime?.active_provider || selectedProvider;
  const activeModel = runtime?.active_model || selectedModel;
  const activeBaseUrl = runtime?.active_base_url;

  const selectedDescriptor = useMemo<LlmRuntimeProviderDescriptor | undefined>(() => {
    return providerDescriptors.find((provider) => provider.name === selectedProvider);
  }, [providerDescriptors, selectedProvider]);

  const ollamaDescriptor = useMemo<LlmRuntimeProviderDescriptor | undefined>(() => {
    return providerDescriptors.find((provider) => provider.name === 'ollama');
  }, [providerDescriptors]);

  const combinedModelOptions = useMemo<CombinedModelOption[]>(() => {
    return providerDescriptors.flatMap((provider) =>
      provider.models.map((modelName) => ({
        providerName: provider.name,
        providerLabel: provider.label,
        modelName,
        displayLabel: prettifyModelName(provider.name, modelName),
      })),
    );
  }, [providerDescriptors]);

  const combinedModelGroups = useMemo(() => {
    return providerDescriptors
      .map((provider) => ({
        provider,
        options: combinedModelOptions.filter((option) => option.providerName === provider.name),
      }))
      .filter((group) => group.options.length > 0);
  }, [combinedModelOptions, providerDescriptors]);

  const isDirty = useMemo(() => {
    if (!runtime) return false;
    if (selectedProvider !== activeProvider) return true;
    if ((selectedModel || '') !== (activeModel || '')) return true;
    if (selectedProvider === 'ollama') {
      return (ollamaBaseUrl.trim() || DEFAULT_OLLAMA_BASE_URL) !== (activeBaseUrl || DEFAULT_OLLAMA_BASE_URL);
    }
    return false;
  }, [activeBaseUrl, activeModel, activeProvider, ollamaBaseUrl, runtime, selectedModel, selectedProvider]);

  const handleRefreshModels = async () => {
    const baseUrl = ollamaBaseUrl.trim() || DEFAULT_OLLAMA_BASE_URL;
    setIsRefreshing(true);
    try {
      const nextRuntime = await llmApi.getRuntime(baseUrl);
      setRuntime(nextRuntime);
      const ollamaDescriptor = nextRuntime.providers.find((provider) => provider.name === 'ollama');
      const refreshedOptions = nextRuntime.providers.flatMap((provider) =>
        provider.models.map((modelName) => ({ providerName: provider.name, modelName })),
      );
      const refreshedSelection = refreshedOptions.find((option) => option.modelName === selectedModel && option.providerName === selectedProvider);
      if (!refreshedSelection && selectedProvider === 'ollama' && ollamaDescriptor) {
        setSelectedModel(ollamaDescriptor.models[0] || selectedModel);
      }
      localStorage.setItem(OLLAMA_BASE_URL_STORAGE_KEY, baseUrl);
      Toast.success('Ollama Models', ollamaDescriptor?.catalog_source === 'live' ? 'Live model catalog refreshed.' : 'Using fallback model list because the Ollama VM is unreachable.');
    } catch (error: any) {
      Toast.error('Ollama Models', error.message || 'Failed to refresh model list');
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleApply = async () => {
    if (!selectedModel) {
      Toast.warning('LLM Runtime', 'Select a model before applying the runtime.');
      return;
    }
    setIsApplying(true);
    try {
      const nextRuntime = await llmApi.updateRuntime({
        provider: selectedProvider,
        model: selectedModel,
        ollama_base_url: selectedProvider === 'ollama' ? (ollamaBaseUrl.trim() || DEFAULT_OLLAMA_BASE_URL) : undefined,
        clear_history: true,
      });
      setRuntime(nextRuntime);
      setSelectedProvider(nextRuntime.active_provider);
      setSelectedModel(nextRuntime.active_model);
      const nextBaseUrl = nextRuntime.active_base_url || ollamaBaseUrl.trim() || DEFAULT_OLLAMA_BASE_URL;
      setOllamaBaseUrl(nextBaseUrl);
      localStorage.setItem(OLLAMA_BASE_URL_STORAGE_KEY, nextBaseUrl);
      Toast.success('LLM Runtime', `${nextRuntime.active_provider} • ${nextRuntime.active_model}`);
    } catch (error: any) {
      Toast.error('LLM Runtime', error.message || 'Failed to apply runtime selection');
    } finally {
      setIsApplying(false);
    }
  };

  const handleModelChange = (nextValue: string) => {
    const [providerName, modelName] = nextValue.split('::', 2);
    setSelectedProvider(providerName || DEFAULT_LLM_PROVIDER);
    setSelectedModel(modelName || '');
  };

  const containerClassName = compact
    ? 'rounded-lg border border-base-300 bg-base-200/70 p-2'
    : 'rounded-xl border border-base-300 bg-base-200/70 p-3';

  return (
    <div className={`${containerClassName} ${className}`.trim()}>
      <div className={`flex ${compact ? 'flex-col gap-2' : 'flex-col gap-3'}`}>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-base-content/60">
            <ServerCog className="h-3.5 w-3.5" />
            <span>LLM Runtime</span>
          </div>
          {isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-base-content/50" />}
        </div>

        <div className={`grid ${compact ? 'grid-cols-1 gap-2' : 'grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_auto]'}`}>
          <label className="form-control w-full">
            <span className="label py-1 text-[11px] text-base-content/60">Model</span>
            <select
              className="select select-sm select-bordered w-full"
              value={selectedModel ? `${selectedProvider}::${selectedModel}` : ''}
              disabled={isLoading || isApplying || combinedModelOptions.length === 0}
              onChange={(event) => handleModelChange(event.target.value)}
            >
              {combinedModelGroups.length === 0 ? (
                <option value="">No models available</option>
              ) : (
                combinedModelGroups.map((group) => (
                  <optgroup key={group.provider.name} label={group.provider.label}>
                    {group.options.map((option) => (
                      <option key={`${option.providerName}:${option.modelName}`} value={`${option.providerName}::${option.modelName}`}>
                        {option.displayLabel}
                      </option>
                    ))}
                  </optgroup>
                ))
              )}
            </select>
          </label>

          <button
            type="button"
            className="btn btn-sm btn-primary self-end"
            disabled={isLoading || isApplying || !selectedModel || !isDirty}
            onClick={handleApply}
          >
            {isApplying ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Apply'}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[11px] text-base-content/60">
          <span className="badge badge-outline badge-sm max-w-full truncate" title={`${activeProvider} / ${prettifyModelName(activeProvider, activeModel || 'unset')}`}>
            Active: {activeProvider} / {prettifyModelName(activeProvider, activeModel || 'unset')}
          </span>
          <span className={`badge badge-sm ${selectedProvider === 'ollama' ? 'badge-secondary' : 'badge-primary'}`}>
            {selectedProvider === 'ollama' ? 'Local model' : 'Cloud model'}
          </span>
          <span className="badge badge-sm badge-accent">Gemini Flash recommended</span>
          {selectedProvider === 'ollama' && (
            <span className={`badge badge-sm ${selectedDescriptor?.available ? 'badge-success' : 'badge-warning'}`}>
              {selectedDescriptor?.available ? 'Ollama reachable' : 'Ollama unavailable'}
            </span>
          )}
          {selectedDescriptor?.error && (
            <span className="truncate text-error" title={selectedDescriptor.error}>
              {selectedDescriptor.error}
            </span>
          )}
        </div>

        {ollamaDescriptor && ollamaDescriptor.catalog_source !== 'live' && (
          <div className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-[11px] text-base-content/75">
            The full Ollama VM catalog is not reachable from this machine right now, so the dropdown can only show the fallback local model entry.
            {ollamaDescriptor.base_url ? ` Endpoint: ${ollamaDescriptor.base_url}` : ''}
          </div>
        )}

        <div className={`grid ${compact ? 'grid-cols-1 gap-2' : 'grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_auto]'}`}>
          <div className={`grid ${compact ? 'grid-cols-1 gap-2' : 'grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_auto]'}`}>
            <label className="form-control w-full">
              <span className="label py-1 text-[11px] text-base-content/60">Local model endpoint</span>
              <input
                type="text"
                className="input input-sm input-bordered w-full"
                value={ollamaBaseUrl}
                disabled={isLoading || isApplying}
                onChange={(event) => setOllamaBaseUrl(event.target.value)}
                placeholder={DEFAULT_OLLAMA_BASE_URL}
              />
            </label>
            <button
              type="button"
              className="btn btn-sm btn-outline self-end"
              disabled={isLoading || isRefreshing || isApplying}
              onClick={handleRefreshModels}
            >
              {isRefreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              Refresh Local Models
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};