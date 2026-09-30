/** @jest-environment jsdom */
// useUserSearch：封装 getUserSearchCapability + 防抖 + 状态。覆盖不支持降级 / 最小长度 / 正常搜索 / 失败态。
import { extendCapabilities } from '@/capabilities';
import { useUserSearch } from '@/hooks/useUserSearch';
import { renderHook, waitFor } from '@testing-library/react';

const mockSearch = jest.fn();

beforeEach(() => {
  mockSearch.mockReset();
});

it('capability 不支持（Open Core 默认）→ supported=false 且不发起搜索', () => {
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'unsupported', value: null, reason: 'no directory' }),
  });
  const { result } = renderHook(() => useUserSearch('张三'));
  expect(result.current.supported).toBe(false);
  expect(result.current.results).toEqual([]);
  expect(mockSearch).not.toHaveBeenCalled();
});

it('关键词短于 minLength 时不发起搜索', () => {
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
  const { result } = renderHook(() => useUserSearch('张'));
  expect(result.current.supported).toBe(true);
  expect(mockSearch).not.toHaveBeenCalled();
  expect(result.current.results).toEqual([]);
});

it('关键词 >=2 且防抖后调 search 并返回映射结果', async () => {
  mockSearch.mockResolvedValue([{ userId: '10086', displayName: '寻三', nickName: '寻三' }]);
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
  const { result } = renderHook(() => useUserSearch('寻三', { debounceMs: 0 }));
  await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('寻三'));
  await waitFor(() => expect(result.current.results).toHaveLength(1));
  expect(result.current.results[0]).toEqual({ userId: '10086', displayName: '寻三', nickName: '寻三' });
  expect(result.current.loading).toBe(false);
  expect(result.current.error).toBeNull();
});

it('search 抛错 → 设置 error 且 results 为空（loading 复位）', async () => {
  mockSearch.mockRejectedValue(new Error('boom'));
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
  const { result } = renderHook(() => useUserSearch('寻三', { debounceMs: 0 }));
  await waitFor(() => expect(result.current.error).toBe('搜索失败，请重试'));
  expect(result.current.results).toEqual([]);
  expect(result.current.loading).toBe(false);
});

it('关键词清空后不发起搜索并清空结果', async () => {
  mockSearch.mockResolvedValue([{ userId: '10086', displayName: '寻三' }]);
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
  const { result, rerender } = renderHook(({ kw }: { kw: string }) => useUserSearch(kw, { debounceMs: 0 }), {
    initialProps: { kw: '寻三' },
  });
  await waitFor(() => expect(result.current.results).toHaveLength(1));
  rerender({ kw: '' });
  await waitFor(() => expect(result.current.results).toEqual([]));
});
