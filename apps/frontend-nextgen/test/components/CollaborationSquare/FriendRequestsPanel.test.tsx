/** @jest-environment jsdom */
// bcs-standalone-friend-discovery Task 4：发现页「好友申请」入口与所选角色的申请处理。
// 组件不直接编排 Controller：渲染 FriendRequestsPanel + 真实 useFriendConnectionRequests，
// 只在 Controller 边界（collaborationFriendConnectionController / createBotSession）mock 响应。
// 覆盖：Owner 切到目标 Bot 后拉 received+pending、通过/拒绝/撤销、错误态重试、
// 切换角色不展示上一角色列表；BCS-only 下任何交互都不得触发 Backend 建聊（createBotSession）。
import { FriendRequestsPanel } from '@/components/CollaborationSquare/FriendRequestsPanel';
import { friendApprovalEntry } from '@/components/CollaborationPrivacy/botVisibilityCopy';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import type {
  FriendConnectionRequestDto,
  ListFriendConnectionRequestsParams,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as privateBotSessionController from '@/services/backendApi/bots/privateBotSessionController';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  ...jest.requireActual('@/services/backendApi/collaboration/collaborationFriendConnectionController'),
  listFriendConnectionRequests: jest.fn(),
  acceptFriendConnectionRequest: jest.fn(),
  rejectFriendConnectionRequest: jest.fn(),
  cancelFriendConnectionRequest: jest.fn(),
}));
jest.mock('@/services/backendApi/bots/privateBotSessionController', () => ({
  ...jest.requireActual('@/services/backendApi/bots/privateBotSessionController'),
  createBotSession: jest.fn(),
}));

const mockedList = friendConnectionController.listFriendConnectionRequests as jest.Mock;
const mockedAccept = friendConnectionController.acceptFriendConnectionRequest as jest.Mock;
const mockedReject = friendConnectionController.rejectFriendConnectionRequest as jest.Mock;
const mockedCancel = friendConnectionController.cancelFriendConnectionRequest as jest.Mock;
const mockedCreateBotSession = privateBotSessionController.createBotSession as jest.Mock;
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
const otherInboxRequest = requestDto('request-b1', { type: 'human', id: '900002' }, otherBot);
const pageEnvelope = (items: FriendConnectionRequestDto[]) => ({ code: 20000, message: 'ok', data: { items, total: items.length } });
const writeEnvelope = (dto: FriendConnectionRequestDto) => ({ code: 20000, message: 'ok', data: dto });

interface PendingRead {
  params: ListFriendConnectionRequestsParams;
  signal: AbortSignal | undefined;
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
}

const pendingReads: PendingRead[] = [];

function enqueueListRead(params: ListFriendConnectionRequestsParams, signal?: AbortSignal) {
  return new Promise<unknown>((resolve, reject) => {
    pendingReads.push({ params, signal, resolve, reject });
  });
}

function resolveDirectionRead(direction: 'sent' | 'received', envelope: unknown) {
  const index = pendingReads.findIndex((read) => read.params.direction === direction);
  if (index < 0)
    throw new Error(
      `没有等待中的 ${direction} 读取；当前队列: ${JSON.stringify(pendingReads.map((read) => read.params.direction))}`,
    );
  const [read] = pendingReads.splice(index, 1);
  return act(async () => {
    if (envelope instanceof Error) read.reject(envelope);
    else read.resolve(envelope);
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function flush(times = 6) {
  for (let i = 0; i < times; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function renderPanel({
  actor = ownerBot,
}: { actor?: { type: 'human' | 'bot'; id: string } | null } = {}) {
  return render(<FriendRequestsPanel resource="bot" actor={actor} userId="900003" />);
}

beforeEach(() => {
  pendingReads.length = 0;
  mockedList.mockReset().mockImplementation((params, signal) => enqueueListRead(params, signal));
  mockedAccept.mockReset();
  mockedReject.mockReset();
  mockedCancel.mockReset();
  mockedCreateBotSession.mockReset();
  mockedNotifyError.mockClear();
  mockedNotifySuccess.mockClear();
});

afterEach(() => {
  jest.restoreAllMocks();
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
});

describe('FriendRequestsPanel 入口与列表', () => {
  it('无操作角色时不渲染；有角色时默认收起，点击展开并发起两向 pending 列表读取', async () => {
    const { container, unmount } = renderPanel({ actor: null });
    expect(container).toBeEmptyDOMElement();
    unmount();

    const screen_ = renderPanel({ actor: ownerBot });
    expect(screen.getByRole('button', { name: '好友申请' })).toBeInTheDocument();
    expect(screen.queryByText('收到的申请')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
    await flush();

    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-7', direction: 'received', status: 'pending', page: 1 }),
      expect.anything(),
    );
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-7', direction: 'sent', status: 'pending', page: 1 }),
      expect.anything(),
    );

    await resolveDirectionRead('received', pageEnvelope([inboxRequest]));
    await resolveDirectionRead('sent', pageEnvelope([outboxRequest]));

    expect(screen.getByText('来自 900003 的好友申请')).toBeInTheDocument();
    expect(screen.getByText('向 bot-x 发出的好友申请')).toBeInTheDocument();
    expect(screen_.container.firstChild).toBeInTheDocument();
    expect(mockedCreateBotSession).not.toHaveBeenCalled();
    screen_.unmount();
  });

  it('收到的申请可通过／拒绝：调用现有 BCS Controller，成功后刷新申请列表', async () => {
    let finishAccept!: (value: unknown) => void;
    mockedAccept.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishAccept = resolve;
        }),
    );
    const { unmount } = renderPanel({ actor: ownerBot });
    fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
    await flush();
    await resolveDirectionRead('received', pageEnvelope([inboxRequest]));
    await resolveDirectionRead('sent', pageEnvelope([outboxRequest]));

    fireEvent.click(screen.getByRole('button', { name: '通过' }));
    expect(mockedAccept).toHaveBeenCalledWith('request-1', expect.anything());

    await act(async () => {
      finishAccept(writeEnvelope({ ...inboxRequest, status: 'approved' }));
      await Promise.resolve();
    });
    // 刷新读回：收件列表不再含已处理的申请
    await resolveDirectionRead('received', pageEnvelope([]));
    await resolveDirectionRead('sent', pageEnvelope([outboxRequest]));

    expect(screen.queryByText('来自 900003 的好友申请')).not.toBeInTheDocument();
    expect(mockedNotifySuccess).toHaveBeenCalledWith('已通过好友申请');
    expect(mockedCreateBotSession).not.toHaveBeenCalled();
    unmount();
  });

  it('拒绝与撤销分别调用 reject／cancel Controller 并在成功后刷新', async () => {
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
    const { unmount } = renderPanel({ actor: ownerBot });
    fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
    await flush();
    await resolveDirectionRead('received', pageEnvelope([inboxRequest]));
    await resolveDirectionRead('sent', pageEnvelope([outboxRequest]));

    fireEvent.click(screen.getByRole('button', { name: '拒绝' }));
    expect(mockedReject).toHaveBeenCalledWith('request-1', expect.anything());
    await act(async () => {
      finishReject(writeEnvelope({ ...inboxRequest, status: 'rejected' }));
      await Promise.resolve();
    });
    await resolveDirectionRead('received', pageEnvelope([]));
    await resolveDirectionRead('sent', pageEnvelope([outboxRequest]));
    expect(mockedNotifySuccess).toHaveBeenCalledWith('已拒绝好友申请');

    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    expect(mockedCancel).toHaveBeenCalledWith('request-2', expect.anything());
    await act(async () => {
      finishCancel(writeEnvelope({ ...outboxRequest, status: 'cancelled' }));
      await Promise.resolve();
    });
    await resolveDirectionRead('received', pageEnvelope([]));
    await resolveDirectionRead('sent', pageEnvelope([]));
    expect(mockedNotifySuccess).toHaveBeenCalledWith('已撤销好友申请');
    expect(mockedCreateBotSession).not.toHaveBeenCalled();
    unmount();
  });

  it('列表失败展示错误态并允许重试，不显示假的成功', async () => {
    const { unmount } = renderPanel({ actor: ownerBot });
    fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
    await flush();

    await resolveDirectionRead('received', new Error('BCS 申请列表暂不可用'));
    await resolveDirectionRead('sent', pageEnvelope([]));

    expect(screen.getByText('BCS 申请列表暂不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(mockedNotifySuccess).not.toHaveBeenCalled();
    expect(mockedCreateBotSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await flush();
    expect(
      mockedList.mock.calls.filter(([params]) => (params as ListFriendConnectionRequestsParams).direction === 'received'),
    ).toHaveLength(2);
    // 重试只重新拉取失败的方向；发出（sent）方向沿用已成功的第一轮结果。
    await resolveDirectionRead('received', pageEnvelope([inboxRequest]));
    expect(screen.getByText('来自 900003 的好友申请')).toBeInTheDocument();
    unmount();
  });

  it('切换到新角色后不展示上一角色列表，并按新角色重新拉取', async () => {
    const { rerender, unmount } = renderPanel({ actor: ownerBot });
    fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
    await flush();
    await resolveDirectionRead('received', pageEnvelope([inboxRequest]));
    await resolveDirectionRead('sent', pageEnvelope([outboxRequest]));
    expect(screen.getByText('来自 900003 的好友申请')).toBeInTheDocument();

    rerender(<FriendRequestsPanel resource="bot" actor={otherBot} userId="900003" />);
    // 旧角色的申请不展示；新角色按 received+pending 重新拉取
    expect(screen.queryByText('来自 900003 的好友申请')).not.toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-9', direction: 'received', status: 'pending' }),
      expect.anything(),
    );
    await resolveDirectionRead('received', pageEnvelope([otherInboxRequest]));
    await resolveDirectionRead('sent', pageEnvelope([]));
    expect(screen.getByText('来自 900002 的好友申请')).toBeInTheDocument();
    expect(mockedCreateBotSession).not.toHaveBeenCalled();
    unmount();
  });

  it('展开中 Human 角色只读本人发出的申请', async () => {
    const { unmount } = renderPanel({ actor: humanActor });
    fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
    await flush();
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'human', actor_id: '900003', direction: 'sent', status: 'pending' }),
      expect.anything(),
    );
    expect(mockedList).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'human', actor_id: '900003', direction: 'received', status: 'pending' }),
      expect.anything(),
    );
    await resolveDirectionRead('sent', pageEnvelope([requestDto('request-3', humanActor, { type: 'bot', id: 'bot-y' })]));
    await resolveDirectionRead('received', pageEnvelope([]));
    expect(screen.getByText('向 bot-y 发出的好友申请')).toBeInTheDocument();
    unmount();
  });
});

describe('友审批入口文案（BCS-only / 全栈）', () => {
  it('Backend 关闭时好友审批说明指向发现页申请处理入口', () => {
    expect(friendApprovalEntry(false)).toEqual({
      approvalEntryLabel: '发现 → 公开 Bot / 好友申请',
      approvalEntryPath: '/collaboration-square/bots',
    });
  });

  it('全栈（Backend 开启）保留通知中心作为增强入口', () => {
    const entry = friendApprovalEntry(true);
    expect(entry.approvalEntryPath).toBe('/ticket-center');
    expect(entry.approvalEntryLabel).toContain('通知中心');
  });
});
