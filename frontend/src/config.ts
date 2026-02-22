// Backend API configuration
export const API_CONFIG = {
  // Use environment variable or default to current host
  WS_URL: import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:8000/ws`,
  API_URL: import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8000`,
};

