/** @jest-environment jsdom */
import { useExternalLoginPrompt } from '@/hooks/useExternalLoginPrompt';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { act, renderHook } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ request: jest.fn() }));

import { request } from '@umijs/max';

const mockedRequest = request as jest.Mock;

beforeEach(() => {
  mockedRequest.mockReset();
  mockedRequest.mockResolvedValue({
    code: 20000,
    data: { user_id: 'u', name: 'Alice', provider: 'github', avatar: null },
  });
  useExternalAuthStore.getState().reset();
  useLoginRedirectStore.getState().reset();
});

afterEach(() => {
  useExternalAuthStore.getState().reset();
  useLoginRedirectStore.getState().reset();
});

describe('useExternalLoginPrompt', () => {
  it('suppresses a late prompt after OAuth authentication has succeeded', async () => {
    useExternalAuthStore.getState().setAuthenticated({
      userId: 'u',
      displayName: 'Alice',
      provider: 'github',
    });
    act(() => {
      useLoginRedirectStore.getState().requestPrompt();
    });

    const { result } = renderHook(() => useExternalLoginPrompt());
    await act(async () => {});

    expect(useExternalAuthStore.getState().status).toBe('authenticated');
    expect(result.current.open).toBe(false);
  });

  it('keeps the prompt visible while authentication is checked', async () => {
    act(() => {
      useLoginRedirectStore.getState().requestPrompt();
    });
    const { result } = renderHook(() => useExternalLoginPrompt());

    expect(result.current.open).toBe(true);
    await act(async () => {});
    expect(result.current.open).toBe(true); // probe does not run from this hook
  });
});
