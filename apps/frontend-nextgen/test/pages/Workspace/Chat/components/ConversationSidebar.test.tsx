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
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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
  teamBots: [],
  teamLoading: false,
  teamError: null,
  retryTeam: jest.fn(),
  managedBots: [managedBotA],
  friendBots: [friendBot],
  managedLoading: false,
  friendLoading: false,
  managedError: null,
  friendError: null,
  retryManaged: jest.fn(),
  retryFriend: jest.fn(),
  ...over,
});

const sessionsModel = (over: Partial<ConversationSessionsModel> = {}): ConversationSessionsModel => ({
  actions: { run: jest.fn(async () => true), isPending: jest.fn(() => false) },
  favorites: { toggleFavorite: jest.fn(async () => true), isPending: jest.fn(() => false) },
  openBotIds: {},
  toggleBot: jest.fn(),
  selectMineSession: jest.fn(),
  selectFriendBotSession: jest.fn(),
  createSession: jest.fn(),
  retrySessions: jest.fn(),
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
    teamBots: [],
    managedBots: [managedBotA],
    friendBots: [friendBot],
    store: seedStore(() => {}),
    directory: directoryModel(),
    sessions: sessionsModel(),
    others: othersModel(),
    onOpenSession: jest.fn(),
    onOpenPublicBots: jest.fn(),
    onOpenAgentCodingBot: jest.fn(),
    ...over,
  };
}

const modelWithManagedAndFriendBots = sidebarProps();

describe('ConversationSidebar', () => {
  it('renders managed, team and friend Bot groups', () => {
    render(<ConversationSidebar {...modelWithManagedAndFriendBots} />);
    expect(screen.getByText('张三管理的 Bot')).toBeInTheDocument();
    expect(screen.getByText('张三的好友 Bot')).toBeInTheDocument();
    expect(screen.getByText('张三的团队 Bot')).toBeInTheDocument();
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

  it('toggles managed Bot expansion through the sessions model and shows only the latest list', () => {
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
    expect(screen.getByRole('button', { name: '管理 Bot A' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: '管理 Bot B' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByText('会话 s-a')).not.toBeInTheDocument();
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

  it('shows AgentCoding Bots in the managed list and opens the coding chat directly', () => {
    const sessions = sessionsModel();
    const onOpenAgentCodingBot = jest.fn();
    const agentCodingBot: ConversationBotView = {
      bot: {
        botId: 'coding-bot:2088',
        realBotId: 'coding-bot',
        ownerId: '2088',
        displayName: 'AgentCoding Bot',
        online: true,
        chatable: true,
        engine: 'claude_code',
        isAgentCodingBot: true,
        templateName: '应用 Bot',
        spaceId: '73',
        spaceName: '测试空间',
      },
      section: 'managed',
    };

    render(
      <ConversationSidebar
        {...sidebarProps({
          managedBots: [agentCodingBot, managedBotA],
          sessions,
          friendBots: [],
          directory: directoryModel({ managedBots: [agentCodingBot, managedBotA], friendBots: [] }),
          onOpenAgentCodingBot,
        })}
      />,
    );
    expect(screen.getByText('AgentCoding Bot')).toBeInTheDocument();
    expect(screen.getByText('应用 Bot')).toBeInTheDocument();
    expect(screen.queryByText('AgentCoding Bot 请前往')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'AgentCoding Bot' }));
    expect(onOpenAgentCodingBot).toHaveBeenCalledWith(agentCodingBot.bot);
    expect(sessions.toggleBot).not.toHaveBeenCalled();
    const agentCodingRow = screen.getByRole('button', { name: 'AgentCoding Bot' }).closest('div');
    expect(within(agentCodingRow as HTMLElement).queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
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

it.each(['managed', 'friend'] as const)('wires %s session favorites without selecting a session', async (section) => {
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
  fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
  fireEvent.click(await screen.findByRole('button', { name: '收藏会话' }));
  expect(sessions.favorites.toggleFavorite).toHaveBeenCalledWith(botId, section, 'fav');
  expect(sessions.selectMineSession).not.toHaveBeenCalled();
  expect(sessions.selectFriendBotSession).not.toHaveBeenCalled();
});

it.each(['managed', 'team', 'friend'] as const)('hides TEClaw favorites and scope filters for %s Bots', (section) => {
  const view: ConversationBotView = {
    ...(section !== 'friend' ? managedBotA : friendBot),
    section,
    bot: { ...(section !== 'friend' ? managedBotA.bot : friendBot.bot), engine: 'TEClaw' },
  };
  const botId = view.bot.botId;
  const store = seedStore((s) => {
    s.setExpandedBot(botId, true);
    const sessions = { ...emptyList(), items: [{ ...sessionOf('s1', botId), favorite: false }] };
    if (section !== 'friend')
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
        teamBots: section === 'team' ? [view] : [],
        friendBots: section === 'friend' ? [view] : [],
      })}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
  expect(screen.getByRole('button', { name: '编辑标题' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole('button', { name: '编辑标题' }), { key: 'Escape' });
  expect(screen.queryByText('全部会话')).not.toBeInTheDocument();
  expect(screen.queryByText('仅看已收藏')).not.toBeInTheDocument();
  if (section !== 'friend') {
    fireEvent.click(screen.getByRole('button', { name: '发起归属' }));
  }
  expect(Boolean(screen.queryByRole('radio', { name: '我发起的' }))).toBe(section !== 'friend');
  expect(
    Boolean(screen.queryByRole('radio', { name: section === 'team' ? '他人发起的（开发中）' : '他人发起的' })),
  ).toBe(section !== 'friend');
});

it.each(['managed', 'friend'] as const)(
  'restores %s session actions including TEClaw, without selecting the row',
  async (section) => {
    const source = section === 'managed' ? managedBotA : friendBot;
    const bot = { ...source, bot: { ...source.bot, engine: 'TEClaw' } };
    const botId = bot.bot.botId;
    const store = seedStore((s) => {
      s.setExpandedBot(botId, true);
      const list = { ...emptyList(), total: 1, items: [sessionOf('edit', botId)] };
      if (section === 'managed')
        s.setManagedBotCache(botId, {
          botId,
          origin: 'mine',
          scope: 'all',
          sessions: list,
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        });
      else s.setFriendBotSessions(botId, list);
    });
    const model = sessionsModel();
    render(
      <ConversationSidebar
        {...sidebarProps({
          store,
          sessions: model,
          managedBots: section === 'managed' ? [bot] : [],
          friendBots: section === 'friend' ? [bot] : [],
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
    fireEvent.click(await screen.findByRole('button', { name: '编辑标题' }));
    fireEvent.change(await screen.findByRole('textbox', { name: '会话标题' }), { target: { value: '新名称' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(model.actions.run).toHaveBeenCalledWith(botId, section, 'edit', { type: 'rename', title: '新名称' }),
    );
    expect(model.selectMineSession).not.toHaveBeenCalled();
    expect(model.selectFriendBotSession).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
  },
);

it('团队位于管理与好友之间，搜索可命中，展开回调携带 team', () => {
  const team: ConversationBotView = { ...botOf('shared:entity', '团队助手'), section: 'team' };
  const sessions = sessionsModel();
  render(<ConversationSidebar {...sidebarProps({ teamBots: [team], sessions })} />);
  const managed = screen.getByRole('group', { name: '张三管理的 Bot' });
  const teamGroup = screen.getByRole('group', { name: '张三的团队 Bot' });
  const friend = screen.getByRole('group', { name: '张三的好友 Bot' });
  expect(managed.compareDocumentPosition(teamGroup) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(teamGroup.compareDocumentPosition(friend) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot' }), { target: { value: '团队助手' } });
  fireEvent.click(screen.getByRole('button', { name: '团队助手' }));
  expect(sessions.toggleBot).toHaveBeenCalledWith('shared:entity', 'team');
  expect(screen.queryByText('未找到匹配的 Bot')).not.toBeInTheDocument();
});

it('团队空态没有误导的添加好友入口；团队错误可单独重试', () => {
  const retry = jest.fn();
  const view = render(<ConversationSidebar {...sidebarProps({ teamBots: [] })} />);
  expect(
    within(screen.getByRole('group', { name: '张三的团队 Bot' })).queryByRole('button', { name: '前往公开 Bot' }),
  ).not.toBeInTheDocument();
  view.rerender(
    <ConversationSidebar
      {...sidebarProps({ teamBots: [], directory: directoryModel({ teamError: '团队加载失败', retryTeam: retry }) })}
    />,
  );
  const teamGroup = screen.getByRole('group', { name: '张三的团队 Bot' });
  expect(within(teamGroup).getByText('团队加载失败')).toBeInTheDocument();
  fireEvent.click(within(teamGroup).getByRole('button'));
  expect(retry).toHaveBeenCalledTimes(1);
});

it('仅团队筛选禁用他人来源，管理 Bot 保持原有可用项', () => {
  const team: ConversationBotView = { ...botOf('team:entity', '团队 Bot'), section: 'team' };
  render(<ConversationSidebar {...sidebarProps({ teamBots: [team] })} />);
  const teamGroup = screen.getByRole('group', { name: '张三的团队 Bot' });
  fireEvent.click(within(teamGroup).getByRole('button', { name: '发起归属与会话范围' }));
  expect(screen.getByRole('radio', { name: '他人发起的（开发中）' })).toBeDisabled();
  fireEvent.click(within(teamGroup).getByRole('button', { name: '发起归属与会话范围' }));
  fireEvent.click(
    within(screen.getByRole('group', { name: '张三管理的 Bot' })).getByRole('button', { name: '发起归属与会话范围' }),
  );
  expect(screen.getByRole('radio', { name: '他人发起的' })).toBeEnabled();
});
