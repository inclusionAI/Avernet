import { backendRequest } from '@/services/backendApi/httpClient';
import {
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
const scope = { botId: 'bot/with space', ownerId: 'owning-entity' };
beforeEach(() => {
  request.mockReset();
  request.mockResolvedValue({ code: 200000, data: {} });
});
test('scope encodes Bot and uses shared system user injection without automatic POST retries', async () => {
  await getRsiBindings(scope);
  expect(request).toHaveBeenCalledWith(
    '/openapi/v1/bots/bot%2Fwith%20space/evolution/bindings',
    expect.objectContaining({ injectUserId: true, retryOnTransient: false, params: { entity_id: scope.ownerId } }),
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
  expect(request.mock.calls[0][0]).toContain("/evolution/strategies");
  expect(request.mock.calls[0][1].params).toEqual({ entity_id: scope.ownerId });
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
