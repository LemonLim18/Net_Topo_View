// Backend API configuration
export const API_CONFIG = {
  // Use environment variable or default to current host
  WS_URL: import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:8000/ws`,
  API_URL: import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8000`,
};

/** Default LLM before GET /llm/.../runtime returns (overridden by server). */
export const DEFAULT_LLM_PROVIDER =
  (import.meta.env.VITE_DEFAULT_LLM_PROVIDER as string | undefined)?.trim() || 'ollama';
export const DEFAULT_OLLAMA_MODEL =
  (import.meta.env.VITE_DEFAULT_OLLAMA_MODEL as string | undefined)?.trim() || 'qwen2.5-coder:3b';
/** Browser → Ollama API; override with VITE_OLLAMA_BASE_URL or localStorage. */
export const DEFAULT_OLLAMA_BASE_URL =
  (import.meta.env.VITE_OLLAMA_BASE_URL as string | undefined)?.trim() || 'http://127.0.0.1:11434';

