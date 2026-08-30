import { backendRequest } from '@/services/backendApi/httpClient';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/stores/identityStore', () => ({
  useIdentityStore: {
    getState: () => ({ currentIdentityId: 'human-1' }),
  },
}));

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
});

describe('backendRequest', () => {
  it('passes AbortSignal to fetch', async () => {
    const signal = new AbortController().signal;
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      headers: { get: () => 'application/json' },
      json: async () => ({ code: 200000, data: { items: [], total: 0 } }),
    } as unknown as Response);
    global.fetch = fetchMock;

    await backendRequest('/openapi/v1/bots/catalog/search', {
      method: 'GET',
      params: { page: 1, page_size: 20 },
      injectUserId: false,
      signal,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/openapi/v1/bots/catalog/search?page=1&page_size=20',
      expect.objectContaining({ signal }),
    );
  });
});
