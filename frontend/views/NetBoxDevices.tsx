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
} from 'lucide-react';
import { netboxApi, type DeviceRecord } from '../src/services/api';
import { Toast, Confirm } from '../src/utils/swal';

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
  { value: 'active', label: 'Active' },
  { value: 'down', label: 'Down' },
  { value: 'unknown', label: 'Unknown' },
];

const vendorLabel = (v: string | null) => {
  if (!v) return '—';
  const found = VENDOR_OPTIONS.find((o) => o.value === v);
  return found ? found.label : v;
};

const statusBadge = (status: string) => {
  switch (status) {
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

// ---------------------------------------------------------------------------
//  Empty form
// ---------------------------------------------------------------------------
const EMPTY_FORM = {
  hostname: '',
  management_ip: '',
  vendor: '',
  model: '',
  os_version: '',
  serial_number: '',
  status: 'unknown',
  ssh_user: '',
  ssh_password: '',
  ssh_port: '22',
};

// ---------------------------------------------------------------------------
//  Component
// ---------------------------------------------------------------------------

const NetBoxDevices: React.FC = () => {
  // Data
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [selected, setSelected] = useState<DeviceRecord | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  // Search / filter
  const [search, setSearch] = useState('');
  const [filterVendor, setFilterVendor] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [formError, setFormError] = useState<string | null>(null);

  // -----------------------------------------------------------------------
  //  Data fetching
  // -----------------------------------------------------------------------

  const fetchDevices = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await netboxApi.listDevices();
      setDevices(res.devices);
    } catch (e: any) {
      Toast.error('Failed to load devices', e.message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDevices();
  }, [fetchDevices]);

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

  // -----------------------------------------------------------------------
  //  CRUD handlers
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
      vendor: d.vendor || '',
      model: d.model || '',
      os_version: d.os_version || '',
      serial_number: d.serial_number || '',
      status: d.status || 'unknown',
      ssh_user: (d as any).ssh_user || '',
      ssh_password: '',
      ssh_port: String((d as any).ssh_port || 22),
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
    setIsLoading(true);

    try {
      if (isEditing && editId !== null) {
        const updated = await netboxApi.updateDevice(editId, {
          hostname: form.hostname,
          management_ip: form.management_ip || undefined,
          vendor: form.vendor || undefined,
          model: form.model || undefined,
          os_version: form.os_version || undefined,
          serial_number: form.serial_number || undefined,
          status: form.status,
          ssh_user: form.ssh_user || undefined,
          ssh_password: form.ssh_password || undefined,
          ssh_port: form.ssh_port ? Number(form.ssh_port) : undefined,
        });
        Toast.success(`Device "${updated.hostname}" updated`);
        setSelected(updated);
      } else {
        const created = await netboxApi.createDevice({
          hostname: form.hostname,
          management_ip: form.management_ip || undefined,
          vendor: form.vendor || undefined,
          model: form.model || undefined,
          os_version: form.os_version || undefined,
          serial_number: form.serial_number || undefined,
          status: form.status,
          ssh_user: form.ssh_user || undefined,
          ssh_password: form.ssh_password || undefined,
          ssh_port: form.ssh_port ? Number(form.ssh_port) : undefined,
        });
        Toast.success(`Device "${created.hostname}" added`, `ID ${created.id}`);
      }
      setShowModal(false);
      fetchDevices();
    } catch (e: any) {
      setFormError(e.message || 'Operation failed');
    } finally {
      setIsLoading(false);
    }
  };

  const confirmDelete = async (d: DeviceRecord) => {
    const ok = await Confirm.danger(
      `Delete ${d.hostname}?`,
      'This action cannot be undone.',
    );
    if (!ok) return;

    setIsLoading(true);
    try {
      await netboxApi.deleteDevice(d.id);
      Toast.success(`Device "${d.hostname}" deleted`);
      if (selected?.id === d.id) setSelected(null);
      fetchDevices();
    } catch (e: any) {
      Toast.error('Delete failed', e.message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSync = async () => {
    setIsSyncing(true);
    try {
      const res = await netboxApi.syncFromNetbox();
      if (res.status === 'success') {
        Toast.success(`Synced ${res.synced_count ?? 0} devices from NetBox`);
      } else {
        Toast.warning('Sync skipped', (res as any).reason || 'no connection');
      }
      fetchDevices();
    } catch (e: any) {
      Toast.error('Sync failed', e.message);
    } finally {
      setIsSyncing(false);
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
          <button className="btn btn-sm btn-outline gap-1" onClick={fetchDevices} disabled={isLoading}>
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
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
                  <th>Hostname</th>
                  <th>Management IP</th>
                  <th>Vendor</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {isLoading && devices.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="text-center py-12 opacity-50">
                      <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
                      Loading devices…
                    </td>
                  </tr>
                ) : filtered.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="text-center py-12 opacity-50">
                      <Server className="w-6 h-6 mx-auto mb-2" />
                      {devices.length === 0
                        ? 'No devices yet. Click "Add Device" or "Sync from NetBox".'
                        : 'No devices match your filters.'}
                    </td>
                  </tr>
                ) : (
                  filtered.map((d) => (
                    <tr
                      key={d.id}
                      className={`cursor-pointer hover:bg-base-200 transition-colors ${
                        selected?.id === d.id ? 'bg-primary/10' : ''
                      }`}
                      onClick={() => setSelected(d)}
                    >
                      <td className="font-mono font-semibold">{d.hostname}</td>
                      <td className="font-mono text-xs">{d.management_ip || '—'}</td>
                      <td className="text-xs">{vendorLabel(d.vendor)}</td>
                      <td className="text-xs">{d.model || '—'}</td>
                      <td>{statusBadge(d.status)}</td>
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
              <div className="p-4 overflow-y-auto grow space-y-3">
                <DetailRow label="ID" value={String(selected.id)} />
                <DetailRow label="Hostname" value={selected.hostname} mono />
                <DetailRow label="Management IP" value={selected.management_ip || '—'} mono />
                <DetailRow label="Vendor" value={vendorLabel(selected.vendor)} />
                <DetailRow label="Model" value={selected.model || '—'} />
                <DetailRow label="OS Version" value={selected.os_version || '—'} />
                <DetailRow label="Serial Number" value={selected.serial_number || '—'} mono />
                <DetailRow label="Status">
                  {statusBadge(selected.status)}
                </DetailRow>
                <div className="divider text-xs opacity-60">SSH Access</div>
                <DetailRow label="SSH User" value={(selected as any).ssh_user || '—'} mono />
                <DetailRow label="SSH Port" value={String((selected as any).ssh_port || 22)} mono />
                <DetailRow label="Password" value={(selected as any).ssh_user ? '••••••••' : '—'} />
                <div className="divider text-xs opacity-60">Timestamps</div>
                <DetailRow label="Last Polled" value={selected.last_polled || 'Never'} />
                <DetailRow label="Created" value={selected.created_at || '—'} />
                <DetailRow label="Updated" value={selected.updated_at || '—'} />
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

      {/* ─── Add / Edit Modal ─── */}
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
              {/* Hostname */}
              <label className="form-control w-full">
                <div className="label"><span className="label-text">Hostname *</span></div>
                <input
                  type="text"
                  className="input input-bordered w-full"
                  placeholder="e.g. R1"
                  value={form.hostname}
                  onChange={(e) => setForm({ ...form, hostname: e.target.value })}
                />
              </label>

              {/* Management IP */}
              <label className="form-control w-full">
                <div className="label"><span className="label-text">Management IP</span></div>
                <input
                  type="text"
                  className="input input-bordered w-full"
                  placeholder="e.g. 192.168.1.1"
                  value={form.management_ip}
                  onChange={(e) => setForm({ ...form, management_ip: e.target.value })}
                />
              </label>

              {/* Vendor + Status row */}
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Vendor</span></div>
                  <select
                    className="select select-bordered w-full"
                    value={form.vendor}
                    onChange={(e) => setForm({ ...form, vendor: e.target.value })}
                  >
                    {VENDOR_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Status</span></div>
                  <select
                    className="select select-bordered w-full"
                    value={form.status}
                    onChange={(e) => setForm({ ...form, status: e.target.value })}
                  >
                    {STATUS_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {/* Model + OS Version */}
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Model</span></div>
                  <input
                    type="text"
                    className="input input-bordered w-full"
                    placeholder="e.g. ISR4331"
                    value={form.model}
                    onChange={(e) => setForm({ ...form, model: e.target.value })}
                  />
                </label>

                <label className="form-control w-full">
                  <div className="label"><span className="label-text">OS Version</span></div>
                  <input
                    type="text"
                    className="input input-bordered w-full"
                    placeholder="e.g. 17.3.4"
                    value={form.os_version}
                    onChange={(e) => setForm({ ...form, os_version: e.target.value })}
                  />
                </label>
              </div>

              {/* Serial Number */}
              <label className="form-control w-full">
                <div className="label"><span className="label-text">Serial Number</span></div>
                <input
                  type="text"
                  className="input input-bordered w-full"
                  placeholder="e.g. FTX1234ABCD"
                  value={form.serial_number}
                  onChange={(e) => setForm({ ...form, serial_number: e.target.value })}
                />
              </label>

              {/* SSH Credentials */}
              <div className="divider text-xs opacity-60 my-1">SSH Credentials</div>
              <div className="grid grid-cols-2 gap-3">
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">SSH Username</span></div>
                  <input
                    type="text"
                    className="input input-bordered w-full"
                    placeholder="admin"
                    value={form.ssh_user}
                    onChange={(e) => setForm({ ...form, ssh_user: e.target.value })}
                  />
                </label>
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">SSH Port</span></div>
                  <input
                    type="number"
                    className="input input-bordered w-full"
                    placeholder="22"
                    value={form.ssh_port}
                    onChange={(e) => setForm({ ...form, ssh_port: e.target.value })}
                  />
                </label>
              </div>
              <label className="form-control w-full">
                <div className="label">
                  <span className="label-text">SSH Password</span>
                  <span className="label-text-alt opacity-50">{isEditing ? 'leave blank to keep existing' : ''}</span>
                </div>
                <input
                  type="password"
                  className="input input-bordered w-full"
                  placeholder="••••••••"
                  value={form.ssh_password}
                  onChange={(e) => setForm({ ...form, ssh_password: e.target.value })}
                />
              </label>
            </div>

            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button className="btn btn-primary gap-1" onClick={handleSubmit} disabled={isLoading}>
                {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                {isEditing ? 'Save Changes' : 'Add Device'}
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setShowModal(false)} />
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
