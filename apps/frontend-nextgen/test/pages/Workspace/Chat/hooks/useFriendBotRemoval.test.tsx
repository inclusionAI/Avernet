/** @jest-environment jsdom */
// 移除好友装配 Hook：成功时清选中/丢缓存/重拉目录；失败保持弹窗且不改变本地状态。
import { useFriendBotRemoval } from '@/pages/Workspace/Chat/hooks/useFriendBotRemoval';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/workspace/collaborationCandidateService', () => ({
  collaborationCandidateService: { removeFriend: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const removeFriend = collaborationCandidateService.removeFriend as jest.Mock;

const friendBot: ChatBotView = {
  botId: 'bot-1:owner-9',
  realBotId: 'bot-1',
  ownerId: 'owner-9',
  displayName: '好友 Bot',
  online: true,
  chatable: true,
  isFriendBot: true,
};

function selectFriend() {
  useConversationStore.getState().selectConversation({
    botId: friendBot.botId,
    section: 'friend',
    origin: 'mine',
    scope: 'all',
    friendUserId: null,
    sessionId: 'session-1',
  });
  useConversationStore.getState().setExpandedBot(friendBot.botId, true);
  useConversationStore.getState().setFriendBotSessions(friendBot.botId, {
    items: [],
    page: 1,
    total: 0,
    hasMore: false,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  });
}

function clearSelection() {
  useConversationStore.getState().selectConversation({
    botId: null,
    section: null,
    origin: 'mine',
    scope: 'all',
    friendUserId: null,
    sessionId: null,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  useConversationStore.getState().reset();
});

describe('useFriendBotRemoval', () => {
  it('确认后调用 DELETE、清除选中好友的选中态与侧栏缓存并刷新目录', async () => {
    selectFriend();
    removeFriend.mockResolvedValue({ ok: true, data: undefined });
    const removeFriendBot = jest.fn();
    const { result } = renderHook(() => useFriendBotRemoval({ removeFriendBot, clearSelection }));

    act(() => result.current.requestRemoval(friendBot));
    expect(result.current.target).toBe(friendBot);

    await act(async () => {
      await result.current.confirmRemoval();
    });

    expect(removeFriend).toHaveBeenCalledWith(friendBot.botId);
    expect(useConversationStore.getState().selectedBotId).toBeNull();
    expect(useConversationStore.getState().expandedBotIds[friendBot.botId]).toBeUndefined();
    expect(useConversationStore.getState().friendBotSessionsByBotId[friendBot.botId]).toBeUndefined();
    expect(removeFriendBot).toHaveBeenCalledWith(friendBot.botId);
    expect(result.current.target).toBeNull();
    expect(toast.success).toHaveBeenCalledWith('已移除好友');
  });

  it('移除的不是当前选中好友时保留现有选中', async () => {
    selectFriend();
    removeFriend.mockResolvedValue({ ok: true, data: undefined });
    const clear = jest.fn(clearSelection);
    const { result } = renderHook(() => useFriendBotRemoval({ removeFriendBot: jest.fn(), clearSelection: clear }));

    act(() => result.current.requestRemoval({ ...friendBot, botId: 'bot-2' }));
    await act(async () => {
      await result.current.confirmRemoval();
    });

    expect(clear).not.toHaveBeenCalled();
    expect(useConversationStore.getState().selectedBotId).toBe(friendBot.botId);
  });

  it('删除失败时保留弹窗与本地状态，并提示可读错误', async () => {
    selectFriend();
    removeFriend.mockResolvedValue({
      ok: false,
      error: { code: 'COLLABORATION_FRIEND_REMOVE_FAILED', friendlyMessage: '移除好友失败，请稍后重试。' },
    });
    const removeFriendBot = jest.fn();
    const { result } = renderHook(() => useFriendBotRemoval({ removeFriendBot, clearSelection }));

    act(() => result.current.requestRemoval(friendBot));
    await act(async () => {
      await result.current.confirmRemoval();
    });

    expect(result.current.target).toBe(friendBot);
    expect(useConversationStore.getState().selectedBotId).toBe(friendBot.botId);
    expect(removeFriendBot).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith('移除好友失败，请稍后重试。');
  });
});