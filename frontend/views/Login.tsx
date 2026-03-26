import React, { useMemo, useState } from 'react';
import { Lock, User as UserIcon, UserPlus } from 'lucide-react';

import type { User } from '../types';
import { normalizeUserRole } from '../types';
import { authApi } from '../src/services/api';
import { LoginToast, Toast } from '../src/utils/swal';

interface LoginProps {
  onLogin: (user: User) => void;
}

const Login: React.FC<LoginProps> = ({ onLogin }) => {
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const subtitle = useMemo(
    () => (mode === 'signin' ? 'Sign in to continue' : 'Create an Engineer account'),
    [mode],
  );

  const finishLogin = (response: { access_token: string; user: { id: number; username: string; role: string; lastLogin: string } }) => {
    localStorage.setItem('auth_token', response.access_token);
    LoginToast.welcome(response.user.username);
    onLogin({
      id: response.user.id,
      username: response.user.username,
      role: normalizeUserRole(response.user.role) as User['role'],
      lastLogin: response.user.lastLogin || new Date().toISOString(),
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === 'signup' && password !== confirmPassword) {
      Toast.error('Signup Failed', 'Passwords do not match');
      return;
    }

    setLoading(true);
    try {
      const response = mode === 'signin'
        ? await authApi.login(username, password)
        : await authApi.signup(username, password);
      finishLogin(response);
    } catch (err: any) {
      Toast.error(mode === 'signin' ? 'Login Failed' : 'Signup Failed', err.message || 'Request failed');
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center h-full w-full bg-base-300 relative overflow-hidden">
      <div className="absolute top-0 left-0 w-full h-full opacity-10 pointer-events-none">
        <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-primary rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-secondary rounded-full blur-[120px]" />
      </div>

      <div className="card w-[28rem] bg-base-100/90 backdrop-blur shadow-2xl z-10">
        <div className="card-body">
          <div className="flex justify-center mb-2">
            <div className="p-2 bg-white rounded-3xl shadow-lg flex items-center justify-center">
              <img src="/logo.png" alt="CogniShield" className="w-16 h-16 object-contain" />
            </div>
          </div>
          <h2 className="card-title justify-center text-2xl font-bold mb-1">CogniShield</h2>
          <p className="text-center text-xs text-base-content/60 mb-2 uppercase tracking-wider">
            LLM-Powered Campus Network Automation
          </p>
          <p className="text-center text-sm text-base-content/70 mb-6">{subtitle}</p>

          <div className="tabs tabs-boxed self-center mb-4">
            <button
              type="button"
              className={`tab ${mode === 'signin' ? 'tab-active' : ''}`}
              onClick={() => {
                setMode('signin');
                setConfirmPassword('');
              }}
            >
              Sign In
            </button>
            <button
              type="button"
              className={`tab ${mode === 'signup' ? 'tab-active' : ''}`}
              onClick={() => {
                setMode('signup');
                if (!username || username === 'admin') setUsername('');
              }}
            >
              Engineer Sign Up
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="form-control">
              <label className="label">
                <span className="label-text font-medium">Username</span>
              </label>
              <label className="input input-bordered flex items-center gap-2">
                <UserIcon className="w-4 h-4 opacity-70" />
                <input
                  type="text"
                  className="grow"
                  placeholder={mode === 'signin' ? 'admin' : 'engineer01'}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
            </div>

            <div className="form-control">
              <label className="label">
                <span className="label-text font-medium">Password</span>
              </label>
              <label className="input input-bordered flex items-center gap-2">
                <Lock className="w-4 h-4 opacity-70" />
                <input
                  type="password"
                  className="grow"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
            </div>

            {mode === 'signup' && (
              <div className="form-control">
                <label className="label">
                  <span className="label-text font-medium">Confirm Password</span>
                </label>
                <label className="input input-bordered flex items-center gap-2">
                  <UserPlus className="w-4 h-4 opacity-70" />
                  <input
                    type="password"
                    className="grow"
                    placeholder="Repeat password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                  />
                </label>
              </div>
            )}

            <div className="form-control mt-6">
              <button type="submit" className="btn btn-primary" disabled={loading}>
                {loading ? (
                  <span className="loading loading-spinner" />
                ) : mode === 'signin' ? (
                  'Sign In'
                ) : (
                  'Create Engineer Account'
                )}
              </button>
            </div>
          </form>

          <div className="divider text-xs opacity-50">Default Accounts</div>
          <div className="text-xs text-center text-base-content/60 bg-base-200 p-3 rounded space-y-1">
            <span className="block font-mono">admin / admin</span>
            <span className="block font-mono">engineer / engineer</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
