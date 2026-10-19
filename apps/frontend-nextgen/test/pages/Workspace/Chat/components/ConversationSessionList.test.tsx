/** @jest-environment jsdom */
import { ConversationSessionList } from '@/pages/Workspace/Chat/components/ConversationSessionList';
import { firstPageOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

it('shows row skeletons instead of the empty state while the first page is loading', () => {
  const { container } = render(
    <ConversationSessionList
      sessions={{ ...firstPageOf([], 0), loading: true }}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={jest.fn()}
      onToggleFavorite={jest.fn()}
      isFavoritePending={() => false}
    />,
  );
  expect(container.querySelector('.animate-pulse')).not.toBeNull();
  // 加载中 ≠ 空列表:不得闪「暂无会话」。
  expect(screen.queryByText('暂无会话')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '加载更多会话' })).not.toBeInTheDocument();
});

it('blocks pagination until an ongoing favorite mutation finishes', () => {
  render(
    <ConversationSessionList
      sessions={firstPageOf(
        [
          {
            sessionId: 's1',
            botId: 'b1',
            title: '会话',
            favorite: true,
            messageCount: 0,
            gmtCreate: '',
            gmtModified: '',
          },
        ],
        20,
      )}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={jest.fn()}
      onToggleFavorite={jest.fn()}
      isFavoritePending={() => true}
    />,
  );
  expect(screen.getByRole('button', { name: '加载更多会话' })).toBeDisabled();
});

it('keeps pagination reachable when all loaded favorites are removed but more remain', () => {
  const onLoadMore = jest.fn();
  render(
    <ConversationSessionList
      sessions={{ ...firstPageOf([], 15), hasMore: true }}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={onLoadMore}
      onToggleFavorite={jest.fn()}
      isFavoritePending={() => false}
    />,
  );
  expect(screen.getByRole('button', { name: '加载更多会话' })).toBeEnabled();
});

it('blocks pagination, favorites and additional menus while a session action is pending', () => {
  render(
    <ConversationSessionList
      sessions={firstPageOf(
        [
          {
            sessionId: 's1',
            botId: 'b',
            title: '会话',
            favorite: true,
            messageCount: 10,
            gmtCreate: '',
            gmtModified: '',
          },
        ],
        20,
      )}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={jest.fn()}
      onToggleFavorite={jest.fn()}
      isFavoritePending={() => false}
      actions={{ run: jest.fn(), pending: true }}
    />,
  );
  expect(screen.getByRole('button', { name: '加载更多会话' })).toBeDisabled();
  // 平齐形态:收藏走常驻星标,pending 期间保持可见但禁用(防并发),菜单按钮同步禁用。
  expect(screen.getByRole('button', { name: '取消收藏' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '会话更多操作' })).toBeDisabled();
});
