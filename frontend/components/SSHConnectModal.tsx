import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Terminal, Loader2, Save } from 'lucide-react';

interface SSHConnectModalProps {
  /** Pre-filled hostname (from the context menu target) */
  hostname: string;
  /** Pre-filled host/IP */
  host: string;
  /** Whether the modal is open */
  isOpen: boolean;
  /** Called when user submits credentials */
  onConnect: (username: string, password: string, port: number, saveToVault: boolean) => Promise<void>;
  /** Called when user cancels */
  onClose: () => void;
}

const SSHConnectModal: React.FC<SSHConnectModalProps> = ({
  hostname,
  host,
  isOpen,
  onConnect,
  onClose,
}) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [port, setPort] = useState(22);
  const [saveToVault, setSaveToVault] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const usernameRef = useRef<HTMLInputElement>(null);

  // Focus username field when modal opens
  useEffect(() => {
    if (isOpen) {
      setUsername('');
      setPassword('');
      setPort(22);
      setSaveToVault(false);
      setError('');
      setLoading(false);
      setTimeout(() => usernameRef.current?.focus(), 100);
    }
  }, [isOpen]);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) {
      setError('Username is required');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await onConnect(username.trim(), password, port, saveToVault);
    } catch (err: any) {
      setError(err.message || 'Connection failed');
      setLoading(false);
    }
  }, [username, password, port, saveToVault, onConnect]);

  if (!isOpen) return null;

  return (
    <dialog className="modal modal-open" onClick={onClose}>
      <div className="modal-box max-w-sm" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-bold text-lg flex items-center gap-2">
          <Terminal size={20} className="text-primary" />
          SSH Connect
        </h3>
        <p className="text-sm text-base-content/60 mt-1">
          Connect to <span className="font-mono font-semibold">{hostname}</span>
          {host !== hostname && (
            <span className="text-base-content/40"> ({host})</span>
          )}
        </p>

        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          <div className="form-control">
            <label className="label py-1">
              <span className="label-text text-xs">Username</span>
            </label>
            <input
              ref={usernameRef}
              type="text"
              className="input input-bordered input-sm w-full"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin"
              autoComplete="username"
              disabled={loading}
            />
          </div>

          <div className="form-control">
            <label className="label py-1">
              <span className="label-text text-xs">Password</span>
            </label>
            <input
              type="password"
              className="input input-bordered input-sm w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
              disabled={loading}
            />
          </div>

          <div className="form-control">
            <label className="label py-1">
              <span className="label-text text-xs">Port</span>
            </label>
            <input
              type="number"
              className="input input-bordered input-sm w-20"
              value={port}
              onChange={(e) => setPort(Number(e.target.value))}
              min={1}
              max={65535}
              disabled={loading}
            />
          </div>

          <div className="form-control">
            <label className="label cursor-pointer gap-2 justify-start py-1">
              <input
                type="checkbox"
                className="checkbox checkbox-sm checkbox-primary"
                checked={saveToVault}
                onChange={(e) => setSaveToVault(e.target.checked)}
                disabled={loading}
              />
              <span className="label-text text-xs flex items-center gap-1">
                <Save size={12} />
                Save credentials to vault
              </span>
            </label>
          </div>

          {error && (
            <div className="text-error text-sm">{error}</div>
          )}

          <div className="modal-action mt-4">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary btn-sm gap-1"
              disabled={loading}
            >
              {loading ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  Connecting...
                </>
              ) : (
                <>
                  <Terminal size={14} />
                  Connect
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </dialog>
  );
};

export default SSHConnectModal;
