/** @jest-environment jsdom */
import { CommunityAuthorAvatar } from '@/components/Community/CommunityAuthorAvatar';
import type { CommunityAuthor, InteractionSummary } from '@/domain/community/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const botAuthor: CommunityAuthor = {
  type: 'bot',
  id: 'bot-001',
  displayName: '协作助手',
  avatarUrl: '/avatar.png',
};

function buildSummary(overrides: Partial<InteractionSummary> = {}): InteractionSummary {
  return {
    scope: 'ACTOR',
    actor: { actorType: 'BOT', id: botAuthor.id },
    viewCount: 0,
    likeCount: 0,
    favoriteCount: 0,
    followCount: 12,
    acceptCount: 0,
    topicCount: 8,
    replyCount: 25,
    acceptedReplyCount: 3,
    replyParticipantCount: 0,
    helpfulReplyCount: 0,
    ...overrides,
  };
}

function renderAvatar(
  overrides: Partial<React.ComponentProps<typeof CommunityAuthorAvatar>> = {},
) {
  const props: React.ComponentProps<typeof CommunityAuthorAvatar> = {
    author: botAuthor,
    currentUserId: 'human-001',
    summary: buildSummary(),
    onLoadSummary: jest.fn(),
    onFollow: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<CommunityAuthorAvatar {...props} />) };
}

describe('CommunityAuthorAvatar', () => {
  test('未提供详情能力时仅渲染头像，不生成资料按钮', () => {
    renderAvatar({ onLoadSummary: undefined, currentUserId: undefined });

    expect(screen.queryByRole('button', { name: '查看协作助手资料' })).not.toBeInTheDocument();
  });

  test('键盘可打开资料卡并读取服务端统计', async () => {
    const user = userEvent.setup();
    const { props } = renderAvatar();

    const trigger = screen.getByRole('button', { name: '查看协作助手资料' });
    trigger.focus();
    await user.keyboard('{Enter}');
    expect(props.onLoadSummary).toHaveBeenCalledTimes(1);

    expect(screen.getByRole('dialog')).toBeVisible();
    expect(screen.getAllByAltText('协作助手').length).toBeGreaterThan(0);
    expect(screen.getByText('BOT · bot-001')).toBeInTheDocument();
    expect(screen.getByText('关注者')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('发布主题')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.getByText('回帖')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
    expect(screen.getByText('被采纳回帖')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  test('鼠标悬停头像即可打开资料卡，无需先点击', () => {
    const { props } = renderAvatar();

    fireEvent.mouseEnter(screen.getByRole('button', { name: '查看协作助手资料' }));

    expect(props.onLoadSummary).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('dialog')).toBeVisible();
    expect(screen.getByText('关注者')).toBeInTheDocument();
  });

  test('followed identity shows and toggles the server-backed followed state', async () => {
    const user = userEvent.setup();
    const { props } = renderAvatar({ followed: true });

    await user.click(screen.getByRole('button', { name: '查看协作助手资料' }));
    const followButton = screen.getByRole('button', { name: '已关注' });
    expect(followButton).toHaveAttribute('aria-pressed', 'true');
    await user.click(followButton);
    expect(props.onFollow).toHaveBeenCalledTimes(1);
  });

  test('其他身份可关注，点击只调用回调', async () => {
    const user = userEvent.setup();
    const { props } = renderAvatar();

    await user.click(screen.getByRole('button', { name: '查看协作助手资料' }));
    await user.click(screen.getByRole('button', { name: '关注' }));
    expect(props.onLoadSummary).toHaveBeenCalledTimes(1);
    expect(props.onFollow).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: '已关注' })).not.toBeInTheDocument();
  });

  test('当前登录的 Human 不对自己显示关注按钮', async () => {
    const user = userEvent.setup();
    renderAvatar({
      author: { type: 'human', id: 'human-001', displayName: '顾客', avatarUrl: undefined },
      summary: buildSummary({
        actor: { actorType: 'HUMAN', id: 'human-001' },
        followCount: 4,
        topicCount: 2,
        replyCount: 7,
        acceptedReplyCount: 1,
      }),
    });

    await user.click(screen.getByRole('button', { name: '查看顾客资料' }));
    expect(screen.getByText('HUMAN · human-001')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '关注' })).not.toBeInTheDocument();
  });
});
