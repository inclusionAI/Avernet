/** @jest-environment jsdom */
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useMessageDeliveries } from '@/pages/Workspace/hooks/useMessageDeliveries';
import { cancelQueuedDelivery } from '@/services/workspace/messageDeliveryService';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/workspace/messageDeliveryService');
const cancel = jest.mocked(cancelQueuedDelivery);
beforeEach(() => { jest.resetAllMocks(); });

type Outcome = Awaited<ReturnType<typeof cancelQueuedDelivery>>;
const emptyOutcome: Outcome = { results: [], firstError: null, clientMessageIds: [], canonicalMessageIds: [] };
const provider = () => ({ subscribeToDeliveryUpdates: () => () => {} }) as unknown as GroupChatProvider;
function deferredCancellation() {
  let resolve!: (outcome: Outcome) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Outcome>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

describe('useMessageDeliveries cancellation', () => {
  it('向当前会话回传服务端确认的关联 ID', async () => {
    cancel.mockResolvedValue({ results: [], firstError: null, clientMessageIds: ['client-1'], canonicalMessageIds: ['canonical-1'] });
    const remove = jest.fn();
    const { result } = renderHook(() => useMessageDeliveries(null, 'session-1', remove));
    await act(async () => { await result.current.cancelDelivery('message-1', 'delivery-1'); });
    expect(cancel).toHaveBeenCalledWith('message-1', 'delivery-1', 'session-1');
    expect(remove).toHaveBeenCalledWith(['client-1'], ['canonical-1']);
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
  });

  it('旧服务端没有关联 ID 时不按文本猜测删除', async () => {
    cancel.mockResolvedValue({ results: [], firstError: null, clientMessageIds: [], canonicalMessageIds: [] });
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
      resolve({ results: [], firstError: null, clientMessageIds: ['client-1'], canonicalMessageIds: ['canonical-1'] });
      await pending;
    });
    expect(remove).not.toHaveBeenCalled();
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
  });

  it('resets pending cancellation on provider replacement and permits retry after a late failure', async () => {
    const first = deferredCancellation();
    cancel.mockReturnValueOnce(first.promise).mockResolvedValue(emptyOutcome);
    const remove = jest.fn();
    const { result, rerender } = renderHook(({ current }) => useMessageDeliveries(current, 'session-1', remove), {
      initialProps: { current: provider() },
    });
    let pending!: Promise<void>;
    act(() => { pending = result.current.cancelDelivery('message-1', 'delivery-1'); });
    expect(result.current.cancellingDeliveryIds.has('delivery-1')).toBe(true);
    rerender({ current: provider() });
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
    await act(async () => { first.reject(new Error('network failure')); await pending; });
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
    await act(async () => { await result.current.cancelDelivery('message-1', 'delivery-1'); });
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(remove).not.toHaveBeenCalled();
  });

  it('ignores old-provider success without clearing a new cancellation of the same delivery', async () => {
    const first = deferredCancellation();
    const second = deferredCancellation();
    cancel.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const remove = jest.fn();
    const { result, rerender } = renderHook(({ current }) => useMessageDeliveries(current, 'session-1', remove), {
      initialProps: { current: provider() },
    });
    let oldPending!: Promise<void>, newPending!: Promise<void>;
    act(() => { oldPending = result.current.cancelDelivery('message-1', 'delivery-1'); });
    rerender({ current: provider() });
    act(() => { newPending = result.current.cancelDelivery('message-1', 'delivery-1'); });
    expect(cancel).toHaveBeenCalledTimes(2);
    await act(async () => {
      first.resolve({ ...emptyOutcome, clientMessageIds: ['old-client'], canonicalMessageIds: ['old-canonical'] });
      await oldPending;
    });
    expect(remove).not.toHaveBeenCalled();
    expect(result.current.cancellingDeliveryIds.has('delivery-1')).toBe(true);
    await act(async () => {
      second.resolve({ ...emptyOutcome, clientMessageIds: ['current-client'], canonicalMessageIds: ['current-canonical'] });
      await newPending;
    });
    expect(remove).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith(['current-client'], ['current-canonical']);
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
  });

  it('allows concurrent cancellation of distinct deliveries for the same message', async () => {
    const response = deferredCancellation();
    cancel.mockReturnValue(response.promise);
    const { result } = renderHook(() => useMessageDeliveries(null, 'session-1'));
    let first!: Promise<void>, second!: Promise<void>;
    act(() => {
      first = result.current.cancelDelivery('message-1', 'delivery-a');
      second = result.current.cancelDelivery('message-1', 'delivery-b');
    });
    expect(cancel).toHaveBeenCalledTimes(2);
    expect([...result.current.cancellingDeliveryIds].sort()).toEqual(['delivery-a', 'delivery-b']);
    await act(async () => { response.resolve(emptyOutcome); await Promise.all([first, second]); });
    expect(result.current.cancellingDeliveryIds.size).toBe(0);
  });
});
