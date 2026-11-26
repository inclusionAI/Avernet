/** @jest-environment jsdom */
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { PublicBot } from '@/domain/collaborationSquare/types';
import { useCollaborationSquareFriendActions } from '@/hooks/useCollaborationSquareFriendActions';
import { collaborationSquareBotService } from '@/services/collaborationSquare';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));

const humanContext = { actorId: 'human_900003', userId: '900003' };
const activeActor = { type: 'human' as const, id: '900003' };

const makeBot = (overrides: Partial<PublicBot> = {}): PublicBot => ({
  id: 'bot-1',
  name: '助手',
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'applying',
  ...overrides,
});

describe('useCollaborationSquareFriendActions 重新申请', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  test('连续两次重试：第二次取消第一次返回的新单号，且每次点击都新建申请', async () => {
    const requestBotFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValueOnce({ status: 'applying', pendingRequestId: 'request-1' })
      .mockResolvedValueOnce({ status: 'applying', pendingRequestId: 'request-2' });
    const cancelBotFriendRequest = jest
      .spyOn(collaborationSquareBotService, 'cancelBotFriendRequest')
      .mockResolvedValue(undefined);

    let currentBot = makeBot();
    const updateBotRelationship = jest.fn(
      (_targetId: string, status: PublicBot['relationshipStatus'], pendingRequestId?: string) => {
        currentBot = {
          ...currentBot,
          relationshipStatus: status,
          pendingFriendRequestId:
            status === 'applying' ? (pendingRequestId ?? currentBot.pendingFriendRequestId) : undefined,
        };
      },
    );

    const { result } = renderHook(() =>
      useCollaborationSquareFriendActions({
        resource: 'bot',
        activeActor,
        humanBotContext: humanContext,
        handleTargetInvalid: jest.fn(),
        busyKeys: [],
        setBusy: jest.fn(),
        updateBotRelationship,
      }),
    );

    // 第一次点击：本地无 pending 单号 → 直接发起申请，写入后端新建的 request-1。
    act(() => result.current.resubmitBotFriendRequest(currentBot));
    await waitFor(() => expect(requestBotFriendship).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(updateBotRelationship).toHaveBeenCalledTimes(1));
    expect(updateBotRelationship).toHaveBeenLastCalledWith('bot-1', 'applying', 'request-1');
    expect(cancelBotFriendRequest).not.toHaveBeenCalled();

    // 第二次点击：取消第一次返回的 request-1，再发起申请并写入 request-2。
    act(() => result.current.resubmitBotFriendRequest(currentBot));
    await waitFor(() => expect(requestBotFriendship).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(updateBotRelationship).toHaveBeenCalledTimes(2));
    expect(cancelBotFriendRequest).toHaveBeenCalledTimes(1);
    expect(cancelBotFriendRequest).toHaveBeenCalledWith('request-1');
    expect(updateBotRelationship).toHaveBeenLastCalledWith('bot-1', 'applying', 'request-2');
    expect(currentBot.pendingFriendRequestId).toBe('request-2');
    expect(notifySuccess).toHaveBeenCalledWith('已重新提交申请，可联系该 Bot Owner 及时处理');
    expect(notifyError).not.toHaveBeenCalled();
  });
});
