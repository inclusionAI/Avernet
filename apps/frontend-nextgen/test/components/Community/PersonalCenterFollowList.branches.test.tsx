/** @jest-environment jsdom */
import { FollowList } from '@/components/Community/CommunityPersonalCenter/FollowList';
import type { CommunityTopic, PersonalCenterActivity } from '@/domain/community/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { resolveBackendBotNames } from '@/services/community/botDisplayNames';
import { communityService } from '@/services/community';

jest.mock('@/services/community/botDisplayNames', () => ({
  resolveBackendBotNames: jest.fn(),
}));
jest.mock('@/services/community', () => ({
  communityService: {
    listTopics: jest.fn(),
    getInteractionSummaryBatch: jest.fn(),
  },
}));

const human: PersonalCenterActivity = {
  activityType: 'FOLLOWED',
  actor: { actorType: 'HUMAN', id: 'user-a' },
  target: { targetType: 'HUMAN', id: 'user-a' },
  happenedAt: '2026-10-01T08:00:00Z',
  bodyPreview: '用户 A',
};

const bot: PersonalCenterActivity = {
  activityType: 'FOLLOWED',
  actor: { actorType: 'HUMAN', id: 'user-a' },
  target: { targetType: 'BOT', id: 'bot-a' },
  happenedAt: '2026-10-01T08:00:00Z',
  bodyPreview: 'bot-a',
};

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '作者主题',
  body: '正文',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'human', id: 'user-a', displayName: '用户 A' },
  createdAt: '2026-10-01T07:00:00Z',
  isMine: false,
  canClose: false,
  canEdit: false,
  canDelete: false,
};

function renderFollow(items: PersonalCenterActivity[] = [human, bot]) {
  return render(
    <FollowList items={items} loading={false} error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={jest.fn()} />,
  );
}

describe('PersonalCenter FollowList branches', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.mocked(resolveBackendBotNames).mockResolvedValue({});
    jest.mocked(communityService.listTopics).mockResolvedValue({ items: [topic], total: 1 });
    jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([]);
  });

  it('renders loading, error, empty, and pending bot-name states', () => {
    const loadingView = render(
      <FollowList items={[human, bot]} loading error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={jest.fn()} />,
    );
    expect(screen.getByLabelText('我的关注加载中')).toBeInTheDocument();
    loadingView.unmount();

    const retry = jest.fn();
    const errorView = render(
      <FollowList items={[]} loading={false} error="加载失败" onRetry={retry} onUnfollow={jest.fn()} onOpenTopic={jest.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(retry).toHaveBeenCalledTimes(1);
    errorView.unmount();

    const emptyView = render(
      <FollowList items={[]} loading={false} error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={jest.fn()} />,
    );
    expect(screen.getByText('暂无关注')).toBeInTheDocument();
    emptyView.unmount();

    jest.mocked(resolveBackendBotNames).mockImplementation(
      () => new Promise<Record<string, string>>(() => {}),
    );
    const pendingView = renderFollow([bot]);
    expect(screen.getByLabelText('Bot 名称解析中')).toBeInTheDocument();
    pendingView.unmount();
  });

  it('uses all follow-name fallbacks and resolves Bot names', async () => {
    const withContext: PersonalCenterActivity = {
      ...human,
      target: { targetType: 'HUMAN', id: 'user-context' },
      bodyPreview: '忽略正文',
      followedContext: {
        targetType: 'HUMAN',
        targetId: 'user-context',
        displayName: '带上下文的用户',
      },
    };
    const withBody: PersonalCenterActivity = {
      ...human,
      target: { targetType: 'HUMAN', id: 'user-body' },
      bodyPreview: '正文用户',
      followedContext: {
        targetType: 'HUMAN',
        targetId: 'user-body',
        displayName: '',
      },
    };
    const withId: PersonalCenterActivity = {
      ...human,
      target: { targetType: 'HUMAN', id: 'user-id' },
      bodyPreview: '   ',
    };
    const withBotContext: PersonalCenterActivity = {
      ...bot,
      target: { targetType: 'BOT', id: 'bot-context' },
      bodyPreview: '忽略 Bot 正文',
      followedContext: { targetType: 'BOT', targetId: 'bot-context', displayName: '旧 Bot 名' },
    };
    const withBotBody: PersonalCenterActivity = {
      ...bot,
      target: { targetType: 'BOT', id: 'bot-body' },
      bodyPreview: 'Bot 正文',
      followedContext: { targetType: 'BOT', targetId: 'bot-body', displayName: '' },
    };
    const withBotId: PersonalCenterActivity = {
      ...bot,
      target: { targetType: 'BOT', id: 'bot-id' },
      bodyPreview: '',
    };

    renderFollow([
      withContext,
      withBody,
      withId,
      withBotContext,
      withBotBody,
      withBotId,
    ]);
    expect(await screen.findByText('带上下文的用户')).toBeInTheDocument();
    expect(screen.getByText('正文用户')).toBeInTheDocument();
    expect(screen.getByText('user-id')).toBeInTheDocument();
    expect(screen.getAllByText(/Bot/).length).toBeGreaterThan(0);
  });

  it('supports keyboard selection, clearing stale selections, and empty author topics', async () => {
    const open = jest.fn();
    const view = render(
      <FollowList items={[human]} loading={false} error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={open} />,
    );

    fireEvent.click(screen.getByText('用户 A'));
    expect(await screen.findByText('用户 A 的帖子')).toBeInTheDocument();

    const identity = screen.getAllByText('用户 A')[0];
    fireEvent.keyDown(identity, { key: 'a' });
    expect(open).not.toHaveBeenCalled();
    fireEvent.keyDown(identity, { key: ' ' });
    fireEvent.keyDown(identity, { key: 'Enter' });
    expect(identity.closest('[role="button"]')).toHaveAttribute('aria-pressed', 'true');

    jest.mocked(communityService.listTopics).mockResolvedValue({ items: [], total: 0 });
    view.rerender(
      <FollowList items={[bot]} loading={false} error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={open} />,
    );
    expect(await screen.findByText('暂无关注的用户')).toBeInTheDocument();

    const view2 = render(
      <FollowList items={[human]} loading={false} error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={open} />,
    );
    fireEvent.click(screen.getByText('用户 A'));
    expect(await screen.findByText('该关注对象暂无帖子')).toBeInTheDocument();

    view2.rerender(
      <FollowList items={[bot]} loading={false} error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={open} />,
    );
    expect(await screen.findByText('暂无关注的用户')).toBeInTheDocument();
    view.unmount();
    view2.unmount();
  });

  it('renders resolved bot topics with summaries and opens topic rows', async () => {
    const open = jest.fn();
    jest.mocked(resolveBackendBotNames).mockResolvedValue({ 'bot-a': '已解析 Bot' });
    jest.mocked(communityService.listTopics).mockResolvedValue({ items: [topic], total: 1 });
    jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([
      {
        scope: 'TARGET',
        target: { targetType: 'TOPIC', id: 'topic-1' },
        viewCount: 12,
        likeCount: 13,
        favoriteCount: 14,
        followCount: 1,
        acceptCount: 0,
        topicCount: 1,
        replyCount: 2,
        acceptedReplyCount: 0,
        replyParticipantCount: 3,
        helpfulReplyCount: 0,
      },
    ]);
    render(
      <FollowList items={[bot]} loading={false} error={null} onRetry={jest.fn()} onUnfollow={jest.fn()} onOpenTopic={open} />,
    );

    fireEvent.click(await screen.findByText('已解析 Bot'));
    expect(await screen.findByText('作者主题')).toBeInTheDocument();
    expect(screen.getByLabelText('浏览 12')).toBeInTheDocument();
    fireEvent.click(screen.getByText('作者主题'));
    expect(open).toHaveBeenCalledWith('topic-1');
  });
});
