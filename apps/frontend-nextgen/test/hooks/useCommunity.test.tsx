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
    createTopic: jest.fn(),
    closeTopic: jest.fn(),
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
  author: { type: 'human', id: '900003', displayName: '顾客' },
  replyCount: 1,
  latestActivityAt: '2026-10-06T06:20:00Z',
  createdAt: '2026-10-05T08:30:00Z',
  isMine: true,
  canClose: true,
};
const reply: CommunityReply = {
  id: 'reply-1',
  topicId: topic.id,
  body: '回复',
  author: { type: 'bot', id: 'bot-1', displayName: '协作助手' },
  createdAt: '2026-10-06T06:20:00Z',
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
  });

  afterEach(() => {
    cleanup();
    useCommunityStore.getState().reset();
    jest.useRealTimers();
  });

  test('进入页面后防抖加载主题，按 query/scope 请求（limit=COMMUNITY_PAGE_SIZE）', async () => {
    useCommunityStore.setState({ query: '  Bot  ', scope: 'mine' });
    const { result } = renderHook(() => useCommunity());

    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(mockedService.listTopics).toHaveBeenCalledWith({
      search: '  Bot  ',
      scope: 'mine',
      authorId: '900003',
      offset: 0,
      limit: COMMUNITY_PAGE_SIZE,
    });
    expect(result.current.topics).toEqual([topic]);
    expect(result.current.hasMore).toBe(false);
  });

  test('使用当前 Human 身份发布主题，成功后加入列表并提示', async () => {
    mockedService.listTopics.mockResolvedValue({ items: [], total: 0 });
    mockedService.createTopic.mockResolvedValue(topic);
    const { result } = renderHook(() => useCommunity());
    await flushDebouncedLoad();
    await waitFor(() => expect(result.current.loading).toBe(false));

    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.publishTopic('标题', '正文');
    });

    expect(succeeded).toBe(true);
    expect(mockedService.createTopic).toHaveBeenCalledWith({
      title: '标题',
      body: '正文',
      authorId: '900003',
      authorName: '当前用户',
      authorAvatarUrl: 'avatar.png',
    });
    expect(result.current.topics[0]).toEqual(topic);
    expect(notifySuccess).toHaveBeenCalledWith('主题发布成功');
  });

  test('打开详情加载主题与第 1 页回复（page=1/pageSize=COMMUNITY_REPLY_PAGE_SIZE），结帖带 author_id：优先主题作者', async () => {
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
      author: { type: 'bot', id: 'bot-x', displayName: 'bot-x' },
      replyCount: 0,
      latestActivityAt: '2026-10-08T08:00:00Z',
      createdAt: '2026-10-08T08:00:00Z',
      isMine: false,
      canClose: false,
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
      expect(await result.current.publishTopic('标题', '正文')).toBe(false);
    });

    expect(result.current.topics).toEqual([topic]);
    expect(notifyError).toHaveBeenCalledWith('后端拒绝', { title: '发布失败' });
  });
});
