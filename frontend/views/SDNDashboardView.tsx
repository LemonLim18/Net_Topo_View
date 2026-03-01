/**
 * Phase 3 — SDN Controller Dashboard.
 *
 * Shows all registered controllers in a card grid, allows CRUD +
 * live sync.  Each controller card shows status, managed device count,
 * last sync time, and quick-action buttons.
 */
import React, { useEffect, useState, useCallback } from 'react';
import {
  Server,
  RefreshCw,
  Trash2,
  Plus,
  Wifi,
  WifiOff,
  Edit,
  Network,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { sdnApi, type SDNControllerDetail } from '../src/services/api';
import type { ViewId } from '../types';

interface Props {
  setView: (v: ViewId) => void;
}

export default function SDNDashboardView({ setView }: Props) {
  const [controllers, setControllers] = useState<SDNControllerDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const [topoData, setTopoData] = useState<Record<number, { devices: any[]; links: any[] }>>({});

  // Form state
  const [form, setForm] = useState({
    name: '',
    controller_type: 'opendaylight',
    url: '',
    username: '',
    password: '',
    verify_ssl: false,
  });

  const fetchControllers = useCallback(async () => {
    try {
      const data = await sdnApi.list();
      setControllers(data);
    } catch (e: any) {
      console.error('Failed to load SDN controllers:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchControllers();
  }, [fetchControllers]);

  const handleCreate = async () => {
    try {
      await sdnApi.create(form);
      setShowAdd(false);
      setForm({ name: '', controller_type: 'opendaylight', url: '', username: '', password: '', verify_ssl: false });
      fetchControllers();
    } catch (e: any) {
      alert(e.message || 'Failed to create controller');
    }
  };

  const handleUpdate = async () => {
    if (editId === null) return;
    try {
      await sdnApi.update(editId, {
        name: form.name || undefined,
        url: form.url || undefined,
        username: form.username || undefined,
        password: form.password || undefined,
        verify_ssl: form.verify_ssl,
      });
      setEditId(null);
      fetchControllers();
    } catch (e: any) {
      alert(e.message || 'Failed to update controller');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this SDN controller?')) return;
    try {
      await sdnApi.delete(id);
      fetchControllers();
    } catch (e: any) {
      alert(e.message || 'Failed to delete');
    }
  };

  const handleSync = async (id: number) => {
    setSyncingId(id);
    try {
      const result = await sdnApi.sync(id);
      setTopoData((prev) => ({
        ...prev,
        [id]: { devices: result.devices, links: result.links },
      }));
      fetchControllers();
    } catch (e: any) {
      alert(e.message || 'Sync failed');
    } finally {
      setSyncingId(null);
    }
  };

  const startEdit = (c: SDNControllerDetail) => {
    setForm({
      name: c.name,
      controller_type: c.controller_type,
      url: c.url,
      username: c.username || '',
      password: '',
      verify_ssl: c.verify_ssl,
    });
    setEditId(c.id);
    setShowAdd(false);
  };

  const statusBadge = (s: string) => {
    const cls =
      s === 'online'
        ? 'badge-success'
        : s === 'error'
        ? 'badge-error'
        : 'badge-warning';
    return <span className={`badge ${cls} badge-sm gap-1`}>{s === 'online' ? <Wifi size={12} /> : <WifiOff size={12} />}{s}</span>;
  };

  const toggleExpand = async (id: number) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (!topoData[id]) {
      try {
        const topo = await sdnApi.topology(id);
        setTopoData((prev) => ({
          ...prev,
          [id]: { devices: topo.devices, links: topo.links },
        }));
      } catch {
        // silently ignore
      }
    }
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Network size={28} /> SDN Controller Dashboard
          </h1>
          <p className="text-base-content/60 text-sm mt-1">
            Register, monitor, and sync SDN controllers (OpenDaylight / ONOS)
          </p>
        </div>
        <button
          className="btn btn-primary btn-sm gap-1"
          onClick={() => {
            setShowAdd(!showAdd);
            setEditId(null);
            setForm({ name: '', controller_type: 'opendaylight', url: '', username: '', password: '', verify_ssl: false });
          }}
        >
          <Plus size={16} /> Add Controller
        </button>
      </div>

      {/* Add / Edit Form */}
      {(showAdd || editId !== null) && (
        <div className="card bg-base-200 shadow">
          <div className="card-body">
            <h3 className="card-title text-sm">{editId !== null ? 'Edit Controller' : 'Register New Controller'}</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <input
                className="input input-bordered input-sm"
                placeholder="Name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
              <select
                className="select select-bordered select-sm"
                value={form.controller_type}
                onChange={(e) => setForm({ ...form, controller_type: e.target.value })}
                disabled={editId !== null}
              >
                <option value="opendaylight">OpenDaylight</option>
                <option value="onos">ONOS</option>
              </select>
              <input
                className="input input-bordered input-sm"
                placeholder="URL (e.g. http://192.168.1.100:8181)"
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
              />
              <input
                className="input input-bordered input-sm"
                placeholder="Username"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
              />
              <input
                className="input input-bordered input-sm"
                type="password"
                placeholder="Password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="checkbox checkbox-sm"
                  checked={form.verify_ssl}
                  onChange={(e) => setForm({ ...form, verify_ssl: e.target.checked })}
                />
                Verify SSL
              </label>
            </div>
            <div className="flex gap-2 mt-3">
              <button
                className="btn btn-primary btn-sm"
                onClick={editId !== null ? handleUpdate : handleCreate}
                disabled={!form.name || !form.url}
              >
                {editId !== null ? 'Save Changes' : 'Register'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => { setShowAdd(false); setEditId(null); }}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Controller Cards */}
      {loading ? (
        <div className="flex justify-center py-20">
          <span className="loading loading-spinner loading-lg" />
        </div>
      ) : controllers.length === 0 ? (
        <div className="text-center py-20 text-base-content/50">
          <Server size={48} className="mx-auto mb-4 opacity-30" />
          <p>No SDN controllers registered yet.</p>
          <p className="text-sm">Click "Add Controller" to get started.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {controllers.map((c) => (
            <div key={c.id} className="card bg-base-100 shadow border border-base-300">
              <div className="card-body p-4 space-y-2">
                {/* Name + Status */}
                <div className="flex items-center justify-between">
                  <h3 className="font-bold text-sm flex items-center gap-2">
                    <Server size={16} className="text-primary" />
                    {c.name}
                  </h3>
                  {statusBadge(c.status)}
                </div>

                {/* Meta */}
                <div className="text-xs text-base-content/60 space-y-0.5">
                  <p><span className="font-medium">Type:</span> {c.controller_type}</p>
                  <p><span className="font-medium">URL:</span> {c.url}</p>
                  <p><span className="font-medium">Devices:</span> {c.managed_device_count}</p>
                  {c.last_synced && (
                    <p><span className="font-medium">Last Sync:</span> {new Date(c.last_synced).toLocaleString()}</p>
                  )}
                </div>

                {/* Actions */}
                <div className="flex gap-1 pt-1">
                  <button
                    className="btn btn-xs btn-outline btn-primary gap-1"
                    onClick={() => handleSync(c.id)}
                    disabled={syncingId === c.id}
                  >
                    <RefreshCw size={12} className={syncingId === c.id ? 'animate-spin' : ''} /> Sync
                  </button>
                  <button className="btn btn-xs btn-outline gap-1" onClick={() => startEdit(c)}>
                    <Edit size={12} /> Edit
                  </button>
                  <button className="btn btn-xs btn-outline btn-error gap-1" onClick={() => handleDelete(c.id)}>
                    <Trash2 size={12} />
                  </button>
                  <button className="btn btn-xs btn-ghost gap-1" onClick={() => toggleExpand(c.id)}>
                    {expandedId === c.id ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                    Topology
                  </button>
                </div>

                {/* Expanded topology */}
                {expandedId === c.id && topoData[c.id] && (
                  <div className="mt-2 p-2 bg-base-200 rounded text-xs max-h-60 overflow-y-auto">
                    <p className="font-bold mb-1">
                      Managed Devices ({topoData[c.id].devices.length})
                    </p>
                    {topoData[c.id].devices.length > 0 ? (
                      <table className="table table-xs w-full">
                        <thead>
                          <tr>
                            <th>ID</th>
                            <th>IP/Host</th>
                            <th>Info</th>
                          </tr>
                        </thead>
                        <tbody>
                          {topoData[c.id].devices.slice(0, 20).map((d, i) => (
                            <tr key={i}>
                              <td>{d.id || d.hostname || i}</td>
                              <td>{d.ip || d.management_ip || '—'}</td>
                              <td>{d.vendor || d.type || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <p className="text-base-content/50">No managed devices found.</p>
                    )}
                    <p className="font-bold mt-2 mb-1">
                      Links ({topoData[c.id].links.length})
                    </p>
                    {topoData[c.id].links.length > 0 ? (
                      <ul className="list-disc list-inside">
                        {topoData[c.id].links.slice(0, 10).map((l, i) => (
                          <li key={i}>
                            {l.src || l.source_node || '?'} → {l.dst || l.dest_node || '?'}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-base-content/50">No topology links found.</p>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
