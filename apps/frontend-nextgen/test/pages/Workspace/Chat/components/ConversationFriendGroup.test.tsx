/** @jest-environment jsdom */
import type { ConversationFriendGroupView, ConversationSessionListState } from '@/domain/conversation/types';
import type { ConversationFriendGroupProps } from '@/pages/Workspace/Chat/components/ConversationFriendGroup';
import { ConversationFriendGroup } from '@/pages/Workspace/Chat/components/ConversationFriendGroup';
import { ConversationFriendRowsSkeleton } from '@/pages/Workspace/Chat/components/ConversationSkeletons';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const friend = { userId: '447147', displayName: '风太' };

const sessionOf = (sessionId: string) => ({
  sessionId,
  botId: 'bot-a:2088',
  title: `会话 ${sessionId}`,
  messageCount: 1,
  gmtCreate: '',
  gmtModified: '',
});

const sessionsOf = (over: Partial<ConversationSessionListState> = {}): ConversationSessionListState => ({
  items: [sessionOf('s1'), sessionOf('s2')],
  page: 1,
  total: 2,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
  ...over,
});

const groupOf = (over: Partial<ConversationFriendGroupView> = {}): ConversationFriendGroupView => ({
  friend,
  state: 'loaded',
  sessions: sessionsOf(),
  expanded: false,
  ...over,
});

function renderGroup(group: ConversationFriendGroupView, over: Partial<ConversationFriendGroupProps> = {}) {
  const props = {
    group,
    expanded: false,
    selectedSessionId: null,
    onToggle: jest.fn(),
    onSelectSession: jest.fn(),
    onRetry: jest.fn(),
    onLoadMore: jest.fn(),
    ...over,
  };
  render(<ConversationFriendGroup {...props} />);
  return { ...props, container: document };
}

describe('ConversationFriendGroup', () => {
  it('collapses sessions behind an aria-expanded friend row', () => {
    const { onToggle } = renderGroup(groupOf(), { expanded: true });
    const row = screen.getByRole('button', { name: '风太' });
    expect(row).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('会话 s1')).toBeInTheDocument();
    fireEvent.click(row);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('renders collapsed rows without sessions', () => {
    renderGroup(groupOf());
    expect(screen.getByRole('button', { name: '风太' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('会话 s1')).not.toBeInTheDocument();
  });

  it('keeps unloaded expanded groups free of empty text', () => {
    renderGroup(groupOf({ state: 'unloaded' }), { expanded: true });
    expect(screen.queryByText('暂无会话')).not.toBeInTheDocument();
    expect(screen.queryByText('会话 s1')).not.toBeInTheDocument();
  });

  it('shows loading skeletons without declaring the list empty', () => {
    const { container } = renderGroup(groupOf({ state: 'loading' }), { expanded: true });
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
    expect(screen.queryByText('暂无会话')).not.toBeInTheDocument();
  });

  it('shows the group error with retry', () => {
    const { onRetry } = renderGroup(groupOf({ state: 'error', error: '好友会话加载失败' }), { expanded: true });
    expect(screen.getByRole('alert')).toHaveTextContent('好友会话加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('selects read-only sessions without interactive actions', () => {
    const { onSelectSession } = renderGroup(groupOf(), { expanded: true, selectedSessionId: 's1' });
    const row = screen.getByRole('button', { name: /^会话 s1/ });
    fireEvent.click(row);
    expect(onSelectSession).toHaveBeenCalledWith('s1');
    expect(row).toHaveAttribute('aria-current', 'page');
    // 只读行不具备任何写操作入口。
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /收藏/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
  });

  it('hides the whole group after its session request succeeds with zero items', () => {
    renderGroup(groupOf({ sessions: sessionsOf({ items: [] }) }), { expanded: true });
    expect(screen.queryByText('风太')).not.toBeInTheDocument();
  });

  it('loads more read-only pages with a loading label', () => {
    const { onLoadMore } = renderGroup(groupOf({ sessions: sessionsOf({ hasMore: true }) }), { expanded: true });
    const more = screen.getByRole('button', { name: '加载更多会话' });
    fireEvent.click(more);
    expect(onLoadMore).toHaveBeenCalledTimes(1);

    const { unmount } = render(
      <ConversationFriendGroup
        group={groupOf({ sessions: sessionsOf({ hasMore: true, isLoadingMore: true }) })}
        expanded
        selectedSessionId={null}
        onToggle={jest.fn()}
        onSelectSession={jest.fn()}
        onRetry={jest.fn()}
        onLoadMore={jest.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: '正在加载…' })).toBeDisabled();
    unmount();
  });
});

describe('other-user row visual density', () => {
  it.each([false, true])('matches compact list typography and avatar geometry (expanded=%s)', (expanded) => {
    renderGroup(groupOf(), { expanded });
    const button = screen.getByRole('button', { name: '风太' });
    expect(button).toHaveClass('h-8', 'gap-2', 'font-normal');
    expect(button.parentElement).toHaveClass('min-h-10', 'py-1');
    expect(button.parentElement).not.toHaveClass('min-h-12');
    expect(button.parentElement?.parentElement).toHaveClass('pl-6');
    expect(screen.getByText('风太')).toHaveClass('text-[13px]', 'font-normal', 'leading-[18px]', 'text-content-strong');
    const avatar = button.querySelector('span[aria-hidden="true"]');
    expect(avatar).toHaveClass('h-6', 'w-6', 'rounded-full', 'bg-muted', 'text-muted-foreground');
    expect(avatar).not.toHaveClass('ring-1');
    expect(avatar?.querySelector('svg')).toHaveClass('h-3.5', 'w-3.5');
    expect(button).not.toHaveTextContent('👤');
  });

  it('matches directory skeleton rows to the loaded row height, avatar and spacing', () => {
    const { container } = render(<ConversationFriendRowsSkeleton rows={3} />);
    const directory = container.firstElementChild;
    expect(directory).toHaveClass('pl-6');
    expect(directory).not.toHaveClass('gap-1', 'py-1');
    const rows = Array.from(directory?.children ?? []);
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(row).toHaveClass('min-h-10', 'gap-2', 'py-1');
      expect(row.firstElementChild).toHaveClass('h-6', 'w-6', 'rounded-full');
      expect(row.lastElementChild).toHaveClass('h-3');
    }
  });
});
