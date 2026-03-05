import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { netboxApi, type DeviceRecord, type LinkRecord } from '../src/services/api';

interface DeviceContextType {
  devices: DeviceRecord[];
  links: LinkRecord[];
  isLoading: boolean;
  refreshDevices: () => Promise<void>;
  refreshLinks: () => Promise<void>;
  refreshAll: () => Promise<void>;
}

const DeviceContext = createContext<DeviceContextType | undefined>(undefined);

const CACHE_KEY_DEVICES = 'netbox_devices_cache';
const CACHE_KEY_LINKS = 'netbox_links_cache';

export const DeviceProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [devices, setDevices] = useState<DeviceRecord[]>(() => {
    const cached = localStorage.getItem(CACHE_KEY_DEVICES);
    return cached ? JSON.parse(cached) : [];
  });
  const [links, setLinks] = useState<LinkRecord[]>(() => {
    const cached = localStorage.getItem(CACHE_KEY_LINKS);
    return cached ? JSON.parse(cached) : [];
  });
  const [isLoading, setIsLoading] = useState(false);
  const [hasLoadedInitially, setHasLoadedInitially] = useState(false);

  const refreshDevices = useCallback(async () => {
    try {
      const res = await netboxApi.listDevices();
      const newDevices = res.devices || [];
      setDevices(newDevices);
      localStorage.setItem(CACHE_KEY_DEVICES, JSON.stringify(newDevices));
    } catch (e) {
      console.error('[DeviceContext] Failed to refresh devices:', e);
    }
  }, []);

  const refreshLinks = useCallback(async () => {
    try {
      const res = await netboxApi.listLinks();
      const newLinks = res.links || [];
      setLinks(newLinks);
      localStorage.setItem(CACHE_KEY_LINKS, JSON.stringify(newLinks));
    } catch (e) {
      console.error('[DeviceContext] Failed to refresh links:', e);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    // Only show loader if we have no cached data, otherwise refresh silently in background
    const hasData = devices.length > 0 || links.length > 0;
    if (!hasData) setIsLoading(true);
    
    try {
      await Promise.all([refreshDevices(), refreshLinks()]);
      setHasLoadedInitially(true);
    } finally {
      setIsLoading(false);
    }
  }, [devices.length, links.length, refreshDevices, refreshLinks]);

  // Initial load on mount
  useEffect(() => {
    if (!hasLoadedInitially) {
      refreshAll();
    }
  }, [hasLoadedInitially, refreshAll]);

  return (
    <DeviceContext.Provider value={{ devices, links, isLoading, refreshDevices, refreshLinks, refreshAll }}>
      {children}
    </DeviceContext.Provider>
  );
};

export const useDeviceContext = () => {
  const context = useContext(DeviceContext);
  if (context === undefined) {
    throw new Error('useDeviceContext must be used within a DeviceProvider');
  }
  return context;
};
