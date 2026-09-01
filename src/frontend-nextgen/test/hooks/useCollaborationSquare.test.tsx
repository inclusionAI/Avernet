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
const viewerFields = { viewerActorType: 'human', viewerActorId: '327325' };

interface PendingRequest {
  query: PublicBotSearchQuery | undefined;
  context: typeof humanContext | undefined;
  signal: AbortSignal | undefined;
  resolve: (page: { items: PublicBot[]; total: number }) => void;
}

const resultBot = (id: string): PublicBot => ({
  id,
  name: id,
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
});

const botPage = (items: PublicBot[], total = items.length) => ({ items, total });

const resultGroup = (id: string): PublicGroup => ({
  id,
  name: id,
  ownerBotName: '主理 Bot',
  ownerUserName: 'Owner',
  typeLabel: '协作群',
  memberCount: 2,
  goal: '',
  memberListVisibility: 'visible',
  canCreateSession: true,
});

const groupPage = (items: PublicGroup[], total = items.length) => ({ items, total });

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

  test('名称搜索按 1 秒防抖、取消旧请求并阻止过期结果覆盖', async () => {
    const pending: PendingRequest[] = [];
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(
      (query, context, signal) =>
        new Promise<{ items: PublicBot[]; total: number }>((resolve) => {
          pending.push({ query, context, signal, resolve });
        }),
    );
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    act(() => {
      jest.advanceTimersByTime(0);
    });
    expect(pending).toHaveLength(1);
    expect(pending[0].query).toEqual({ page: 1, pageSize: 24, ...viewerFields });
    expect(pending[0].context).toEqual(humanContext);

    act(() => {
      result.current.setQuery('bot', ' workflow ');
    });
    expect(pending[0].signal?.aborted).toBe(true);
    act(() => {
      jest.advanceTimersByTime(999);
    });
    expect(pending).toHaveLength(1);
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(pending).toHaveLength(2);
    expect(pending[1].query).toEqual({ search: 'workflow', page: 1, pageSize: 24, ...viewerFields });

    await act(async () => {
      pending[1].resolve(botPage([resultBot('new')]));
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['new']);

    await act(async () => {
      pending[0].resolve(botPage([resultBot('stale')]));
    });
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toEqual(['new']);

    unmount();
  });

  test('首屏使用 24 条并在加载更多时请求下一页且去重合并', async () => {
    const listBots = jest.spyOn(collaborationSquareBotService, 'listBotPage').mockImplementation(async (query) => {
      if (query?.page === 1)
        return botPage(
          Array.from({ length: 24 }, (_, index) => resultBot(`page-1-${index}`)),
          26,
        );
      return botPage([resultBot('page-2-0'), resultBot('page-1-0')], 26);
    });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listBots).toHaveBeenLastCalledWith(
      { page: 1, pageSize: 24, ...viewerFields },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadMore();
    });

    expect(listBots).toHaveBeenLastCalledWith(
      { page: 2, pageSize: 24, ...viewerFields },
      humanContext,
      expect.any(AbortSignal),
    );
    expect(useCollaborationSquareStore.getState().bots).toHaveLength(25);
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.id)).toContain('page-2-0');
    expect(result.current.hasMore).toBe(false);

    unmount();
  });

  test('智能搜索对非空能力描述调用真实 Discovery，空关键词只展示提示不发请求', async () => {
    const listBots = jest
      .spyOn(collaborationSquareBotService, 'listBotPage')
      .mockResolvedValue(botPage([resultBot('default')]));
    const discoverBots = jest
      .spyOn(collaborationSquareBotService, 'discoverBots')
      .mockResolvedValue([
        { ...resultBot('code'), name: '研发助手', description: '代码审查与质量改进', capabilities: ['代码审查'] },
      ]);
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(listBots).toHaveBeenLastCalledWith(
      { page: 1, pageSize: 24, ...viewerFields },
      humanContext,
      expect.any(AbortSignal),
    );

    // 切到智能搜索但未输入关键词：不发请求、清空列表，交由面板展示输入提示。
    act(() => {
      result.current.setBotSearchMode('smart');
    });
    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(discoverBots).not.toHaveBeenCalled();
    expect(result.current.visibleBots).toHaveLength(0);

    act(() => {
      result.current.setQuery('bot', '代码');
    });
    await act(async () => {
      jest.advanceTimersByTime(999);
    });
    expect(discoverBots).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(1);
    });

    expect(discoverBots).toHaveBeenLastCalledWith(
      { keyword: '代码', topK: 20, minScore: 0.1, runtimeState: 'online', ...viewerFields },
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
    // 清空关键词再次回到提示态：不调用 Discovery、也不退回默认目录。
    expect(discoverBots).toHaveBeenCalledTimes(1);
    expect(result.current.visibleBots).toHaveLength(0);

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
    const listGroups = jest
      .spyOn(collaborationSquareGroupService, 'listGroupPage')
      .mockResolvedValue(groupPage([group]));
    const realMembers = jest.spyOn(collaborationSquareGroupService, 'listGroupMembers');
    const legacyMembers = jest.spyOn(collaborationSquareService, 'listGroupMembers').mockResolvedValue([]);
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(listGroups).toHaveBeenLastCalledWith({ offset: 0, limit: 24 }, expect.any(AbortSignal));

    act(() => {
      result.current.setQuery('group', ' 公开 ');
    });
    await act(async () => {
      jest.advanceTimersByTime(999);
    });
    expect(listGroups).toHaveBeenCalledTimes(1);
    await act(async () => {
      jest.advanceTimersByTime(1);
    });
    expect(listGroups).toHaveBeenLastCalledWith({ search: '公开', offset: 0, limit: 24 }, expect.any(AbortSignal));

    await act(async () => {
      await result.current.openGroupMembers(group);
    });
    // 公开群成员经真实 service 调群详情 participants 反查（见 adapter.listGroupMembers），
    // 不再走 legacy/mock 链路（mock 在 dev/pre 会 404 误报 target_invalid）。
    expect(realMembers).toHaveBeenCalledWith(group.id);
    expect(legacyMembers).not.toHaveBeenCalled();

    unmount();
  });

  test('公开群在接近列表底部时按 offset=24 加载下一页并去重合并', async () => {
    const firstPage = Array.from({ length: 24 }, (_, index) => resultGroup(`group-page-1-${index}`));
    const secondPage = [resultGroup('group-page-2-0'), resultGroup(firstPage[0].id)];
    const listGroups = jest
      .spyOn(collaborationSquareGroupService, 'listGroupPage')
      .mockImplementation(async (query) =>
        query?.offset === 0 ? groupPage(firstPage, 26) : groupPage(secondPage, 26),
      );
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadMore();
    });

    expect(listGroups).toHaveBeenLastCalledWith({ offset: 24, limit: 24 }, expect.any(AbortSignal));
    expect(useCollaborationSquareStore.getState().groups).toHaveLength(25);
    expect(useCollaborationSquareStore.getState().groups.map((item) => item.id)).toContain('group-page-2-0');
    expect(result.current.hasMore).toBe(false);

    unmount();
  });

  test('公开群创建会话调用真实 Group Service，并只使用服务端返回的会话信息导航', async () => {
    const group: PublicGroup = {
      id: 'group-real-session',
      name: '公开群',
      ownerBotName: '主理 Bot',
      ownerUserName: 'Owner',
      typeLabel: '协作群',
      memberCount: 2,
      goal: '',
      memberListVisibility: 'visible',
      canCreateSession: true,
    };
    jest.spyOn(collaborationSquareGroupService, 'listGroupPage').mockResolvedValue(groupPage([group]));
    const createGroupSession = jest
      .spyOn(collaborationSquareGroupService, 'createGroupSession')
      .mockResolvedValue({ sessionId: 'session-real-group', defaultRole: '顾问' });
    const legacyCreateGroupSession = jest.spyOn(collaborationSquareService, 'createGroupSession');
    const { result, unmount } = renderHook(() => useCollaborationSquare('group'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    act(() => {
      result.current.createGroupSession(group);
    });
    expect(result.current.createSessionTarget).toEqual(group);

    await act(async () => {
      result.current.submitCreateSession({ title: '测试会话', query: '测试协作目标' });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createGroupSession).toHaveBeenCalledWith(group.id, humanContext, {
      title: '测试会话',
      query: '测试协作目标',
    });
    expect(legacyCreateGroupSession).not.toHaveBeenCalled();
    expect(history.push).toHaveBeenCalledWith(
      '/workspace?tab=group&group=group-real-session&session=session-real-group&defaultRole=%E9%A1%BE%E9%97%AE',
    );

    unmount();
  });

  test('Bot 列表和好友申请使用真实 Service，画像仍保留原有链路', async () => {
    const bot = resultBot('legacy-actions');
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
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

  test('好友申请优先把搜索结果中的 friendRequestBotId 传给真实 Service', async () => {
    const bot = { ...resultBot('default'), friendRequestBotId: 'default:366656' };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    const requestFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValue({ status: 'applying' });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
    });

    expect(requestFriendship).toHaveBeenCalledWith(bot.id, humanContext, 'default:366656');
    unmount();
  });

  test('重复原始 Bot ID 时只锁定并更新发起申请的目标卡片', async () => {
    const botA = { ...resultBot('default'), name: 'Bot A', friendRequestBotId: 'default:entity-a' };
    const botB = { ...resultBot('default'), name: 'Bot B', friendRequestBotId: 'default:entity-b' };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([botA, botB]));
    let resolveRequest: ((value: { status: 'applying' }) => void) | undefined;
    const requestFriendship = jest.spyOn(collaborationSquareBotService, 'requestBotFriendship').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(botA);
      await Promise.resolve();
    });

    expect(requestFriendship).toHaveBeenCalledWith(botA.id, humanContext, botA.friendRequestBotId);
    expect(useCollaborationSquareStore.getState().busyKeys).toEqual(['bot:default:entity-a']);
    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.relationshipStatus)).toEqual(['none', 'none']);

    await act(async () => {
      resolveRequest?.({ status: 'applying' });
      await Promise.resolve();
    });

    expect(useCollaborationSquareStore.getState().bots.map((bot) => bot.relationshipStatus)).toEqual([
      'applying',
      'none',
    ]);
    expect(useCollaborationSquareStore.getState().busyKeys).toEqual([]);
    unmount();
  });

  test('已是好友时创建真实 Bot 会话并使用 Workspace 单聊 URL 导航', async () => {
    const bot = { ...resultBot('bot-1:2088'), relationshipStatus: 'friend' as const };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
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

  test('自有公开 Bot 直接创建会话，不发起好友申请', async () => {
    const bot = { ...resultBot('owned-bot'), isOwnedByViewer: true };
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
    const requestFriendship = jest.spyOn(collaborationSquareBotService, 'requestBotFriendship');
    const openConversation = jest
      .spyOn(collaborationSquareBotService, 'openBotConversation')
      .mockResolvedValue({ sessionId: 'session-owned' });
    const { result, unmount } = renderHook(() => useCollaborationSquare('bot'));

    await act(async () => {
      jest.advanceTimersByTime(0);
    });
    await act(async () => {
      result.current.primaryBotAction(bot);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requestFriendship).not.toHaveBeenCalled();
    expect(openConversation).toHaveBeenCalledWith(bot.id, humanContext);
    expect(history.push).toHaveBeenCalledWith('/workspace?tab=chat&bot=owned-bot&session=session-owned');

    unmount();
  });

  test('好友申请 bot_not_found 失败时保留 Bot 列表项并恢复按钮状态', async () => {
    const bot = resultBot('bot-request-not-found');
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
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
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
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
    jest.spyOn(collaborationSquareBotService, 'listBotPage').mockResolvedValue(botPage([bot]));
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
