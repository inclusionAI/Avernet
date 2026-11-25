import { backendRequest } from '@/services/backendApi/httpClient';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
jest.mock('@/stores/identityStore', () => ({ useIdentityStore: { getState: () => ({ currentIdentityId: 'actor' }) } }));
const original = global.fetch;
afterEach(() => {
  global.fetch = original;
  useLoginRedirectStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
});
it('preserves same-origin credentials and ACE detection for limited text responses', async () => {
  useLoginRedirectStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
  global.fetch = jest
    .fn()
    .mockResolvedValue(
      new Response(
        JSON.stringify({
          actionType: 'LOGIN',
          buserviceErrorCode: 'USER_NOT_LOGIN',
          decisionBy: 'ACE',
          buserviceErrorMsg: 'https://login.example.com/',
        }),
        { headers: { 'content-type': 'application/json' } },
      ),
    );
  await expect(
    backendRequest('/api/example/raw', { responseType: 'text', maxResponseBytes: 2048, injectUserId: false }),
  ).rejects.toMatchObject({ name: 'AceLoginRedirectError' });
  expect(global.fetch).toHaveBeenCalledWith('/api/example/raw', expect.objectContaining({ credentials: 'include' }));
});
it('keeps JSON errors structured instead of returning them as preview text', async () => {
  global.fetch = jest
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: 'NO_ACCESS' } }), {
        status: 403,
        headers: { 'content-type': 'application/json' },
      }),
    );
  await expect(
    backendRequest('/api/example/raw', { responseType: 'text', maxResponseBytes: 2048, injectUserId: false }),
  ).rejects.toMatchObject({ status: 403, data: { detail: { code: 'NO_ACCESS' } } });
});
it('keeps successful JSON previews as raw text for responseType text', async () => {
  const body = JSON.stringify({ hello: '知识库预览' });
  global.fetch = jest
    .fn()
    .mockResolvedValue(new Response(body, { headers: { 'content-type': 'application/json' } }));
  await expect(
    backendRequest('/api/example/raw', { responseType: 'text', maxResponseBytes: 2048, injectUserId: false }),
  ).resolves.toBe(body);
});
it('still parses JSON bodies for bounded reads without explicit responseType', async () => {
  global.fetch = jest
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ code: 0, data: { ok: true } }), {
        headers: { 'content-type': 'application/json' },
      }),
    );
  await expect(
    backendRequest('/api/example/raw', { maxResponseBytes: 2048, injectUserId: false }),
  ).resolves.toEqual({ code: 0, data: { ok: true } });
});
it('fails fast when combining the preview byte limit with blob responses', async () => {
  global.fetch = jest.fn();
  await expect(
    backendRequest('/api/example/raw', { responseType: 'blob', maxResponseBytes: 2048, injectUserId: false }),
  ).rejects.toThrow('maxResponseBytes');
  expect(global.fetch).not.toHaveBeenCalled();
});
it('keeps 401 login handling ahead of the preview size limit', async () => {
  useLoginRedirectStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
  // 未登录网关常把 401 夹带大体积 HTML/文本体；预览大小限制不得吞掉登录处置。
  global.fetch = jest
    .fn()
    .mockResolvedValue(new Response('x'.repeat(256), { status: 401, headers: { 'content-length': '256' } }));
  await expect(
    backendRequest('/api/example/raw', { responseType: 'text', maxResponseBytes: 64, injectUserId: false }),
  ).rejects.toMatchObject({ name: 'AceLoginRedirectError' });
  expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
});
it('degrades oversized error bodies to status-only handling', async () => {
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
  global.fetch = jest
    .fn()
    .mockResolvedValue(new Response('x'.repeat(4096), { status: 403, headers: { 'content-length': '4096' } }));
  await expect(
    backendRequest('/api/example/raw', { responseType: 'text', maxResponseBytes: 64, injectUserId: false }),
  ).rejects.toMatchObject({ status: 403 });
});
