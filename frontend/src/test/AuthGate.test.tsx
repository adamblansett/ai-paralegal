import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AuthGate } from '../components/AuthGate';
import { tokenStore } from '../api/client';

describe('AuthGate', () => {
  it('stores access and refresh tokens on demo login', async () => {
    const user = userEvent.setup();
    const onAuth = vi.fn();

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: 'jwt-access-123',
        refresh_token: 'refresh-tok-456',
        expires_in: 28800,
        token_type: 'bearer',
        user: {
          id: 'u-1',
          email: 'partner@firm.example.com',
          full_name: 'Managing Partner',
          role: 'partner',
          is_active: true,
          created_at: new Date().toISOString(),
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<AuthGate onAuthenticated={onAuth} />);
    await user.click(screen.getByRole('button', { name: /Partner/i }));

    await waitFor(() => expect(onAuth).toHaveBeenCalledTimes(1));
    expect(tokenStore.get()).toBe('jwt-access-123');
    expect(tokenStore.getRefresh()).toBe('refresh-tok-456');

    vi.unstubAllGlobals();
  });
});
