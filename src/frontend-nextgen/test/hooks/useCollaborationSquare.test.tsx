/** @jest-environment jsdom */
import type { PublicBot, PublicBotSearchQuery, PublicGroup } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import {
  CollaborationSquareError,
  collaborationSquareBotService,
  collaborationSquareGroupService,
  collaborationSquareService,
} from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const humanContext = { actorId: 'human_327325', userId: '327325' };

interface PendingRequest {
  query: PublicBotSearchQuery | undefined;
  context: typeof humanContext | undefined;
  signal: AbortSignal | undefined;
  resolve: (bots: PublicBot[]) => void;
}

const resultBot = (id: string): PublicBot => ({
  id,
  name: id,
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
});

describe('useCollaborationSquare Bot Search', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    (history.push as jest.Mock).mockClear();
    useCollaborationSquareStore.getState().reset();
    useWorkspaceStore.getState().reset();
    useWorkspaceStore
      .getState()
      .setIdentities([{ id: 'human_327325', kind: 'user', displayName: '当前用户', online: true }], 'human_327325');
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '327325', displayName: '当前用户', online: true },
      status: 'ready',
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
    jest.useRealTimers();
  });

  test('名称搜索防抖、取消旧请求并阻止过期结果覆盖', async () => {
    const pending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBots').mockImplementation(
      (query, context, signal) =>
        new Promise<PublicBot[]>((resolve) => {
          pending.push({ query, context, signal, resolve });
        }),
    );
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    act(() => {
      jest.advanceTimersByTime(0);
    });
    expect(pending).toHaveLength(1);
    expect(pending[0].query).toEqual({ page: 1, pageSize: 20 });
    expect(pending[0].context).toEqual(humanContext);

    act(() => {
      result.current.setQuery('bot', ' workflow ');
    });
    expect(pending[0].signal?.aborted).toBe(true);
    act(() => {
      jest.advanceTimersByTime(299);
    });
    expect(pending).toHaveLength(1);
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(pending).toHaveLength(2);
    expect(pending[1].query).toEqual({ search: 'workflow', page: 1, pageSize: 20 });

    await act(async () => {
      pending[1].resolve([resultBot('new')]);
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['new']);

    await act(async () => {
      pending[0].resolve([resultBot('stale')]);
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['new']);

    unmount();
  });

  test('智能搜索对非空能力描述调用真实 Discovery，空输入恢复默认目录', async () => {
    const listBots = jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([resultBot('default')]);
    const discoverBots = jest
      .spyOn(collaborationSquareBotService, 'discoverBots')
      .mockResolvedValue([
        { ...resultBot('code'), name: '研发助手', description: '代码审查与质量改进', capabilities: ['代码审查'] },
      ]);
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(listBots).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }, humanContext, expect.any(AbortSignal));

    act(() => {
      result.current.setBotSearchMode('smart');
    });
    act(() => {
      result.current.setQuery('bot', '代码');
    });
    await act(async () => {
      jest.advanceTimersByTime(299);
    });
    expect(discoverBots).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(1);
    });

    expect(discoverBots).toHaveBeenLastCalledWith(
      { keyword: '代码', topK: 20, minScore: 0.1, runtimeState: 'online' },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(result.current.visibleBots.map((bot) => bot.id)).toEqual(['code']);

    act(() => {
      result.current.setQuery('bot', '');
    });
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(listBots).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 }, humanContext, expect.any(AbortSignal));

    unmount();
  });

  test('公开群首次加载和名称搜索调用真实 Group List，详情仍保留原 Service 链路', async () => {
    const group: PublicGroup = {
      id: 'group-real-1',
      name: '公开群',
      ownerBotName: '主理 Bot',
      ownerUserName: 'Owner',
      typeLabel: '协作群',
      memberCount: 2,
      goal: '',
      memberListVisibility: 'visible',
      canCreateSession: true,
    };
    const listGroups = jest.spyOn(collaborationSquareGroupService, 'listGroups').mockResolvedValue([group]);
    const realMembers = jest.spyOn(collaborationSquareGroupService, 'listGroupMembers');
    const legacyMembers = jest.spyOn(collaborationSquareService, 'listGroupMembers').mockResolvedValue([]);
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(listGroups).toHaveBeenLastCalledWith({ offset: 0, limit: 20 }, expect.any(AbortSignal));

    act(() => {
      result.current.setQuery('group', ' 公开 ');
    });
    await act(async () => {
      jest.advanceTimersByTime(299);
    });
    expect(listGroups).toHaveBeenCalledTimes(1);
    await act(async () => {
      jest.advanceTimersByTime(1);
    });
    expect(listGroups).toHaveBeenLastCalledWith({ search: '公开', offset: 0, limit: 20 }, expect.any(AbortSignal));

    await act(async () => {
      await result.current.openGroupMembers(group);
    });
    expect(legacyMembers).toHaveBeenCalledWith(group.id);
    expect(realMembers).not.toHaveBeenCalled();

    unmount();
  });

  test('Bot 列表和好友申请使用真实 Service，画像仍保留原有链路', async () => {
    const bot = resultBot('legacy-actions');
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([bot]);
    const realProfile = jest.spyOn(collaborationSquareBotService, 'getBotProfile');
    const legacyProfile = jest.spyOn(collaborationSquareService, 'getBotProfile').mockResolvedValue({
      id: bot.id,
      name: bot.name,
      ownerName: bot.ownerName,
      description: bot.description,
      capabilities: [],
    });
    const realFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValue({ status: 'applying' });
    const legacyFriendship = jest.spyOn(collaborationSquareService, 'requestBotFriendship');
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      await result.current.openBotProfile(bot);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
    });

    expect(legacyProfile).toHaveBeenCalledWith(bot.id);
    expect(realProfile).not.toHaveBeenCalled();
    expect(realFriendship).toHaveBeenCalledWith(bot.id, humanContext);
    expect(legacyFriendship).not.toHaveBeenCalled();
    expect(useCollaborationSquareStore.getState().bots[0].relationshipStatus).toBe('applying');

    unmount();
  });

  test('已是好友时创建真实 Bot 会话并使用 Workspace 单聊 URL 导航', async () => {
    const bot = { ...resultBot('bot-1:2088'), relationshipStatus: 'friend' as const };
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([bot]);
    const openConversation = jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockResolvedValue({ sessionId: 'session-1' });
    const legacyConversation = jest.spyOn(collaborationSquareService, 'openBotConversation');
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(openConversation).toHaveBeenCalledWith(bot.id, humanContext);
    expect(legacyConversation).not.toHaveBeenCalled();
    expect(history.push).toHaveBeenCalledWith('/workspace?tab=chat&bot=bot-1%3A2088&session=session-1');

    unmount();
  });

  test('好友申请 bot_not_found 失败时保留 Bot 列表项并恢复按钮状态', async () => {
    const bot = resultBot('bot-request-not-found');
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([bot]);
    jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockRejectedValue(new CollaborationSquareError('network', '目标 Bot 当前不可用，申请未提交，请稍后重试'));
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().bots.map((item) => item.id)).toEqual([bot.id]);
    expect(useCollaborationSquareStore.getState().busyKeys).toEqual([]);

    unmount();
  });

  test('好友申请直接返回 friend 时继续创建真实会话后导航', async () => {
    const bot = resultBot('bot-direct-friend');
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([bot]);
    jest.spyOn(collaborationSquareBotService, 'requestBotFriendship').mockResolvedValue({ status: 'friend' });
    const openConversation = jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockResolvedValue({ sessionId: 'session-direct' });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(openConversation).toHaveBeenCalledWith(bot.id, humanContext);
    expect(history.push).toHaveBeenCalledWith('/workspace?tab=chat&bot=bot-direct-friend&session=session-direct');

    unmount();
  });

  test('复合 Bot ID 创建会话失效时移除完整目标而不是截断 ID', async () => {
    const bot = { ...resultBot('bot-invalid:2088'), relationshipStatus: 'friend' as const };
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([bot]);
    jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockRejectedValue(new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问'));
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().bots).toEqual([]);

    unmount();
  });
});
