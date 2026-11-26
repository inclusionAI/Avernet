import { backendRequest } from '@/services/backendApi/httpClient';
import {
  captureGenomeRevision, getGenomeDiff, getGenomeDrift, getGenomePromotion, getGenomeRefs,
  isGenomePromotionTerminal, listGenomePromotions, promoteGenomeRevision,
} from '@/services/rsi/service';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
const scope = { botId: 'bot/1', ownerId: 'owner', actorId: 'actor' };
beforeEach(() => { request.mockReset(); request.mockResolvedValue({ code: 200000, data: {} }); });

test('all Genome operations use authenticated actual actor and owner scope, without retrying writes', async () => {
  await getGenomeRefs(scope); await getGenomeDrift(scope); await captureGenomeRevision(scope);
  await getGenomeDiff(scope, 'sha256:a/b', 'sha256:parent');
  await promoteGenomeRevision(scope, 'sha256:live', 'sha256:parent', 'owner reason');
  await getGenomePromotion(scope, 'promotion/1'); await listGenomePromotions(scope);
  expect(request.mock.calls.map(([path]) => path)).toEqual([
    '/openapi/v1/bots/bot%2F1/genome/refs', '/openapi/v1/bots/bot%2F1/genome/drift',
    '/openapi/v1/bots/bot%2F1/genome/revisions:capture',
    '/openapi/v1/bots/bot%2F1/genome/revisions/sha256%3Aa%2Fb/diff',
    '/openapi/v1/bots/bot%2F1/genome/promotions',
    '/openapi/v1/bots/bot%2F1/genome/promotions/promotion%2F1',
    '/openapi/v1/bots/bot%2F1/genome/promotions',
  ]);
  for (const [, options] of request.mock.calls) expect(options).toMatchObject({
    injectUserId: false, retryOnTransient: false, params: { entity_id: 'owner', user_id: 'actor' },
  });
  expect(request.mock.calls[3][1]?.params).toMatchObject({ against: 'sha256:parent' });
  expect(request.mock.calls[4][1]).toMatchObject({
    method: 'POST', data: { revision: 'sha256:live', expected_revision: 'sha256:parent', reason: 'owner reason' },
  });
  expect(request.mock.calls[4][1]?.headers).toBeUndefined(); // No invented idempotency contract.
});

test('partial delivery and failure are terminal, but terminal does not mean successfully reconciled', () => {
  expect(isGenomePromotionTerminal('pending')).toBe(false);
  expect(isGenomePromotionTerminal('applying')).toBe(false);
  expect(isGenomePromotionTerminal('applied_partial')).toBe(true);
  expect(isGenomePromotionTerminal('apply_failed')).toBe(true);
  expect(isGenomePromotionTerminal('applied')).toBe(true);
});
