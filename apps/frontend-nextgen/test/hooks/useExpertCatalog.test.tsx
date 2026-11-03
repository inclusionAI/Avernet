/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useExpertCatalog } from '@/hooks/useExpertCatalog';
import { listExperts } from '@/services/expertMarketService';
import type { ExpertPage, ExpertQuery } from '@/domain/expertMarket';

jest.mock('@/services/expertMarketService', () => ({ listExperts: jest.fn() }));
const list = jest.mocked(listExperts);
const query: ExpertQuery = { keyword: '', category: '', sort: 'hot', page_num: 1, page_size: 20 };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

test('a late response for an old keyword cannot replace the current catalog', async () => {
  const old = deferred<ExpertPage>();
  const current = deferred<ExpertPage>();
  list.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  const { result, rerender } = renderHook((props) => useExpertCatalog(props), { initialProps: query });
  rerender({ ...query, keyword: '产品' });
  await act(async () => current.resolve({ total: 0, items: [] }));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => old.resolve({ total: 23, items: [] }));
  expect(result.current.page.total).toBe(0);
  expect(result.current.error).toBe('');
});
