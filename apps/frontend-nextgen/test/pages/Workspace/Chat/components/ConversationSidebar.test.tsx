/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation/types';
import type { ConversationSidebarProps } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import { ConversationSidebar } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import type { ConversationDirectoryModel } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import type { ConversationSessionsModel } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import type { ManagedBotOthersModel } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import { useConversationStore } from '@/stores/conversationStore';
import type { ConversationState } from '@/stores/conversationStoreState';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: '327325', displayName: '张三', online: true },
    status: 'ready',
  }),
}));

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  useConversationStore.getState().reset();
});

const botOf = (botId: string, displayName: string, isFriendBot = false): ConversationBotView => {
  const idx = botId.indexOf(':');
  return {
    bot: {
      botId,
      realBotId: idx < 0 ? botId : botId.slice(0, idx),
      ownerId: idx < 0 ? undefined : botId.slice(idx + 1),
      displayName,
      online: true,
      chatable: true,
      isFriendBot,
    },
    section: isFriendBot ? 'friend' : 'managed',
  };
};

const managedBotA = botOf('bot-a:2088', '管理 Bot A');
const managedBotB = botOf('bot-b:2088', '管理 Bot B');
const friendBot = botOf('fb-1:327325', '好友 Bot', true);

const sessionOf = (sessionId: string, botId = 'bot-a:2088') => ({
  sessionId,
  botId,
  title: `会话 ${sessionId}`,
  messageCount: 1,
  gmtCreate: '',
  gmtModified: '',
});

const emptyList = () => ({
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
});

const directoryModel = (over: Partial<ConversationDirectoryModel> = {}): ConversationDirectoryModel => ({
  managedBots: [managedBotA],
  friendBots: [friendBot],
  managedLoading: false,
  friendLoading: false,
  managedError: null,
  friendError: null,
  hasAgentCodingBots: false,
  retryManaged: jest.fn(),
  retryFriend: jest.fn(),
  ...over,
});

const sessionsModel = (over: Partial<ConversationSessionsModel> = {}): ConversationSessionsModel => ({
  favorites: { toggleFavorite: jest.fn(async () => true), isPending: jest.fn(() => false) },
  openBotIds: {},
  toggleBot: jest.fn(),
  selectMineSession: jest.fn(),
  selectFriendBotSession: jest.fn(),
  createSession: jest.fn(),
  loadMoreSessions: jest.fn(),
  ...over,
});

const othersModel = (over: Partial<ManagedBotOthersModel> = {}): ManagedBotOthersModel => ({
  toggleFriend: jest.fn(),
  retryDirectory: jest.fn(),
  retryFriendSessions: jest.fn(),
  loadMoreFriendSessions: jest.fn(),
  ...over,
});

function seedStore(apply: (store: ConversationState) => void): ConversationState {
  useConversationStore.getState().reset();
  apply(useConversationStore.getState());
  return useConversationStore.getState();
}

function sidebarProps(over: Partial<ConversationSidebarProps> = {}): ConversationSidebarProps {
  return {
    managedBots: [managedBotA],
    friendBots: [friendBot],
    store: seedStore(() => {}),
    directory: directoryModel(),
    sessions: sessionsModel(),
    others: othersModel(),
    onOpenSession: jest.fn(),
    onOpenPublicBots: jest.fn(),
    onOpenBotWorkshop: jest.fn(),
    ...over,
  };
}

const modelWithManagedAndFriendBots = sidebarProps();

describe('ConversationSidebar', () => {
  it('renders only managed and friend Bot groups, not team Bots', () => {
    render(<ConversationSidebar {...modelWithManagedAndFriendBots} />);
    expect(screen.getByText('张三管理的 Bot')).toBeInTheDocument();
    expect(screen.getByText('张三的好友 Bot')).toBeInTheDocument();
    expect(screen.queryByText('我的团队 Bot')).not.toBeInTheDocument();
  });

  it('filters both Bot groups by search and shows a search-empty state', () => {
    render(<ConversationSidebar {...sidebarProps({ managedBots: [managedBotA, managedBotB] })} />);
    const search = screen.getByRole('textbox', { name: '搜索 Bot' });
    fireEvent.change(search, { target: { value: 'Bot A' } });
    expect(screen.getByText('管理 Bot A')).toBeInTheDocument();
    expect(screen.queryByText('好友 Bot')).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: '不存在' } });
    expect(screen.getByText('未找到匹配的 Bot')).toBeInTheDocument();
  });

  it('toggles managed Bot expansion through the sessions model and allows multiple open', () => {
    const sessions = sessionsModel();
    const first = render(
      <ConversationSidebar {...sidebarProps({ managedBots: [managedBotA, managedBotB], sessions })} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '管理 Bot A' }));
    expect(sessions.toggleBot).toHaveBeenCalledWith('bot-a:2088', 'managed');
    first.unmount();

    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setExpandedBot('bot-b:2088', true);
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList(), items: [sessionOf('s-a')] },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
      s.setManagedBotCache('bot-b:2088', {
        botId: 'bot-b:2088',
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList(), items: [sessionOf('s-b', 'bot-b:2088')] },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
    });
    render(<ConversationSidebar {...sidebarProps({ managedBots: [managedBotA, managedBotB], store })} />);
    expect(screen.getByRole('button', { name: '管理 Bot A' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '管理 Bot B' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('会话 s-a')).toBeInTheDocument();
    expect(screen.getByText('会话 s-b')).toBeInTheDocument();
  });

  it('writes origin and scope changes to the store through the sync setters', () => {
    // 页面(任务 8)会订阅 Store 并把新快照传回;此处用 rerender 模拟同一契约。
    const { rerender } = render(<ConversationSidebar {...sidebarProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '发起归属与会话范围' }));
    fireEvent.click(screen.getByRole('radio', { name: '他人发起的' }));
    expect(useConversationStore.getState().originByManagedBotId['bot-a:2088']).toBe('others');

    rerender(<ConversationSidebar {...sidebarProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '发起归属与会话范围' }));
    fireEvent.click(screen.getByRole('radio', { name: '仅看已收藏' }));
    expect(useConversationStore.getState().scopeByManagedBotId['bot-a:2088']).toBe('favorite');
  });

  it('shows scope filter only on managed rows and keeps friend rows plain', () => {
    render(<ConversationSidebar {...sidebarProps()} />);
    expect(screen.getByRole('button', { name: '发起归属与会话范围' })).toBeInTheDocument();
    expect(screen.getByText('仅看已收藏')).toBeInTheDocument();
    // 仅一个管理 Bot 行有筛选入口；好友 Bot 行没有。
    expect(screen.getAllByRole('button', { name: '发起归属与会话范围' })).toHaveLength(1);
  });

  it('selects mine sessions through the sessions model with create actions', () => {
    const sessions = sessionsModel();
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList(), items: [sessionOf('s-a')] },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
    });
    render(
      <ConversationSidebar
        {...sidebarProps({ store, sessions, friendBots: [], directory: directoryModel({ friendBots: [] }) })}
      />,
    );
    fireEvent.click(screen.getByText('会话 s-a'));
    expect(sessions.selectMineSession).toHaveBeenCalledWith('bot-a:2088', 's-a');
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(sessions.createSession).toHaveBeenCalledWith('bot-a:2088');
  });

  it('selects friend Bot sessions through the sessions model', () => {
    const sessions = sessionsModel();
    const store = seedStore((s) => {
      s.setExpandedBot('fb-1:327325', true);
      s.setFriendBotSessions('fb-1:327325', {
        ...emptyList(),
        items: [{ ...sessionOf('fs-1', 'fb-1:327325'), title: '好友会话 1' }],
      });
    });
    render(<ConversationSidebar {...sidebarProps({ store, sessions })} />);
    fireEvent.click(screen.getByText('好友会话 1'));
    expect(sessions.selectFriendBotSession).toHaveBeenCalledWith('fb-1:327325', 'fs-1');
  });

  it('loads more sessions through the sessions model', () => {
    const sessions = sessionsModel();
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList(), items: [sessionOf('s-a')], hasMore: true },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
    });
    render(<ConversationSidebar {...sidebarProps({ store, sessions })} />);
    fireEvent.click(screen.getByRole('button', { name: '加载更多会话' }));
    expect(sessions.loadMoreSessions).toHaveBeenCalledWith('bot-a:2088', 'managed', 'all');
  });

  it('shows managed session errors without wiping the list', () => {
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList(), error: '会话加载失败' },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
    });
    render(<ConversationSidebar {...sidebarProps({ store })} />);
    expect(screen.getByRole('alert')).toHaveTextContent('会话加载失败');
  });

  it('shows directory errors with retry per section', () => {
    const directory = directoryModel({ managedError: '目录加载失败' });
    render(<ConversationSidebar {...sidebarProps({ directory })} />);
    expect(screen.getByRole('alert')).toHaveTextContent('目录加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(directory.retryManaged).toHaveBeenCalledTimes(1);
  });

  it('shows empty managed and friend states with the public Bots action', () => {
    const onOpenPublicBots = jest.fn();
    render(
      <ConversationSidebar
        {...sidebarProps({
          managedBots: [],
          friendBots: [],
          directory: directoryModel({ managedBots: [], friendBots: [] }),
          onOpenPublicBots,
        })}
      />,
    );
    expect(screen.getByText('暂无管理的 Bot')).toBeInTheDocument();
    expect(screen.getByText('暂无好友 Bot')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: '前往公开 Bot' })[0]);
    expect(onOpenPublicBots).toHaveBeenCalledTimes(1);
  });

  it('renders read-only others sessions without interactive actions and opens via onOpenSession', () => {
    const onOpenSession = jest.fn();
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotOrigin('bot-a:2088', 'others');
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'others',
        scope: 'all',
        sessions: emptyList(),
        friendDirectory: { items: [{ userId: '447147', displayName: '风太' }], loading: false, error: null },
        friendGroups: {
          '447147': {
            friend: { userId: '447147', displayName: '风太' },
            state: 'loaded',
            sessions: { ...emptyList(), items: [sessionOf('s-o')] },
            expanded: true,
          },
        },
      });
      s.setExpandedFriend('bot-a:2088', '447147', true);
    });
    render(
      <ConversationSidebar
        {...sidebarProps({ store, onOpenSession, friendBots: [], directory: directoryModel({ friendBots: [] }) })}
      />,
    );
    expect(screen.getByText('风太')).toBeInTheDocument();
    const row = screen.getByRole('button', { name: /^会话 s-o/ });
    fireEvent.click(row);
    expect(onOpenSession).toHaveBeenCalledWith('bot-a:2088', 'managed', 's-o', '447147');
    // 只读会话无交互动作。
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
  });

  it('hides friend groups whose session request succeeded with zero items', () => {
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotOrigin('bot-a:2088', 'others');
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'others',
        scope: 'all',
        sessions: emptyList(),
        friendDirectory: {
          items: [
            { userId: '447147', displayName: '风太' },
            { userId: '555555', displayName: '李四' },
          ],
          loading: false,
          error: null,
        },
        friendGroups: {
          '447147': {
            friend: { userId: '447147', displayName: '风太' },
            state: 'loaded',
            sessions: emptyList(),
            expanded: true,
          },
          '555555': {
            friend: { userId: '555555', displayName: '李四' },
            state: 'loaded',
            sessions: { ...emptyList(), items: [sessionOf('s-l', 'bot-a:2088')] },
            expanded: true,
          },
        },
      });
    });
    render(<ConversationSidebar {...sidebarProps({ store })} />);
    expect(screen.queryByText('风太')).not.toBeInTheDocument();
    expect(screen.getByText('李四')).toBeInTheDocument();
  });

  it('shows the others empty state when every friend group loaded with zero sessions', () => {
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotOrigin('bot-a:2088', 'others');
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'others',
        scope: 'all',
        sessions: emptyList(),
        friendDirectory: {
          items: [
            { userId: '447147', displayName: '风太' },
            { userId: '555555', displayName: '李四' },
          ],
          loading: false,
          error: null,
        },
        friendGroups: {
          '447147': {
            friend: { userId: '447147', displayName: '风太' },
            state: 'loaded',
            sessions: emptyList(),
            expanded: true,
          },
          '555555': {
            friend: { userId: '555555', displayName: '李四' },
            state: 'loaded',
            sessions: emptyList(),
            expanded: true,
          },
        },
      });
    });
    render(<ConversationSidebar {...sidebarProps({ store })} />);
    // 全部分组成功且零会话 → 分组隐藏 + AC-9 空态兜底。
    expect(screen.queryByText('风太')).not.toBeInTheDocument();
    expect(screen.queryByText('李四')).not.toBeInTheDocument();
    expect(screen.getByText('暂无他人发起的会话')).toBeInTheDocument();
  });

  it('does not show the others empty state while any group is still unloaded', () => {
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotOrigin('bot-a:2088', 'others');
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin: 'others',
        scope: 'all',
        sessions: emptyList(),
        friendDirectory: {
          items: [
            { userId: '447147', displayName: '风太' },
            { userId: '555555', displayName: '李四' },
          ],
          loading: false,
          error: null,
        },
        friendGroups: {
          '447147': {
            friend: { userId: '447147', displayName: '风太' },
            state: 'loaded',
            sessions: emptyList(),
            expanded: true,
          },
          // 未展开的好友尚未加载:隐藏的另一分组 ≠ 整区为空。
          '555555': {
            friend: { userId: '555555', displayName: '李四' },
            state: 'unloaded',
            sessions: emptyList(),
            expanded: false,
          },
        },
      });
    });
    render(<ConversationSidebar {...sidebarProps({ store })} />);
    expect(screen.getByText('李四')).toBeInTheDocument();
    expect(screen.queryByText('暂无他人发起的会话')).not.toBeInTheDocument();
  });

  it('links to the Bot workshop when AgentCoding Bots exist', () => {
    const onOpenBotWorkshop = jest.fn();
    render(
      <ConversationSidebar
        {...sidebarProps({ directory: directoryModel({ hasAgentCodingBots: true }), onOpenBotWorkshop })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Bot 工坊' }));
    expect(onOpenBotWorkshop).toHaveBeenCalledTimes(1);
  });
  it.each(['mine', 'others'] as const)('ends the %s session tree before pagination and errors', (origin) => {
    const list = {
      ...emptyList(),
      items: [sessionOf('first'), sessionOf('last')],
      hasMore: true,
      loadMoreError: '分页失败',
    };
    const store = seedStore((s) => {
      s.setExpandedBot('bot-a:2088', true);
      s.setManagedBotOrigin('bot-a:2088', origin);
      s.setManagedBotCache('bot-a:2088', {
        botId: 'bot-a:2088',
        origin,
        scope: 'all',
        sessions: list,
        friendDirectory: { items: [{ userId: 'friend-1', displayName: '好友用户' }], loading: false, error: null },
        friendGroups: {
          'friend-1': {
            friend: { userId: 'friend-1', displayName: '好友用户' },
            state: 'loaded',
            sessions: list,
            expanded: true,
          },
        },
      });
      s.setExpandedFriend('bot-a:2088', 'friend-1', true);
    });
    render(<ConversationSidebar {...sidebarProps({ store })} />);
    const lastRow = screen.getByRole('button', { name: /^会话 last/ }).parentElement!;
    expect(lastRow).toHaveClass('last:[&_[data-session-tree-rail]]:bottom-1/2');
    expect(lastRow.nextElementSibling).toBeNull();
    expect(screen.getByRole('button', { name: '加载更多会话' })).toBeInTheDocument();
    expect(screen.getByText('分页失败')).toBeInTheDocument();
  });
});

it.each(['managed', 'friend'] as const)('wires %s session favorites without selecting a session', (section) => {
  const botId = section === 'managed' ? managedBotA.bot.botId : friendBot.bot.botId;
  const store = seedStore((s) => {
    s.setExpandedBot(botId, true);
    const sessions = { ...emptyList(), items: [{ ...sessionOf('fav', botId), favorite: false }] };
    if (section === 'managed')
      s.setManagedBotCache(botId, {
        botId,
        origin: 'mine',
        scope: 'all',
        sessions,
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
    else s.setFriendBotSessions(botId, sessions);
  });
  const sessions = sessionsModel();
  render(<ConversationSidebar {...sidebarProps({ store, sessions })} />);
  fireEvent.click(screen.getByRole('button', { name: '收藏会话' }));
  expect(sessions.favorites.toggleFavorite).toHaveBeenCalledWith(botId, section, 'fav');
  expect(sessions.selectMineSession).not.toHaveBeenCalled();
  expect(sessions.selectFriendBotSession).not.toHaveBeenCalled();
});

it.each(['managed', 'friend'] as const)('hides TEClaw favorites and scope filters for %s Bots', (section) => {
  const view = {
    ...(section === 'managed' ? managedBotA : friendBot),
    bot: { ...(section === 'managed' ? managedBotA.bot : friendBot.bot), engine: 'TEClaw' },
  };
  const botId = view.bot.botId;
  const store = seedStore((s) => {
    s.setExpandedBot(botId, true);
    const sessions = { ...emptyList(), items: [{ ...sessionOf('s1', botId), favorite: false }] };
    if (section === 'managed')
      s.setManagedBotCache(botId, {
        botId,
        origin: 'mine',
        scope: 'all',
        sessions,
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
    else s.setFriendBotSessions(botId, sessions);
  });
  render(
    <ConversationSidebar
      {...sidebarProps({
        store,
        managedBots: section === 'managed' ? [view] : [],
        friendBots: section === 'friend' ? [view] : [],
      })}
    />,
  );
  expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
  expect(screen.queryByText('全部会话')).not.toBeInTheDocument();
  expect(screen.queryByText('仅看已收藏')).not.toBeInTheDocument();
  if (section === 'managed') {
    fireEvent.click(screen.getByRole('button', { name: '发起归属' }));
  }
  expect(Boolean(screen.queryByRole('radio', { name: '我发起的' }))).toBe(section === 'managed');
  expect(Boolean(screen.queryByRole('radio', { name: '他人发起的' }))).toBe(section === 'managed');
});
