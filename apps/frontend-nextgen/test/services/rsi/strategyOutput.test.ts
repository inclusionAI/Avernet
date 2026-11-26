import { backendRequest } from '@/services/backendApi/httpClient';
import { getRsiStrategyOutput, RSI_IDENTITY_MISSING } from '@/services/rsi/service';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
jest.mock('@/stores/identityStore', () => ({ useIdentityStore: { getState: () => ({ currentIdentityId: '' }) } }));
const request = jest.mocked(backendRequest);
beforeEach(() => jest.clearAllMocks());
test('uses authenticated Bot-scoped path and encodes Run ID without automatic retries', async () => {
  request.mockResolvedValue({ code: 200000, data: { output: null } });
  expect(await getRsiStrategyOutput({ botId: 'b/a', ownerId: 'entity', actorId: 'actor' }, 'run/a')).toEqual({
    output: null,
  });
  expect(request).toHaveBeenCalledWith(
    '/openapi/v1/bots/b%2Fa/evolution/runs/run%2Fa/strategy-output',
    expect.objectContaining({
      injectUserId: false,
      retryOnTransient: false,
      params: { entity_id: 'entity', user_id: 'actor' },
    }),
  );
});
test('missing identity refuses before network and errors are not converted to empty output', async () => {
  await expect(getRsiStrategyOutput({ botId: 'bot', ownerId: 'entity' }, 'run')).rejects.toThrow(RSI_IDENTITY_MISSING);
  expect(request).not.toHaveBeenCalled();
  request.mockRejectedValue(new Error('network'));
  await expect(getRsiStrategyOutput({ botId: 'bot', ownerId: 'entity', actorId: 'actor' }, 'run')).rejects.toThrow(
    'network',
  );
});
