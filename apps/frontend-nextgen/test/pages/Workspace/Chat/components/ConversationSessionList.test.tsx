/** @jest-environment jsdom */
import { ConversationSessionList } from '@/pages/Workspace/Chat/components/ConversationSessionList';
import { firstPageOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ConversationSessionAction } from '@/services/workspace/conversationSessionActionService';

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

it('selects a non-active session before opening its logs', async () => {
  const onSelectSession = jest.fn();
  const run = jest.fn().mockResolvedValue(true);
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
      selectedSessionId="other-session"
      onSelectSession={onSelectSession}
      onLoadMore={jest.fn()}
      onToggleFavorite={jest.fn()}
      isFavoritePending={() => false}
      actions={{ run, pending: false, features: { logs: true } }}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
  const logs = await screen.findByRole('button', { name: '日志' });
  fireEvent.click(logs);

  expect(onSelectSession.mock.invocationCallOrder[0]).toBeLessThan(run.mock.invocationCallOrder[0]);
  expect(onSelectSession).toHaveBeenCalledWith('s1');
  expect(run).toHaveBeenCalledWith('s1', { type: 'logs' });
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

const initiatorSession = {
  sessionId: 'bcs-cli:another-session',
  botId: 'coding-bot',
  title: '他人发起的会话',
  messageCount: 1,
  gmtCreate: '',
  gmtModified: '',
};

it('keeps ordinary initiator rows read-only even when parent descriptions are advanced', () => {
  render(
    <ConversationSessionList
      sessions={firstPageOf([initiatorSession], 1)}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={jest.fn()}
      isFavoritePending={() => false}
      showInitiator
      showCreateHint={false}
      actions={{
        run: jest.fn<Promise<boolean>, [string, ConversationSessionAction]>().mockResolvedValue(true),
        pending: false,
      }}
    />,
  );
  expect(screen.getByText('只读')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
});

it('allows the AgentCoding initiator-row exception to expose the more-actions menu', () => {
  render(
    <ConversationSessionList
      sessions={firstPageOf([initiatorSession], 1)}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={jest.fn()}
      isFavoritePending={() => false}
      showInitiator
      showCreateHint={false}
      allowActionsWithInitiator
      actions={{
        run: jest.fn<Promise<boolean>, [string, ConversationSessionAction]>().mockResolvedValue(true),
        pending: false,
      }}
    />,
  );
  expect(screen.getByRole('button', { name: '会话更多操作' })).toBeInTheDocument();
  expect(screen.queryByText('只读')).not.toBeInTheDocument();
});

describe('ConversationSessionList replyingSessionId 透传（#2990 还原）', () => {
  const sessionOf = (sessionId: string) => ({
    sessionId,
    botId: 'b1',
    title: '会话',
    messageCount: 0,
    gmtCreate: '',
    gmtModified: '',
  });

  it('命中行渲染「Bot 正在回复」指示,未传时缺省不渲染', () => {
    const { unmount } = render(
      <ConversationSessionList
        sessions={firstPageOf([sessionOf('s1'), sessionOf('s2')], 20)}
        selectedSessionId="s1"
        onSelectSession={jest.fn()}
        onLoadMore={jest.fn()}
        onToggleFavorite={jest.fn()}
        isFavoritePending={() => false}
        replyingSessionId="s2"
      />,
    );
    const statuses = screen.getAllByRole('status', { name: 'Bot 正在回复' });
    expect(statuses).toHaveLength(1);
    expect(statuses[0].className).toContain('left-[40px]');
    unmount();

    render(
      <ConversationSessionList
        sessions={firstPageOf([sessionOf('s1')], 20)}
        selectedSessionId="s1"
        onSelectSession={jest.fn()}
        onLoadMore={jest.fn()}
        onToggleFavorite={jest.fn()}
        isFavoritePending={() => false}
      />,
    );
    expect(screen.queryByRole('status', { name: 'Bot 正在回复' })).not.toBeInTheDocument();
  });
});
