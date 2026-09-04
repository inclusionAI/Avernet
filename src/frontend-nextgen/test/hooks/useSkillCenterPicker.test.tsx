/** @jest-environment jsdom */
import { useSkillCenterPicker } from '@/hooks/useSkillCenterPicker';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { act, renderHook, waitFor } from '@testing-library/react';
jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: { searchSkillCenterSkills: jest.fn() },
}));
const search = jest.mocked(botEditorService.searchSkillCenterSkills);
afterEach(() => jest.resetAllMocks());
it('服务端搜索、翻页去重，关键词变化重置第一页', async () => {
  search
    .mockResolvedValueOnce({ items: [{ id: 'a', name: 'A', active: false }], hasMore: true })
    .mockResolvedValueOnce({
      items: [
        { id: 'a', name: 'A', active: false },
        { id: 'b', name: 'B', active: false },
      ],
      hasMore: false,
    })
    .mockResolvedValueOnce({ items: [{ id: 'c', name: '不含关键词但服务端命中', active: false }], hasMore: false });
  const { result, rerender } = renderHook(({ keyword }) => useSkillCenterPicker(true, keyword), {
    initialProps: { keyword: '' },
  });
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  await act(() => result.current.loadMore());
  expect(result.current.items.map((item) => item.id)).toEqual(['a', 'b']);
  expect(search).toHaveBeenNthCalledWith(2, '', 2);
  rerender({ keyword: ' remote ' });
  await waitFor(() => expect(result.current.items[0]?.id).toBe('c'));
  expect(search).toHaveBeenLastCalledWith('remote', 1);
});
it('搜索变化后丢弃在途旧请求', async () => {
  let resolveOld!: (value: { items: []; hasMore: boolean }) => void;
  search
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValueOnce({ items: [{ id: 'new', name: 'New', active: false }], hasMore: false });
  const { result, rerender } = renderHook(({ keyword }) => useSkillCenterPicker(true, keyword), {
    initialProps: { keyword: 'old' },
  });
  await waitFor(() => expect(search).toHaveBeenCalled());
  rerender({ keyword: 'new' });
  await waitFor(() => expect(result.current.items[0]?.id).toBe('new'));
  await act(async () => resolveOld({ items: [], hasMore: false }));
  expect(result.current.items[0].id).toBe('new');
});
it('首次和追加失败均可重试，追加失败保留已有结果且页码不跳过', async () => {
  search
    .mockRejectedValueOnce(new Error('first failed'))
    .mockResolvedValueOnce({ items: [{ id: 'a', name: 'A', active: false }], hasMore: true })
    .mockRejectedValueOnce(new Error('next failed'))
    .mockResolvedValueOnce({ items: [{ id: 'b', name: 'B', active: false }], hasMore: false });
  const { result } = renderHook(() => useSkillCenterPicker(true, ''));
  await waitFor(() => expect(result.current.error).toBe('first failed'));
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  await act(() => result.current.loadMore());
  expect(result.current.error).toBe('next failed');
  expect(result.current.items).toHaveLength(1);
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.items).toHaveLength(2));
  expect(result.current.error).toBe('');
  expect(search.mock.calls.map((call) => call[1])).toEqual([1, 1, 2, 2]);
});
