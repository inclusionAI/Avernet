/** @jest-environment jsdom */
import { useMessageDeliveries } from '@/pages/Workspace/hooks/useMessageDeliveries';
import { cancelQueuedDelivery } from '@/services/workspace/messageDeliveryService';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/workspace/messageDeliveryService');
const cancel = jest.mocked(cancelQueuedDelivery);
beforeEach(() => { jest.resetAllMocks(); });

describe('useMessageDeliveries cancellation', () => {
  it('向当前会话回传服务端确认的关联 ID', async () => {
    cancel.mockResolvedValue({ results: [], firstError: null, clientMessageIds: ['client-1'] });
    const remove = jest.fn();
    const { result } = renderHook(() => useMessageDeliveries(null, 'session-1', remove));
    await act(async () => { await result.current.cancelDelivery('message-1', 'delivery-1'); });
    expect(cancel).toHaveBeenCalledWith('message-1', 'delivery-1', 'session-1');
    expect(remove).toHaveBeenCalledWith(['client-1']);
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
  });

  it('旧服务端没有关联 ID 时不按文本猜测删除', async () => {
    cancel.mockResolvedValue({ results: [], firstError: null, clientMessageIds: [] });
    const remove = jest.fn();
    const { result } = renderHook(() => useMessageDeliveries(null, 'session-1', remove));
    await act(async () => { await result.current.cancelDelivery('message-1', 'delivery-1'); });
    expect(remove).not.toHaveBeenCalled();
  });

  it('切换会话后丢弃旧取消请求的迟到结果', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof cancelQueuedDelivery>>) => void;
    cancel.mockReturnValue(new Promise((done) => { resolve = done; }));
    const remove = jest.fn();
    const { result, rerender } = renderHook(({ session }) => useMessageDeliveries(null, session, remove), {
      initialProps: { session: 'session-1' },
    });
    let pending!: Promise<void>;
    act(() => { pending = result.current.cancelDelivery('message-1', 'delivery-1'); });
    rerender({ session: 'session-2' });
    await act(async () => {
      resolve({ results: [], firstError: null, clientMessageIds: ['client-1'] });
      await pending;
    });
    expect(remove).not.toHaveBeenCalled();
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
  });
});
