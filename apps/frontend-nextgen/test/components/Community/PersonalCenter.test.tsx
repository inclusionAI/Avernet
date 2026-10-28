/** @jest-environment jsdom */
import { CommunityPersonalCenter } from '@/components/Community/CommunityPersonalCenter';
import type { UsePersonalCenterResult } from '@/hooks/usePersonalCenter';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

const mockedCenter: UsePersonalCenterResult = {
  ownerUserId: '149844',
  botActors: [{ actorType: 'BOT', id: 'bot-A' }],
  actorScope: 'ALL',
  activityType: 'AUTHORED',
  targetType: 'ALL',
  page: 1,
  pageSize: 10,
  activityPage: {
    items: [
      {
        activityType: 'AUTHORED',
        actor: { actorType: 'BOT', id: 'bot-A' },
        target: { targetType: 'TOPIC', id: 'topic-1' },
        topicId: 'topic-1',
        happenedAt: '2026-10-27T10:00:00Z',
        bodyPreview: 'Bot 发布的主题',
      },
    ],
    total: 1,
  },
  identityStats: [
    {
      actor: { actorType: 'HUMAN', id: '149844' },
      followCount: 1,
      topicCount: 2,
      replyCount: 3,
      acceptedReplyCount: 1,
      viewCount: 0,
      likeCount: 0,
      favoriteCount: 0,
      acceptCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    },
    {
      actor: { actorType: 'BOT', id: 'bot-A' },
      followCount: 4,
      topicCount: 5,
      replyCount: 6,
      acceptedReplyCount: 0,
      viewCount: 0,
      likeCount: 0,
      favoriteCount: 0,
      acceptCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    },
  ],
  aggregateStats: {
    followCount: 5,
    topicCount: 7,
    replyCount: 9,
    acceptedReplyCount: 1,
    viewCount: 0,
    likeCount: 0,
    favoriteCount: 0,
    acceptCount: 0,
    replyParticipantCount: 0,
    helpfulReplyCount: 0,
  },
  loading: false,
  loadingStats: false,
  subscriptionLoading: false,
  error: null,
  setActorScope: jest.fn(),
  setActivityType: jest.fn(),
  setTargetType: jest.fn(),
  goToPage: jest.fn(),
  reload: jest.fn(),
};

jest.mock('@/hooks/usePersonalCenter', () => ({
  usePersonalCenter: () => mockedCenter,
  PERSONAL_CENTER_PAGE_SIZE: 10,
}));

describe('CommunityPersonalCenter', () => {
  it('defaults to ALL identity scope and renders aggregate cards with stable actor identifiers', () => {
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={jest.fn()} />);

    expect(
      within(screen.getByRole('group', { name: '身份筛选' }))
        .getByRole('button', { name: '全部' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('关注者')).toBeInTheDocument();
    expect(screen.getAllByText('5').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bot-A/).length).toBeGreaterThan(0);
    expect(screen.getByText('AUTHORED · TOPIC')).toBeInTheDocument();
  });

  it('renders five activity tabs and provides the shared target filter', () => {
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={jest.fn()} />);

    const activities = screen.getByRole('group', { name: '活动类型' });
    expect(within(activities).getAllByRole('button').map((item) => item.textContent)).toEqual([
      '我的发布',
      '我的收藏',
      '我的浏览',
      '我的点赞',
      '我的回复',
    ]);
    expect(
      within(screen.getByRole('group', { name: '内容类型' })).getAllByRole('button').length,
    ).toBe(3);
  });

  it('opens the related Topic when a Post or Topic activity row is clicked', () => {
    const onOpenTopic = jest.fn();
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />);
    fireEvent.click(screen.getByText('Bot 发布的主题'));
    expect(onOpenTopic).toHaveBeenCalledWith('topic-1');
  });
});
