/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useFriendConnectionRequests } from '@/hooks/useFriendConnectionRequests';
import { friendConnectionRequestService as service } from '@/services/collaborationSquare/friendConnectionRequestService';
import type { FriendRequestItem, FriendRequestSnapshot } from '@/domain/collaborationSquare/friendRequests';
import { notifySuccess } from '@/components/ui/notify';

jest.mock('@/services/collaborationSquare/friendConnectionRequestService', () => ({
  friendConnectionRequestService: { load: jest.fn(), decide: jest.fn() },
}));
jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn() }));
const actor = { type: 'bot' as const, id: 'mine:owner' };
const item: FriendRequestItem = { requestId: 'r1', fromActor: { type: 'human', id: '7', name: '7' },
  toActor: { ...actor, name: '我的 Bot' }, status: 'pending' };
const snapshot: FriendRequestSnapshot = {
  received: { items: [item], total: 30, page: 1, pageSize: 20 },
  sent: { items: [], total: 0, page: 1, pageSize: 20 },
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
beforeEach(() => {
  jest.resetAllMocks();
  jest.mocked(service.load).mockResolvedValue(snapshot);
  jest.mocked(service.decide).mockResolvedValue({ ...item, status: 'approved' });
});

it('挂载加载双向页，分页保持另一个方向的页码', async () => {
  const { result } = renderHook(() => useFriendConnectionRequests(actor, jest.fn()));
  await waitFor(() => expect(result.current.data?.received.items).toHaveLength(1));
  act(() => result.current.goToPage('received', 2));
  await waitFor(() => expect(service.load).toHaveBeenLastCalledWith(actor, { sent: 1, received: 2 }, expect.any(AbortSignal)));
});

it.each(['accept', 'reject'] as const)('%s 期间禁用重复操作，确认后更新状态且刷新外层列表', async (decision) => {
  const action = deferred<FriendRequestItem>();
  jest.mocked(service.decide).mockReturnValue(action.promise);
  const changed = jest.fn();
  const { result } = renderHook(() => useFriendConnectionRequests(actor, changed));
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => { void result.current.decide('r1', decision); void result.current.decide('r1', decision); });
  expect(service.decide).toHaveBeenCalledTimes(1);
  expect(result.current.busyIds).toContain('r1');
  expect(result.current.data?.received.items[0].status).toBe('pending');
  const status = decision === 'accept' ? 'approved' : 'rejected';
  await act(async () => action.resolve({ ...item, status }));
  expect(result.current.data?.received.items[0].status).toBe(status);
  expect(result.current.busyIds).toEqual([]);
  expect(changed).toHaveBeenCalledTimes(1);
  expect(notifySuccess).toHaveBeenCalledTimes(1);
});

it('失败保留 pending 行并展示错误，允许重试；不伪造成功', async () => {
  jest.mocked(service.decide).mockRejectedValue(new Error('审批失败'));
  const changed = jest.fn();
  const { result } = renderHook(() => useFriendConnectionRequests(actor, changed));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => { await result.current.decide('r1', 'accept'); });
  expect(result.current.actionErrors.r1).toBe('审批失败');
  expect(result.current.data?.received.items[0].status).toBe('pending');
  expect(changed).not.toHaveBeenCalled();
  expect(notifySuccess).not.toHaveBeenCalled();
});

it('关闭期间的成功结果不刷新列表/通知；传入请求取消信号', async () => {
  const action = deferred<FriendRequestItem>();
  jest.mocked(service.decide).mockReturnValue(action.promise);
  const changed = jest.fn();
  const { result, unmount } = renderHook(() => useFriendConnectionRequests(actor, changed));
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => { void result.current.decide('r1', 'accept'); });
  const signal = jest.mocked(service.decide).mock.calls[0][2];
  unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => action.resolve({ ...item, status: 'approved' }));
  expect(changed).not.toHaveBeenCalled();
  expect(notifySuccess).not.toHaveBeenCalled();
});

it('A→B→A 不能接收旧代次的审批结果', async () => {
  const action = deferred<FriendRequestItem>();
  jest.mocked(service.decide).mockReturnValue(action.promise);
  const changed = jest.fn();
  const { result, rerender } = renderHook(({ id }) => useFriendConnectionRequests({ ...actor, id }, changed), { initialProps: { id: actor.id } });
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => { void result.current.decide('r1', 'accept'); });
  rerender({ id: 'other:owner' });
  rerender({ id: actor.id });
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => action.resolve({ ...item, status: 'approved' }));
  expect(result.current.data?.received.items[0].status).toBe('pending');
  expect(changed).not.toHaveBeenCalled();
});

it('旧读请求晚返回不能覆盖新刷新结果，旧信号被取消', async () => {
  const old = deferred<FriendRequestSnapshot>();
  jest.mocked(service.load).mockReturnValueOnce(old.promise);
  const { result } = renderHook(() => useFriendConnectionRequests(actor, jest.fn()));
  const signal = jest.mocked(service.load).mock.calls[0][2];
  act(() => { void result.current.refresh(); });
  await waitFor(() => expect(result.current.data?.received.total).toBe(30));
  expect(signal?.aborted).toBe(true);
  await act(async () => old.resolve({ ...snapshot, received: { ...snapshot.received, total: 99 } }));
  expect(result.current.data?.received.total).toBe(30);
});
it('不允许审批发出的、不属于当前接收方的或已处理的申请', async () => {
  jest.mocked(service.load).mockResolvedValue({ ...snapshot, received: { ...snapshot.received, items: [
    { ...item, status: 'approved' }, { ...item, requestId: 'foreign', toActor: { ...item.toActor, id: 'other' } },
  ] }, sent: { ...snapshot.sent, items: [{ ...item, requestId: 'sent' }] } });
  const { result } = renderHook(() => useFriendConnectionRequests(actor, jest.fn()));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => { for (const id of ['r1', 'sent', 'foreign']) await result.current.decide(id, 'accept'); });
  expect(service.decide).not.toHaveBeenCalled();
});
it('加载失败展示错误，refresh 可以重试', async () => {
  jest.mocked(service.load).mockRejectedValueOnce(new Error('加载失败'));
  const { result } = renderHook(() => useFriendConnectionRequests(actor, jest.fn()));
  await waitFor(() => expect(result.current.error).toBe('加载失败'));
  act(() => { void result.current.refresh(); });
  await waitFor(() => expect(result.current.data?.received.total).toBe(30));
  expect(result.current.error).toBeNull();
});
