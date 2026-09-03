/** @jest-environment node */
// 通道 B 默认兜底:backendRequest !ok → 投递 errorNotifyStore(带 toastKey)+ throw BackendRequestError(挂 toastKey/alreadyHandled)。
// injectUserId:false 避免注入 user_id 改变 url/query 导致 apiPath 不确定。
import { jest } from '@jest/globals';

import { AceLoginRedirectError, BackendRequestError, backendRequest } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';

interface FakeResponseInit {
  ok: boolean;
  status?: number;
  data: unknown;
  contentType?: string;
}

function fakeResponse({ ok, status = 200, data, contentType = 'application/json' }: FakeResponseInit): Response {
  const body = typeof data === 'string' ? data : JSON.stringify(data);
  return {
    ok,
    status,
    headers: { get: (k: string) => (k === 'content-type' ? contentType : null) },
    text: async () => body,
    json: async () => (typeof data === 'string' ? data : data),
    blob: async () => data,
  } as unknown as Response;
}

const originalFetch = global.fetch;

beforeEach(() => {
  useErrorNotifyStore.getState().reset();
  useLoginRedirectStore.getState().reset();
});

afterEach(() => {
  global.fetch = originalFetch;
  jest.restoreAllMocks();
});

describe('backendRequest 失败默认提示投递(通道 B,global-error-notify-dedup)', () => {
  it('!ok → 投递 errorNotifyStore(带 apiPath/operation/toastKey)+ throw BackendRequestError', async () => {
    global.fetch = jest.fn(async () =>
      fakeResponse({ ok: false, status: 502, data: { message: '服务器错误' } }),
    ) as unknown as typeof fetch;

    await expect(
      backendRequest('/openapi/v1/bots/spaces/create', {
        method: 'POST',
        operation: 'create-skill',
        injectUserId: false,
      }),
    ).rejects.toBeInstanceOf(BackendRequestError);

    const queue = useErrorNotifyStore.getState().queue;
    expect(queue).toHaveLength(1);
    const [item] = queue;
    expect(item.apiPath).toContain('/openapi/v1/bots/spaces/create');
    expect(item.operation).toBe('create-skill');
    expect(item.toastKey).toBe(`req:${item.apiPath}:create-skill`);
    expect(item.message).toBe('服务器错误');
    expect(item.cancelled).toBeUndefined();
  });

  it('抛出的 BackendRequestError 携带 toastKey 与 alreadyHandled=true,供 Hook 守卫去重', async () => {
    global.fetch = jest.fn(async () =>
      fakeResponse({ ok: false, status: 403, data: { message: '无权限' } }),
    ) as unknown as typeof fetch;

    let err: unknown;
    try {
      await backendRequest('/api/x', { operation: 'op', injectUserId: false });
    } catch (e) {
      err = e;
    }

    expect(err).toBeInstanceOf(BackendRequestError);
    const e = err as BackendRequestError;
    expect(e.toastKey).toBe('req:/api/x:op');
    expect(e.alreadyHandled).toBe(true);
    expect(e.message).toBe('无权限');
  });

  it('成功路径不投递 errorNotifyStore', async () => {
    global.fetch = jest.fn(async () =>
      fakeResponse({ ok: true, status: 200, data: { code: 200000, data: { id: 1 } } }),
    ) as unknown as typeof fetch;

    const res = await backendRequest('/api/x', { injectUserId: false });
    expect(res).toEqual({ code: 200000, data: { id: 1 } });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('无 operation 时 toastKey 用 apiPath + message 哈希兜底', async () => {
    global.fetch = jest.fn(async () =>
      fakeResponse({ ok: false, status: 500, data: { message: 'boom' } }),
    ) as unknown as typeof fetch;

    try {
      await backendRequest('/api/y', { injectUserId: false });
    } catch {
      /* swallow */
    }

    const key = useErrorNotifyStore.getState().queue[0].toastKey;
    expect(key).toMatch(/^req:\/api\/y:[0-9a-f]+$/);
  });

  it('同 operation 重复失败 toastKey 稳定一致(去重键可缓存)', async () => {
    global.fetch = jest.fn(async () =>
      fakeResponse({ ok: false, status: 500, data: { message: 'boom' } }),
    ) as unknown as typeof fetch;

    try {
      await backendRequest('/api/z', { operation: 'op', injectUserId: false });
    } catch {
      /* swallow */
    }
    try {
      await backendRequest('/api/z', { operation: 'op', injectUserId: false });
    } catch {
      /* swallow */
    }

    const keys = useErrorNotifyStore.getState().queue.map((i) => i.toastKey);
    expect(keys).toEqual(['req:/api/z:op', 'req:/api/z:op']);
  });

  it('多源失败(同接口不同 operation)toastKey 不同,不被误合并', async () => {
    global.fetch = jest.fn(async () =>
      fakeResponse({ ok: false, status: 500, data: { message: 'boom' } }),
    ) as unknown as typeof fetch;

    try {
      await backendRequest('/api/z', { operation: 'create', injectUserId: false });
    } catch {
      /* swallow */
    }
    try {
      await backendRequest('/api/z', { operation: 'edit', injectUserId: false });
    } catch {
      /* swallow */
    }

    const keys = useErrorNotifyStore.getState().queue.map((i) => i.toastKey);
    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it('ACE 登录体路径抛 AceLoginRedirectError 且不投递默认提示(不被默认提示打断)', async () => {
    const aceBody = {
      actionType: 'LOGIN',
      buserviceErrorCode: 'USER_NOT_LOGIN',
      decisionBy: 'ACE',
      buserviceErrorMsg: 'https://login.example/x',
    };
    global.fetch = jest.fn(async () =>
      fakeResponse({ ok: true, status: 200, data: aceBody }),
    ) as unknown as typeof fetch;

    await expect(backendRequest('/api/any', { injectUserId: false })).rejects.toBeInstanceOf(AceLoginRedirectError);
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });
});
