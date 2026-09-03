/** @jest-environment jsdom */
import { notifyError } from '@/components/ui/notify';
import { useExternalAuth } from '@/hooks/useExternalAuth';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { navigateToUrl, reloadCurrentTab } from '@/utils/redirectCurrentTab';
import { act, renderHook, waitFor } from '@testing-library/react';

// authApiController 经 umi `request` 调用;本测试 mock @umijs/max request 控制契约。
jest.mock('@umijs/max', () => ({ request: jest.fn() }));
jest.mock('@/components/ui/notify');
jest.mock('@/utils/redirectCurrentTab');

import { request } from '@umijs/max';

const mockedRequest = request as jest.Mock;
const mockedNavigateToUrl = navigateToUrl as jest.MockedFunction<typeof navigateToUrl>;
const mockedReloadCurrentTab = reloadCurrentTab as jest.MockedFunction<typeof reloadCurrentTab>;
const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;

beforeEach(() => {
  useExternalAuthStore.getState().reset();
  mockedRequest.mockReset();
  mockedNavigateToUrl.mockClear();
  mockedReloadCurrentTab.mockClear();
  mockedNotifyError.mockClear();
});

afterEach(() => {
  useExternalAuthStore.getState().reset();
});

describe('useExternalAuth', () => {
  it('checkAuth 成功 → authenticated + user(toAuthUser 映射)', async () => {
    mockedRequest.mockResolvedValue({ user_id: 'u-1', name: 'Alice', provider: 'alipay', avatar: 'https://a' });
    const { result } = renderHook(() => useExternalAuth());

    let ok = false;
    await act(async () => {
      ok = await result.current.checkAuth();
    });

    expect(ok).toBe(true);
    const s = useExternalAuthStore.getState();
    expect(s.status).toBe('authenticated');
    expect(s.user).toEqual({ userId: 'u-1', displayName: 'Alice', provider: 'alipay', avatarUrl: 'https://a' });
  });

  it('checkAuth 401(axios 形 error.response.status) → unauthenticated,不 notify', async () => {
    mockedRequest.mockRejectedValue({ response: { status: 401, data: {} } });
    const { result } = renderHook(() => useExternalAuth());

    let ok = true;
    await act(async () => {
      ok = await result.current.checkAuth();
    });

    expect(ok).toBe(false);
    expect(useExternalAuthStore.getState().status).toBe('unauthenticated');
    expect(mockedNotifyError).not.toHaveBeenCalled();
  });

  it('checkAuth 401 携带 BCS 40100 信封体 → unauthenticated(status 判定不受信封影响)', async () => {
    mockedRequest.mockRejectedValue({
      response: {
        status: 401,
        data: {
          code: 40100,
          message: 'Authentication is required',
          data: { error_code: 'unauthenticated' },
          request_id: 'r',
        },
      },
    });
    const { result } = renderHook(() => useExternalAuth());

    let ok = true;
    await act(async () => {
      ok = await result.current.checkAuth();
    });

    expect(ok).toBe(false);
    expect(useExternalAuthStore.getState().status).toBe('unauthenticated');
  });

  it('checkAuth 成功(BCS 20000 信封)→ 解包 data 后 authenticated', async () => {
    mockedRequest.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { user_id: 'u-1', name: 'Alice', provider: 'alipay', avatar: 'https://a' },
      request_id: 'r',
    });
    const { result } = renderHook(() => useExternalAuth());

    let ok = false;
    await act(async () => {
      ok = await result.current.checkAuth();
    });

    expect(ok).toBe(true);
    expect(useExternalAuthStore.getState().user).toEqual({
      userId: 'u-1',
      displayName: 'Alice',
      provider: 'alipay',
      avatarUrl: 'https://a',
    });
  });

  it('checkAuth 非 401 错误 → error + notifyError', async () => {
    mockedRequest.mockRejectedValue({ response: { status: 500, data: { message: 'boom' } } });
    const { result } = renderHook(() => useExternalAuth());

    await act(async () => {
      await result.current.checkAuth();
    });

    expect(useExternalAuthStore.getState().status).toBe('error');
    expect(mockedNotifyError).not.toHaveBeenCalled(); // checkAuth 不 notify(仅 setAuthError)
    expect(useExternalAuthStore.getState().error).toBe('boom');
  });

  it('loadLoginUrl 成功 → providers[0].url', async () => {
    mockedRequest.mockResolvedValue({
      providers: [{ name: 'alipay', url: 'https://login.example/a' }],
    });
    const { result } = renderHook(() => useExternalAuth());

    let url: string | null = 'x';
    await act(async () => {
      url = await result.current.loadLoginUrl();
    });

    expect(url).toBe('https://login.example/a');
    expect(useExternalAuthStore.getState().loginUrl).toBe('https://login.example/a');
  });

  it('loadLoginUrl providers 空 → notifyError + 返回 null', async () => {
    mockedRequest.mockResolvedValue({ providers: [] });
    const { result } = renderHook(() => useExternalAuth());

    let url: string | null = 'x';
    await act(async () => {
      url = await result.current.loadLoginUrl();
    });

    expect(url).toBeNull();
    expect(useExternalAuthStore.getState().loginUrl).toBeNull();
    expect(mockedNotifyError).toHaveBeenCalledWith('登录地址获取失败，请稍后重试');
  });

  it('login 有 loginUrl → navigateToUrl(providerUrl)', async () => {
    useExternalAuthStore.getState().setLoginUrl('https://login.example/known');
    const { result } = renderHook(() => useExternalAuth());

    await act(async () => {
      await result.current.login();
    });

    expect(mockedRequest).not.toHaveBeenCalled(); // 不再拉 /auth/url
    expect(mockedNavigateToUrl).toHaveBeenCalledWith('https://login.example/known');
  });

  it('login 无 loginUrl → 先 loadLoginUrl 再 navigateToUrl', async () => {
    mockedRequest.mockResolvedValue({ providers: [{ name: 'alipay', url: 'https://login.example/fetched' }] });
    const { result } = renderHook(() => useExternalAuth());

    await act(async () => {
      await result.current.login();
    });

    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/auth/url', expect.objectContaining({ method: 'GET' }));
    expect(mockedNavigateToUrl).toHaveBeenCalledWith('https://login.example/fetched');
  });

  it('logout 成功 → reloadCurrentTab', async () => {
    mockedRequest.mockResolvedValue({});
    const { result } = renderHook(() => useExternalAuth());

    await act(async () => {
      await result.current.logout();
    });

    await waitFor(() => expect(mockedReloadCurrentTab).toHaveBeenCalled());
    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/auth/logout', expect.objectContaining({ method: 'POST' }));
  });
});
