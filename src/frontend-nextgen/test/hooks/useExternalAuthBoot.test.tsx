/** @jest-environment jsdom */
import { useExternalAuthBoot } from '@/hooks/useExternalAuthGuard';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { act, renderHook } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ request: jest.fn() }));
jest.mock('@/components/ui/notify');
jest.mock('@/utils/redirectCurrentTab');

import { request } from '@umijs/max';

const mockedRequest = request as jest.Mock;

beforeEach(() => {
  useExternalAuthStore.getState().reset();
  useLoginRedirectStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway'); // 默认内部（不 boot）
  mockedRequest.mockReset();
});

afterEach(() => {
  useExternalAuthStore.getState().reset();
  useLoginRedirectStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
});

describe('useExternalAuthBoot', () => {
  it('oauth-provider 策略 + 401 → (soft 1.5s) requestPrompt（全系统主动 boot）', async () => {
    jest.useFakeTimers();
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    mockedRequest.mockRejectedValue({ response: { status: 401, data: {} } });

    renderHook(() => useExternalAuthBoot());
    await act(async () => {}); // flush checkAuth → unauthenticated
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined(); // 未到 1.5s

    act(() => {
      jest.advanceTimersByTime(1500);
    });
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    jest.useRealTimers();
  });

  it('ace-gateway 策略 → 不调 checkAuth、不弹（内部走 ACE 反应式）', async () => {
    renderHook(() => useExternalAuthBoot());
    await act(async () => {});

    expect(mockedRequest).not.toHaveBeenCalled();
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });

  it('oauth-provider 策略 + 已登录 → 不弹、置 authenticated', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    mockedRequest.mockResolvedValue({ user_id: 'u', name: 'Alice', provider: 'alipay', avatar: null });

    renderHook(() => useExternalAuthBoot());
    await act(async () => {});

    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(useExternalAuthStore.getState().status).toBe('authenticated');
  });
});
