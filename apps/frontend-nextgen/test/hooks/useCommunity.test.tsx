/** @jest-environment jsdom */
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { CommunityReply, CommunityTopic } from '@/domain/community/types';
import { COMMUNITY_PAGE_SIZE, COMMUNITY_REPLY_PAGE_SIZE, useCommunity } from '@/hooks/useCommunity';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { communityService } from '@/services/community';
import { useCommunityStore } from '@/stores/communityStore';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/community', () => ({
  communityService: {
    listTopics: jest.fn(),
    getTopic: jest.fn(),
    listReplies: jest.fn(),
    createReply: jest.fn(),
    createTopic: jest.fn(),
    closeTopic: jest.fn(),
    setPostHelpful: jest.fn(),
    createInteraction: jest.fn(),
    getInteractionSummaryBatch: jest.fn(),
  },
}));
jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({
  useOwnedBots: jest.fn(() => ({
    chatBots: [],
    hasAgentCodingBots: false,
    isLoading: false,
    error: null,
    reload: jest.fn(),
  })),
}));
jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn(), notifyError: jest.fn() }));

const mockedService = communityService as jest.Mocked<typeof communityService>;
const mockedIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '社区主题',
  body: '主题正文',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'human', id: '900003', displayName: '顾客' },
  replyCount: 1,
  latestActivityAt: '2026-10-06T06:20:00Z',
  createdAt: '2026-10-05T08:30:00Z',
  isMine: true,
  canClose: true,
  canEdit: true,
  canDelete: true,
};
const reply: CommunityReply = {
  id: 'reply-1',
  topicId: topic.id,
  body: '回复',
  author: { type: 'bot', id: 'bot-1', displayName: '协作助手' },
  createdAt: '2026-10-06T06:20:00Z',
  helpful: false,
};
const pollTopic: CommunityTopic = {
  ...topic,
  topicType: 'poll',
  topicConfig: { poll: { selection: 'multi', options: ['方案 A', '方案 B'] } },
};
const helpTopic: CommunityTopic = {
  ...topic,
  topicType: 'help',
};

async function flushDebouncedLoad() {
  await act(async () => {
    jest.advanceTimersByTime(200);
    await Promise.resolve();
  });
}

describe('useCommunity', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    useCommunityStore.getState().reset();
    mockedIdentity.mockReturnValue({
      identity: { userId: '900003', displayName: '当前用户', avatarUrl: 'avatar.png', online: true },
      status: 'ready',
    });
    mockedService.listTopics.mockResolvedValue({ items: [topic], total: 1 });
    mockedService.listReplies.mockResolvedValue({ items: [reply], total: 1 });
    mockedService.setPostHelpful.mockResolvedValue(undefined);
    mockedService.createReply.mockResolvedValue('created-reply');
    mockedService.createInteraction.mockResolvedValue(undefined);
    mockedService.getInteractionSummaryBatch.mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    useCommunityStore.getState().reset();
    jest.useRealTimers();
  });

  test('进入页面后防抖加载主题，按 query/scope 请求（limit=10）', async () => {
    expect(COMMUNITY_PAGE_SIZE).toBe(10);
    useCommunityStore.setState({ query: '  Bot  ', scope: 'mine' });
    const { result } = renderHook(() => useCommunity());

    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(mockedService.listTopics).toHaveBeenCalledWith({
      search: '  Bot  ',
      scope: 'mine',
      topicType: 'all',
      authorId: '900003',
      offset: 0,
      limit: COMMUNITY_PAGE_SIZE,
    });
    expect(result.current.topics).toEqual([topic]);
    expect(result.current.hasMore).toBe(false);
  });

  test('类型筛选随列表请求下发', async () => {
    useCommunityStore.setState({ topicType: 'share' });
    const { result } = renderHook(() => useCommunity());

    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(mockedService.listTopics).toHaveBeenCalledWith(
      expect.objectContaining({ topicType: 'share', offset: 0, limit: COMMUNITY_PAGE_SIZE }),
    );
  });

  test('使用当前 Human 身份发布主题，成功后加入列表并提示', async () => {
    mockedService.listTopics.mockResolvedValue({ items: [], total: 0 });
    mockedService.createTopic.mockResolvedValue(topic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.publishTopic({ title: '标题', body: '正文' });
    });

    expect(succeeded).toBe(true);
    expect(mockedService.createTopic).toHaveBeenCalledWith({
      title: '标题',
      body: '正文',
      topicType: undefined,
      poll: undefined,
      authorId: '900003',
      authorName: '当前用户',
      authorAvatarUrl: 'avatar.png',
    });
    expect(result.current.topics[0]).toEqual(topic);
    expect(notifySuccess).toHaveBeenCalledWith('主题发布成功');
  });

  test('当前类型筛选不匹配时发布成功但不插入列表', async () => {
    const sharedTopic: CommunityTopic = {
      ...topic,
      id: 'share-1',
      topicType: 'share',
    };
    useCommunityStore.setState({ topicType: 'help' });
    mockedService.listTopics.mockResolvedValue({ items: [], total: 0 });
    mockedService.createTopic.mockResolvedValue(sharedTopic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      expect(
        await result.current.publishTopic({
          title: '分享资料',
          body: '请查收',
          topicType: 'share',
        }),
      ).toBe(true);
    });

    expect(result.current.topics).toEqual([]);
    expect(notifySuccess).toHaveBeenCalledWith('主题发布成功');
  });

  test('发布投票主题时透传 Topic 级 poll 配置', async () => {
    mockedService.listTopics.mockResolvedValue({ items: [], total: 0 });
    mockedService.createTopic.mockResolvedValue(topic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.publishTopic({
        title: '选择协作方案',
        body: '请说明理由',
        topicType: 'poll',
        poll: {
          selection: 'multi',
          options: ['方案 A', '方案 B'],
          deadlineAt: '2026-10-20T12:00:00Z',
        },
      });
    });

    expect(succeeded).toBe(true);
    expect(mockedService.createTopic).toHaveBeenCalledWith({
      title: '选择协作方案',
      body: '请说明理由',
      topicType: 'poll',
      poll: {
        selection: 'multi',
        options: ['方案 A', '方案 B'],
        deadlineAt: '2026-10-20T12:00:00Z',
      },
      authorId: '900003',
      authorName: '当前用户',
      authorAvatarUrl: 'avatar.png',
    });
  });

  test('打开详情加载主题与第 1 页回复（每页 10 条），结帖带 author_id：优先主题作者', async () => {
    expect(COMMUNITY_REPLY_PAGE_SIZE).toBe(10);
    mockedService.getTopic.mockResolvedValue(topic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.openTopic(topic);
    });
    expect(result.current.selectedTopic).toEqual(topic);
    expect(mockedService.listReplies).toHaveBeenCalledWith(topic.id, { page: 1, pageSize: COMMUNITY_REPLY_PAGE_SIZE });
    expect(result.current.replies).toEqual([reply]);
    expect(result.current.replyPage).toBe(1);

    await act(async () => {
      await result.current.closeTopic();
    });
    expect(mockedService.closeTopic).toHaveBeenCalledWith(topic.id, '900003');
    expect(result.current.selectedTopic).toMatchObject({ status: 'closed', canClose: false });
    expect(result.current.topics[0]).toMatchObject({ status: 'closed', canClose: false });
    expect(notifySuccess).toHaveBeenCalledWith('主题已结帖');
  });

  test('未知作者主体现无法结帖（canClose 由 author_id 派生为 false → 守卫拦截，不发起结帖）', async () => {
    const unknownAuthor: CommunityTopic = { ...topic, author: { ...topic.author, id: 'unknown' } };
    mockedService.getTopic.mockResolvedValue(unknownAuthor);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.openTopic(unknownAuthor);
    });
    expect(result.current.selectedTopic?.canClose).toBe(false);

    let closed = false;
    await act(async () => {
      closed = await result.current.closeTopic();
    });
    expect(closed).toBe(false);
    expect(mockedService.closeTopic).not.toHaveBeenCalled();
  });

  test('Human 可提交主题回复：投票携带结构化 poll_selection 并刷新回复页', async () => {
    mockedService.getTopic.mockResolvedValue(pollTopic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.openTopic(pollTopic);
    });
    expect(mockedService.createReply).not.toHaveBeenCalled();
    mockedService.listReplies.mockClear();

    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.createReply({
        body: '投票选择：方案 A',
        pollSelection: [0],
      });
    });

    expect(succeeded).toBe(true);
    expect(mockedService.createReply).toHaveBeenCalledWith({
      topicId: pollTopic.id,
      body: '投票选择：方案 A',
      pollSelection: [0],
      authorId: '900003',
      authorName: '当前用户',
    });
    expect(mockedService.listReplies).toHaveBeenLastCalledWith(pollTopic.id, {
      page: 1,
      pageSize: COMMUNITY_REPLY_PAGE_SIZE,
    });
    expect(result.current.replySaving).toBe(false);
    expect(notifySuccess).toHaveBeenCalledWith('回复发布成功');
  });

  test('goToReplyPage 切换回帖分页', async () => {
    mockedService.getTopic.mockResolvedValue(topic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.openTopic(topic);
    });
    mockedService.listReplies.mockResolvedValueOnce({ items: [reply], total: 25 });

    await act(async () => {
      await result.current.goToReplyPage(2);
    });
    expect(mockedService.listReplies).toHaveBeenLastCalledWith(topic.id, {
      page: 2,
      pageSize: COMMUNITY_REPLY_PAGE_SIZE,
    });
    expect(result.current.replyPage).toBe(2);
  });

  test('求助作者标记有帮助后刷新当前回复页并提示，关闭状态也允许操作', async () => {
    const closedHelpTopic: CommunityTopic = { ...helpTopic, status: 'closed', canClose: false };
    mockedService.getTopic.mockResolvedValue(closedHelpTopic);
    mockedService.listReplies.mockResolvedValue({ items: [reply], total: 25 });
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.openTopic(closedHelpTopic);
    });
    await act(async () => {
      await result.current.goToReplyPage(2);
    });
    await act(async () => {
      expect(await result.current.setPostHelpful(reply.id, true)).toBe(true);
    });

    expect(mockedService.setPostHelpful).toHaveBeenCalledWith(
      closedHelpTopic.id,
      reply.id,
      'HUMAN',
      closedHelpTopic.author.id,
      true,
    );
    expect(mockedService.listReplies).toHaveBeenLastCalledWith(closedHelpTopic.id, {
      page: 2,
      pageSize: COMMUNITY_REPLY_PAGE_SIZE,
    });
    expect(notifySuccess).toHaveBeenCalledWith('已标记为有帮助');
    expect(result.current.helpfulPostId).toBeNull();
  });

  test('取消有帮助提示成功，失败时展示操作失败并清理 loading 状态', async () => {
    mockedService.getTopic.mockResolvedValue(helpTopic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.openTopic(helpTopic);
    });

    await act(async () => {
      expect(await result.current.setPostHelpful(reply.id, false)).toBe(true);
    });
    expect(notifySuccess).toHaveBeenCalledWith('已取消有帮助');

    mockedService.setPostHelpful.mockRejectedValueOnce(new Error('后端拒绝'));
    await act(async () => {
      expect(await result.current.setPostHelpful(reply.id, true)).toBe(false);
    });
    expect(notifyError).toHaveBeenCalledWith('后端拒绝', { title: '操作失败' });
    expect(result.current.helpfulPostId).toBeNull();
  });

  test('非本人或非求助主题不会发起有帮助请求', async () => {
    const otherHelpTopic: CommunityTopic = {
      ...helpTopic,
      isMine: false,
      canClose: false,
      canEdit: false,
      canDelete: false,
      author: { ...helpTopic.author, id: 'other-user' },
    };
    mockedService.getTopic.mockResolvedValue(otherHelpTopic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.openTopic(otherHelpTopic);
    });
    await act(async () => {
      expect(await result.current.setPostHelpful(reply.id, true)).toBe(false);
    });
    expect(mockedService.setPostHelpful).not.toHaveBeenCalled();

    mockedService.getTopic.mockResolvedValue(topic);
    await act(async () => {
      await result.current.openTopic(topic);
    });
    await act(async () => {
      expect(await result.current.setPostHelpful(reply.id, true)).toBe(false);
    });
    expect(mockedService.setPostHelpful).not.toHaveBeenCalled();
  });

  test('求助结帖使用已解决文案，失败时使用标记失败文案', async () => {
    mockedService.getTopic.mockResolvedValue(helpTopic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.openTopic(helpTopic);
    });
    await act(async () => {
      expect(await result.current.closeTopic()).toBe(true);
    });
    expect(notifySuccess).toHaveBeenCalledWith('求助已标记为已解决');

    mockedService.getTopic.mockResolvedValue(helpTopic);
    mockedService.closeTopic.mockRejectedValueOnce(new Error('关闭失败'));
    await act(async () => {
      await result.current.openTopic(helpTopic);
    });
    await act(async () => {
      expect(await result.current.closeTopic()).toBe(false);
    });
    expect(notifyError).toHaveBeenCalledWith('关闭失败', { title: '标记已解决失败' });
  });

  test('BOT 作者名兜底为本人 owned bots 的 bot_name（后端 §4.1 display_name 缺失时前端补）', async () => {
    const useOwnedBotsMock = jest.requireMock('@/pages/Workspace/hooks/useOwnedBots').useOwnedBots as jest.Mock;
    useOwnedBotsMock.mockReturnValue({
      chatBots: [{ botId: 'bot-x', realBotId: 'bot-x', displayName: '我的研究助手', online: true, chatable: true }],
      hasAgentCodingBots: false,
      isLoading: false,
      error: null,
      reload: jest.fn(),
    });
    const botTopic: CommunityTopic = {
      id: 'topic-bot-1',
      title: 'Bot 发的主题',
      body: '正文',
      status: 'open',
      topicType: 'discussion',
      author: { type: 'bot', id: 'bot-x', displayName: 'bot-x' },
      replyCount: 0,
      latestActivityAt: '2026-10-08T08:00:00Z',
      createdAt: '2026-10-08T08:00:00Z',
      isMine: false,
      canClose: false,
      canEdit: false,
      canDelete: false,
    };
    mockedService.listTopics.mockResolvedValue({ items: [botTopic], total: 1 });

    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.topics).toHaveLength(1));

    expect(result.current.topics[0].author.id).toBe('bot-x');
    expect(result.current.topics[0].author.displayName).toBe('我的研究助手');
  });

  test('发布失败不改列表并展示错误提示', async () => {
    mockedService.createTopic.mockRejectedValue(new Error('后端拒绝'));
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      expect(await result.current.publishTopic({ title: '标题', body: '正文' })).toBe(false);
    });

    expect(result.current.topics).toEqual([topic]);
    expect(notifyError).toHaveBeenCalledWith('后端拒绝', { title: '发布失败' });
  });
});
