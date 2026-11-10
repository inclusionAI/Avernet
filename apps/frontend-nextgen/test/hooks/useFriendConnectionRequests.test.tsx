/** @jest-environment jsdom */
// bcs-standalone-friend-discovery Task 4：所选角色好友申请列表与通过/拒绝/撤销的编排。
// - 角色/展开状态变化：AbortController 清理读取、同步清掉旧代次的 busy，不展示上一角色列表；
// - 写操作（accept/reject/cancel）调用现有 BCS Controller，成功后刷新申请列表与卡片（回调），
//   失败只报真实错误，不显示假的成功；
// - 迟到响应（故意在换角色后仍 resolve/reject）不得污染新角色的列表、busy 与提示，
//   保护来自代次守卫而非 AbortSignal。
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useFriendConnectionRequests } from '@/hooks/useFriendConnectionRequests';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import type {
  FriendConnectionRequestDto,
  ListFriendConnectionRequestsParams,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  ...jest.requireActual('@/services/backendApi/collaboration/collaborationFriendConnectionController'),
  listFriendConnectionRequests: jest.fn(),
  acceptFriendConnectionRequest: jest.fn(),
  rejectFriendConnectionRequest: jest.fn(),
  cancelFriendConnectionRequest: jest.fn(),
}));

const mockedList = friendConnectionController.listFriendConnectionRequests as jest.Mock;
const mockedAccept = friendConnectionController.acceptFriendConnectionRequest as jest.Mock;
const mockedReject = friendConnectionController.rejectFriendConnectionRequest as jest.Mock;
const mockedCancel = friendConnectionController.cancelFriendConnectionRequest as jest.Mock;
const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;
const mockedNotifySuccess = notifySuccess as jest.MockedFunction<typeof notifySuccess>;

const ownerBot = { type: 'bot' as const, id: 'bot-7' };
const otherBot = { type: 'bot' as const, id: 'bot-9' };
const humanActor = { type: 'human' as const, id: '900003' };

const requestDto = (
  requestId: string,
  from: { type: 'human' | 'bot'; id: string },
  to: { type: 'human' | 'bot'; id: string },
): FriendConnectionRequestDto => ({
  request_id: requestId,
  from_actor: from,
  to_actor: to,
  status: 'pending',
  message: '希望协作',
});

const inboxRequest = requestDto('request-1', humanActor, ownerBot);
const outboxRequest = requestDto('request-2', ownerBot, { type: 'bot', id: 'bot-x' });

const pageEnvelope = (items: FriendConnectionRequestDto[], total = items.length) => ({
  code: 20000,
  message: 'ok',
  data: { items, total },
});

const writeEnvelope = (dto: FriendConnectionRequestDto) => ({ code: 20000, message: 'ok', data: dto });

interface PendingRead {
  params: ListFriendConnectionRequestsParams;
  signal: AbortSignal | undefined;
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
}

const pendingReads: PendingRead[] = [];
let onRelationshipsChanged: jest.Mock;

function enqueueListRead(params: ListFriendConnectionRequestsParams, signal?: AbortSignal) {
  return new Promise<unknown>((resolve, reject) => {
    pendingReads.push({ params, signal, resolve, reject });
  });
}

function resolveRead(direction: 'sent' | 'received', envelope: unknown) {
  const index = pendingReads.findIndex((read) => read.params.direction === direction);
  if (index < 0) throw new Error(`没有等待中的 ${direction} 读取`);
  const [read] = pendingReads.splice(index, 1);
  return act(async () => {
    if (envelope instanceof Error) read.reject(envelope);
    else read.resolve(envelope);
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function flush() {
  for (let i = 0; i < 6; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function renderRequestsHook({
  actor = ownerBot,
  enabled = true,
}: { actor?: { type: 'human' | 'bot'; id: string } | null; enabled?: boolean } = {}) {
  return renderHook(
    ({ actor: currentActor, enabled: currentEnabled }: { actor: { type: 'human' | 'bot'; id: string } | null; enabled: boolean }) =>
      useFriendConnectionRequests({
        resource: 'bot',
        actor: currentActor,
        userId: '900003',
        enabled: currentEnabled,
        onRelationshipsChanged,
      }),
    { initialProps: { actor, enabled } },
  );
}

beforeEach(() => {
  pendingReads.length = 0;
  onRelationshipsChanged = jest.fn();
  mockedList.mockReset().mockImplementation((params, signal) => enqueueListRead(params, signal));
  mockedAccept.mockReset();
  mockedReject.mockReset();
  mockedCancel.mockReset();
  mockedNotifyError.mockClear();
  mockedNotifySuccess.mockClear();
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('useFriendConnectionRequests 读取编排', () => {
  it('未展开或无角色不发请求；展开后按所选角色分别拉 received/sent 两向 pending 列表', async () => {
    const collapsed = renderRequestsHook({ enabled: false });
    await flush();
    expect(mockedList).not.toHaveBeenCalled();
    collapsed.unmount();

    const noActor = renderRequestsHook({ actor: null });
    await flush();
    expect(mockedList).not.toHaveBeenCalled();
    noActor.unmount();

    const { result, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({
        actor_type: 'bot',
        actor_id: 'bot-7',
        direction: 'received',
        status: 'pending',
        page: 1,
      }),
      expect.anything(),
    );
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-7', direction: 'sent', status: 'pending', page: 1 }),
      expect.anything(),
    );

    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([outboxRequest]));

    expect(result.current.received.items.map((item) => item.requestId)).toEqual(['request-1']);
    expect(result.current.received.items[0].fromActorId).toBe('900003');
    expect(result.current.sent.items.map((item) => item.requestId)).toEqual(['request-2']);
    expect(result.current.received.loading).toBe(false);
    expect(result.current.received.error).toBeNull();
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
    unmount();
  });

  it('Human 角色读取本人发出的申请（只看本人），Bot 角色由 BCS 复验管理权限', async () => {
    const { result, unmount } = renderRequestsHook({ actor: humanActor });
    await flush();
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'human', actor_id: '900003', direction: 'sent', status: 'pending' }),
      expect.anything(),
    );
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'human', actor_id: '900003', direction: 'received', status: 'pending' }),
      expect.anything(),
    );
    await resolveRead('sent', pageEnvelope([]));
    await resolveRead('received', pageEnvelope([]));
    expect(result.current.sent.items).toEqual([]);
    unmount();
  });

  it('翻页把 page 原样传入列表查询并更新当前页', async () => {
    const { result, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    mockedList.mockClear();
    await resolveRead('received', pageEnvelope([inboxRequest], 21));
    await resolveRead('sent', pageEnvelope([]));

    act(() => {
      result.current.received.gotoPage(2);
    });
    await flush();
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-7', direction: 'received', page: 2 }),
      expect.anything(),
    );
    await resolveRead('received', pageEnvelope([outboxRequest], 21));
    expect(result.current.received.page).toBe(2);
    expect(result.current.received.total).toBe(21);
    unmount();
  });

  it('列表失败进入错误态，可重试且不显示假的成功', async () => {
    const { result, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', new Error('BCS 申请列表暂不可用'));
    await resolveRead('sent', pageEnvelope([]));

    expect(result.current.received.error).toBe('BCS 申请列表暂不可用');
    expect(result.current.received.items).toEqual([]);
    expect(mockedNotifySuccess).not.toHaveBeenCalled();

    act(() => {
      result.current.received.reload();
    });
    await flush();
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ direction: 'received', page: 1 }),
      expect.anything(),
    );
    await resolveRead('received', pageEnvelope([inboxRequest]));
    expect(result.current.received.error).toBeNull();
    expect(result.current.received.items.map((item) => item.requestId)).toEqual(['request-1']);
    unmount();
  });
});

describe('useFriendConnectionRequests 写操作', () => {
  it('通过申请调用现有 BCS Controller，成功后刷新列表并通知卡片关系刷新', async () => {
    let finishAccept!: (value: unknown) => void;
    mockedAccept.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishAccept = resolve;
        }),
    );
    const { result, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([outboxRequest]));

    act(() => {
      result.current.accept('request-1');
    });
    expect(mockedAccept).toHaveBeenCalledWith('request-1', expect.anything());
    expect(result.current.busyRequestIds).toEqual(['accept:request-1']);

    await act(async () => {
      finishAccept(writeEnvelope({ ...inboxRequest, status: 'approved' }));
      await Promise.resolve();
    });
    await flush();
    expect(mockedNotifySuccess).toHaveBeenCalledWith('已通过好友申请');
    expect(onRelationshipsChanged).toHaveBeenCalled();

    // 审批完成 → 两个方向重新拉取
    expect(
      mockedList.mock.calls.filter(([params]) => (params as ListFriendConnectionRequestsParams).direction === 'received'),
    ).toHaveLength(2);
    await resolveRead('received', pageEnvelope([]));
    await resolveRead('sent', pageEnvelope([outboxRequest]));
    expect(result.current.received.items).toEqual([]);
    expect(result.current.busyRequestIds).toEqual([]);
    unmount();
  });

  it('拒绝与撤销分别调用 reject/cancel 并刷新', async () => {
    let finishReject!: (value: unknown) => void;
    let finishCancel!: (value: unknown) => void;
    mockedReject.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishReject = resolve;
        }),
    );
    mockedCancel.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishCancel = resolve;
        }),
    );
    const { result, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([outboxRequest]));

    act(() => {
      result.current.reject('request-1');
    });
    expect(mockedReject).toHaveBeenCalledWith('request-1', expect.anything());
    await act(async () => {
      finishReject(writeEnvelope({ ...inboxRequest, status: 'rejected' }));
      await Promise.resolve();
    });
    await flush();
    expect(mockedNotifySuccess).toHaveBeenCalledWith('已拒绝好友申请');

    act(() => {
      result.current.cancel('request-2');
    });
    expect(mockedCancel).toHaveBeenCalledWith('request-2', expect.anything());
    await act(async () => {
      finishCancel(writeEnvelope({ ...outboxRequest, status: 'cancelled' }));
      await Promise.resolve();
    });
    await flush();
    expect(mockedNotifySuccess).toHaveBeenCalledWith('已撤销好友申请');
    await resolveRead('received', pageEnvelope([]));
    await resolveRead('sent', pageEnvelope([]));
    expect(result.current.busyRequestIds).toEqual([]);
    unmount();
  });

  it('通过失败只报真实错误，不提示成功、不刷新、不清掉申请项', async () => {
    let failAccept!: (reason?: unknown) => void;
    mockedAccept.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          failAccept = reject;
        }),
    );
    const { result, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([]));
    const receivedReadsBefore = mockedList.mock.calls.filter(
      ([params]) => (params as ListFriendConnectionRequestsParams).direction === 'received',
    ).length;

    act(() => {
      result.current.accept('request-1');
    });
    await act(async () => {
      failAccept(new Error('目标 Bot 已撤回申请'));
      await Promise.resolve();
    });
    await flush();

    expect(mockedNotifyError).toHaveBeenCalledWith('目标 Bot 已撤回申请');
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
    expect(onRelationshipsChanged).not.toHaveBeenCalled();
    expect(
      mockedList.mock.calls.filter(([params]) => (params as ListFriendConnectionRequestsParams).direction === 'received'),
    ).toHaveLength(receivedReadsBefore);
    expect(result.current.received.items.map((item) => item.requestId)).toEqual(['request-1']);
    expect(result.current.busyRequestIds).toEqual([]);
    unmount();
  });
});

describe('useFriendConnectionRequests 角色代次隔离', () => {
  it('切换角色后旧通过的迟到成功不提示、不刷新、不污染新角色 busy', async () => {
    let finishAccept!: (value: unknown) => void;
    mockedAccept.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishAccept = resolve;
        }),
    );
    const { result, rerender, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([]));

    act(() => {
      result.current.accept('request-1');
    });

    rerender({ actor: otherBot, enabled: true });
    // 同步 cleanup 清掉旧代次的 busy，不等待旧请求 finally。
    expect(result.current.busyRequestIds).toEqual([]);
    // 上 一 角色的列表不再展示（新角色加载中为空）
    expect(result.current.received.items).toEqual([]);
    await resolveRead('received', pageEnvelope([requestDto('request-b1', humanActor, otherBot)]));
    await resolveRead('sent', pageEnvelope([]));
    expect(result.current.received.items.map((item) => item.requestId)).toEqual(['request-b1']);

    // A 的迟到成功（发起后未 abort 传输，故意仍 resolve）不得影响 B
    const receivedCalls = mockedList.mock.calls.filter(
      ([params]) => (params as ListFriendConnectionRequestsParams).direction === 'received',
    ).length;
    await act(async () => {
      finishAccept(writeEnvelope({ ...inboxRequest, status: 'approved' }));
      await Promise.resolve();
    });
    await flush();
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
    expect(onRelationshipsChanged).not.toHaveBeenCalled();
    expect(
      mockedList.mock.calls.filter(([params]) => (params as ListFriendConnectionRequestsParams).direction === 'received'),
    ).toHaveLength(receivedCalls);
    expect(result.current.received.items.map((item) => item.requestId)).toEqual(['request-b1']);
    expect(result.current.busyRequestIds).toEqual([]);
    unmount();
  });

  it('切换角色后旧拒绝的迟到失败不弹过期错误', async () => {
    let failAccept!: (reason?: unknown) => void;
    mockedAccept.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          failAccept = reject;
        }),
    );
    const { result, rerender, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([]));
    act(() => {
      result.current.accept('request-1');
    });

    rerender({ actor: otherBot, enabled: true });
    await resolveRead('received', pageEnvelope([]));
    await resolveRead('sent', pageEnvelope([]));

    await act(async () => {
      failAccept(new Error('过期失败'));
      await Promise.resolve();
    });
    await flush();
    expect(mockedNotifyError).not.toHaveBeenCalled();
    expect(result.current.received.items).toEqual([]);
    unmount();
  });

  it('切换角色取消旧列表读取（AbortSignal 置 aborted），但早到的旧结果不覆盖新列表', async () => {
    const { result, rerender, unmount } = renderRequestsHook({ actor: ownerBot });
    await flush();
    expect(pendingReads[0].signal?.aborted).toBe(false);

    rerender({ actor: otherBot, enabled: true });
    // 新角色读取发出，旧读取被 abort
    expect(pendingReads[0].signal?.aborted).toBe(true);
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-9', direction: 'received', status: 'pending' }),
      expect.anything(),
    );

    // 故意在 abort 后仍 resolve 旧读取：新角色在等待的列表保持清空
    const staleIndex = pendingReads.findIndex((read) => read.params.actor_id === 'bot-7');
    const [oldRead] = pendingReads.splice(staleIndex, 1);
    await act(async () => {
      oldRead.resolve(pageEnvelope([inboxRequest]));
      await Promise.resolve();
    });
    expect(result.current.received.items).toEqual([]);
    await resolveRead('received', pageEnvelope([requestDto('request-b1', humanActor, otherBot)]));
    await resolveRead('sent', pageEnvelope([]));
    expect(result.current.received.items.map((item) => item.requestId)).toEqual(['request-b1']);
    unmount();
  });

  it('卸载后迟到的通过成功不产生任何提示；重新挂载可对新实例再次通过，busy 不被遗留禁用', async () => {
    let finishAccept!: (value: unknown) => void;
    mockedAccept.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishAccept = resolve;
        }),
    );
    const first = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([]));
    act(() => {
      first.result.current.accept('request-1');
    });
    act(() => {
      first.unmount();
    });
    // 卸载后 cleanup 同步清掉本 Hook 管理的 busy（此时全局难以观测，以新实例可用性为准）
    await act(async () => {
      finishAccept(writeEnvelope({ ...inboxRequest, status: 'approved' }));
      await Promise.resolve();
    });
    await flush();
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
    expect(onRelationshipsChanged).not.toHaveBeenCalled();

    // 重新挂载：新实例对同一目标可以再次发起通过，不被上一实例的 busy 永久禁用
    const second = renderRequestsHook({ actor: ownerBot });
    await flush();
    await resolveRead('received', pageEnvelope([inboxRequest]));
    await resolveRead('sent', pageEnvelope([]));
    expect(second.result.current.busyRequestIds).toEqual([]);
    let finishSecondAccept!: (value: unknown) => void;
    mockedAccept.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishSecondAccept = resolve;
        }),
    );
    act(() => {
      second.result.current.accept('request-1');
    });
    expect(mockedAccept).toHaveBeenCalledTimes(2);
    expect(second.result.current.busyRequestIds).toEqual(['accept:request-1']);
    await act(async () => {
      finishSecondAccept(writeEnvelope({ ...inboxRequest, status: 'approved' }));
      await Promise.resolve();
    });
    await flush();
    expect(mockedNotifySuccess).toHaveBeenCalledWith('已通过好友申请');
    expect(second.result.current.busyRequestIds).toEqual([]);
    second.unmount();
  });
});
