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
  it('ignores a stale-epoch 401 after a fresh OAuth round has succeeded', async () => {
    // 第一轮会话发出请求 → 回流重新登录成功（认证代次 +1）
    const staleEpoch = useExternalAuthStore.getState().authEpoch;
    useExternalAuthStore.getState().setAuthenticated({
      userId: 'u',
      displayName: 'Alice',
      provider: 'github',
    });
    act(() => {
      // 旧请求的 401 晚到：携带过期代次 → 信号被防线忽略，登录状态不被回翻转。
      useLoginRedirectStore.getState().requestPrompt(staleEpoch);
    });

    const { result } = renderHook(() => useExternalLoginPrompt());
    await act(async () => {});

    expect(useExternalAuthStore.getState().status).toBe('authenticated');
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(result.current.open).toBe(false);
  });

  it('re-opens the prompt when an expired session finally reports 401', async () => {
    // 长时间挂机：本地状态仍停留在会话过期前的 authenticated（陈旧快照）。
    useExternalAuthStore.getState().setAuthenticated({
      userId: 'u',
      displayName: 'Alice',
      provider: 'github',
    });
    const epoch = useExternalAuthStore.getState().authEpoch;
    const { result } = renderHook(() => useExternalLoginPrompt());
    await act(async () => {});
    expect(result.current.open).toBe(false); // 已认证视图下弹窗关闭

    // 会话在网关过期，当前代次请求返回 401：信号登记同时刷新登录事实。
    act(() => {
      useLoginRedirectStore.getState().requestPrompt(epoch);
    });
    await act(async () => {});

    expect(useExternalAuthStore.getState().status).toBe('unauthenticated');
    expect(result.current.open).toBe(true); // 回归锚：重新登录提示不再被旧的已认证快照屏蔽
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
