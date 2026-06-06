import React, { useMemo, useState, useRef } from 'react';
import { Lock, User as UserIcon, Mail, Upload, ArrowLeft, HelpCircle, Eye, EyeOff } from 'lucide-react';
import type { User } from '../types';
import { normalizeUserRole } from '../types';
import { authApi } from '../src/services/api';
import { LoginToast, Toast } from '../src/utils/swal';

interface LoginProps {
  onLogin: (user: User) => void;
}

const PRESETS = ['avatar-1', 'avatar-2', 'avatar-3', 'avatar-4'];

/* ── Floating logo specs (deterministic to avoid layout shift) ───────── */
const FLOATING_LOGOS = [
  { x: 5,  y: 8,  size: 62, opacity: 0.14, dur: 11, delay: 0,    rot: 15,  drift: 30  },
  { x: 78, y: 5,  size: 48, opacity: 0.10, dur: 13, delay: -3,   rot: -20, drift: 20  },
  { x: 20, y: 70, size: 72, opacity: 0.12, dur: 15, delay: -5,   rot: 10,  drift: 35  },
  { x: 88, y: 60, size: 54, opacity: 0.16, dur: 12, delay: -1,   rot: -12, drift: 25  },
  { x: 50, y: 15, size: 40, opacity: 0.09, dur: 17, delay: -7,   rot: 25,  drift: 18  },
  { x: 35, y: 85, size: 64, opacity: 0.12, dur: 11, delay: -4,   rot: -8,  drift: 28  },
  { x: 65, y: 40, size: 50, opacity: 0.10, dur: 14, delay: -6,   rot: 18,  drift: 22  },
  { x: 10, y: 42, size: 44, opacity: 0.11, dur: 16, delay: -9,   rot: -22, drift: 15  },
  { x: 92, y: 30, size: 56, opacity: 0.08, dur: 13, delay: -2,   rot: 8,   drift: 32  },
  { x: 55, y: 78, size: 46, opacity: 0.13, dur: 14, delay: -4,   rot: -15, drift: 20  },
  { x: 42, y: 50, size: 38, opacity: 0.08, dur: 18, delay: -10,  rot: 30,  drift: 12  },
  { x: 72, y: 88, size: 58, opacity: 0.10, dur: 10, delay: -1,   rot: -5,  drift: 26  },
];

/* ── Floating Logos Background ───────────────────────────────────────── */
const FloatingLogos: React.FC = () => (
  <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
    {FLOATING_LOGOS.map((logo, i) => (
      <img
        key={i}
        src="/floating-icon.ico"
        alt=""
        draggable={false}
        className="absolute select-none"
        style={{
          left: `${logo.x}%`,
          top: `${logo.y}%`,
          width: logo.size,
          height: logo.size,
          opacity: logo.opacity,
          filter: 'grayscale(0.3)',
          animation: `loginFloat${i % 3} ${logo.dur}s ease-in-out ${logo.delay}s infinite`,
          transform: `rotate(${logo.rot}deg)`,
        }}
      />
    ))}
  </div>
);

/* ── Decorative SVG art overlay for the left panel ───────────────────── */
const DecorativeArt: React.FC = () => (
  <svg
    className="absolute inset-0 w-full h-full pointer-events-none"
    viewBox="0 0 500 600"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    preserveAspectRatio="xMidYMid slice"
  >
    {/* Large organic blob */}
    <path
      d="M-30 100 C50 50,180 20,250 80 C320 140,380 60,420 120 C460 180,500 300,440 380 C380 460,280 500,200 460 C120 420,30 480,-20 400 Z"
      fill="rgba(255,255,255,0.06)"
    />
    {/* Secondary blob */}
    <path
      d="M350 0 C400 40,500 100,480 200 C460 300,520 350,500 400 L500 0 Z"
      fill="rgba(255,255,255,0.05)"
    />

    {/* Topographic / wavy contour lines */}
    <path d="M-10 520 C60 480,140 510,200 490 C260 470,320 500,400 480 C440 472,480 490,520 480" stroke="rgba(255,255,255,0.18)" strokeWidth="1.5" fill="none" />
    <path d="M-10 500 C50 460,130 490,190 470 C250 450,310 480,390 460 C430 452,470 470,520 460" stroke="rgba(255,255,255,0.14)" strokeWidth="1.5" fill="none" />
    <path d="M-10 480 C40 440,120 470,180 450 C240 430,300 460,380 440 C420 432,460 450,520 440" stroke="rgba(255,255,255,0.10)" strokeWidth="1.5" fill="none" />

    {/* Wavy lines at top-right */}
    <path d="M280 40 C300 60,340 30,370 50 C400 70,430 40,470 60 C490 70,510 50,530 60" stroke="rgba(255,255,255,0.12)" strokeWidth="1.2" fill="none" />
    <path d="M300 55 C320 75,360 45,390 65 C420 85,450 55,490 75" stroke="rgba(255,255,255,0.09)" strokeWidth="1.2" fill="none" />

    {/* Cross / plus signs */}
    <g stroke="rgba(255,255,255,0.35)" strokeWidth="2" strokeLinecap="round">
      <line x1="60" y1="50" x2="60" y2="70" />
      <line x1="50" y1="60" x2="70" y2="60" />
    </g>
    <g stroke="rgba(255,255,255,0.25)" strokeWidth="2" strokeLinecap="round">
      <line x1="100" y1="430" x2="100" y2="450" />
      <line x1="90" y1="440" x2="110" y2="440" />
    </g>
    <g stroke="rgba(255,255,255,0.20)" strokeWidth="1.5" strokeLinecap="round">
      <line x1="380" y1="320" x2="380" y2="340" />
      <line x1="370" y1="330" x2="390" y2="330" />
    </g>

    {/* Circles */}
    <circle cx="240" cy="90" r="8" stroke="rgba(255,255,255,0.22)" strokeWidth="1.5" fill="none" />
    <circle cx="55" cy="380" r="10" stroke="rgba(255,255,255,0.18)" strokeWidth="1.5" fill="none" />
    <circle cx="420" cy="160" r="5" stroke="rgba(255,255,255,0.15)" strokeWidth="1.2" fill="none" />

    {/* Dot cluster grid */}
    {[0, 1, 2, 3, 4].map(r =>
      [0, 1, 2, 3].map(c => (
        <circle
          key={`dot-${r}-${c}`}
          cx={300 + c * 8}
          cy={10 + r * 8}
          r="1.5"
          fill="rgba(255,255,255,0.3)"
        />
      ))
    )}

    {/* Scattered dots */}
    <circle cx="160" cy="140" r="2" fill="rgba(255,255,255,0.20)" />
    <circle cx="350" cy="250" r="2" fill="rgba(255,255,255,0.15)" />
    <circle cx="80" cy="300" r="2.5" fill="rgba(255,255,255,0.12)" />
  </svg>
);

const Login: React.FC<LoginProps> = ({ onLogin }) => {
  const [mode, setMode] = useState<'signin' | 'signup' | 'forgot'>('signin');
  
  // Login State
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  
  // Password Visibility States
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [showResetNewPassword, setShowResetNewPassword] = useState(false);
  const [showResetConfirmPassword, setShowResetConfirmPassword] = useState(false);

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

  // Password strength helpers
  const passwordStrength = useMemo(() => {
    if (!password) return { score: 0, text: 'No Password', color: 'bg-base-content/10', textColor: 'text-base-content/40' };
    let score = 0;
    if (password.length >= 6) score += 1;
    if (/[a-zA-Z]/.test(password)) score += 1;
    if (/[0-9]/.test(password)) score += 1;
    if (/[^A-Za-z0-9]/.test(password)) score += 1;

    if (score <= 1) return { score, text: 'Weak', color: 'bg-error', textColor: 'text-error' };
    if (score === 2) return { score, text: 'Fair', color: 'bg-warning', textColor: 'text-warning' };
    if (score === 3) return { score, text: 'Good', color: 'bg-info', textColor: 'text-info' };
    return { score, text: 'Strong', color: 'bg-success', textColor: 'text-success' };
  }, [password]);

  /* ── Left-panel dynamic content based on mode ──────────────────────── */
  const panelContent = useMemo(() => {
    if (mode === 'signup') return {
      title: 'Create your profile.',
      subtitle: 'Register your authorized credentials to access the operations dashboard.',
    };
    if (mode === 'forgot') return {
      title: 'Recover access.',
      subtitle: 'Verify your identity to securely reset your credentials.',
    };
    return {
      title: 'Welcome back!',
      subtitle: 'Sign in to access your network operations dashboard.',
    };
  }, [mode]);

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
        if (!fullName.trim()) {
          throw new Error('Full Name is required');
        }
        if (!email.trim() || !email.includes('@')) {
          throw new Error('Valid Email Address is required');
        }
        if (password !== confirmPassword) {
          throw new Error('Passwords do not match');
        }
        if (passwordStrength.score < 3) {
          throw new Error('Password is too weak. Make sure it contains both letters and digits.');
        }
        if (!securityQuestion || !securityAnswer.trim()) {
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
      Toast.error(mode === 'signin' ? 'Login Failed' : 'Registration Failed', err.message || 'Request failed');
      setLoading(false);
    }
  };

  const handleFetchResetQuestion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resetUsername.trim()) {
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
    if (!resetAnswer.trim()) {
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

  /* ── Shared input classes ──────────────────────────────────────────── */
  const inputCls = 'w-full h-11 rounded-full border border-base-content/15 bg-base-100 pl-10 pr-10 text-sm text-base-content placeholder:text-base-content/35 outline-none transition-colors focus:border-primary';
  const inputSmCls = 'w-full h-9 rounded-full border border-base-content/15 bg-base-100 px-4 text-xs text-base-content placeholder:text-base-content/35 outline-none transition-colors focus:border-primary';
  const inputSmIconCls = 'w-full h-9 rounded-full border border-base-content/15 bg-base-100 pl-9 pr-4 text-xs text-base-content placeholder:text-base-content/35 outline-none transition-colors focus:border-primary';
  const labelCls = 'block text-[10px] font-bold uppercase tracking-wider text-base-content/50 mb-1';
  const iconLeftCls = 'absolute left-3.5 top-1/2 -translate-y-1/2 text-base-content/30 pointer-events-none';
  const eyeBtnCls = 'absolute right-3 top-1/2 -translate-y-1/2 text-base-content/30 hover:text-base-content/60 transition-colors';

  return (
    <div className="relative min-h-screen w-full flex items-center justify-center bg-base-300 p-4 font-[Plus_Jakarta_Sans,sans-serif] overflow-hidden">

      {/* Floating bouncing logo background */}
      <FloatingLogos />

      {/* ── Card Container ──────────────────────────────────────────── */}
      <div className="w-full max-w-[920px] grid grid-cols-1 md:grid-cols-2 rounded-2xl overflow-hidden shadow-2xl">

        {/* ── LEFT PANEL: decorative, theme-primary gradient ────────── */}
        <div className="relative hidden md:flex flex-col justify-center bg-gradient-to-br from-primary via-primary to-primary/80 px-10 py-12 overflow-hidden min-h-[480px]">
          <DecorativeArt />

          <div className="relative z-10">
            <h1 className="text-[28px] font-extrabold text-primary-content leading-tight mb-3">
              {panelContent.title}
            </h1>
            <p className="text-sm text-primary-content/75 leading-relaxed max-w-[280px]">
              {panelContent.subtitle}
            </p>
          </div>
        </div>

        {/* ── RIGHT PANEL: form ─────────────────────────────────────── */}
        <div className="flex flex-col justify-center bg-base-100 px-8 py-10 md:px-11 md:py-10 overflow-y-auto max-h-[92vh]">

          {/* Mobile-only branding */}
          <div className="flex items-center gap-2.5 mb-5 md:hidden">
            <img src="/logo.png" alt="CogniShield" className="h-8 w-8 object-contain" />
            <span className="font-bold text-base text-primary">CogniShield</span>
          </div>

          {/* ─── SIGN IN ─────────────────────────────────────────── */}
          {mode === 'signin' && (
            <form onSubmit={handleSubmit} className="flex flex-col gap-5">
              <h2 className="text-2xl font-extrabold text-base-content mb-0.5">Sign In</h2>

              {/* Username */}
              <div className="relative">
                <span className={iconLeftCls}><UserIcon size={16} /></span>
                <input
                  type="text"
                  placeholder="Username or email"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  className={inputCls}
                />
              </div>

              {/* Password */}
              <div className="relative">
                <span className={iconLeftCls}><Lock size={16} /></span>
                <input
                  type={showPassword ? 'text' : 'password'}
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className={inputCls}
                />
                <button type="button" onClick={() => setShowPassword(!showPassword)} className={eyeBtnCls}>
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>

              {/* Remember me + Forgot password */}
              <div className="flex items-center justify-between text-xs text-base-content/60">
                <label className="flex items-center gap-1.5 cursor-pointer select-none">
                  <input type="checkbox" className="checkbox checkbox-xs checkbox-primary rounded" />
                  Remember me
                </label>
                <button
                  type="button"
                  onClick={() => setMode('forgot')}
                  className="font-semibold text-primary hover:underline"
                >
                  Forgot password?
                </button>
              </div>

              {/* Submit */}
              <button
                type="submit"
                disabled={loading}
                className="w-full h-11 rounded-full bg-primary text-primary-content text-sm font-semibold shadow-md hover:brightness-110 active:scale-[0.98] transition-all disabled:opacity-60"
              >
                {loading ? <span className="loading loading-spinner loading-sm" /> : 'Sign In'}
              </button>

              {/* Register link */}
              <p className="text-center text-xs text-base-content/45 mt-1">
                Authorized personnel?{' '}
                <button
                  type="button"
                  onClick={() => setMode('signup')}
                  className="font-bold text-primary hover:underline"
                >
                  Register an Account
                </button>
              </p>
            </form>
          )}

          {/* ─── SIGN UP (Registration) ──────────────────────────── */}
          {mode === 'signup' && (
            <form onSubmit={handleSubmit} className="flex flex-col gap-3.5 overflow-y-auto max-h-[calc(92vh-80px)] pr-1 scrollbar-thin">
              <h2 className="text-xl font-extrabold text-base-content">Register Account</h2>
              <p className="text-xs text-base-content/45 -mt-1 mb-1">Registration is restricted to authorized admin and engineer roles.</p>

              {/* Avatar section */}
              <div className="bg-base-200/50 border border-base-content/5 rounded-xl p-3.5">
                <span className="text-[9px] font-bold uppercase tracking-wider text-base-content/50 block mb-2.5">Profile Avatar</span>
                <div className="flex items-center gap-3.5">
                  <div className="shrink-0">
                    {avatar ? (
                      PRESETS.includes(avatar) ? (
                        <div className="w-11 h-11 rounded-full bg-primary/15 text-primary flex items-center justify-center border border-primary/25">
                          <UserIcon size={20} />
                        </div>
                      ) : (
                        <img src={avatar} alt="Preview" className="w-11 h-11 rounded-full object-cover border border-base-content/15" />
                      )
                    ) : (
                      <div className="w-11 h-11 rounded-full bg-base-300 text-base-content/40 flex items-center justify-center text-base font-bold border border-base-content/10">?</div>
                    )}
                  </div>

                  <div className="flex-1 space-y-2">
                    <div
                      onClick={() => fileInputRef.current?.click()}
                      onDragOver={handleDragOver}
                      onDragLeave={handleDragLeave}
                      onDrop={handleDrop}
                      className={`border border-dashed rounded-lg p-2 text-center cursor-pointer transition-all ${
                        dragOver ? 'border-primary bg-primary/5' : 'border-base-content/15 hover:border-primary/40'
                      }`}
                    >
                      <Upload size={13} className="mx-auto mb-0.5 opacity-50 text-primary" />
                      <span className="text-[10px] font-semibold text-base-content/50">Upload or Drag Photo</span>
                      <input type="file" ref={fileInputRef} className="hidden" accept="image/*" onChange={handleFileChange} />
                    </div>

                    <div className="flex gap-1.5 items-center flex-wrap">
                      {PRESETS.map((p, idx) => (
                        <button
                          key={p}
                          type="button"
                          onClick={() => setAvatar(p)}
                          className={`w-7 h-7 rounded-full text-[10px] font-bold flex items-center justify-center transition-all border ${
                            avatar === p
                              ? 'bg-primary border-primary text-primary-content'
                              : 'border-base-content/10 bg-base-100 hover:bg-base-200 text-base-content/60'
                          }`}
                        >
                          P{idx + 1}
                        </button>
                      ))}
                      {avatar && (
                        <button
                          type="button"
                          className="text-[9px] font-bold text-error hover:underline ml-auto"
                          onClick={() => setAvatar('')}
                        >
                          Clear
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Username + Full Name */}
              <div className="grid grid-cols-2 gap-2.5">
                <div>
                  <label className={labelCls}>Username</label>
                  <input type="text" placeholder="engineer01" value={username} onChange={(e) => setUsername(e.target.value)} required className={inputSmCls} />
                </div>
                <div>
                  <label className={labelCls}>Full Name</label>
                  <input type="text" placeholder="John Doe" value={fullName} onChange={(e) => setFullName(e.target.value)} required className={inputSmCls} />
                </div>
              </div>

              {/* Email */}
              <div>
                <label className={labelCls}>Email Address</label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/30 pointer-events-none"><Mail size={14} /></span>
                  <input type="email" placeholder="email@company.com" value={email} onChange={(e) => setEmail(e.target.value)} required className={inputSmIconCls} />
                </div>
              </div>

              {/* Passwords */}
              <div className="grid grid-cols-2 gap-2.5">
                <div>
                  <label className={labelCls}>Password</label>
                  <div className="relative">
                    <input type={showPassword ? 'text' : 'password'} placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required className={`${inputSmCls} pr-8`} />
                    <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-base-content/30 hover:text-base-content/60">
                      {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
                <div>
                  <label className={labelCls}>Confirm Password</label>
                  <div className="relative">
                    <input type={showConfirmPassword ? 'text' : 'password'} placeholder="Confirm" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required className={`${inputSmCls} pr-8`} />
                    <button type="button" onClick={() => setShowConfirmPassword(!showConfirmPassword)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-base-content/30 hover:text-base-content/60">
                      {showConfirmPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
              </div>

              {/* Password strength */}
              {password && (
                <div className="bg-base-200/50 rounded-lg border border-base-content/5 p-2 space-y-1">
                  <div className="flex justify-between text-[9px] font-bold">
                    <span className="text-base-content/40">Strength:</span>
                    <span className={passwordStrength.textColor}>{passwordStrength.text}</span>
                  </div>
                  <div className="w-full h-1 bg-base-content/10 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-300 ${passwordStrength.color}`} style={{ width: `${(passwordStrength.score / 4) * 100}%` }} />
                  </div>
                </div>
              )}

              {/* Security question */}
              <div className="bg-base-200/50 border border-base-content/5 rounded-xl p-3 space-y-2">
                <span className="text-[9px] font-bold uppercase tracking-wider text-base-content/50 flex items-center gap-1">
                  <HelpCircle size={13} className="text-warning" /> Recovery Question
                </span>
                <select
                  value={securityQuestion}
                  onChange={(e) => setSecurityQuestion(e.target.value)}
                  required
                  className="w-full h-8 rounded-full border border-base-content/15 bg-base-100 px-3 text-xs text-base-content outline-none focus:border-primary"
                >
                  <option value="">Select recovery question...</option>
                  <option value="What was the name of your first pet?">What was the name of your first pet?</option>
                  <option value="In what city were you born?">In what city were you born?</option>
                  <option value="What is your favorite book?">What is your favorite book?</option>
                  <option value="What was your childhood nickname?">What was your childhood nickname?</option>
                </select>
                <input
                  type="text"
                  placeholder="Your answer"
                  value={securityAnswer}
                  onChange={(e) => setSecurityAnswer(e.target.value)}
                  required
                  className="w-full h-8 rounded-full border border-base-content/15 bg-base-100 px-4 text-xs text-base-content placeholder:text-base-content/35 outline-none focus:border-primary"
                />
              </div>

              {/* Submit */}
              <button
                type="submit"
                disabled={loading}
                className="w-full h-10 rounded-full bg-primary text-primary-content text-xs font-semibold shadow-md hover:brightness-110 active:scale-[0.98] transition-all disabled:opacity-60 mt-1"
              >
                {loading ? <span className="loading loading-spinner loading-sm" /> : 'Submit Registration'}
              </button>

              <p className="text-center text-xs text-base-content/45">
                Already have an account?{' '}
                <button type="button" onClick={() => setMode('signin')} className="font-bold text-primary hover:underline">
                  Sign In
                </button>
              </p>
            </form>
          )}

          {/* ─── FORGOT PASSWORD ─────────────────────────────────── */}
          {mode === 'forgot' && (
            <div className="flex flex-col gap-4">
              <button
                type="button"
                onClick={() => { setMode('signin'); setResetStep(1); }}
                className="inline-flex items-center gap-1 text-xs font-bold text-primary hover:underline self-start mb-1"
              >
                <ArrowLeft size={15} /> Back to Sign In
              </button>

              {resetStep === 1 ? (
                <form onSubmit={handleFetchResetQuestion} className="flex flex-col gap-4">
                  <div>
                    <h2 className="text-2xl font-extrabold text-base-content">Reset Password</h2>
                    <p className="text-xs text-base-content/45 mt-1">Enter your username to retrieve your security question.</p>
                  </div>
                  <div className="relative">
                    <span className={iconLeftCls}><UserIcon size={16} /></span>
                    <input
                      type="text"
                      placeholder="Enter your username"
                      value={resetUsername}
                      onChange={(e) => setResetUsername(e.target.value)}
                      required
                      className={inputCls}
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full h-11 rounded-full bg-primary text-primary-content text-sm font-semibold shadow-md hover:brightness-110 active:scale-[0.98] transition-all disabled:opacity-60"
                  >
                    {loading ? <span className="loading loading-spinner loading-sm" /> : 'Retrieve Question'}
                  </button>
                </form>
              ) : (
                <form onSubmit={handlePerformPasswordReset} className="flex flex-col gap-4">
                  <div>
                    <h2 className="text-2xl font-extrabold text-base-content">Verify Identity</h2>
                    <p className="text-xs text-base-content/45 mt-1">Answer the security question and set a new password.</p>
                  </div>

                  <div className="bg-base-200/60 border border-base-content/5 rounded-xl p-3.5 space-y-1">
                    <span className="text-[9px] font-bold uppercase tracking-wider text-base-content/40">Security Question</span>
                    <p className="text-xs font-bold text-base-content">{resetQuestion}</p>
                  </div>

                  <div>
                    <label className={labelCls}>Your Answer</label>
                    <input
                      type="password"
                      placeholder="Type your answer"
                      value={resetAnswer}
                      onChange={(e) => setResetAnswer(e.target.value)}
                      required
                      className={`${inputCls} pl-4`}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <div>
                      <label className={labelCls}>New Password</label>
                      <div className="relative">
                        <input type={showResetNewPassword ? 'text' : 'password'} placeholder="New password" value={resetNewPassword} onChange={(e) => setResetNewPassword(e.target.value)} required className={`${inputSmCls} pr-8`} />
                        <button type="button" onClick={() => setShowResetNewPassword(!showResetNewPassword)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-base-content/30 hover:text-base-content/60">
                          {showResetNewPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className={labelCls}>Confirm Password</label>
                      <div className="relative">
                        <input type={showResetConfirmPassword ? 'text' : 'password'} placeholder="Confirm" value={resetConfirmPassword} onChange={(e) => setResetConfirmPassword(e.target.value)} required className={`${inputSmCls} pr-8`} />
                        <button type="button" onClick={() => setShowResetConfirmPassword(!showResetConfirmPassword)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-base-content/30 hover:text-base-content/60">
                          {showResetConfirmPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                      </div>
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full h-11 rounded-full bg-primary text-primary-content text-sm font-semibold shadow-md hover:brightness-110 active:scale-[0.98] transition-all disabled:opacity-60"
                  >
                    {loading ? <span className="loading loading-spinner loading-sm" /> : 'Reset Password'}
                  </button>
                </form>
              )}
            </div>
          )}

        </div>
      </div>

      {/* Keyframe animations for floating logos */}
      <style>{`
        @keyframes loginFloat0 {
          0%, 100% { transform: translateY(0px) translateX(0px) rotate(0deg); }
          25%      { transform: translateY(-28px) translateX(12px) rotate(6deg); }
          50%      { transform: translateY(-8px) translateX(-10px) rotate(-4deg); }
          75%      { transform: translateY(-22px) translateX(6px) rotate(3deg); }
        }
        @keyframes loginFloat1 {
          0%, 100% { transform: translateY(0px) translateX(0px) rotate(0deg); }
          20%      { transform: translateY(-18px) translateX(-14px) rotate(-5deg); }
          50%      { transform: translateY(-32px) translateX(8px) rotate(7deg); }
          80%      { transform: translateY(-12px) translateX(-6px) rotate(-3deg); }
        }
        @keyframes loginFloat2 {
          0%, 100% { transform: translateY(0px) translateX(0px) rotate(0deg); }
          30%      { transform: translateY(-24px) translateX(16px) rotate(8deg); }
          60%      { transform: translateY(-6px) translateX(-12px) rotate(-6deg); }
          85%      { transform: translateY(-20px) translateX(4px) rotate(2deg); }
        }
      `}</style>
    </div>
  );
};

export default Login;
