import React, { useMemo, useState } from 'react';
import { Lock, User as UserIcon, UserPlus, ShieldCheck, Sparkles } from 'lucide-react';

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
    <div className="relative h-full w-full overflow-hidden bg-base-300">
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute -top-32 -left-24 h-80 w-80 rounded-full bg-primary/20 blur-3xl" />
        <div className="absolute bottom-0 right-0 h-96 w-96 rounded-full bg-secondary/20 blur-3xl" />
        <div className="absolute top-1/3 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full bg-accent/15 blur-3xl" />
      </div>

      <div className="relative z-10 mx-auto grid h-full w-full max-w-6xl grid-cols-1 items-center gap-8 px-4 py-8 md:px-8 lg:grid-cols-[1.1fr,0.9fr]">
        {/* <section className="hidden rounded-3xl border border-base-content/10 bg-base-100/30 p-8 backdrop-blur lg:block">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-primary">
            <Sparkles className="h-3.5 w-3.5" />
            Network Automation Platform
          </div>
          <h1 className="text-4xl font-black leading-tight text-base-content">
            Secure Access to CogniShield Ops
          </h1>
          <p className="mt-4 max-w-xl text-sm leading-6 text-base-content/75">
            Unified control for intent-driven automation, compliance workflows, and deployment approvals across your network.
          </p>
          <div className="mt-8 space-y-3">
            <div className="flex items-start gap-3 rounded-xl border border-base-content/10 bg-base-100/60 p-3">
              <ShieldCheck className="mt-0.5 h-4 w-4 text-success" />
              <div>
                <p className="text-sm font-semibold">Role-based workspace isolation</p>
                <p className="text-xs text-base-content/65">Separate operator and engineer actions with approval-safe workflows.</p>
              </div>
            </div>
            <div className="flex items-start gap-3 rounded-xl border border-base-content/10 bg-base-100/60 p-3">
              <ShieldCheck className="mt-0.5 h-4 w-4 text-success" />
              <div>
                <p className="text-sm font-semibold">Intent to execution traceability</p>
                <p className="text-xs text-base-content/65">Track each request from prompt, preview, dry-run, and final deployment.</p>
              </div>
            </div>
          </div>
        </section> */}

        <section className="mx-auto w-full max-w-md rounded-3xl border border-base-content/10 bg-base-100/90 p-6 shadow-2xl backdrop-blur md:p-7">
          <div className="mb-5 flex items-center gap-3">
            <div className="rounded-2xl border border-base-content/10 bg-base-100 p-2 shadow">
              <img src="/logo.png" alt="CogniShield" className="h-12 w-12 object-contain" />
            </div>
            <div>
              <h2 className="text-2xl font-extrabold leading-tight">CogniShield</h2>
              <p className="text-[11px] uppercase tracking-[0.12em] text-base-content/55">Campus Network Automation</p>
            </div>
          </div>

          <div className="mb-5 rounded-2xl border border-base-content/10 bg-base-200/70 p-1">
            <div className="grid grid-cols-2 gap-1">
              <button
                type="button"
                className={`btn btn-sm rounded-xl border-0 ${mode === 'signin' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => {
                  setMode('signin');
                  setConfirmPassword('');
                }}
              >
                Sign In
              </button>
              <button
                type="button"
                className={`btn btn-sm rounded-xl border-0 ${mode === 'signup' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => {
                  setMode('signup');
                  if (!username || username === 'admin') setUsername('');
                }}
              >
                Sign Up
              </button>
            </div>
          </div>

          <p className="mb-5 text-sm text-base-content/70">{subtitle}</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text text-xs font-semibold uppercase tracking-[0.08em] text-base-content/60">Username</span>
              </label>
              <label className="input input-bordered flex h-11 w-full items-center gap-2 rounded-xl bg-base-100">
                <UserIcon className="h-4 w-4 opacity-70" />
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
              <label className="label py-1">
                <span className="label-text text-xs font-semibold uppercase tracking-[0.08em] text-base-content/60">Password</span>
              </label>
              <label className="input input-bordered flex h-11 w-full items-center gap-2 rounded-xl bg-base-100">
                <Lock className="h-4 w-4 opacity-70" />
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
                <label className="label py-1">
                  <span className="label-text text-xs font-semibold uppercase tracking-[0.08em] text-base-content/60">Confirm Password</span>
                </label>
                <label className="input input-bordered flex h-11 w-full items-center gap-2 rounded-xl bg-base-100">
                  <UserPlus className="h-4 w-4 opacity-70" />
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

            <div className="pt-2">
              <button type="submit" className="btn btn-primary h-11 w-full rounded-xl" disabled={loading}>
                {loading ? <span className="loading loading-spinner" /> : mode === 'signin' ? 'Sign In' : 'Create Engineer Account'}
              </button>
            </div>
          </form>

          <div className="divider my-5 text-[11px] uppercase tracking-[0.12em] text-base-content/45">Default Accounts</div>
          <div className="grid gap-2 rounded-xl border border-base-content/10 bg-base-200/70 p-3 text-xs text-base-content/75">
            <div className="flex items-center justify-between rounded-lg bg-base-100 px-3 py-2">
              <span className="font-medium">Administrator</span>
              <span className="font-mono">admin / admin</span>
            </div>
            <div className="flex items-center justify-between rounded-lg bg-base-100 px-3 py-2">
              <span className="font-medium">Engineer</span>
              <span className="font-mono">engineer / engineer</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};

export default Login;
