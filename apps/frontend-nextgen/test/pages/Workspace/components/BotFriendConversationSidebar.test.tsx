/** @jest-environment jsdom */
import { BotFriendConversationList } from '@/pages/Workspace/components/BotFriendConversationSidebar';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
});

const humanFriend = {
  actorType: 'human' as const,
  actorId: '447147',
  queryId: 'human_447147',
  displayName: '风太',
  online: false,
  detailsResolved: true,
};
const botFriend = {
  actorType: 'bot' as const,
  actorId: 'bot-b:100',
  queryId: 'bot-b:100',
  displayName: '皮皮虾好友',
  online: true,
  detailsResolved: true,
  disabledReason: '暂不支持查看 Bot 好友对话',
};
const thirdPartyBotFriend = {
  ...botFriend,
  actorId: 'bot_partner',
  queryId: 'bot_partner',
  displayName: '三方好友',
};
const unknownPlatformBotFriend = {
  ...botFriend,
  actorId: 'unknown-platform',
  queryId: 'unknown-platform',
  displayName: '未知平台好友',
};
const session = {
  sessionId: 'session-1',
  friendUserId: '447147',
  title: '历史会话',
  messageCount: 2,
  gmtCreate: '2026-09-15T10:00:00Z',
  gmtModified: '2026-09-16T10:00:00Z',
};

const baseProps = {
  view: 'chat' as const,
  onViewChange: jest.fn(),
  availableViews: ['chat', 'group'] as const,
  identityName: '皮皮虾',
  humanFriends: [humanFriend],
  botFriends: [botFriend],
  humanLoading: false,
  botLoading: false,
  humanError: null,
  botError: null,
  onRetryHuman: jest.fn(),
  onRetryBot: jest.fn(),
  expandedFriendUserId: '447147',
  sessions: [session],
  sessionsLoading: false,
  sessionsError: null,
  selectedSessionId: 'session-1',
  hasMoreSessions: false,
  isLoadingMoreSessions: false,
  loadMoreSessionsError: null,
  onToggleFriend: jest.fn(),
  onSelectSession: jest.fn(),
  onRetrySessions: jest.fn(),
  onLoadMoreSessions: jest.fn(),
};

describe('BotFriendConversationList', () => {
  it('renders name-only Human and Bot friend rows with read-only Sessions', () => {
    const { container } = render(<BotFriendConversationList {...baseProps} />);

    expect(screen.getByLabelText('皮皮虾的好友用户')).toBeInTheDocument();
    expect(screen.getByLabelText('皮皮虾的好友 Bot')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '风太（447147）' })).toBeEnabled();
    expect(screen.getByText('皮皮虾好友').closest('[aria-disabled="true"]')).toBeInTheDocument();
    expect(screen.getByText('皮皮虾好友')).toHaveClass('text-muted-foreground');
    expect(screen.getByText('历史会话')).toBeInTheDocument();
    expect(container.querySelector('img')).not.toBeInTheDocument();
    expect(screen.getByText('👤')).toBeInTheDocument();
    expect(screen.getByText('BOT')).toBeInTheDocument();
    expect(screen.getByText('👤')).toHaveClass('bg-primary/15', 'text-primary', 'ring-primary/30');
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /收藏会话|取消收藏/ })).not.toBeInTheDocument();
  });

  it('aligns section headers and friend rows with the Human-identity sidebar layout', () => {
    render(<BotFriendConversationList {...baseProps} />);

    const humanHeader = screen.getByRole('button', { name: '皮皮虾的好友用户 (1)' });
    const botHeader = screen.getByRole('button', { name: '皮皮虾的好友 Bot (1)' });
    expect(humanHeader).toHaveClass('min-h-9', 'px-4', 'py-2', 'text-xs');
    expect(humanHeader).toHaveAttribute('aria-expanded', 'true');
    expect(botHeader).toHaveAttribute('aria-expanded', 'true');

    const humanTrigger = screen.getByRole('button', { name: '风太（447147）' });
    expect(humanTrigger).toHaveClass('gap-3');
    const humanRow = humanTrigger.parentElement;
    expect(humanRow).toHaveClass('min-h-16', 'gap-3', 'px-4', 'py-2.5', 'bg-muted');
    const botTrigger = screen.getByText('皮皮虾好友').closest('[aria-disabled="true"]');
    expect(botTrigger).toHaveClass('gap-3');
    const botRow = botTrigger?.parentElement;
    expect(botRow).toHaveClass('min-h-16', 'gap-3', 'px-4', 'py-2.5');
  });

  it('collapses and expands both friend sections without clearing their data', () => {
    render(<BotFriendConversationList {...baseProps} />);

    const humanHeader = screen.getByRole('button', { name: '皮皮虾的好友用户 (1)' });
    fireEvent.click(humanHeader);
    expect(humanHeader).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: '风太（447147）' })).not.toBeInTheDocument();
    fireEvent.click(humanHeader);
    expect(screen.getByRole('button', { name: '风太（447147）' })).toBeInTheDocument();

    const botHeader = screen.getByRole('button', { name: '皮皮虾的好友 Bot (1)' });
    fireEvent.click(botHeader);
    expect(botHeader).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('皮皮虾好友')).not.toBeInTheDocument();
    fireEvent.click(botHeader);
    expect(screen.getByText('皮皮虾好友')).toBeInTheDocument();
  });

  it('shows mutually exclusive platform tags on Bot friend items', () => {
    render(
      <BotFriendConversationList
        {...baseProps}
        botFriends={[botFriend, thirdPartyBotFriend, unknownPlatformBotFriend]}
      />,
    );

    expect(screen.getByText('当前平台 Bot')).toHaveClass('bg-success/10', 'text-success');
    expect(screen.getByText('外部平台 Bot')).toHaveClass('bg-primary/10', 'text-primary');
    expect(screen.getAllByText(/平台 Bot/)).toHaveLength(2);
    expect(screen.getByText('未知平台好友').parentElement).not.toHaveTextContent('平台 Bot');
  });

  it('does not duplicate the staff number when Human detail name falls back to the ID', () => {
    render(
      <BotFriendConversationList
        {...baseProps}
        humanFriends={[{ ...humanFriend, displayName: '447147' }]}
        expandedFriendUserId={null}
        sessions={[]}
        selectedSessionId={null}
      />,
    );

    expect(screen.getByRole('button', { name: '447147' })).toBeInTheDocument();
    expect(screen.queryByText('447147（447147）')).not.toBeInTheDocument();
  });

  it('expands Human friends, selects Sessions, and never selects Bot friends', async () => {
    const onToggleFriend = jest.fn();
    const onSelectSession = jest.fn();
    render(
      <BotFriendConversationList
        {...baseProps}
        expandedFriendUserId={null}
        sessions={[]}
        selectedSessionId={null}
        onToggleFriend={onToggleFriend}
        onSelectSession={onSelectSession}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '风太（447147）' }));
    expect(onToggleFriend).toHaveBeenCalledWith('447147');

    const disabledBot = screen.getByText('皮皮虾好友').closest('[role="button"]') as HTMLElement;
    fireEvent.click(disabledBot);
    expect(onToggleFriend).toHaveBeenCalledTimes(1);
    await userEvent.hover(disabledBot);
    expect(await screen.findByText('暂不支持查看 Bot 好友对话')).toBeInTheDocument();

    const { rerender } = render(<BotFriendConversationList {...baseProps} onSelectSession={onSelectSession} />);
    fireEvent.click(screen.getByRole('button', { name: '历史会话' }));
    expect(onSelectSession).toHaveBeenCalledWith('session-1');
    rerender(<BotFriendConversationList {...baseProps} />);
  });

  it('keeps Human and Bot section errors independent', () => {
    const onRetryHuman = jest.fn();
    render(
      <BotFriendConversationList
        {...baseProps}
        humanFriends={[]}
        botFriends={[botFriend]}
        humanError="好友用户加载失败"
        onRetryHuman={onRetryHuman}
        expandedFriendUserId={null}
        sessions={[]}
        selectedSessionId={null}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('好友用户加载失败');
    expect(screen.getByText('皮皮虾好友')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetryHuman).toHaveBeenCalledTimes(1);
  });

  it('filters both sections by friend name and shows a search-empty state', () => {
    render(
      <BotFriendConversationList {...baseProps} expandedFriendUserId={null} sessions={[]} selectedSessionId={null} />,
    );
    const search = screen.getByRole('textbox', { name: '搜索好友' });

    fireEvent.change(search, { target: { value: '447147' } });
    expect(screen.getByText('风太（447147）')).toBeInTheDocument();
    expect(screen.queryByText('皮皮虾好友')).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: '不存在' } });
    expect(screen.getByText('未找到匹配的好友')).toBeInTheDocument();
  });
});
