import { backendRequest } from '@/services/backendApi/httpClient';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
jest.mock('@/stores/identityStore', () => ({ useIdentityStore: { getState: () => ({ currentIdentityId: 'actor' }) } }));
const original = global.fetch;
afterEach(() => {
  global.fetch = original;
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
