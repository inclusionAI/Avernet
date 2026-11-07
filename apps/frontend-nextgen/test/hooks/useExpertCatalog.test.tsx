/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useExpertCatalog, useExpertCatalogFeed } from '@/hooks/useExpertCatalog';
import { listExperts } from '@/services/expertMarketService';
import type { ExpertPage, ExpertQuery, ExpertSummary } from '@/domain/expertMarket';

jest.mock('@/services/expertMarketService', () => ({ listExperts: jest.fn() }));
const list = jest.mocked(listExperts);
const query: ExpertQuery = { keyword: '', category: '', sort: 'hot', page_num: 1, page_size: 20 };
const feedQuery = { keyword: '', category: '', sort: 'hot' as const, page_size: 20 };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

let expertSequence = 0;
function item(name: string): ExpertSummary {
  expertSequence += 1;
  return {
    expert_id: `e-${expertSequence}`, name, description: name, category: '产品', tags: [],
    creation_count: 0, first_added_at: '2026-10-27T00:00:00Z',
  };
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

test('feed 追加可在 total 口径变化时截停，失败时保留已载内容只报错', async () => {
  list
    .mockResolvedValueOnce({ total: 3, items: [item('a'), item('b')] })
    .mockRejectedValueOnce(new Error('追加失败'))
    .mockResolvedValueOnce({ total: 9, items: [item('c')] })
    .mockResolvedValueOnce({ total: 9, items: [] });
  const { result } = renderHook(() => useExpertCatalogFeed(feedQuery));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.items.map((e) => e.name)).toEqual(['a', 'b']);
  expect(result.current.hasMore).toBe(true);

  // 追加失败：不重置已载内容、不吞错，流头保持在断点页。
  act(() => result.current.loadMore());
  await waitFor(() => expect(result.current.loadMoreError).toBe('追加失败'));
  expect(result.current.items.map((e) => e.name)).toEqual(['a', 'b']);
  expect(result.current.loadingMore).toBe(false);

  // 断点重试成功后 total 口径变成 9（仍可继续）。
  act(() => result.current.loadMore());
  await waitFor(() => expect(result.current.items.map((e) => e.name)).toEqual(['a', 'b', 'c']));
  expect(result.current.total).toBe(9);
  expect(result.current.hasMore).toBe(true);

  // 服务端返回空页（口径漂移）时收敛 total，避免滚动加载空转。
  act(() => result.current.loadMore());
  await waitFor(() => expect(result.current.hasMore).toBe(false));
  expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page_num: 3 }), expect.anything());
});

test('feed 的筛选变化回到第 1 页，旧关键词的迟到响应不可落盘', async () => {
  const old = deferred<ExpertPage>();
  const fresh = deferred<ExpertPage>();
  list.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
  const { result, rerender } = renderHook((props) => useExpertCatalogFeed(props), { initialProps: feedQuery });
  rerender({ ...feedQuery, keyword: '产品' });
  await act(async () => fresh.resolve({ total: 1, items: [item('current')] }));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => old.resolve({ total: 23, items: [item('stale')] }));
  expect(result.current.items.map((e) => e.name)).toEqual(['current']);
  expect(result.current.total).toBe(1);
  // 重拉走的仍是第 1 页，而不是继承上一屏的断点页号。
  expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: '产品', page_num: 1 }), expect.anything());
});
