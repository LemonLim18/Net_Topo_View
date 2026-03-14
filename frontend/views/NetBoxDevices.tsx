import React, { useState, useEffect, useCallback } from 'react';
import {
  Server,
  Plus,
  RefreshCw,
  Search,
  Edit3,
  Trash2,
  CheckCircle,
  XCircle,
  HelpCircle,
  Loader2,
  Download,
  AlertTriangle,
  Upload,
  Network,
  Cable,
  Clock,
  Cpu,
  ChevronUp,
  ChevronDown,
} from 'lucide-react';
import { netboxApi, type DeviceRecord, type InterfaceRecord, type LinkRecord } from '../src/services/api';
import { useDeviceContext } from '../contexts/DeviceContext';
import { Toast, Confirm } from '../src/utils/swal';

const NETBOX_SELECTED_DEVICE_KEY = 'netbox.selectedDeviceId';
const NETBOX_DETAIL_TAB_KEY = 'netbox.detailTab';
const NETBOX_SEARCH_KEY = 'netbox.search';
const NETBOX_FILTER_VENDOR_KEY = 'netbox.filterVendor';
const NETBOX_FILTER_STATUS_KEY = 'netbox.filterStatus';
const NETBOX_INTERFACES_CACHE_KEY = 'netbox.interfacesCache';

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------

const VENDOR_OPTIONS = [
  { value: '', label: 'Select vendor…' },
  { value: 'cisco_ios', label: 'Cisco IOS / IOS-XE' },
  { value: 'juniper_junos', label: 'Juniper JunOS' },
  { value: 'huawei_vrp', label: 'Huawei VRP' },
];

const STATUS_OPTIONS = [
  { value: 'planned', label: 'Planned' },
  { value: 'provisioning', label: 'Provisioning' },
  { value: 'active', label: 'Active' },
  { value: 'down', label: 'Down' },
  { value: 'unknown', label: 'Unknown' },
];

const ROLE_OPTIONS = [
  // { value: 'network-device', label: 'Network Device' },
  { value: 'core', label: 'Core Switch' },
  { value: 'distribution', label: 'Distribution Switch' },
  { value: 'access', label: 'Access Switch' },
  { value: 'firewall', label: 'Firewall' },
];

const vendorLabel = (v: string | null) => {
  if (!v) return '—';
  const found = VENDOR_OPTIONS.find((o) => o.value === v);
  return found ? found.label : v;
};

const statusBadge = (status: string) => {
  switch (status) {
    case 'planned':
      return (
        <span className="badge badge-sm badge-info gap-1">
          <Upload className="w-3 h-3" /> Planned
        </span>
      );
    case 'provisioning':
      return (
        <span className="badge badge-sm badge-secondary gap-1">
          <Clock className="w-3 h-3" /> Provisioning
        </span>
      );
    case 'active':
      return (
        <span className="badge badge-sm badge-success gap-1">
          <CheckCircle className="w-3 h-3" /> Active
        </span>
      );
    case 'down':
      return (
        <span className="badge badge-sm badge-error gap-1">
          <XCircle className="w-3 h-3" /> Down
        </span>
      );
    default:
      return (
        <span className="badge badge-sm badge-ghost gap-1">
          <HelpCircle className="w-3 h-3" /> Unknown
        </span>
      );
  }
};

const sourceBadge = (source: string) => {
  switch (source) {
    case 'manual':
      return <span className="badge badge-xs badge-outline">Manual</span>;
    case 'crawler':
      return <span className="badge badge-xs badge-primary badge-outline">Crawler</span>;
    case 'netbox':
      return <span className="badge badge-xs badge-accent badge-outline">NetBox</span>;
    default:
      return <span className="badge badge-xs badge-ghost">{source}</span>;
  }
};

const roleBadge = (role: string | null) => {
  if (!role) return <span className="badge badge-xs badge-ghost whitespace-nowrap">Unknown</span>;
  const label = ROLE_OPTIONS.find((o) => o.value === role)?.label || role;

  let colorClass = 'badge-ghost';
  switch (role) {
    case 'core':
      colorClass = 'badge-primary';
      break;
    case 'distribution':
      colorClass = 'badge-secondary';
      break;
    case 'access':
      colorClass = 'badge-accent';
      break;
    case 'firewall':
      colorClass = 'badge-error';
      break;
  }

  return (
    <span className={`badge ${colorClass} badge-sm badge-outline opacity-90 whitespace-nowrap py-1 px-2`}>
      {label}
    </span>
  );
};

interface SortHeaderProps {
  label: string;
  sortKey: string;
  currentKey: string;
  order: 'asc' | 'desc';
  onSort: (key: string) => void;
}

const SortHeader: React.FC<SortHeaderProps> = ({ label, sortKey, currentKey, order, onSort }) => {
  const isActive = currentKey === sortKey;
  return (
    <th 
      className="cursor-pointer hover:bg-base-300 transition-colors select-none group"
      onClick={() => onSort(sortKey)}
    >
      <div className="flex items-center gap-1">
        {label}
        <div className={`transition-opacity ${isActive ? 'opacity-100' : 'opacity-0 group-hover:opacity-40'}`}>
          {isActive && order === 'desc' ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
        </div>
      </div>
    </th>
  );
};

// ---------------------------------------------------------------------------
//  Empty form
// ---------------------------------------------------------------------------
const EMPTY_FORM = {
  hostname: '',
  management_ip: '',
  management_subnet_mask: '255.255.255.0',
  vendor: '',
  model: '',
  os_version: '',
  serial_number: '',
  status: 'planned',
  role: 'core',
  ssh_user: '',
  ssh_password: '',
  ssh_port: '22',
};

const EMPTY_IFACE_FORM = {
  name: '',
  ip_address: '',
  subnet_mask: '255.255.255.0',
  mac_address: '',
  speed: '',
};

// ---------------------------------------------------------------------------
//  Component
// ---------------------------------------------------------------------------

const NetBoxDevices: React.FC = () => {
  const [persistedSelectedDeviceId] = useState<number | null>(() => {
    const raw = Number(localStorage.getItem(NETBOX_SELECTED_DEVICE_KEY));
    return Number.isFinite(raw) ? raw : null;
  });

  // Data from Context
  const { 
    devices, 
    links, 
    isLoading: isGlobalLoading, 
    refreshDevices, 
    refreshLinks, 
    refreshAll 
  } = useDeviceContext();

  const [selected, setSelected] = useState<DeviceRecord | null>(null);
  const [isActionLoading, setIsActionLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  // Interfaces for selected device
  const [interfaces, setInterfaces] = useState<InterfaceRecord[]>(() => {
    try {
      const cached = localStorage.getItem(NETBOX_INTERFACES_CACHE_KEY);
      const selId = localStorage.getItem(NETBOX_SELECTED_DEVICE_KEY);
      if (cached && selId) {
        const cacheObj = JSON.parse(cached);
        return cacheObj[selId] || [];
      }
    } catch (e) { /* ignore */ }
    return [];
  });
  const [loadingIfaces, setLoadingIfaces] = useState(false);

  // Detail tab
  const [detailTab, setDetailTab] = useState<'info' | 'interfaces' | 'links'>(() => {
    const raw = localStorage.getItem(NETBOX_DETAIL_TAB_KEY);
    if (raw === 'info' || raw === 'interfaces' || raw === 'links') return raw;
    return 'info';
  });

  // Search / filter
  const [search, setSearch] = useState(() => localStorage.getItem(NETBOX_SEARCH_KEY) || '');
  const [filterVendor, setFilterVendor] = useState(() => localStorage.getItem(NETBOX_FILTER_VENDOR_KEY) || '');
  const [filterStatus, setFilterStatus] = useState(() => localStorage.getItem(NETBOX_FILTER_STATUS_KEY) || '');

  // Sorting state
  const [sortKey, setSortKey] = useState<string>('hostname');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  // Device modal state
  const [showModal, setShowModal] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [formError, setFormError] = useState<string | null>(null);

  // Interface modal state
  const [showIfaceModal, setShowIfaceModal] = useState(false);
  const [isEditingIface, setIsEditingIface] = useState(false);
  const [editIfaceId, setEditIfaceId] = useState<number | null>(null);
  const [ifaceForm, setIfaceForm] = useState({ ...EMPTY_IFACE_FORM });
  const [ifaceFormError, setIfaceFormError] = useState<string | null>(null);

  // Link modal state
  const [showLinkModal, setShowLinkModal] = useState(false);
  const [linkForm, setLinkForm] = useState({
    src_device_id: 0,
    src_interface_id: 0,
    dst_device_id: 0,
    dst_interface_id: 0,
  });
  const [linkFormError, setLinkFormError] = useState<string | null>(null);
  const [srcInterfaces, setSrcInterfaces] = useState<InterfaceRecord[]>([]);
  const [dstInterfaces, setDstInterfaces] = useState<InterfaceRecord[]>([]);

  // -----------------------------------------------------------------------
  //  Data fetching
  // -----------------------------------------------------------------------

  const fetchDevices = useCallback(async (updateCache = false) => {
    try {
      await refreshDevices(updateCache);
      
      if (selected?.id) {
        // We don't have the updated list here yet since setDevices is async,
        // but we can trust the context will update.
      }
    } catch (e: any) {
      Toast.error('Failed to load devices', e.message);
    }
  }, [refreshDevices, selected?.id]);

  const fetchInterfaces = useCallback(async (deviceId: number, updateCache = false) => {
    // 1. Try to load from cache immediately to avoid spinner
    let cacheFound = false;
    try {
      const cachedRaw = localStorage.getItem(NETBOX_INTERFACES_CACHE_KEY);
      if (cachedRaw) {
        const cacheObj = JSON.parse(cachedRaw);
        const cachedIfaces = cacheObj[deviceId];
        if (cachedIfaces && Array.isArray(cachedIfaces)) {
          setInterfaces(cachedIfaces);
          cacheFound = true;
        }
      }
    } catch (e) { /* ignore cache read errors */ }

    // 2. Only show spinner if no cached data for this device
    if (!cacheFound) {
      setInterfaces([]); // Clear if no cache to avoid showing previous device data
      setLoadingIfaces(true);
    }
    
    try {
      const res = await netboxApi.listInterfaces(deviceId);
      const newIfaces = res.interfaces || [];
      setInterfaces(newIfaces);
      
      // Update persistent cache ONLY if updateCache is true (Sync or Manual Refresh)
      if (updateCache) {
        try {
          const cachedRaw = localStorage.getItem(NETBOX_INTERFACES_CACHE_KEY);
          const cacheObj = cachedRaw ? JSON.parse(cachedRaw) : {};
          cacheObj[deviceId] = newIfaces;
          localStorage.setItem(NETBOX_INTERFACES_CACHE_KEY, JSON.stringify(cacheObj));
        } catch (ce) { /* ignore cache write errors */ }
      }
      
    } catch (e: any) {
      Toast.error('Failed to load interfaces', e.message);
    } finally {
      setLoadingIfaces(false);
    }
  }, []);

  const fetchLinks = useCallback(async (updateCache = false) => {
    try {
      await refreshLinks(updateCache);
    } catch (e: any) {
      Toast.error('Failed to load links', e.message);
    }
  }, [refreshLinks]);

  // Handle selected device hydration from persisted ID
  useEffect(() => {
    if (!selected && persistedSelectedDeviceId && devices.length > 0) {
      const restored = devices.find(d => d.id === persistedSelectedDeviceId) || null;
      if (restored) setSelected(restored);
    }
  }, [devices, persistedSelectedDeviceId, selected]);

  useEffect(() => {
    if (selected) {
      fetchInterfaces(selected.id);
    } else {
      setInterfaces([]);
    }
  }, [selected, fetchInterfaces]);

  useEffect(() => {
    if (selected?.id) {
      localStorage.setItem(NETBOX_SELECTED_DEVICE_KEY, String(selected.id));
    } else {
      localStorage.removeItem(NETBOX_SELECTED_DEVICE_KEY);
    }
  }, [selected]);

  useEffect(() => {
    localStorage.setItem(NETBOX_DETAIL_TAB_KEY, detailTab);
  }, [detailTab]);

  useEffect(() => {
    localStorage.setItem(NETBOX_SEARCH_KEY, search);
  }, [search]);

  useEffect(() => {
    localStorage.setItem(NETBOX_FILTER_VENDOR_KEY, filterVendor);
  }, [filterVendor]);

  useEffect(() => {
    localStorage.setItem(NETBOX_FILTER_STATUS_KEY, filterStatus);
  }, [filterStatus]);

  // -----------------------------------------------------------------------
  //  Filtering
  // -----------------------------------------------------------------------

  const filtered = devices.filter((d) => {
    const q = search.toLowerCase();
    const matchesSearch =
      !q ||
      d.hostname.toLowerCase().includes(q) ||
      (d.management_ip && d.management_ip.toLowerCase().includes(q)) ||
      (d.vendor && d.vendor.toLowerCase().includes(q)) ||
      (d.model && d.model.toLowerCase().includes(q));
    const matchesVendor = !filterVendor || d.vendor === filterVendor;
    const matchesStatus = !filterStatus || d.status === filterStatus;
    return matchesSearch && matchesVendor && matchesStatus;
  });

  const sortedDevices = React.useMemo(() => {
    const result = [...filtered];
    result.sort((a: any, b: any) => {
      let valA = a[sortKey] || '';
      let valB = b[sortKey] || '';

      // Special handling for labels if needed, but strings work fine for these fields
      if (typeof valA === 'string') valA = valA.toLowerCase();
      if (typeof valB === 'string') valB = valB.toLowerCase();

      if (valA < valB) return sortOrder === 'asc' ? -1 : 1;
      if (valA > valB) return sortOrder === 'asc' ? 1 : -1;
      return 0;
    });
    return result;
  }, [filtered, sortKey, sortOrder]);

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortOrder('asc');
    }
  };

  // -----------------------------------------------------------------------
  //  Device CRUD handlers
  // -----------------------------------------------------------------------

  const handleCreate = () => {
    setForm({ ...EMPTY_FORM });
    setIsEditing(false);
    setEditId(null);
    setFormError(null);
    setShowModal(true);
  };

  const handleEdit = (d: DeviceRecord) => {
    setForm({
      hostname: d.hostname,
      management_ip: d.management_ip || '',
      management_subnet_mask: d.management_subnet_mask || '255.255.255.0',
      vendor: d.vendor || '',
      model: d.model || '',
      os_version: d.os_version || '',
      serial_number: d.serial_number || '',
      status: (d.status === 'registered' || !d.status) ? 'planned' : d.status,
      role: d.role || 'network-device',
      ssh_user: d.ssh_user || '',
      ssh_password: '',
      ssh_port: String(d.ssh_port || 22),
    });
    setIsEditing(true);
    setEditId(d.id);
    setFormError(null);
    setShowModal(true);
  };

  const handleSubmit = async () => {
    if (!form.hostname.trim()) {
      setFormError('Hostname is required');
      return;
    }
    setFormError(null);
    setIsActionLoading(true);

    try {
      if (isEditing && editId !== null) {
        const updated = await netboxApi.updateDevice(editId, {
          hostname: form.hostname,
          management_ip: form.management_ip,
          management_subnet_mask: form.management_subnet_mask,
          vendor: form.vendor,
          model: form.model,
          os_version: form.os_version,
          serial_number: form.serial_number,
          status: form.status,
          role: form.role,
          ssh_user: form.ssh_user,
          ssh_password: form.ssh_password,
          ssh_port: form.ssh_port ? Number(form.ssh_port) : undefined,
        });
        Toast.success(`Device "${updated.hostname}" updated`);
        if (updated.netbox_synced === false) {
          Toast.warning('Saved locally', updated.netbox_error || 'NetBox sync failed. Check NETBOX_URL and NETBOX_TOKEN.');
        }
        setSelected(updated);
      } else {
        const created = await netboxApi.createDevice({
          hostname: form.hostname,
          management_ip: form.management_ip,
          management_subnet_mask: form.management_subnet_mask,
          vendor: form.vendor,
          model: form.model,
          os_version: form.os_version,
          serial_number: form.serial_number,
          status: form.status,
          role: form.role,
          ssh_user: form.ssh_user,
          ssh_password: form.ssh_password,
          ssh_port: form.ssh_port ? Number(form.ssh_port) : undefined,
        });
        Toast.success(`Device "${created.hostname}" added`, `ID ${created.id}`);
        if (created.netbox_synced === false) {
          Toast.warning('Saved locally', created.netbox_error || 'NetBox sync failed. Check NETBOX_URL and NETBOX_TOKEN.');
        }
      }
      setShowModal(false);
      fetchDevices(true); // Mutation (Create/Edit) -> Update cache
    } catch (e: any) {
      setFormError(e.message || 'Operation failed');
    } finally {
      setIsActionLoading(false);
    }
  };

  const confirmDelete = async (d: DeviceRecord) => {
    const ok = await Confirm.danger(
      `Delete ${d.hostname}?`,
      'This action cannot be undone.',
    );
    if (!ok) return;

    setIsActionLoading(true);
    try {
      await netboxApi.deleteDevice(d.id);
      Toast.success(`Device "${d.hostname}" deleted`);
      if (selected?.id === d.id) setSelected(null);
      fetchDevices(true); // Mutation (Delete) -> Update cache
    } catch (e: any) {
      Toast.error('Delete failed', e.message);
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleSync = async () => {
    setIsSyncing(true);
    try {
      const res = await netboxApi.syncFromNetbox();
      if (res.status === 'success') {
        const ifaceMsg = (res as any).interface_count ? `, ${(res as any).interface_count} interfaces` : '';
        Toast.success(`Synced ${res.synced_count ?? 0} devices${ifaceMsg} from NetBox`);
      } else if (res.status === 'skipped') {
        Toast.warning('Sync skipped', (res as any).reason || 'no connection');
      } else {
        // Legacy 'accepted' or other statuses — treat as success
        Toast.success('Sync completed');
      }
      fetchDevices(true); // Explicit Sync -> Update cache
    } catch (e: any) {
      Toast.error('Sync failed', e.message);
    } finally {
      setIsSyncing(false);
    }
  };

  // -----------------------------------------------------------------------
  //  Interface CRUD handlers
  // -----------------------------------------------------------------------

  const handleAddInterface = () => {
    setIfaceForm({ ...EMPTY_IFACE_FORM });
    setIsEditingIface(false);
    setEditIfaceId(null);
    setIfaceFormError(null);
    setShowIfaceModal(true);
  };

  const handleEditInterface = (iface: InterfaceRecord) => {
    setIfaceForm({
      name: iface.name,
      ip_address: iface.ip_address || '',
      subnet_mask: iface.subnet_mask || '255.255.255.0',
      mac_address: iface.mac_address || '',
      speed: iface.speed || '',
    });
    setIsEditingIface(true);
    setEditIfaceId(iface.id);
    setIfaceFormError(null);
    setShowIfaceModal(true);
  };

  const handleSaveInterface = async () => {
    if (!ifaceForm.name.trim()) {
      setIfaceFormError('Interface name is required');
      return;
    }
    if (!selected) return;
    setIfaceFormError(null);
    try {
      if (isEditingIface && editIfaceId) {
        await netboxApi.updateInterface(selected.id, editIfaceId, {
          name: ifaceForm.name,
          ip_address: ifaceForm.ip_address,
          subnet_mask: ifaceForm.subnet_mask,
          mac_address: ifaceForm.mac_address,
          speed: ifaceForm.speed,
        });
        Toast.success(`Interface "${ifaceForm.name}" updated`);
      } else {
        await netboxApi.createInterface(selected.id, {
          name: ifaceForm.name,
          ip_address: ifaceForm.ip_address,
          subnet_mask: ifaceForm.subnet_mask,
          mac_address: ifaceForm.mac_address,
          speed: ifaceForm.speed,
        });
        Toast.success(`Interface "${ifaceForm.name}" added`);
      }
      setShowIfaceModal(false);
      fetchInterfaces(selected.id, true); // Mutation (Add/Edit Interface) -> Update cache
    } catch (e: any) {
      setIfaceFormError(e.message || `Failed to ${isEditingIface ? 'update' : 'add'} interface`);
    }
  };

  const handleDeleteInterface = async (iface: InterfaceRecord) => {
    if (!selected) return;
    const ok = await Confirm.danger(
      `Delete interface ${iface.name}?`,
      'This will also remove any links using this interface.',
    );
    if (!ok) return;
    try {
      await netboxApi.deleteInterface(selected.id, iface.id);
      Toast.success(`Interface "${iface.name}" deleted`);
      fetchInterfaces(selected.id, true); // Mutation (Delete Interface) -> Update cache
    } catch (e: any) {
      Toast.error('Delete failed', e.message);
    }
  };

  // -----------------------------------------------------------------------
  //  Link CRUD handlers
  // -----------------------------------------------------------------------

  const handleAddLink = () => {
    setLinkForm({ src_device_id: 0, src_interface_id: 0, dst_device_id: 0, dst_interface_id: 0 });
    setLinkFormError(null);
    setSrcInterfaces([]);
    setDstInterfaces([]);
    setShowLinkModal(true);
    fetchLinks(true); // Open modal -> Update cache (as it involves fresh interface loading soon)
  };

  const loadIfacesForLink = async (deviceId: number, side: 'src' | 'dst') => {
    if (!deviceId) return;
    try {
      const res = await netboxApi.listInterfaces(deviceId);
      if (side === 'src') setSrcInterfaces(res.interfaces);
      else setDstInterfaces(res.interfaces);
    } catch { /* ignore */ }
  };

  const handleSaveLink = async () => {
    if (!linkForm.src_device_id || !linkForm.dst_device_id) {
      setLinkFormError('Both source and destination devices are required');
      return;
    }
    setLinkFormError(null);
    try {
      await netboxApi.createLink({
        src_device_id: linkForm.src_device_id,
        src_interface_id: linkForm.src_interface_id || undefined,
        dst_device_id: linkForm.dst_device_id,
        dst_interface_id: linkForm.dst_interface_id || undefined,
        discovery_protocol: 'manual',
      });
      Toast.success('Link created');
      setShowLinkModal(false);
      fetchLinks(true); // Mutation (Create Link) -> Update cache
    } catch (e: any) {
      setLinkFormError(e.message || 'Failed to create link');
    }
  };

  const handleDeleteLink = async (link: LinkRecord) => {
    const ok = await Confirm.danger(
      `Delete link?`,
      `${link.src_hostname} → ${link.dst_hostname}`,
    );
    if (!ok) return;
    try {
      await netboxApi.deleteLink(link.id);
      Toast.success('Link deleted');
      fetchLinks(true); // Mutation (Delete Link) -> Update cache
    } catch (e: any) {
      Toast.error('Delete failed', e.message);
    }
  };

  // -----------------------------------------------------------------------
  //  Render
  // -----------------------------------------------------------------------

  return (
    <div className="p-6 h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between mb-6 shrink-0">
        <div className="flex items-center gap-3">
          <Server className="w-7 h-7 text-primary" />
          <h1 className="text-2xl font-bold">Device Inventory</h1>
          <span className="badge badge-primary badge-outline">{devices.length} devices</span>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-sm btn-outline gap-1" onClick={handleSync} disabled={isSyncing}>
            {isSyncing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            Sync from NetBox
          </button>
            <button className="btn btn-sm btn-outline gap-1" onClick={() => refreshAll(true)} disabled={isGlobalLoading || isActionLoading}>
            <RefreshCw className={`w-4 h-4 ${(isGlobalLoading || isActionLoading) ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button className="btn btn-sm btn-primary gap-1" onClick={handleCreate}>
            <Plus className="w-4 h-4" />
            Add Device
          </button>
        </div>
      </div>

      {/* Search + Filters */}
      <div className="flex flex-wrap gap-3 mb-4 shrink-0">
        <div className="relative grow max-w-sm">
          <Search className="absolute left-3 top-2.5 w-4 h-4 opacity-50" />
          <input
            type="text"
            placeholder="Search hostname, IP, vendor…"
            className="input input-sm input-bordered w-full pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="select select-sm select-bordered"
          value={filterVendor}
          onChange={(e) => setFilterVendor(e.target.value)}
        >
          <option value="">All Vendors</option>
          {VENDOR_OPTIONS.filter((o) => o.value).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          className="select select-sm select-bordered"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">All Statuses</option>
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {/* Main grid: table + detail panel */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 grow overflow-hidden min-h-0">
        {/* ─── Device Table ─── */}
        <div className="lg:col-span-2 bg-base-100 rounded-xl shadow-sm border border-base-300 flex flex-col overflow-hidden">
          <div className="overflow-y-auto grow">
            <table className="table table-sm table-zebra w-full">
              <thead className="sticky top-0 bg-base-200 z-10">
                <tr>
                  <SortHeader label="Hostname" sortKey="hostname" currentKey={sortKey} order={sortOrder} onSort={toggleSort} />
                  <SortHeader label="Role" sortKey="role" currentKey={sortKey} order={sortOrder} onSort={toggleSort} />
                  <SortHeader label="Management IP" sortKey="management_ip" currentKey={sortKey} order={sortOrder} onSort={toggleSort} />
                  <SortHeader label="Vendor" sortKey="vendor" currentKey={sortKey} order={sortOrder} onSort={toggleSort} />
                  <SortHeader label="Status" sortKey="status" currentKey={sortKey} order={sortOrder} onSort={toggleSort} />
                  <SortHeader label="Source" sortKey="source" currentKey={sortKey} order={sortOrder} onSort={toggleSort} />
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {isGlobalLoading && devices.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="text-center py-12 opacity-50">
                      <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
                      Loading devices…
                    </td>
                  </tr>
                ) : sortedDevices.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="text-center py-12 opacity-50">
                      <Server className="w-6 h-6 mx-auto mb-2" />
                      {devices.length === 0
                        ? 'No devices yet. Click "Add Device" or "Sync from NetBox".'
                        : 'No devices match your filters.'}
                    </td>
                  </tr>
                ) : (
                  sortedDevices.map((d) => (
                    <tr
                      key={d.id}
                      className={`cursor-pointer hover:bg-base-200 transition-colors ${
                        selected?.id === d.id ? 'bg-primary/10' : ''
                      }`}
                      onClick={() => setSelected(d)}
                    >
                      <td className="font-mono font-semibold">{d.hostname}</td>
                      <td className="text-xs align-middle whitespace-nowrap">
                        {roleBadge(d.role)}
                      </td>
                      <td className="font-mono text-xs">{d.management_ip || '—'}</td>
                      <td className="text-xs">{vendorLabel(d.vendor)}</td>
                      <td>{statusBadge(d.status)}</td>
                      <td>{sourceBadge(d.source)}</td>
                      <td className="text-right">
                        <div className="flex gap-1 justify-end">
                          <button
                            className="btn btn-ghost btn-xs"
                            title="Edit"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleEdit(d);
                            }}
                          >
                            <Edit3 className="w-3.5 h-3.5" />
                          </button>
                          <button
                            className="btn btn-ghost btn-xs text-error"
                            title="Delete"
                            onClick={(e) => {
                              e.stopPropagation();
                              confirmDelete(d);
                            }}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* ─── Detail Panel ─── */}
        <div className="bg-base-100 rounded-xl shadow-sm border border-base-300 flex flex-col overflow-hidden">
          {selected ? (
            <>
              <div className="p-4 bg-base-200/50 border-b border-base-300 flex items-center justify-between">
                <h2 className="font-bold text-lg truncate">{selected.hostname}</h2>
                <div className="flex gap-1">
                  <button className="btn btn-xs btn-outline gap-1" onClick={() => handleEdit(selected)}>
                    <Edit3 className="w-3 h-3" /> Edit
                  </button>
                  <button
                    className="btn btn-xs btn-outline btn-error gap-1"
                    onClick={() => confirmDelete(selected)}
                  >
                    <Trash2 className="w-3 h-3" /> Delete
                  </button>
                </div>
              </div>

              {/* Tabs */}
              <div className="tabs tabs-bordered px-4 pt-2 shrink-0">
                <button
                  className={`tab tab-sm ${detailTab === 'info' ? 'tab-active' : ''}`}
                  onClick={() => setDetailTab('info')}
                >
                  <Cpu className="w-3.5 h-3.5 mr-1" /> Info
                </button>
                <button
                  className={`tab tab-sm ${detailTab === 'interfaces' ? 'tab-active' : ''}`}
                  onClick={() => setDetailTab('interfaces')}
                >
                  <Network className="w-3.5 h-3.5 mr-1" /> Interfaces ({interfaces.length})
                </button>
                <button
                  className={`tab tab-sm ${detailTab === 'links' ? 'tab-active' : ''}`}
                  onClick={() => { setDetailTab('links'); fetchLinks(); }}
                >
                  <Cable className="w-3.5 h-3.5 mr-1" /> Links
                </button>
              </div>

              <div className="p-4 overflow-y-auto grow">
                {/* Info Tab */}
                {detailTab === 'info' && (
                  <div className="space-y-3">
                    <DetailRow label="ID" value={String(selected.id)} />
                    <DetailRow label="Hostname" value={selected.hostname} mono />
                    <DetailRow label="Management IP" value={selected.management_ip || '—'} mono />
                    <DetailRow label="Subnet Mask" value={selected.management_subnet_mask || '—'} mono />
                    <DetailRow label="Vendor" value={vendorLabel(selected.vendor)} />
                    <DetailRow label="Role" value={ROLE_OPTIONS.find(r => r.value === selected.role)?.label || selected.role} />
                    <DetailRow label="Model" value={selected.model || '—'} />
                    <DetailRow label="OS Version" value={selected.os_version || '—'} />
                    <DetailRow label="Serial Number" value={selected.serial_number || '—'} mono />
                    <DetailRow label="Status">{statusBadge(selected.status)}</DetailRow>
                    <DetailRow label="Source">{sourceBadge(selected.source)}</DetailRow>
                    <div className="divider text-xs opacity-60">SSH Access</div>
                    <DetailRow label="SSH User" value={selected.ssh_user || '—'} mono />
                    <DetailRow label="SSH Port" value={String(selected.ssh_port || 22)} mono />
                    <DetailRow label="Password" value={selected.ssh_user ? '••••••••' : '—'} />
                    <div className="divider text-xs opacity-60">Timestamps</div>
                    <DetailRow label="Last Polled" value={selected.last_polled || 'Never'} />
                    <DetailRow label="Created" value={selected.created_at || '—'} />
                    <DetailRow label="Updated" value={selected.updated_at || '—'} />
                  </div>
                )}

                {/* Interfaces Tab */}
                {detailTab === 'interfaces' && (
                  <div className="space-y-3">
                    <div className="flex justify-between items-center mb-2">
                      <span className="text-sm font-semibold opacity-70">
                        {interfaces.length} interface{interfaces.length !== 1 ? 's' : ''}
                      </span>
                      <button className="btn btn-xs btn-primary gap-1" onClick={handleAddInterface}>
                        <Plus className="w-3 h-3" /> Add Interface
                      </button>
                    </div>

                    {(loadingIfaces && interfaces.length === 0) ? (
                      <div className="text-center py-6 opacity-50">
                        <Loader2 className="w-5 h-5 animate-spin mx-auto mb-1" />
                        Loading…
                      </div>
                    ) : interfaces.length === 0 ? (
                      <div className="text-center py-6 opacity-40">
                        <Network className="w-6 h-6 mx-auto mb-1" />
                        <p className="text-xs">No interfaces defined yet</p>
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="table table-xs w-full">
                          <thead>
                            <tr>
                              <th>Name</th>
                              <th>IP Address</th>
                              <th>Subnet</th>
                              <th></th>
                            </tr>
                          </thead>
                          <tbody>
                            {interfaces.map((iface) => (
                              <tr key={iface.id}>
                                <td className="font-mono text-xs">{iface.name}</td>
                                <td className="font-mono text-xs">{iface.ip_address || '—'}</td>
                                <td className="text-xs">{iface.subnet_mask || '—'}</td>
                                <td className="text-right flex justify-end gap-1">
                                  <button
                                    className="btn btn-ghost btn-xs text-primary"
                                    onClick={() => handleEditInterface(iface)}
                                    title="Edit Interface"
                                  >
                                    <Edit3 className="w-3 h-3" />
                                  </button>
                                  <button
                                    className="btn btn-ghost btn-xs text-error"
                                    onClick={() => handleDeleteInterface(iface)}
                                    title="Delete Interface"
                                  >
                                    <Trash2 className="w-3 h-3" />
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}

                {/* Links Tab */}
                {detailTab === 'links' && (
                  <div className="space-y-3">
                    <div className="flex justify-between items-center mb-2">
                      <span className="text-sm font-semibold opacity-70">Topology Links</span>
                      <button className="btn btn-xs btn-primary gap-1" onClick={handleAddLink}>
                        <Plus className="w-3 h-3" /> Add Link
                      </button>
                    </div>

                    {(isGlobalLoading && links.length === 0) ? (
                      <div className="text-center py-6 opacity-50">
                        <Loader2 className="w-5 h-5 animate-spin mx-auto mb-1" />
                        Loading…
                      </div>
                    ) : links.length === 0 ? (
                      <div className="text-center py-6 opacity-40">
                        <Cable className="w-6 h-6 mx-auto mb-1" />
                        <p className="text-xs">No links defined yet</p>
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="table table-xs w-full">
                          <thead>
                            <tr>
                              <th>Source</th>
                              <th></th>
                              <th>Destination</th>
                              <th>Type</th>
                              <th></th>
                            </tr>
                          </thead>
                          <tbody>
                            {links
                              .filter(l =>
                                l.src_hostname === selected.hostname ||
                                l.dst_hostname === selected.hostname
                              )
                              .map((link) => (
                              <tr key={link.id}>
                                <td className="text-xs">
                                  <div className="font-mono font-semibold">{link.src_hostname}</div>
                                  <div className="opacity-60">{link.src_interface_name || '—'}</div>
                                </td>
                                <td className="text-center opacity-40">→</td>
                                <td className="text-xs">
                                  <div className="font-mono font-semibold">{link.dst_hostname}</div>
                                  <div className="opacity-60">{link.dst_interface_name || '—'}</div>
                                </td>
                                <td>
                                  <span className="badge badge-xs badge-ghost">
                                    {link.discovery_protocol || 'manual'}
                                  </span>
                                </td>
                                <td className="text-right">
                                  <button
                                    className="btn btn-ghost btn-xs text-error"
                                    onClick={() => handleDeleteLink(link)}
                                  >
                                    <Trash2 className="w-3 h-3" />
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full opacity-40 gap-2 p-4">
              <Server className="w-10 h-10" />
              <p className="text-sm">Select a device to view details</p>
            </div>
          )}
        </div>
      </div>

      {/* ─── Add / Edit Device Modal ─── */}
      {showModal && (
        <div className="modal modal-open">
          <div className="modal-box max-w-lg">
            <h3 className="font-bold text-lg mb-4">
              {isEditing ? 'Edit Device' : 'Add New Device'}
            </h3>

            {formError && (
              <div className="alert alert-error alert-sm mb-3">
                <AlertTriangle className="w-4 h-4" />
                <span>{formError}</span>
              </div>
            )}

            <div className="space-y-3">
              <label className="form-control w-full">
                <div className="label"><span className="label-text">Hostname *</span></div>
                <input type="text" className="input input-bordered w-full" placeholder="e.g. R1"
                  value={form.hostname} onChange={(e) => setForm({ ...form, hostname: e.target.value })} />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Management IP</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="e.g. 192.168.1.1"
                    value={form.management_ip} onChange={(e) => setForm({ ...form, management_ip: e.target.value })} />
                </label>
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Subnet Mask</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="255.255.255.0"
                    value={form.management_subnet_mask} onChange={(e) => setForm({ ...form, management_subnet_mask: e.target.value })} />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Vendor</span></div>
                  <select className="select select-bordered w-full" value={form.vendor}
                    onChange={(e) => setForm({ ...form, vendor: e.target.value })}>
                    {VENDOR_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
                  </select>
                </label>
                <div className="flex gap-3 w-full">
                  {/* Status is now handled automatically as 'registered' for manual entries */}
                  <label className="form-control w-full">
                    <div className="label"><span className="label-text">Role</span></div>
                    <select className="select select-bordered w-full" value={form.role}
                      onChange={(e) => setForm({ ...form, role: e.target.value })}>
                      {ROLE_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
                    </select>
                  </label>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Model</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="e.g. ISR4331"
                    value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
                </label>
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">OS Version</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="e.g. 17.3.4"
                    value={form.os_version} onChange={(e) => setForm({ ...form, os_version: e.target.value })} />
                </label>
              </div>

              <label className="form-control w-full">
                <div className="label"><span className="label-text">Serial Number</span></div>
                <input type="text" className="input input-bordered w-full" placeholder="e.g. FTX1234ABCD"
                  value={form.serial_number} onChange={(e) => setForm({ ...form, serial_number: e.target.value })} />
              </label>

              <div className="divider text-xs opacity-60 my-1">SSH Credentials</div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">SSH Username</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="admin"
                    value={form.ssh_user} onChange={(e) => setForm({ ...form, ssh_user: e.target.value })} />
                </label>
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">SSH Port</span></div>
                  <input type="number" className="input input-bordered w-full" placeholder="22"
                    value={form.ssh_port} onChange={(e) => setForm({ ...form, ssh_port: e.target.value })} />
                </label>
              </div>
              <label className="form-control w-full">
                <div className="label">
                  <span className="label-text">SSH Password</span>
                  <span className="label-text-alt opacity-50">{isEditing ? 'leave blank to keep existing' : ''}</span>
                </div>
                <input type="password" className="input input-bordered w-full" placeholder="••••••••"
                  value={form.ssh_password} onChange={(e) => setForm({ ...form, ssh_password: e.target.value })} />
              </label>
            </div>

            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowModal(false)}>Cancel</button>
              <button className="btn btn-primary gap-1" onClick={handleSubmit} disabled={isActionLoading}>
                {isActionLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                {isEditing ? 'Save Changes' : 'Add Device'}
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setShowModal(false)} />
        </div>
      )}

      {/* ─── Add Interface Modal ─── */}
      {showIfaceModal && (
        <div className="modal modal-open">
          <div className="modal-box max-w-lg">
            <h3 className="font-bold text-lg mb-4">{isEditingIface ? 'Edit' : 'Add'} Interface</h3>

            {ifaceFormError && (
              <div className="alert alert-error alert-sm mb-3">
                <AlertTriangle className="w-4 h-4" />
                <span>{ifaceFormError}</span>
              </div>
            )}

            <div className="space-y-3">
              <label className="form-control w-full">
                <div className="label"><span className="label-text">Interface Name *</span></div>
                <input type="text" className="input input-bordered w-full"
                  placeholder="e.g. GigabitEthernet0/0/0"
                  value={ifaceForm.name}
                  onChange={(e) => setIfaceForm({ ...ifaceForm, name: e.target.value })} />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">IP Address</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="10.0.1.1"
                    value={ifaceForm.ip_address}
                    onChange={(e) => setIfaceForm({ ...ifaceForm, ip_address: e.target.value })} />
                </label>
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Subnet Mask</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="255.255.255.252"
                    value={ifaceForm.subnet_mask}
                    onChange={(e) => setIfaceForm({ ...ifaceForm, subnet_mask: e.target.value })} />
                </label>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">MAC Address</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="00:11:22:33:44:55"
                    value={ifaceForm.mac_address}
                    onChange={(e) => setIfaceForm({ ...ifaceForm, mac_address: e.target.value })} />
                </label>
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Speed</span></div>
                  <input type="text" className="input input-bordered w-full" placeholder="1Gbps"
                    value={ifaceForm.speed}
                    onChange={(e) => setIfaceForm({ ...ifaceForm, speed: e.target.value })} />
                </label>
              </div>
            </div>

            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowIfaceModal(false)}>Cancel</button>
              <button className="btn btn-primary gap-1" onClick={handleSaveInterface}>
                {isEditingIface ? <Edit3 className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                {isEditingIface ? 'Update' : 'Add'} Interface
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setShowIfaceModal(false)} />
        </div>
      )}

      {/* ─── Add Link Modal ─── */}
      {showLinkModal && (
        <div className="modal modal-open">
          <div className="modal-box max-w-lg">
            <h3 className="font-bold text-lg mb-4">Add Link (Neighbor Connection)</h3>

            {linkFormError && (
              <div className="alert alert-error alert-sm mb-3">
                <AlertTriangle className="w-4 h-4" />
                <span>{linkFormError}</span>
              </div>
            )}

            <div className="space-y-4">
              {/* Source side */}
              <div className="p-3 bg-base-200 rounded-lg space-y-2">
                <p className="text-xs font-semibold opacity-60">Source Device</p>
                <select
                  className="select select-bordered select-sm w-full"
                  value={linkForm.src_device_id}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    setLinkForm({ ...linkForm, src_device_id: id, src_interface_id: 0 });
                    loadIfacesForLink(id, 'src');
                  }}
                >
                  <option value={0}>Select device…</option>
                  {devices.map(d => (
                    <option key={d.id} value={d.id}>{d.hostname} ({d.management_ip || 'no IP'})</option>
                  ))}
                </select>
                <select
                  className="select select-bordered select-sm w-full"
                  value={linkForm.src_interface_id}
                  onChange={(e) => setLinkForm({ ...linkForm, src_interface_id: Number(e.target.value) })}
                  disabled={!linkForm.src_device_id}
                >
                  <option value={0}>Select interface (optional)…</option>
                  {srcInterfaces.filter(i => i.name !== 'Management').map(i => (
                    <option key={i.id} value={i.id}>{i.name} {i.ip_address ? `(${i.ip_address})` : ''}</option>
                  ))}
                </select>
              </div>

              <div className="text-center opacity-40 text-sm">↕ connected to ↕</div>

              {/* Destination side */}
              <div className="p-3 bg-base-200 rounded-lg space-y-2">
                <p className="text-xs font-semibold opacity-60">Destination Device</p>
                <select
                  className="select select-bordered select-sm w-full"
                  value={linkForm.dst_device_id}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    setLinkForm({ ...linkForm, dst_device_id: id, dst_interface_id: 0 });
                    loadIfacesForLink(id, 'dst');
                  }}
                >
                  <option value={0}>Select device…</option>
                  {devices.map(d => (
                    <option key={d.id} value={d.id}>{d.hostname} ({d.management_ip || 'no IP'})</option>
                  ))}
                </select>
                <select
                  className="select select-bordered select-sm w-full"
                  value={linkForm.dst_interface_id}
                  onChange={(e) => setLinkForm({ ...linkForm, dst_interface_id: Number(e.target.value) })}
                  disabled={!linkForm.dst_device_id}
                >
                  <option value={0}>Select interface (optional)…</option>
                  {dstInterfaces.filter(i => i.name !== 'Management').map(i => (
                    <option key={i.id} value={i.id}>{i.name} {i.ip_address ? `(${i.ip_address})` : ''}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowLinkModal(false)}>Cancel</button>
              <button className="btn btn-primary gap-1" onClick={handleSaveLink}>
                <Cable className="w-4 h-4" /> Create Link
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setShowLinkModal(false)} />
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
//  Detail row sub-component
// ---------------------------------------------------------------------------

const DetailRow: React.FC<{
  label: string;
  value?: string;
  mono?: boolean;
  children?: React.ReactNode;
}> = ({ label, value, mono, children }) => (
  <div className="flex justify-between items-center text-sm">
    <span className="opacity-60">{label}</span>
    {children || <span className={mono ? 'font-mono' : ''}>{value}</span>}
  </div>
);

export default NetBoxDevices;
