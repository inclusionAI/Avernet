/** @jest-environment jsdom */
// 移除好友动线 Hook 契约(移除好友 PRD):确认后调 DELETE friend-connection(主体为登录
// 用户,目标由 query 指定),清理该 Bot 本地状态并重拉好友目录;正选中该好友时清除选中;
// 失败保持弹窗打开、不重拉目录。
import { useFriendBotRemoval } from '@/pages/Workspace/Chat/hooks/useFriendBotRemoval';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  deleteFriendConnection: require('jest-mock').fn(),
}));

import { deleteFriendConnection } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
const deleteFriendConnectionMock = deleteFriendConnection as unknown as jest.Mock;

const friendBot: ChatBotView = {
  botId: 'friend-bot:1',
  realBotId: 'friend-bot:1',
  displayName: '好友 Bot',
  online: true,
  chatable: true,
};

function renderRemoval(retryFriend = jest.fn()) {
  return { ...renderHook(() => useFriendBotRemoval({ retryFriend })), retryFriend };
}

beforeEach(() => {
  deleteFriendConnectionMock.mockReset();
  useConversationStore.getState().reset();
});

describe('useFriendBotRemoval', () => {
  it('open 指定目标 Bot,cancel 关闭弹窗', () => {
    const { result } = renderRemoval();
    expect(result.current.target).toBeNull();
    act(() => result.current.open(friendBot));
    expect(result.current.target?.botId).toBe('friend-bot:1');
    act(() => result.current.cancel());
    expect(result.current.target).toBeNull();
  });

  it('confirm 成功:query 指定目标调 DELETE,清本地状态,重拉好友目录,关闭弹窗', async () => {
    const store = useConversationStore.getState();
    store.setExpandedBot('friend-bot:1', true);
    store.setFriendBotScope('friend-bot:1', 'favorite');
    store.setSessionKeyFilter('friend-bot:1', 'sk-1');
    store.selectConversation({
      botId: 'other-bot:2',
      section: 'friend',
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: 's-other',
    });
    deleteFriendConnectionMock.mockResolvedValue({ code: 20000, data: {} });
    const { result, retryFriend } = renderRemoval();
    act(() => result.current.open(friendBot));

    await act(async () => {
      await result.current.confirm();
    });

    // 经 Service 层包装(conversationService.removeFriendConnection)后,controller
    // 收到 (query 参数, signal);未透传 signal 时第二参为 undefined。
    expect(deleteFriendConnectionMock).toHaveBeenCalledWith(
      {
        target_actor_type: 'bot',
        target_actor_id: 'friend-bot:1',
      },
      undefined,
    );
    const next = useConversationStore.getState();
    expect(next.expandedBotIds['friend-bot:1']).toBeUndefined();
    expect(next.scopeByFriendBotId['friend-bot:1']).toBeUndefined();
    expect(next.sessionKeyByBotId['friend-bot:1']).toBeUndefined();
    expect(retryFriend).toHaveBeenCalledTimes(1);
    expect(result.current.target).toBeNull();
    // 未选中的其他好友会话不受影响。
    expect(next.selectedBotId).toBe('other-bot:2');
  });

  it('confirm 成功且正选中该好友:清除选中回到默认态(R9)', async () => {
    useConversationStore.getState().selectConversation({
      botId: 'friend-bot:1',
      section: 'friend',
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: 's-1',
    });
    deleteFriendConnectionMock.mockResolvedValue({ code: 20000, data: {} });
    const { result } = renderRemoval();
    act(() => result.current.open(friendBot));

    await act(async () => {
      await result.current.confirm();
    });

    const next = useConversationStore.getState();
    expect(next.selectedBotId).toBeNull();
    expect(next.selectedSection).toBeNull();
    expect(next.selectedSessionId).toBeNull();
  });

  it('confirm 失败:保持弹窗打开,不清理本地状态、不重拉目录', async () => {
    const store = useConversationStore.getState();
    store.setExpandedBot('friend-bot:1', true);
    deleteFriendConnectionMock.mockRejectedValue(new Error('network down'));
    const { result, retryFriend } = renderRemoval();
    act(() => result.current.open(friendBot));

    await act(async () => {
      await result.current.confirm();
    });

    expect(result.current.target?.botId).toBe('friend-bot:1');
    expect(useConversationStore.getState().expandedBotIds['friend-bot:1']).toBe(true);
    expect(retryFriend).not.toHaveBeenCalled();
  });
});
