// Backend API configuration
export const API_CONFIG = {
  // Use environment variable or default to localhost
  // WS_URL: import.meta.env.VITE_WS_URL || 'ws://192.168.1.250:8000/ws',
  WS_URL: import.meta.env.VITE_WS_URL || 'ws://192.168.1.2:8000/ws',
  // API_URL: import.meta.env.VITE_API_URL || 'http://192.168.1.250:8000',
  API_URL: import.meta.env.VITE_API_URL || 'http://192.168.1.2:8000',
};

