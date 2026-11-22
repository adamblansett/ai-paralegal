import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Trash2, UserPlus, Users, X } from 'lucide-react';
import { api } from '../api/client';
import type { AccessLevel, MatterMember, UserProfile } from '../types/api';

interface TeamAccessModalProps {
  open: boolean;
  matterId: string;
  matterTitle: string;
  currentUser: UserProfile;
  onClose: () => void;
}

export function TeamAccessModal({
  open,
  matterId,
  matterTitle,
  currentUser,
  onClose,
}: TeamAccessModalProps) {
  const [members, setMembers] = useState<MatterMember[]>([]);
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [selectedUserId, setSelectedUserId] = useState('');
  const [accessLevel, setAccessLevel] = useState<AccessLevel>('editor');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!open) return;
    setError(null);
    try {
      const [mList, uList] = await Promise.all([
        api.listMatterMembers(matterId),
        api.listUsers(),
      ]);
      setMembers(mList);
      setUsers(uList);
      const unassigned = uList.find((u) => !mList.some((m) => m.user_id === u.id));
      if (unassigned) {
        setSelectedUserId(unassigned.id);
      } else if (uList[0]) {
        setSelectedUserId(uList[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load matter team.');
    }
  }, [matterId, open]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  if (!open) return null;

  const isOwner = members.some(
    (m) => m.user_id === currentUser.id && m.access_level === 'owner'
  );
  const canManage = currentUser.role === 'partner' || isOwner;

  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    if (!selectedUserId) return;
    setLoading(true);
    setError(null);
    try {
      await api.addMatterMember(matterId, selectedUserId, accessLevel);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to grant access.');
    } finally {
      setLoading(false);
    }
  }

  async function handleRemove(userId: string) {
    setError(null);
    try {
      await api.removeMatterMember(matterId, userId);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove member.');
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
      <div className="w-full max-w-xl rounded-xl border border-slate-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-indigo-600" />
            <div>
              <h2 className="text-sm font-semibold text-slate-900">Matter Access Control</h2>
              <p className="text-[11px] text-slate-500">{matterTitle}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 p-5">
          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
              {error}
            </div>
          )}

          {canManage && (
            <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-2">
              <div className="flex-1 min-w-[200px]">
                <label className="block text-[11px] font-medium text-slate-700">
                  Assign Firm User
                </label>
                <select
                  value={selectedUserId}
                  onChange={(e) => setSelectedUserId(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                >
                  {users.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.full_name} ({u.role}) — {u.email}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-medium text-slate-700">
                  Matter Access
                </label>
                <select
                  value={accessLevel}
                  onChange={(e) => setAccessLevel(e.target.value as AccessLevel)}
                  className="mt-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                >
                  <option value="editor">Editor</option>
                  <option value="reviewer">Reviewer</option>
                  <option value="viewer">Viewer (Read-Only)</option>
                  <option value="owner">Owner</option>
                </select>
              </div>
              <button
                type="submit"
                disabled={loading || !selectedUserId}
                className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                <UserPlus className="h-3.5 w-3.5" />
                Assign
              </button>
            </form>
          )}

          <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
            {members.map((m) => (
              <div
                key={m.id}
                className="flex items-center justify-between px-3.5 py-2.5 text-xs"
              >
                <div>
                  <p className="font-semibold text-slate-900">
                    {m.user_full_name}{' '}
                    <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-600">
                      {m.user_role}
                    </span>
                  </p>
                  <p className="text-[11px] text-slate-500">{m.user_email}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-800">
                    {m.access_level}
                  </span>
                  {canManage && m.user_id !== currentUser.id && (
                    <button
                      type="button"
                      onClick={() => void handleRemove(m.user_id)}
                      className="rounded border border-rose-200 p-1 text-rose-600 hover:bg-rose-50"
                      title="Remove access"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
