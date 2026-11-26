import { backendRequest } from '@/services/backendApi/httpClient';
import {
  applyRsiCandidate,
  authorizeRsiPromotion,
  cancelRsiRun,
  getRsiBindings,
  getRsiEvidence,
  isRsiTerminal,
  listRsiStrategies,
  putRsiBindings,
  submitRsiRun,
} from '@/services/rsi/service';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const scope = { botId: 'bot/with space', ownerId: 'owning-entity', actorId: 'acting-user' };
beforeEach(() => {
  request.mockReset();
  request.mockResolvedValue({ code: 200000, data: {} });
});
test('scope encodes Bot and uses resolved actor identity without automatic POST retries', async () => {
  await getRsiBindings(scope);
  expect(request).toHaveBeenCalledWith(
    '/openapi/v1/bots/bot%2Fwith%20space/evolution/bindings',
    expect.objectContaining({
      injectUserId: false,
      retryOnTransient: false,
      params: { entity_id: scope.ownerId, user_id: scope.actorId },
    }),
  );
});
test('ETag is quoted including the first empty ETag; idempotency key is caller-owned', async () => {
  await putRsiBindings(scope, { etag: '', bindings: [] });
  expect(request.mock.calls[0][1]).toMatchObject({
    method: 'PUT',
    headers: { 'If-Match': '""' },
    data: { bindings: [] },
  });
  request.mockResolvedValue({ code: 202000, data: { run: 'run1' } });
  expect(await submitRsiRun(scope, 'binding', 'same-attempt')).toEqual({ run: 'run1' });
  expect(request.mock.calls[1][1]).toMatchObject({
    method: 'POST',
    headers: { 'Idempotency-Key': 'same-attempt' },
    data: { binding_id: 'binding' },
  });
});
test('catalog uses the same Bot scope, evidence/cancellation encode run IDs', async () => {
  await listRsiStrategies(scope);
  expect(request.mock.calls[0][0]).toContain('/evolution/strategies');
  expect(request.mock.calls[0][1].params).toEqual({ entity_id: scope.ownerId, user_id: scope.actorId });
  await getRsiEvidence(scope, 'run/1');
  expect(request.mock.calls[1][0]).toContain('/runs/run%2F1/evidence');
  await cancelRsiRun(scope, 'run/1');
  expect(request.mock.calls[2][0]).toContain('/runs/run%2F1:cancel');
});
test.each([0, 200, 400000, NaN, undefined])('rejects unconfirmed/legacy envelope %s', async (code) => {
  request.mockResolvedValue({ code, data: {}, request_id: 'trace' });
  await expect(getRsiBindings(scope)).rejects.toThrow('trace');
});
test('completed is terminal but pending is not; no verdict-as-promotion inference', () => {
  for (const state of ['completed', 'failed', 'cancelled', 'budget_exhausted']) expect(isRsiTerminal(state)).toBe(true);
  expect(isRsiTerminal('running')).toBe(false);
  expect(isRsiTerminal('pending')).toBe(false);
});

test('manual authorization and Apply are distinct authenticated operations with no hidden retries', async () => {
  const body = { revision_id: 'sha256:target', parent_revision_id: 'sha256:base', reason: 'human reason' };
  await authorizeRsiPromotion(scope, 'sha256:candidate', 'explicit-key', body);
  expect(request.mock.calls[0][0]).toContain('/candidates/sha256%3Acandidate:authorize-promotion');
  expect(request.mock.calls[0][1]).toMatchObject({ method: 'POST', retryOnTransient: false,
    headers: { 'Idempotency-Key': 'explicit-key' }, data: body,
    params: { entity_id: scope.ownerId, user_id: scope.actorId } });
  expect(request).toHaveBeenCalledTimes(1);
  await applyRsiCandidate(scope, 'sha256:candidate', 'a'.repeat(32));
  expect(request.mock.calls[1][0]).toContain('/candidates/sha256%3Acandidate:apply');
  expect(request.mock.calls[1][1]).toMatchObject({ method: 'POST', retryOnTransient: false,
    data: { authorization_id: 'a'.repeat(32) } });
});
