import React, { useState, useEffect, useRef } from 'react';
import { X, User as UserIcon, Mail, ShieldAlert, Key, Upload, CheckCircle2, AlertTriangle, HelpCircle } from 'lucide-react';
import { authApi } from '../src/services/api';
import { Toast, Confirm } from '../src/utils/swal';
import type { User } from '../types';

interface UserProfileModalProps {
  user: User;
  onClose: () => void;
  onUpdateUser: (updatedUser: User) => void;
}

const PRESETS = ['avatar-1', 'avatar-2', 'avatar-3', 'avatar-4'];

const UserProfileModal: React.FC<UserProfileModalProps> = ({ user, onClose, onUpdateUser }) => {
  const [fullName, setFullName] = useState(user.fullName || '');
  const [email, setEmail] = useState(user.email || '');
  const [avatar, setAvatar] = useState(user.avatar || '');
  const [securityQuestion, setSecurityQuestion] = useState('');
  const [securityAnswer, setSecurityAnswer] = useState('');
  
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch security question on load
  useEffect(() => {
    const fetchMe = async () => {
      try {
        const me = await authApi.me();
        setFullName(me.fullName || '');
        setEmail(me.email || '');
        setAvatar(me.avatar || '');
      } catch (err: any) {
        console.error('Failed to fetch full profile details:', err);
      }
    };
    fetchMe();
  }, []);

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

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (email && !email.includes('@')) {
      Toast.error('Validation Error', 'Please enter a valid email address');
      return;
    }

    if (newPassword) {
      if (!currentPassword) {
        Toast.error('Validation Error', 'Current password is required to change password');
        return;
      }
      if (newPassword !== confirmNewPassword) {
        Toast.error('Validation Error', 'New passwords do not match');
        return;
      }
      if (newPassword.length < 6 || !/[a-zA-Z]/.test(newPassword) || !/[0-9]/.test(newPassword)) {
        Toast.error('Weak Password', 'New password must be at least 6 characters and contain letters and numbers');
        return;
      }
    }

    if (newPassword || (securityQuestion && securityAnswer)) {
      const ok = await Confirm.ask(
        'Update account security?',
        newPassword
          ? 'This will change your account password and any supplied recovery question details.'
          : 'This will update your account recovery question details.',
      );
      if (!ok) return;
    }

    setLoading(true);
    try {
      const payload: any = {
        full_name: fullName,
        email: email,
        avatar: avatar,
      };

      if (securityQuestion && securityAnswer) {
        payload.security_question = securityQuestion;
        payload.security_answer = securityAnswer;
      }

      if (newPassword) {
        payload.current_password = currentPassword;
        payload.new_password = newPassword;
      }

      await authApi.updateProfile(payload);
      
      // Update state in App
      onUpdateUser({
        ...user,
        fullName: fullName,
        email: email,
        avatar: avatar,
      });

      Toast.success('Profile Saved', 'Your account profile has been successfully updated');
      onClose();
    } catch (err: any) {
      Toast.error('Update Failed', err.message || 'An error occurred during updating profile');
    } finally {
      setLoading(false);
    }
  };

  const isPresetAvatar = (avatarStr: string) => PRESETS.includes(avatarStr);

  const getAvatarRender = () => {
    if (!avatar) {
      return (
        <div className="w-20 h-20 rounded-full bg-neutral text-neutral-content flex items-center justify-center text-3xl font-bold border border-base-content/10">
          {user.username.charAt(0).toUpperCase()}
        </div>
      );
    }
    if (isPresetAvatar(avatar)) {
      return (
        <div className="w-20 h-20 rounded-full bg-primary/20 text-primary flex items-center justify-center border border-primary/30 shadow-inner">
          <UserIcon className="w-10 h-10" />
        </div>
      );
    }
    return (
      <img
        src={avatar}
        alt="Profile"
        className="w-20 h-20 rounded-full object-cover border border-base-content/20 shadow-md"
      />
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      {/* Premium Glass Container */}
      <div className="relative w-full max-w-2xl bg-base-100/90 border border-base-content/10 rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        
        {/* Animated Glow in background */}
        <div className="absolute top-0 right-0 w-64 h-64 bg-primary/10 rounded-full blur-3xl pointer-events-none -mr-20 -mt-20" />
        <div className="absolute bottom-0 left-0 w-64 h-64 bg-secondary/15 rounded-full blur-3xl pointer-events-none -ml-20 -mb-20" />

        {/* Modal Header */}
        <div className="relative z-10 flex items-center justify-between px-6 py-4 border-b border-base-content/10 shrink-0">
          <div>
            <h3 className="font-extrabold text-lg text-base-content">Profile Settings</h3>
            <p className="text-xs opacity-60">Manage your CogniShield credentials and profile information</p>
          </div>
          <button onClick={onClose} className="btn btn-sm btn-circle btn-ghost hover:bg-base-200">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="relative z-10 p-6 overflow-y-auto grow space-y-6">
          <form onSubmit={handleSaveProfile} className="space-y-6">
            
            {/* Avatar Selection Section */}
            <div className="flex flex-col md:flex-row items-center gap-6 bg-base-200/50 p-5 rounded-2xl border border-base-content/5">
              <div className="flex flex-col items-center shrink-0">
                {getAvatarRender()}
                <span className="text-[10px] uppercase tracking-wider font-semibold opacity-50 mt-2">Current Image</span>
              </div>
              
              <div className="grow space-y-3 w-full">
                <span className="text-xs font-bold uppercase tracking-wider text-base-content/75 block">Upload Custom Picture or Select Preset</span>
                
                {/* Upload Area */}
                <div 
                  className={`border-2 border-dashed rounded-xl p-4 text-center cursor-pointer transition-all flex flex-col items-center justify-center gap-1.5 ${
                    dragOver ? 'border-primary bg-primary/5' : 'border-base-content/20 hover:border-primary/50'
                  }`}
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                >
                  <Upload className="w-5 h-5 opacity-60 text-primary" />
                  <span className="text-xs font-semibold">Drag & drop picture here, or <span className="text-primary hover:underline">browse</span></span>
                  <span className="text-[10px] opacity-50">Supports PNG, JPG (Max 1MB)</span>
                  <input 
                    type="file" 
                    ref={fileInputRef} 
                    className="hidden" 
                    accept="image/*" 
                    onChange={handleFileChange} 
                  />
                </div>

                {/* Presets List */}
                <div className="flex gap-2 justify-center md:justify-start">
                  {PRESETS.map((p, idx) => (
                    <button
                      key={p}
                      type="button"
                      className={`btn btn-xs rounded-lg px-2.5 ${avatar === p ? 'btn-primary' : 'btn-outline btn-ghost'}`}
                      onClick={() => setAvatar(p)}
                    >
                      Preset {idx + 1}
                    </button>
                  ))}
                  {avatar && (
                    <button
                      type="button"
                      className="btn btn-xs btn-outline btn-error rounded-lg"
                      onClick={() => setAvatar('')}
                    >
                      Clear
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Profile Info fields */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs font-bold text-base-content/70">Full Name</span></label>
                <div className="relative">
                  <UserIcon className="absolute left-3.5 top-3.5 w-4 h-4 opacity-50" />
                  <input
                    type="text"
                    className="input input-bordered w-full pl-10 rounded-xl text-sm"
                    placeholder="Enter full name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
                </div>
              </div>

              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs font-bold text-base-content/70">Email Address</span></label>
                <div className="relative">
                  <Mail className="absolute left-3.5 top-3.5 w-4 h-4 opacity-50" />
                  <input
                    type="email"
                    className="input input-bordered w-full pl-10 rounded-xl text-sm"
                    placeholder="name@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
              </div>
            </div>

            {/* Security Question Config */}
            <div className="collapse bg-base-200/40 border border-base-content/10 rounded-2xl">
              <input type="checkbox" /> 
              <div className="collapse-title text-sm font-bold flex items-center gap-2 py-3 px-4">
                <HelpCircle className="w-4 h-4 text-warning" />
                Configure Password Reset Security Question
              </div>
              <div className="collapse-content px-4 pb-4 space-y-4">
                <div className="form-control">
                  <label className="label py-1"><span className="label-text text-xs font-semibold">Security Question</span></label>
                  <select 
                    className="select select-bordered w-full rounded-xl text-sm"
                    value={securityQuestion}
                    onChange={(e) => setSecurityQuestion(e.target.value)}
                  >
                    <option value="">Select a security question...</option>
                    <option value="What was the name of your first pet?">What was the name of your first pet?</option>
                    <option value="In what city were you born?">In what city were you born?</option>
                    <option value="What is your favorite book?">What is your favorite book?</option>
                    <option value="What was your childhood nickname?">What was your childhood nickname?</option>
                  </select>
                </div>
                <div className="form-control">
                  <label className="label py-1"><span className="label-text text-xs font-semibold">Answer</span></label>
                  <input
                    type="password"
                    className="input input-bordered w-full rounded-xl text-sm"
                    placeholder="Enter security question answer (write-only)"
                    value={securityAnswer}
                    onChange={(e) => setSecurityAnswer(e.target.value)}
                  />
                  <span className="text-[10px] opacity-50 mt-1 flex items-center gap-1">
                    <ShieldAlert className="w-3 h-3 text-warning" /> Saved securely using cryptographic hashing. Used to reset password if locked out.
                  </span>
                </div>
              </div>
            </div>

            {/* Password Change config */}
            <div className="collapse bg-base-200/40 border border-base-content/10 rounded-2xl">
              <input type="checkbox" /> 
              <div className="collapse-title text-sm font-bold flex items-center gap-2 py-3 px-4">
                <Key className="w-4 h-4 text-primary" />
                Change Password
              </div>
              <div className="collapse-content px-4 pb-4 space-y-4">
                <div className="form-control">
                  <label className="label py-1"><span className="label-text text-xs font-semibold">Current Password</span></label>
                  <input
                    type="password"
                    className="input input-bordered w-full rounded-xl text-sm"
                    placeholder="Enter current password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                  />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="form-control">
                    <label className="label py-1"><span className="label-text text-xs font-semibold">New Password</span></label>
                    <input
                      type="password"
                      className="input input-bordered w-full rounded-xl text-sm"
                      placeholder="Enter new password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                    />
                  </div>
                  <div className="form-control">
                    <label className="label py-1"><span className="label-text text-xs font-semibold">Confirm New Password</span></label>
                    <input
                      type="password"
                      className="input input-bordered w-full rounded-xl text-sm"
                      placeholder="Repeat new password"
                      value={confirmNewPassword}
                      onChange={(e) => setConfirmNewPassword(e.target.value)}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Form Actions */}
            <div className="flex gap-3 justify-end pt-2 border-t border-base-content/10">
              <button type="button" onClick={onClose} className="btn btn-ghost rounded-xl">
                Cancel
              </button>
              <button type="submit" className="btn btn-primary px-6 rounded-xl min-w-28" disabled={loading}>
                {loading ? <span className="loading loading-spinner" /> : 'Save Changes'}
              </button>
            </div>

          </form>
        </div>

      </div>
    </div>
  );
};

export default UserProfileModal;
