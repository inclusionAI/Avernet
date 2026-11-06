/** @jest-environment jsdom */
import { useGroupMemberNames } from '@/assets/TaskPanel/hooks/useGroupMemberNames';
import { describe, expect, it, jest } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

// 未覆盖分支补充:单个群详情拉取失败被 catch 吞掉(失败静默降级),不影响其余群名册。

const jsonResponse = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => body }) as Response;

describe('useGroupMemberNames 失败静默降级', () => {
  it('其中一个群请求失败 → 该群静默缺失,其余群正常返回成员名', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          data: {
            group_id: 'g-ok',
            name: '正常群',
            status: 'active',
            participants: [{ actor_id: 'b1', actor_kind: 'bot', name: '甲', role: 'worker', mode: 'auto' }],
          },
        }),
      )
      .mockResolvedValueOnce(({ ok: false, status: 500 }) as Response);
    global.fetch = fetchMock;

    try {
      const { result } = renderHook(() => useGroupMemberNames(['g-ok', 'g-fail']));
      await waitFor(() => expect(result.current['g-ok']).toBeDefined());
      expect(result.current['g-ok']!.names).toEqual(['甲']);
      expect(result.current['g-ok']!.nameByActorId).toEqual({ b1: '甲' });
      // 失败群被 catch 吞掉 → 名册里无该键
      expect(Object.keys(result.current)).toEqual(['g-ok']);
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('空群列表 → 名册保持为空', async () => {
    const { result } = renderHook(() => useGroupMemberNames([]));
    expect(result.current).toEqual({});
  });

  it('群详情无有效成员(actor/name 全缺)→ 不产名册条目', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ data: { group_id: 'g-bare', name: '', status: 'active', participants: [] } }),
    );
    global.fetch = fetchMock;

    try {
      const { result } = renderHook(() => useGroupMemberNames(['g-bare']));
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
      // toMemberInfo 对零成员群返回 null → 该群不进名册
      await waitFor(() => expect(result.current).toEqual({}));
    } finally {
      global.fetch = originalFetch;
    }
  });
});
