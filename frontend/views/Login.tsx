import React, { useState } from 'react';
import type { User } from '../types';
import { Lock, User as UserIcon, Shield } from 'lucide-react';

interface LoginProps {
    onLogin: (user: User) => void;
}

const Login: React.FC<LoginProps> = ({ onLogin }) => {
    const [username, setUsername] = useState('admin');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleLogin = (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError('');

        // Mock Authentication Logic
        setTimeout(() => {
            if (username === 'admin' && password === 'admin') {
                onLogin({
                    username: 'admin',
                    role: 'admin',
                    lastLogin: new Date().toISOString()
                });
            } else if (username === 'operator' && password === 'op') {
                onLogin({
                    username: 'operator',
                    role: 'operator',
                    lastLogin: new Date().toISOString()
                });
            } else {
                setError('Invalid credentials. Try admin/admin');
                setLoading(false);
            }
        }, 800);
    };

    return (
        <div className="flex items-center justify-center h-full w-full bg-base-300 relative overflow-hidden">
            {/* Background Decoration */}
            <div className="absolute top-0 left-0 w-full h-full opacity-10 pointer-events-none">
                 <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-primary rounded-full blur-[120px]"></div>
                 <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-secondary rounded-full blur-[120px]"></div>
            </div>

            <div className="card w-96 bg-base-100/90 backdrop-blur shadow-2xl z-10">
                <div className="card-body">
                    <div className="flex justify-center mb-4">
                        <div className="p-4 bg-gradient-to-tr from-primary to-secondary rounded-xl text-white shadow-lg">
                            <Shield className="w-8 h-8" />
                        </div>
                    </div>
                    <h2 className="card-title justify-center text-2xl font-bold mb-1">CogniShield</h2>
                    <p className="text-center text-xs text-base-content/60 mb-6 uppercase tracking-wider">Secure Network Operations</p>
                    
                    <form onSubmit={handleLogin} className="space-y-4">
                        <div className="form-control">
                            <label className="label">
                                <span className="label-text font-medium">Username</span>
                            </label>
                            <label className="input input-bordered flex items-center gap-2">
                                <UserIcon className="w-4 h-4 opacity-70" />
                                <input 
                                    type="text" 
                                    className="grow" 
                                    placeholder="admin" 
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
                                    placeholder="••••••" 
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                />
                            </label>
                        </div>
                        
                        {error && (
                            <div className="alert alert-error text-xs py-2 mt-2 rounded-lg">
                                <svg xmlns="http://www.w3.org/2000/svg" className="stroke-current shrink-0 h-4 w-4" fill="none" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                                <span>{error}</span>
                            </div>
                        )}

                        <div className="form-control mt-6">
                            <button type="submit" className="btn btn-primary" disabled={loading}>
                                {loading ? <span className="loading loading-spinner"></span> : 'Sign In'}
                            </button>
                        </div>
                    </form>
                    
                    <div className="divider text-xs opacity-50">Authorized Access Only</div>
                    <div className="text-xs text-center text-base-content/40 bg-base-200 p-2 rounded">
                        <span className="block font-mono">admin / admin</span>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default Login;