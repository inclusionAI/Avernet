import {
  BOT_GRANT_RECONCILIATION_ENDPOINT,
  reconcileOwnedBotGrants,
} from '@/services/backendApi/collaboration/botGrantReconciliationController';

describe('reconcileOwnedBotGrants', () => {
  const fetchMock = jest.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    Object.defineProperty(globalThis, 'fetch', {
      configurable: true,
      value: fetchMock,
    });
  });

  it('posts an empty body for full-fleet reconciliation', async () => {
    fetchMock.mockResolvedValue({ ok: true });

    await reconcileOwnedBotGrants();

    expect(fetchMock).toHaveBeenCalledWith(BOT_GRANT_RECONCILIATION_ENDPOINT, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
  });

  it('posts one product Bot ID when creation completes', async () => {
    fetchMock.mockResolvedValue({ ok: true });

    await reconcileOwnedBotGrants('bot-1');

    expect(fetchMock).toHaveBeenCalledWith(
      BOT_GRANT_RECONCILIATION_ENDPOINT,
      expect.objectContaining({ body: JSON.stringify({ bot_id: 'bot-1' }) }),
    );
  });

  it('silently resolves for non-2xx responses and transport failures', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 404 });
    await expect(reconcileOwnedBotGrants()).resolves.toBeUndefined();

    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(reconcileOwnedBotGrants('bot-1')).resolves.toBeUndefined();
  });
});
