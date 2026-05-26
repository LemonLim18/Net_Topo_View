import React, { useMemo, useState, useRef } from 'react';
import { Lock, User as UserIcon, UserPlus, ShieldCheck, Sparkles, Mail, Upload, ArrowLeft, HelpCircle } from 'lucide-react';
import type { User } from '../types';
import { normalizeUserRole } from '../types';
import { authApi } from '../src/services/api';
import { LoginToast, Toast } from '../src/utils/swal';

interface LoginProps {
  onLogin: (user: User) => void;
}

const PRESETS = ['avatar-1', 'avatar-2', 'avatar-3', 'avatar-4'];

const Login: React.FC<LoginProps> = ({ onLogin }) => {
  const [mode, setMode] = useState<'signin' | 'signup' | 'forgot'>('signin');
  
  // Login State
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  
  // Registration State
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [avatar, setAvatar] = useState('');
  const [securityQuestion, setSecurityQuestion] = useState('');
  const [securityAnswer, setSecurityAnswer] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  
  // Reset Password State
  const [resetStep, setResetStep] = useState<1 | 2>(1);
  const [resetUsername, setResetUsername] = useState('');
  const [resetQuestion, setResetQuestion] = useState('');
  const [resetAnswer, setResetAnswer] = useState('');
  const [resetNewPassword, setResetNewPassword] = useState('');
  const [resetConfirmPassword, setResetConfirmPassword] = useState('');

  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Password strength check helpers
  const passwordStrength = useMemo(() => {
    if (!password) return { score: 0, text: 'No Password', color: 'bg-neutral-content/20' };
    let score = 0;
    if (password.length >= 6) score += 1;
    if (/[a-zA-Z]/.test(password)) score += 1;
    if (/[0-9]/.test(password)) score += 1;
    if (/[^A-Za-z0-9]/.test(password)) score += 1;

    if (score <= 1) return { score, text: 'Weak', color: 'bg-error' };
    if (score === 2) return { score, text: 'Fair', color: 'bg-warning' };
    if (score === 3) return { score, text: 'Good', color: 'bg-info' };
    return { score, text: 'Strong', color: 'bg-success' };
  }, [password]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) processFile(file);
  };

  const processFile = (file: File) => {
    if (!file.type.startsWith('image/')) {
      Toast.error('Invalid File Type', 'Please upload an image file (PNG/JPG)');
      return;
    }
    if (file.size > 1 * 1024 * 1024) {
      Toast.error('File Too Large', 'Please upload an image smaller than 1MB');
      return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      if (e.target?.result) {
        setAvatar(e.target.result as string);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) processFile(file);
  };

  const finishLogin = (response: { access_token: string; user: { id: number; username: string; role: string; lastLogin: string; fullName: string; email: string; avatar: string } }) => {
    localStorage.setItem('auth_token', response.access_token);
    LoginToast.welcome(response.user.username);
    onLogin({
      id: response.user.id,
      username: response.user.username,
      role: normalizeUserRole(response.user.role) as User['role'],
      lastLogin: response.user.lastLogin || new Date().toISOString(),
      fullName: response.user.fullName,
      email: response.user.email,
      avatar: response.user.avatar,
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === 'signin') {
        const response = await authApi.login(username, password);
        finishLogin(response);
      } else if (mode === 'signup') {
        if (!fullName.strip()) {
          throw new Error('Full Name is required');
        }
        if (!email.strip() || !email.includes('@')) {
          throw new Error('Valid Email Address is required');
        }
        if (password !== confirmPassword) {
          throw new Error('Passwords do not match');
        }
        if (passwordStrength.score < 3) {
          throw new Error('Password is too weak. Make sure it contains both letters and digits.');
        }
        if (!securityQuestion || !securityAnswer.strip()) {
          throw new Error('Please configure a security question and answer for account recovery');
        }

        const response = await authApi.signup({
          username,
          password,
          full_name: fullName,
          email,
          avatar: avatar || null,
          security_question: securityQuestion,
          security_answer: securityAnswer,
        });
        finishLogin(response);
      }
    } catch (err: any) {
      Toast.error(mode === 'signin' ? 'Login Failed' : 'Signup Failed', err.message || 'Request failed');
      setLoading(false);
    }
  };

  const handleFetchResetQuestion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resetUsername.strip()) {
      Toast.error('Validation Error', 'Please enter your username');
      return;
    }
    setLoading(true);
    try {
      const res = await authApi.getResetQuestion(resetUsername);
      setResetQuestion(res.question);
      setResetStep(2);
    } catch (err: any) {
      Toast.error('Retrieve Failed', err.message || 'Could not fetch security question');
    } finally {
      setLoading(false);
    }
  };

  const handlePerformPasswordReset = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resetAnswer.strip()) {
      Toast.error('Validation Error', 'Please answer the security question');
      return;
    }
    if (resetNewPassword !== resetConfirmPassword) {
      Toast.error('Validation Error', 'Passwords do not match');
      return;
    }
    if (resetNewPassword.length < 6 || !/[a-zA-Z]/.test(resetNewPassword) || !/[0-9]/.test(resetNewPassword)) {
      Toast.error('Weak Password', 'Password must be at least 6 characters and contain letters and numbers');
      return;
    }

    setLoading(true);
    try {
      await authApi.resetPassword({
        username: resetUsername,
        security_answer: resetAnswer,
        new_password: resetNewPassword,
      });
      Toast.success('Reset Successful', 'Your password has been reset. You can now login.');
      setMode('signin');
      setPassword('');
      setResetUsername('');
      setResetAnswer('');
      setResetQuestion('');
      setResetNewPassword('');
      setResetConfirmPassword('');
      setResetStep(1);
    } catch (err: any) {
      Toast.error('Reset Failed', err.message || 'Could not reset password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-base-300 flex items-center justify-center">
      
      {/* Dynamic Ambient Glows */}
      <style>{`
        @keyframes float-slow-1 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(50px, 30px) scale(1.1); }
        }
        @keyframes float-slow-2 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(-40px, -60px) scale(1.15); }
        }
        .ambient-glowing-blob-1 {
          animation: float-slow-1 12s ease-in-out infinite;
        }
        .ambient-glowing-blob-2 {
          animation: float-slow-2 15s ease-in-out infinite;
        }
      `}</style>
      
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-32 -left-24 h-96 w-96 rounded-full bg-primary/20 blur-[100px] ambient-glowing-blob-1" />
        <div className="absolute -bottom-32 -right-24 h-96 w-96 rounded-full bg-secondary/20 blur-[100px] ambient-glowing-blob-2" />
        <div className="absolute top-1/3 left-1/2 h-80 w-80 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/10 blur-[90px]" />
      </div>

      <div className="relative z-10 w-full max-w-5xl mx-auto px-4 flex justify-center items-center">
        <div className="grid grid-cols-1 lg:grid-cols-[1.1fr,0.9fr] gap-8 items-stretch w-full max-h-[90vh] bg-base-100/60 border border-base-content/10 shadow-2xl backdrop-blur-md rounded-3xl overflow-hidden p-6 md:p-8">
          
          {/* Left panel info (visible on large screen) */}
          <section className="hidden lg:flex flex-col justify-between border-r border-base-content/10 pr-8 py-2">
            <div>
              <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-bold uppercase tracking-[0.14em] text-primary">
                <Sparkles className="h-3.5 w-3.5" />
                Network Automation Platform
              </div>
              <h1 className="text-3xl font-black leading-tight text-base-content">
                Secure Access to CogniShield Ops
              </h1>
              <p className="mt-4 text-xs leading-6 text-base-content/70">
                Unified control for intent-driven automation, compliance workflows, and deployment approvals across your network infrastructure.
              </p>
              
              <div className="mt-8 space-y-4">
                <div className="flex items-start gap-3 rounded-2xl border border-base-content/10 bg-base-100/70 p-3 shadow-sm hover:shadow transition-shadow">
                  <ShieldCheck className="mt-0.5 h-4 w-4 text-success shrink-0" />
                  <div>
                    <p className="text-xs font-bold">Role-based Access Protection</p>
                    <p className="text-[11px] text-base-content/65 mt-0.5">Strict separation of Operator, Engineer, and Admin policies.</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 rounded-2xl border border-base-content/10 bg-base-100/70 p-3 shadow-sm hover:shadow transition-shadow">
                  <ShieldCheck className="mt-0.5 h-4 w-4 text-success shrink-0" />
                  <div>
                    <p className="text-xs font-bold">Self-Service Account Recovery</p>
                    <p className="text-[11px] text-base-content/65 mt-0.5">Restore password access immediately using secure challenge hashing.</p>
                  </div>
                </div>
              </div>
            </div>

            <div className="pt-4">
              <div className="divider text-[10px] uppercase tracking-[0.12em] text-base-content/45 mt-0 mb-4">Default System Accounts</div>
              <div className="grid grid-cols-2 gap-2 text-[10px] text-base-content/75">
                <div className="bg-base-100/80 rounded-xl px-3 py-2 border border-base-content/5 shadow-sm text-center">
                  <span className="font-semibold block">Administrator</span>
                  <span className="font-mono text-primary mt-0.5 block">admin / admin</span>
                </div>
                <div className="bg-base-100/80 rounded-xl px-3 py-2 border border-base-content/5 shadow-sm text-center">
                  <span className="font-semibold block">Default Engineer</span>
                  <span className="font-mono text-primary mt-0.5 block">engineer / engineer</span>
                </div>
              </div>
            </div>
          </section>

          {/* Form Content */}
          <section className="flex flex-col justify-center overflow-y-auto max-h-[80vh] px-1">
            <div className="flex items-center gap-3 mb-6">
              <div className="rounded-2xl border border-base-content/10 bg-base-100 p-2 shadow-md">
                <img src="/logo.png" alt="CogniShield" className="h-10 w-10 object-contain" />
              </div>
              <div className="text-left">
                <h2 className="text-xl font-black leading-none">CogniShield</h2>
                <p className="text-[10px] uppercase tracking-[0.12em] text-base-content/50 mt-1">Campus Controller</p>
              </div>
            </div>

            {mode !== 'forgot' && (
              <div className="mb-5 rounded-xl border border-base-content/10 bg-base-200/50 p-1 flex">
                <button
                  type="button"
                  className={`flex-1 btn btn-xs h-8 rounded-lg border-0 text-xs ${mode === 'signin' ? 'btn-primary shadow-sm' : 'btn-ghost hover:bg-base-200'}`}
                  onClick={() => setMode('signin')}
                >
                  Sign In
                </button>
                <button
                  type="button"
                  className={`flex-1 btn btn-xs h-8 rounded-lg border-0 text-xs ${mode === 'signup' ? 'btn-primary shadow-sm' : 'btn-ghost hover:bg-base-200'}`}
                  onClick={() => setMode('signup')}
                >
                  Sign Up
                </button>
              </div>
            )}

            {mode === 'signin' && (
              <form onSubmit={handleSubmit} className="space-y-4 text-left">
                <div className="form-control">
                  <label className="label py-1"><span className="label-text text-[11px] font-bold uppercase tracking-wider text-base-content/60">Username</span></label>
                  <label className="input input-bordered flex h-11 w-full items-center gap-2 rounded-xl bg-base-100 border-base-content/10 focus-within:border-primary/50">
                    <UserIcon className="h-4 w-4 opacity-50 text-primary" />
                    <input
                      type="text"
                      className="grow text-sm"
                      placeholder="Enter username"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                    />
                  </label>
                </div>

                <div className="form-control">
                  <div className="flex justify-between items-center">
                    <label className="label py-1"><span className="label-text text-[11px] font-bold uppercase tracking-wider text-base-content/60">Password</span></label>
                    <button 
                      type="button" 
                      onClick={() => setMode('forgot')}
                      className="text-xs text-primary hover:underline font-semibold"
                    >
                      Forgot Password?
                    </button>
                  </div>
                  <label className="input input-bordered flex h-11 w-full items-center gap-2 rounded-xl bg-base-100 border-base-content/10 focus-within:border-primary/50">
                    <Lock className="h-4 w-4 opacity-50 text-primary" />
                    <input
                      type="password"
                      className="grow text-sm"
                      placeholder="Enter password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </label>
                </div>

                <button type="submit" className="btn btn-primary h-11 w-full rounded-xl mt-2 text-sm shadow-md" disabled={loading}>
                  {loading ? <span className="loading loading-spinner" /> : 'Sign In'}
                </button>
              </form>
            )}

            {mode === 'signup' && (
              <form onSubmit={handleSubmit} className="space-y-3.5 text-left max-h-[50vh] overflow-y-auto pr-1.5 scrollbar-thin">
                
                {/* Profile Picture Uploader */}
                <div className="bg-base-200/40 p-3 rounded-xl border border-base-content/5 space-y-2.5">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-base-content/60 block">Avatar Selection / File Upload</span>
                  <div className="flex items-center gap-4">
                    {avatar ? (
                      PRESETS.includes(avatar) ? (
                        <div className="w-14 h-14 rounded-full bg-primary/20 text-primary flex items-center justify-center border border-primary/30 shrink-0">
                          <UserIcon className="w-7 h-7" />
                        </div>
                      ) : (
                        <img src={avatar} alt="Preview" className="w-14 h-14 rounded-full object-cover border border-base-content/20 shrink-0 shadow-sm" />
                      )
                    ) : (
                      <div className="w-14 h-14 rounded-full bg-neutral text-neutral-content flex items-center justify-center text-xl font-bold border border-base-content/10 shrink-0">
                        ?
                      </div>
                    )}
                    
                    <div className="grow space-y-2">
                      <div 
                        className={`border border-dashed rounded-lg p-2.5 text-center cursor-pointer transition-all flex flex-col items-center justify-center ${
                          dragOver ? 'border-primary bg-primary/5' : 'border-base-content/20 hover:border-primary/50'
                        }`}
                        onClick={() => fileInputRef.current?.click()}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                      >
                        <Upload className="w-4 h-4 opacity-60 text-primary mb-0.5" />
                        <span className="text-[10px] font-semibold">Upload file or drag/drop</span>
                        <input type="file" ref={fileInputRef} className="hidden" accept="image/*" onChange={handleFileChange} />
                      </div>
                      
                      <div className="flex gap-1">
                        {PRESETS.map((p, idx) => (
                          <button
                            key={p}
                            type="button"
                            className={`btn btn-xxs h-5 min-h-0 text-[9px] rounded px-1.5 ${avatar === p ? 'btn-primary' : 'btn-outline btn-ghost'}`}
                            onClick={() => setAvatar(p)}
                          >
                            P{idx + 1}
                          </button>
                        ))}
                        {avatar && (
                          <button
                            type="button"
                            className="btn btn-xxs h-5 min-h-0 btn-outline btn-error rounded px-1.5 ml-auto"
                            onClick={() => setAvatar('')}
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="form-control">
                    <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">Username</span></label>
                    <input
                      type="text"
                      className="input input-bordered h-9 text-xs rounded-lg bg-base-100 border-base-content/10"
                      placeholder="engineer01"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                    />
                  </div>
                  <div className="form-control">
                    <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">Full Name</span></label>
                    <input
                      type="text"
                      className="input input-bordered h-9 text-xs rounded-lg bg-base-100 border-base-content/10"
                      placeholder="John Doe"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                    />
                  </div>
                </div>

                <div className="form-control">
                  <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">Email Address</span></label>
                  <label className="input input-bordered flex h-9 w-full items-center gap-2 rounded-lg bg-base-100 border-base-content/10">
                    <Mail className="h-3.5 w-3.5 opacity-50 text-primary" />
                    <input
                      type="email"
                      className="grow text-xs"
                      placeholder="email@company.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </label>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="form-control">
                    <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">Password</span></label>
                    <input
                      type="password"
                      className="input input-bordered h-9 text-xs rounded-lg bg-base-100 border-base-content/10"
                      placeholder="Password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </div>
                  <div className="form-control">
                    <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">Confirm Password</span></label>
                    <input
                      type="password"
                      className="input input-bordered h-9 text-xs rounded-lg bg-base-100 border-base-content/10"
                      placeholder="Confirm"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                    />
                  </div>
                </div>

                {/* Password Strength Indicator */}
                {password && (
                  <div className="space-y-1">
                    <div className="flex justify-between text-[9px] font-bold">
                      <span className="opacity-60">Password Strength:</span>
                      <span className={passwordStrength.color === 'bg-error' ? 'text-error' : passwordStrength.color === 'bg-warning' ? 'text-warning' : 'text-success'}>
                        {passwordStrength.text}
                      </span>
                    </div>
                    <div className="w-full h-1.5 bg-base-content/10 rounded-full overflow-hidden">
                      <div 
                        className={`h-full transition-all duration-300 ${passwordStrength.color}`} 
                        style={{ width: `${(passwordStrength.score / 4) * 100}%` }}
                      />
                    </div>
                    <p className="text-[8px] opacity-50 leading-tight">Password must be at least 6 characters and contain both letters and digits.</p>
                  </div>
                )}

                {/* Security Question Config */}
                <div className="p-3 bg-base-200/40 border border-base-content/5 rounded-xl space-y-2">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-base-content/60 block flex items-center gap-1">
                    <HelpCircle className="w-3.5 h-3.5 text-warning" /> Configure Recovery Question
                  </span>
                  <div className="space-y-2">
                    <select 
                      className="select select-bordered select-sm w-full rounded-lg text-xs h-8 min-h-0 py-0"
                      value={securityQuestion}
                      onChange={(e) => setSecurityQuestion(e.target.value)}
                    >
                      <option value="">Select a recovery question...</option>
                      <option value="What was the name of your first pet?">What was the name of your first pet?</option>
                      <option value="In what city were you born?">In what city were you born?</option>
                      <option value="What is your favorite book?">What is your favorite book?</option>
                      <option value="What was your childhood nickname?">What was your childhood nickname?</option>
                    </select>
                    <input
                      type="text"
                      className="input input-bordered h-8 text-xs rounded-lg bg-base-100 border-base-content/10 w-full"
                      placeholder="Your answer"
                      value={securityAnswer}
                      onChange={(e) => setSecurityAnswer(e.target.value)}
                    />
                  </div>
                </div>

                <button type="submit" className="btn btn-primary h-10 w-full rounded-xl text-xs mt-2 shadow-md" disabled={loading}>
                  {loading ? <span className="loading loading-spinner" /> : 'Create Account'}
                </button>
              </form>
            )}

            {mode === 'forgot' && (
              <div className="space-y-4 text-left">
                <button 
                  type="button" 
                  onClick={() => { setMode('signin'); setResetStep(1); }} 
                  className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline font-bold mb-2"
                >
                  <ArrowLeft className="w-3.5 h-3.5" /> Back to Sign In
                </button>

                {resetStep === 1 ? (
                  <form onSubmit={handleFetchResetQuestion} className="space-y-4">
                    <p className="text-xs opacity-75">Enter your username below. We will retrieve your security recovery question to reset your password.</p>
                    <div className="form-control">
                      <label className="label py-1"><span className="label-text text-[11px] font-bold uppercase tracking-wider text-base-content/60">Username</span></label>
                      <label className="input input-bordered flex h-11 w-full items-center gap-2 rounded-xl bg-base-100 border-base-content/10">
                        <UserIcon className="h-4 w-4 opacity-50 text-primary" />
                        <input
                          type="text"
                          className="grow text-sm"
                          placeholder="Enter your username"
                          value={resetUsername}
                          onChange={(e) => setResetUsername(e.target.value)}
                        />
                      </label>
                    </div>
                    <button type="submit" className="btn btn-primary h-11 w-full rounded-xl shadow-md" disabled={loading}>
                      {loading ? <span className="loading loading-spinner" /> : 'Get Security Question'}
                    </button>
                  </form>
                ) : (
                  <form onSubmit={handlePerformPasswordReset} className="space-y-3.5">
                    <div className="bg-base-200/50 p-3 rounded-xl border border-base-content/5 space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider opacity-60">Security Recovery Question</span>
                      <p className="text-xs font-bold text-base-content">{resetQuestion}</p>
                    </div>

                    <div className="form-control">
                      <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">Your Answer</span></label>
                      <input
                        type="password"
                        className="input input-bordered h-9 text-xs rounded-lg bg-base-100 border-base-content/10 w-full"
                        placeholder="Type answer here"
                        value={resetAnswer}
                        onChange={(e) => setResetAnswer(e.target.value)}
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="form-control">
                        <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">New Password</span></label>
                        <input
                          type="password"
                          className="input input-bordered h-9 text-xs rounded-lg bg-base-100 border-base-content/10"
                          placeholder="New password"
                          value={resetNewPassword}
                          onChange={(e) => setResetNewPassword(e.target.value)}
                        />
                      </div>
                      <div className="form-control">
                        <label className="label py-0.5"><span className="label-text text-[10px] font-bold uppercase tracking-wider text-base-content/60">Confirm Password</span></label>
                        <input
                          type="password"
                          className="input input-bordered h-9 text-xs rounded-lg bg-base-100 border-base-content/10"
                          placeholder="Confirm"
                          value={resetConfirmPassword}
                          onChange={(e) => setResetConfirmPassword(e.target.value)}
                        />
                      </div>
                    </div>

                    <button type="submit" className="btn btn-primary h-11 w-full rounded-xl shadow-md mt-2" disabled={loading}>
                      {loading ? <span className="loading loading-spinner" /> : 'Reset Password'}
                    </button>
                  </form>
                )}
              </div>
            )}

          </section>

        </div>
      </div>
    </div>
  );
};

export default Login;
