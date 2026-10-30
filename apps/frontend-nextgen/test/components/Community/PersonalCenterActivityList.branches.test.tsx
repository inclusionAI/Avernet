/** @jest-environment jsdom */
import { ActivityList, missingBotName } from '@/components/Community/CommunityPersonalCenter/ActivityList';
import type { PersonalCenterActivity } from '@/domain/community/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { resolveBackendBotNames } from '@/services/community/botDisplayNames';

jest.mock('@/services/community/botDisplayNames', () => ({
  resolveBackendBotNames: jest.fn(),
}));

const activity: PersonalCenterActivity = {
  activityType: 'AUTHORED',
  actor: { actorType: 'HUMAN', id: 'user-1' },
  target: { targetType: 'TOPIC', id: 'topic-1' },
  topicId: 'topic-1',
  happenedAt: '2026-10-01T08:00:00Z',
  bodyPreview: '活动内容',
};

function renderList(items: PersonalCenterActivity[] = [activity]) {
  return render(
    <ActivityList items={items} loading={false} error={null} onRetry={jest.fn()} onOpenTopic={jest.fn()} />,
  );
}

describe('PersonalCenter ActivityList state branches', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.mocked(resolveBackendBotNames).mockResolvedValue({});
  });

  it('renders loading, error, and empty states', () => {
    const retry = jest.fn();
    render(<ActivityList items={[activity]} loading error={null} onRetry={retry} onOpenTopic={jest.fn()} />);
    expect(screen.getByLabelText('个人中心列表加载中')).toBeInTheDocument();
    const view = render(
      <ActivityList items={[]} loading={false} error="加载失败" onRetry={retry} onOpenTopic={jest.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(retry).toHaveBeenCalledTimes(1);
    const view2 = render(
      <ActivityList items={[]} loading={false} error={null} onRetry={jest.fn()} onOpenTopic={jest.fn()} />,
    );
    expect(screen.getByText('暂无内容')).toBeInTheDocument();
    view.unmount();
    view2.unmount();
  });

  it('falls back to activity fields and resolves missing Bot names', async () => {
    jest.mocked(resolveBackendBotNames).mockResolvedValue({ 'bot-a': '自动写作' });
    renderList([
      {
        ...activity,
        actor: { actorType: 'BOT', id: 'bot-a' },
        bodyPreview: '    ',
        topicId: 'topic-a',
        target: { targetType: 'TOPIC', id: 'topic-a' },
      },
      {
        ...activity,
        actor: { actorType: 'BOT', id: 'bot-b' },
        bodyPreview: 'bot-b',
        topicId: 'topic-b',
        target: { targetType: 'TOPIC', id: 'topic-b' },
        topicContext: {
          title: 'Bot B 主题',
          bodyPreview: '',
          topicType: 'discussion',
          status: 'open',
          author: { actorType: 'BOT', id: 'bot-b' },
          authorDisplayName: '',
          replyCount: 0,
          likeCount: 0,
          viewCount: 0,
          favoriteCount: 0,
          isHot: false,
          createdAt: '2026-10-01T07:00:00Z',
        },
      },
      {
        ...activity,
        actor: { actorType: 'BOT', id: 'bot-named' },
        bodyPreview: '已命名主题',
        topicId: 'topic-named',
        target: { targetType: 'TOPIC', id: 'topic-named' },
        topicContext: {
          title: '已命名主题',
          bodyPreview: '',
          topicType: 'discussion',
          status: 'open',
          author: { actorType: 'BOT', id: 'bot-named' },
          authorDisplayName: 'Bot · 已命名',
          replyCount: 0,
          likeCount: 0,
          viewCount: 0,
          favoriteCount: 0,
          isHot: false,
          createdAt: '2026-10-01T07:00:00Z',
        },
      },
    ]);

    await waitFor(() => expect(resolveBackendBotNames).toHaveBeenCalledWith(['bot-a', 'bot-b']));
    expect(await screen.findByText('Bot · 自动写作')).toBeInTheDocument();
    expect(screen.getByText('Bot B 主题')).toBeInTheDocument();
    expect(await screen.findByText('bot-b')).toBeInTheDocument();
  });

  it('recognizes a resolved Bot display name so it is not resolved again', () => {
    expect(missingBotName('已解析 Bot', 'bot-x', 'BOT')).toBe(false);
    expect(missingBotName('Bot · bot-x', 'bot-x', 'BOT')).toBe(true);
    expect(missingBotName(undefined, 'bot-x', 'BOT')).toBe(true);
    expect(missingBotName(undefined, 'bot-x', 'HUMAN')).toBe(false);
  });

  it('renders topic context, skips non-topic activities, and opens topics', () => {
    const open = jest.fn();
    const withContext: PersonalCenterActivity = {
      ...activity,
      topicContext: {
        title: '完整主题',
        bodyPreview: '主题正文',
        topicType: 'discussion',
        status: 'open',
        author: { actorType: 'HUMAN', id: 'author-1' },
        authorDisplayName: '作者',
        replyCount: 1,
        likeCount: 2,
        viewCount: 3,
        favoriteCount: 4,
        isHot: false,
        createdAt: '2026-10-01T07:00:00Z',
      },
    };
    const withoutTopicId: PersonalCenterActivity = {
      ...activity,
      topicId: undefined,
      target: { targetType: 'POST', id: 'post-1' },
    };
    render(
      <ActivityList
        items={[withContext, withoutTopicId]}
        loading={false}
        error={null}
        onRetry={jest.fn()}
        onOpenTopic={open}
      />,
    );

    expect(screen.getByText('完整主题')).toBeInTheDocument();
    expect(screen.queryByText('活动内容')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('完整主题'));
    expect(open).toHaveBeenCalledWith('topic-1');
  });

  it('groups replies and handles title, click, and reply fallbacks', async () => {
    const open = jest.fn();
    const replyActivity = (id: string, extra: Partial<PersonalCenterActivity>): PersonalCenterActivity => ({
      ...activity,
      activityType: 'REPLIED',
      target: { targetType: 'POST', id },
      bodyPreview: `回复 ${id}`,
      topicId: 'topic-parent',
      postId: id,
      ...extra,
    });
    const first = replyActivity('reply-1', {
      topicContext: {
        title: '父主题',
        bodyPreview: '',
        topicType: 'discussion',
        status: 'open',
        author: { actorType: 'HUMAN', id: 'author-1' },
        authorDisplayName: '作者',
        replyCount: 0,
        likeCount: 0,
        viewCount: 0,
        favoriteCount: 0,
        isHot: false,
        createdAt: '2026-10-01T07:00:00Z',
      },
      repliedToPost: {
        postId: 'parent-1',
        author: { actorType: 'BOT', id: 'bot-a' },
        authorDisplayName: 'bot-a',
        bodyPreview: '父回复',
      },
    });
    const second = replyActivity('reply-2', {});
    const noTopicId = replyActivity('reply-3', { postId: undefined, topicId: undefined });

    const view = render(
      <ActivityList
        items={[first, second, noTopicId]}
        loading={false}
        error={null}
        onRetry={jest.fn()}
        onOpenTopic={open}
      />,
    );

    expect(await screen.findByText('父主题')).toBeInTheDocument();
    expect(screen.getByText('回复 @bot-a：父回复')).toBeInTheDocument();
    expect(screen.getByText('回复 reply-2')).toBeInTheDocument();

    fireEvent.click(screen.getByText('回复 reply-2'));
    expect(open).toHaveBeenCalledWith('topic-parent');

    const withoutAnything: PersonalCenterActivity = {
      ...replyActivity('reply-4', {}),
      topicId: undefined,
      postId: undefined,
      topicContext: undefined,
      bodyPreview: undefined as unknown as string,
      target: { targetType: 'POST', id: 'threadless' },
    };
    view.rerender(
      <ActivityList
        items={[withoutAnything]}
        loading={false}
        error={null}
        onRetry={jest.fn()}
        onOpenTopic={open}
      />,
    );
    expect(screen.getByText('话题 #')).toBeInTheDocument();
    fireEvent.click(screen.getByText('话题 #'));
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('waits until missing Bot names are resolved before painting activity rows', () => {
    jest.mocked(resolveBackendBotNames).mockImplementation(
      () => new Promise<Record<string, string>>(() => {}),
    );
    const item: PersonalCenterActivity = {
      ...activity,
      actor: { actorType: 'BOT', id: 'bot-pending' },
      topicContext: {
        title: '等待名称',
        bodyPreview: '',
        topicType: 'discussion',
        status: 'open',
        author: { actorType: 'BOT', id: 'bot-pending' },
        authorDisplayName: 'Bot · bot-pending',
        replyCount: 0,
        likeCount: 0,
        viewCount: 0,
        favoriteCount: 0,
        isHot: false,
        createdAt: '2026-10-01T07:00:00Z',
      },
    };
    const view = renderList([item]);
    expect(screen.getByLabelText('Bot 名称解析中')).toBeInTheDocument();
    view.unmount();
  });
});
