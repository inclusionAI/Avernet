/** @jest-environment jsdom */
import { useExternalAuthGuard } from '@/hooks/useExternalAuthGuard';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { act, renderHook } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ request: jest.fn() }));
jest.mock('@/components/ui/notify');
jest.mock('@/utils/redirectCurrentTab');

import { request } from '@umijs/max';

const mockedRequest = request as jest.Mock;

beforeEach(() => {
  useExternalAuthStore.getState().reset();
  useLoginRedirectStore.getState().reset();
  mockedRequest.mockReset();
});

afterEach(() => {
  useExternalAuthStore.getState().reset();
  useLoginRedirectStore.getState().reset();
});

describe('useExternalAuthGuard', () => {
  it('force 模式 + checkAuth 401 → 立即 requestPrompt + shouldBlockContent=true', async () => {
    mockedRequest.mockRejectedValue({ response: { status: 401, data: {} } });
    const { result } = renderHook(() => useExternalAuthGuard({ mode: 'force' }));

    await act(async () => {}); // flush checkAuth → status unauthenticated → force 立即触发

    expect(useExternalAuthStore.getState().status).toBe('unauthenticated');
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(result.current.shouldBlockContent).toBe(true);
    expect(result.current.isUnauthenticated).toBe(true);
  });

  it('soft 模式 + checkAuth 401 → 1.5s 后 requestPrompt;期间不触发、不阻断', async () => {
    jest.useFakeTimers();
    mockedRequest.mockRejectedValue({ response: { status: 401, data: {} } });
    renderHook(() => useExternalAuthGuard({ mode: 'soft' }));

    await act(async () => {}); // flush checkAuth → status unauthenticated
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined(); // 未到 1.5s

    act(() => {
      jest.advanceTimersByTime(1500);
    });
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    jest.useRealTimers();
  });

  it('checkAuth 已登录 → 不触发 prompt、不阻断', async () => {
    mockedRequest.mockResolvedValue({ user_id: 'u', name: 'Alice', provider: 'alipay', avatar: null });
    const { result } = renderHook(() => useExternalAuthGuard({ mode: 'force' }));

    await act(async () => {});

    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.shouldBlockContent).toBe(false);
  });

  it('enabled=false → 不 checkAuth、不提示', async () => {
    mockedRequest.mockRejectedValue({ response: { status: 401, data: {} } });
    renderHook(() => useExternalAuthGuard({ mode: 'force', enabled: false }));

    await act(async () => {});
    // enabled=false → 不调 checkAuth → status 仍 unknown → 不触发 prompt
    expect(mockedRequest).not.toHaveBeenCalled();
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });
});
