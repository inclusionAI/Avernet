/** @jest-environment jsdom */
import { CommunityPersonalCenter } from '@/components/Community/CommunityPersonalCenter';
import type { PersonalCenterTopicContext } from '@/domain/community/types';
import type { UsePersonalCenterResult } from '@/hooks/usePersonalCenter';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { resolveBotNames } from '@/services/collaborationSquare/collaborationSquareApiHelpers';
import { communityService } from '@/services/community';

jest.mock('@/services/collaborationSquare/collaborationSquareApiHelpers', () => ({
  resolveBotNames: jest.fn(),
}));

jest.mock('@/services/community', () => ({
  communityService: {
    listTopics: jest.fn(),
  },
}));

const baseActivity = {
  activityType: 'AUTHORED',
  actor: { actorType: 'BOT', id: 'bot-A' },
  target: { targetType: 'TOPIC', id: 'topic-1' },
  topicId: 'topic-1',
  happenedAt: '2026-10-27T10:00:00Z',
  bodyPreview: 'PRD 风格主题卡片',
  topicContext: {
    title: 'PRD 风格主题卡片',
    bodyPreview: '这是一个带完整上下文的帖子内容预览。',
    topicType: 'discussion',
    status: 'open',
    author: { actorType: 'BOT', id: 'bot-A' },
    authorDisplayName: '协作助手',
    replyCount: 12,
    likeCount: 34,
    viewCount: 56,
    favoriteCount: 7,
    isHot: true,
    createdAt: '2026-10-27T09:00:00Z',
    latestReplyAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  },
} as const;

const mockedCenter: UsePersonalCenterResult = {
  ownerUserId: '149844',
  identity: {
    userId: '149844',
    displayName: '国科',
    avatarUrl: 'https://example.test/guoke.png',
    online: true,
  },
  activityType: 'AUTHORED',
  page: 1,
  pageSize: 10,
  activityPage: {
    items: [{ ...baseActivity }],
    total: 1,
  },
  loading: false,
  error: null,
  setActivityType: jest.fn(),
  goToPage: jest.fn(),
  reload: jest.fn(),
  unfollow: jest.fn(),
};

jest.mock('@/hooks/usePersonalCenter', () => ({
  usePersonalCenter: () => mockedCenter,
  PERSONAL_CENTER_PAGE_SIZE: 10,
}));

describe('CommunityPersonalCenter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedCenter.activityType = 'AUTHORED';
    mockedCenter.activityPage = { items: [{ ...baseActivity }], total: 1 };
    mockedCenter.unfollow = jest.fn();
    jest.mocked(resolveBotNames).mockImplementation(
      () => new Promise<Record<string, string>>(() => {}),
    );
  });

  it('renders topic activities with PRD content, stats, publisher, and elapsed time', () => {
    const onOpenTopic = jest.fn();
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />);

    expect(screen.getByText('我的')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument();
    expect(screen.getByAltText('国科')).toHaveAttribute('src', 'https://example.test/guoke.png');
    expect(screen.getByText('国科')).toBeInTheDocument();
    expect(
      within(screen.getByRole('group', { name: '我的内容' }))
        .getAllByRole('button')
        .map((item) => item.textContent),
    ).toEqual(['我的帖子', '我的回贴', '我的收藏', '我的点赞', '我的关注', '浏览历史']);
    expect(screen.getByText('PRD 风格主题卡片')).toBeInTheDocument();
    expect(screen.getByText('这是一个带完整上下文的帖子内容预览。')).toBeInTheDocument();
    expect(screen.getByText('讨论')).toBeInTheDocument();
    expect(screen.getByText('协作助手')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('34')).toBeInTheDocument();
    expect(screen.getByText('最新回复：2 小时前')).toBeInTheDocument();

    fireEvent.click(screen.getByText('PRD 风格主题卡片'));
    expect(onOpenTopic).toHaveBeenCalledWith('topic-1');
  });

  it('个人中心帖子作者为 Bot 时优先展示解析出的 Bot 名称，而不是 Bot ID', async () => {
    mockedCenter.activityType = 'FAVORITED';
    mockedCenter.activityPage = {
      items: [
        {
          ...baseActivity,
          activityType: 'FAVORITED',
          topicContext: {
            ...baseActivity.topicContext,
            author: { actorType: 'BOT', id: 'bot-post-author' },
            authorDisplayName: 'bot-post-author',
          },
        },
      ],
      total: 1,
    };
    jest
      .mocked(resolveBotNames)
      .mockResolvedValueOnce({ 'bot-post-author': '内容创作 Bot' });

    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={jest.fn()} />);

    expect(await screen.findByText('内容创作 Bot')).toBeInTheDocument();
    expect(screen.queryByText('bot-post-author')).not.toBeInTheDocument();
    expect(resolveBotNames).toHaveBeenCalledWith(['bot-A', 'bot-post-author']);
  });

  it('truncates favorite and browse activity content to one line', () => {
    const onOpenTopic = jest.fn();
    mockedCenter.activityType = 'FAVORITED';
    mockedCenter.activityPage = {
      items: [{ ...baseActivity, activityType: 'FAVORITED' }],
      total: 1,
    };
    const favoriteView = render(
      <CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />,
    );
    expect(
      screen.getByText('这是一个带完整上下文的帖子内容预览。'),
    ).toHaveClass('truncate');

    favoriteView.unmount();
    mockedCenter.activityType = 'VIEWED';
    mockedCenter.activityPage = {
      items: [{ ...baseActivity, activityType: 'VIEWED' }],
      total: 1,
    };
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />);
    expect(
      screen.getByText('这是一个带完整上下文的帖子内容预览。'),
    ).toHaveClass('truncate');
  });

  it('renders follow identities, their topics, and supports unfollowing', async () => {
    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'FOLLOWED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'HUMAN', id: 'user-b' },
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: '张三',
          followedContext: {
            targetType: 'HUMAN',
            targetId: 'user-b',
            displayName: '张三',
            avatarUrl: 'https://example.test/zhangsan.png',
          },
        },
        {
          activityType: 'FOLLOWED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'BOT', id: 'bot-b' },
          happenedAt: '2026-10-27T10:01:00Z',
          bodyPreview: '协作 Bot',
          followedContext: {
            targetType: 'BOT',
            targetId: 'bot-b',
            displayName: '协作 Bot',
          },
        },
      ],
      total: 2,
    };
    jest
      .mocked(communityService.listTopics)
      .mockResolvedValueOnce({
        items: [
          {
            id: 'topic-2',
            title: '张三的主题',
            body: '正文',
            status: 'open',
            topicType: 'discussion',
            author: { type: 'human', id: 'user-b', displayName: '张三' },
            createdAt: '2026-10-27T09:00:00Z',
            isMine: false,
            canClose: false,
            canEdit: false,
            canDelete: false,
          },
        ],
        total: 1,
      })
      .mockResolvedValue({ items: [], total: 0 });
    const onOpenTopic = jest.fn();
    const onUnfollow = jest.fn().mockResolvedValue(undefined);
    mockedCenter.unfollow = onUnfollow;
    render(
      <CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />,
    );

    expect(screen.getByText('关注的用户')).toBeInTheDocument();
    expect(screen.getByText('关注的 Bot')).toBeInTheDocument();
    expect(screen.queryByText('暂无关注的用户')).not.toBeInTheDocument();
    expect(screen.queryByText('暂无关注的 Bot')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('张三'));

    await waitFor(() =>
      expect(communityService.listTopics).toHaveBeenCalledWith({
        authorId: 'user-b',
        limit: 20,
      }),
    );
    expect(screen.getByText('张三 的帖子')).toBeInTheDocument();
    fireEvent.click(screen.getByText('张三的主题'));
    expect(onOpenTopic).toHaveBeenCalledWith('topic-2');
    fireEvent.click(screen.getByRole('button', { name: '取消关注 张三' }));
    expect(onUnfollow).toHaveBeenCalledTimes(1);
  });

  it('关注 Bot 时使用批量接口解析展示名称，避免直接展示 bot id', async () => {
    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'FOLLOWED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'BOT', id: 'bot-lookup' },
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: 'bot-lookup',
          followedContext: {
            targetType: 'BOT',
            targetId: 'bot-lookup',
            displayName: 'bot-lookup',
          },
        },
      ],
      total: 1,
    };
    jest
      .mocked(resolveBotNames)
      .mockResolvedValueOnce({ 'bot-lookup': '自动化协作 Bot' });

    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={jest.fn()} />);

    expect(await screen.findByText('自动化协作 Bot')).toBeInTheDocument();
    expect(screen.queryByText('bot-lookup')).not.toBeInTheDocument();
    expect(resolveBotNames).toHaveBeenCalledWith(['bot-lookup']);
  });

  it('groups replies by topic and explains nested reply targets', () => {
    mockedCenter.activityType = 'REPLIED';
    const topicContext: PersonalCenterTopicContext = {
      title: '误报率怎么压下来',
      bodyPreview: '求助一个关于误报率治理的问题。',
      topicType: 'help',
      status: 'open',
      author: { actorType: 'BOT', id: 'bot-risk' },
      authorDisplayName: '风控分析 Bot',
      replyCount: 8,
      likeCount: 5,
      viewCount: 12,
      favoriteCount: 2,
      isHot: false,
      createdAt: '2026-10-27T09:00:00Z',
      latestReplyAt: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    };
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'REPLIED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'TOPIC', id: 'topic-2' },
          topicId: 'topic-2',
          postId: 'post-direct',
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: '我直接回复帖子本身的内容。',
          topicContext,
        },
        {
          activityType: 'REPLIED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'POST', id: 'post-parent' },
          topicId: 'topic-2',
          postId: 'post-child',
          happenedAt: '2026-10-27T10:05:00Z',
          bodyPreview: '我回复另一个回复的内容。',
          repliedToPost: {
            postId: 'post-parent',
            author: { actorType: 'BOT', id: 'bot-risk' },
            authorDisplayName: '风控分析 Bot',
            bodyPreview: '先按 true/false positive 分类标注误报。',
          },
          topicContext,
        },
      ],
      total: 2,
    };
    const onOpenTopic = jest.fn();
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />);

    expect(screen.getAllByText('误报率怎么压下来')).toHaveLength(1);
    expect(screen.getByText('我直接回复帖子本身的内容。')).toBeInTheDocument();
    expect(screen.getByText('我回复另一个回复的内容。')).toBeInTheDocument();
    expect(
      screen.getByText('回复 @风控分析 Bot：先按 true/false positive 分类标注误报。'),
    ).toBeInTheDocument();
    expect(screen.getByText('我直接回复帖子本身的内容。')).toHaveClass('truncate');
    expect(screen.getByText('我回复另一个回复的内容。')).toHaveClass('truncate');
    expect(
      screen.getByText('回复 @风控分析 Bot：先按 true/false positive 分类标注误报。'),
    ).toHaveClass('truncate');

    fireEvent.click(screen.getByText('误报率怎么压下来'));
    expect(onOpenTopic).toHaveBeenCalledWith('topic-2');
  });
});
