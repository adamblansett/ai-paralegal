import { useState, type FormEvent } from 'react';
import { Lock, Scale, ShieldCheck, UserPlus } from 'lucide-react';
import { api, tokenStore } from '../api/client';
import type { UserProfile, UserRole } from '../types/api';

interface AuthGateProps {
  onAuthenticated: (user: UserProfile) => void;
}

const DEMO_ACCOUNTS: Array<{
  label: string;
  role: UserRole;
  email: string;
  desc: string;
}> = [
  {
    label: 'Partner',
    role: 'partner',
    email: 'partner@firm.example.com',
    desc: 'All matters • Approve & manage team',
  },
  {
    label: 'Associate',
    role: 'associate',
    email: 'associate@firm.example.com',
    desc: 'Assigned matters • Approve & redline',
  },
  {
    label: 'Paralegal',
    role: 'paralegal',
    email: 'paralegal@firm.example.com',
    desc: 'Assigned matters • Ingest, analyze & flag',
  },
  {
    label: 'Auditor',
    role: 'auditor',
    email: 'auditor@firm.example.com',
    desc: 'Read-only compliance & audit logs',
  },
];

export function AuthGate({ onAuthenticated }: AuthGateProps) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('partner@firm.example.com');
  const [password, setPassword] = useState('ParalegalDemo2026!');
  const [fullName, setFullName] = useState('');
  const [role, setRole] = useState<UserRole>('associate');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submitLogin(loginEmail: string, loginPass: string) {
    setError(null);
    setLoading(true);
    try {
      const res = await api.login(loginEmail, loginPass);
      tokenStore.set(res.access_token);
      onAuthenticated(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed.');
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (mode === 'login') {
      await submitLogin(email, password);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const res = await api.register({
        email,
        full_name: fullName,
        password,
        role,
      });
      tokenStore.set(res.access_token);
      onAuthenticated(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 p-6">
      <div className="w-full max-w-md rounded-2xl border border-slate-700 bg-white p-7 shadow-2xl">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-600 text-white">
            <Scale className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-slate-900">AI Paralegal Workspace</h1>
            <p className="text-xs text-slate-500">
              Role-Based Access &amp; Cryptographic Attorney Attribution
            </p>
          </div>
        </div>

        <div className="mt-5 flex rounded-lg bg-slate-100 p-1 text-xs font-semibold">
          <button
            type="button"
            onClick={() => {
              setMode('login');
              setError(null);
            }}
            className={`flex-1 rounded-md py-1.5 transition ${
              mode === 'login' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600'
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => {
              setMode('register');
              setError(null);
            }}
            className={`flex-1 rounded-md py-1.5 transition ${
              mode === 'register' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600'
            }`}
          >
            Register Account
          </button>
        </div>

        {error && (
          <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-4 space-y-3.5">
          {mode === 'register' && (
            <>
              <div>
                <label className="block text-xs font-medium text-slate-700">Full Name *</label>
                <input
                  required
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Jordan Vance, Esq."
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700">Firm Role *</label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as UserRole)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                >
                  <option value="partner">Partner (Full Access &amp; Approvals)</option>
                  <option value="associate">Associate Attorney (Assigned Matters &amp; Approvals)</option>
                  <option value="paralegal">Paralegal (Ingest, Analyze &amp; Flag)</option>
                  <option value="auditor">Auditor (Read-Only Compliance)</option>
                </select>
              </div>
            </>
          )}

          <div>
            <label className="block text-xs font-medium text-slate-700">Firm Email *</label>
            <input
              required
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700">Password *</label>
            <input
              required
              type="password"
              minLength={mode === 'register' ? 8 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {mode === 'login' ? <Lock className="h-3.5 w-3.5" /> : <UserPlus className="h-3.5 w-3.5" />}
            {loading
              ? 'Authenticating...'
              : mode === 'login'
              ? 'Sign In to Workspace'
              : 'Create Account'}
          </button>
        </form>

        {mode === 'login' && (
          <div className="mt-5 border-t border-slate-200 pt-4">
            <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              <ShieldCheck className="h-3.5 w-3.5 text-indigo-600" />
              Quick Demo Role Sign-In (Dev Seeded)
            </p>
            <div className="mt-2.5 grid grid-cols-2 gap-2">
              {DEMO_ACCOUNTS.map((acct) => (
                <button
                  key={acct.role}
                  type="button"
                  disabled={loading}
                  onClick={() => {
                    setEmail(acct.email);
                    setPassword('ParalegalDemo2026!');
                    void submitLogin(acct.email, 'ParalegalDemo2026!');
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-2 text-left transition hover:border-indigo-400 hover:bg-indigo-50/50"
                >
                  <p className="text-xs font-semibold text-slate-900">{acct.label}</p>
                  <p className="mt-0.5 text-[10px] leading-snug text-slate-500">{acct.desc}</p>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
