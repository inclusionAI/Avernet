/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation/types';
import { ConversationSidebar } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import { WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/pages/Workspace/components/ResizableWorkspaceSidebar';
import { useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import {
  botOf,
  managedBotA,
  managedBotB,
  friendBot,
  teamBot,
  sessionOf,
  emptyList,
  directoryModel,
  sessionsModel,
  seedStore,
  sidebarProps,
} from './ConversationSidebar.testUtils';

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
  // 整栏收起记忆持久化在 localStorage:用例间清掉,避免收起竖轨用例泄漏到其余用例。
  window.localStorage.removeItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY);
  useConversationStore.getState().reset();
});

const modelWithManagedAndFriendBots = sidebarProps();

describe('ConversationSidebar', () => {
  it('shows bot-shaped loading skeletons while a directory is still loading', () => {
    const { container } = render(
      <ConversationSidebar
        {...sidebarProps({
          managedBots: [],
          friendBots: [],
          directory: directoryModel({ managedLoading: true, friendLoading: true }),
        })}
      />,
    );
    // 加载中 ≠ 空列表:给目录行回声骨架,不落「暂无」空态。
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThanOrEqual(6);
    expect(screen.queryByText('暂无管理的 Bot')).not.toBeInTheDocument();
    expect(screen.queryByText('暂无好友 Bot')).not.toBeInTheDocument();
  });

  it('renders three groups 我的 Bot / 团队 Bot / 好友 Bot when team Bots exist', () => {
    render(
      <ConversationSidebar
        {...sidebarProps({ teamBots: [teamBot], directory: directoryModel({ teamBots: [teamBot] }) })}
      />,
    );
    expect(screen.getByText('我的 Bot')).toBeInTheDocument();
    expect(screen.getByText('团队 Bot')).toBeInTheDocument();
    expect(screen.getByText('好友 Bot')).toBeInTheDocument();
    expect(screen.getByText('团队空间 Bot')).toBeInTheDocument();
  });

  it('分组标题右缘展示成员计数（aria-hidden 纯视觉，切换按钮可访问名保持纯标题）', () => {
    render(
      <ConversationSidebar
        {...sidebarProps({
          teamBots: [teamBot],
          managedBots: [managedBotA, managedBotB],
          directory: directoryModel({ managedBots: [managedBotA, managedBotB], teamBots: [teamBot] }),
        })}
      />,
    );
    // 各分组计数 = 本组可见成员数(2026-10-22 体验精修)。
    const managedGroup = screen.getByRole('group', { name: '我的 Bot' });
    expect(within(managedGroup).getByText('2')).toBeInTheDocument();
    const teamGroup = screen.getByRole('group', { name: '团队 Bot' });
    expect(within(teamGroup).getByText('1')).toBeInTheDocument();
    // 计数置于切换按钮之外 → 按钮可访问名不带数字,既有 getByRole(name:'我的 Bot') 契约不破。
    expect(screen.getByRole('button', { name: '我的 Bot' })).toBeInTheDocument();
    // 搜索过滤时计数随可见行数联动。
    fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot' }), { target: { value: '管理 Bot A' } });
    const filteredGroup = screen.getByRole('group', { name: '我的 Bot' });
    expect(within(filteredGroup).getByText('1')).toBeInTheDocument();
  });

  it('keeps the empty 团队 Bot group visible with its empty state', () => {
    render(<ConversationSidebar {...modelWithManagedAndFriendBots} />);
    expect(screen.getByText('我的 Bot')).toBeInTheDocument();
    expect(screen.getByText('好友 Bot')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '团队 Bot' })).toBeInTheDocument();
    expect(screen.getByText('暂无运行中的团队 Bot')).toBeInTheDocument();
  });

  it('covers the team Bot group in keyword search', () => {
    render(
      <ConversationSidebar
        {...sidebarProps({
          teamBots: [teamBot],
          managedBots: [managedBotA, managedBotB],
          directory: directoryModel({ managedBots: [managedBotA, managedBotB], teamBots: [teamBot] }),
        })}
      />,
    );
    const search = screen.getByRole('textbox', { name: '搜索 Bot' });
    // 关键词只命中团队 Bot:管理/好友分组仍渲染组题但行过滤不出。
    fireEvent.change(search, { target: { value: '团队空间' } });
    expect(screen.getByText('团队空间 Bot')).toBeInTheDocument();
    expect(screen.queryByText('管理 Bot A')).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: '不存在' } });
    expect(screen.getByText('未找到匹配的 Bot')).toBeInTheDocument();
  });

  it('expands team Bots through the team section (collaborations 目录 section=team)', () => {
    const sessions = sessionsModel();
    const teamSectionBot = { ...teamBot, section: 'team' as const };
    render(
      <ConversationSidebar
        {...sidebarProps({
          teamBots: [teamSectionBot],
          sessions,
          directory: directoryModel({ teamBots: [teamSectionBot] }),
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '团队空间 Bot' }));
    expect(sessions.toggleBot).toHaveBeenCalledWith('bot-team:2088', 'team');
  });

  it('filters both Bot groups by search and shows a search-empty state', () => {
    render(<ConversationSidebar {...sidebarProps({ managedBots: [managedBotA, managedBotB] })} />);
    const search = screen.getByRole('textbox', { name: '搜索 Bot' });
    fireEvent.change(search, { target: { value: 'Bot A' } });
    expect(screen.getByText('管理 Bot A')).toBeInTheDocument();
    expect(screen.queryByText('朋友 Bot')).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: '发起人与会话筛选' }));
    fireEvent.click(screen.getByRole('button', { name: '他人发起' }));
    expect(useConversationStore.getState().originByManagedBotId['bot-a:2088']).toBe('others');

    rerender(<ConversationSidebar {...sidebarProps()} />);
    fireEvent.click(screen.getByRole('button', { name: '发起人与会话筛选' }));
    // 管理/好友两行弹层常驻挂载同名「已收藏」按钮:按 DOM 序取管理行(先渲染)。
    fireEvent.click(screen.getAllByRole('button', { name: '已收藏' })[0]);
    expect(useConversationStore.getState().scopeByManagedBotId['bot-a:2088']).toBe('favorite');
  });

  it('renders the origin filter on managed rows and the scope-only filter on friend rows', () => {
    render(<ConversationSidebar {...sidebarProps()} />);
    // 管理 Bot 行:「发起人与会话筛选」(发起人 + 会话);好友 Bot 行:仅「会话」筛选(无发起人)。
    expect(screen.getAllByRole('button', { name: '发起人与会话筛选' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: '会话筛选' })).toBeInTheDocument();
    expect(screen.getByText('发起人')).toBeInTheDocument();
    // 管理/好友两行弹层常驻挂载,各带一份「已收藏」选项。
    expect(screen.getAllByRole('button', { name: '已收藏' })).toHaveLength(2);
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

  it('shows empty managed and friend states with inline route links', () => {
    const onOpenPublicBots = jest.fn();
    const onOpenBotWorkshop = jest.fn();
    render(
      <ConversationSidebar
        {...sidebarProps({
          managedBots: [],
          friendBots: [],
          directory: directoryModel({ managedBots: [], friendBots: [] }),
          onOpenPublicBots,
          onOpenBotWorkshop,
        })}
      />,
    );
    expect(screen.getByText('暂无管理的 Bot')).toBeInTheDocument();
    expect(screen.getByText('暂无好友 Bot')).toBeInTheDocument();
    // 我的 Bot 空态引导去 Bot 管理创建(2026-10-14 体验修复);好友 Bot 空态引导去公开 Bot 发现。
    fireEvent.click(screen.getByRole('button', { name: '「Bot 管理」' }));
    expect(onOpenBotWorkshop).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '「公开 Bot」' }));
    expect(onOpenPublicBots).toHaveBeenCalledTimes(1);
  });

  it('不再渲染分组标题行的「+」添加入口', () => {
    render(
      <ConversationSidebar
        {...sidebarProps({
          teamBots: [teamBot],
          directory: directoryModel({ teamBots: [teamBot] }),
        })}
      />,
    );
    expect(screen.queryByRole('button', { name: '添加 Bot' })).not.toBeInTheDocument();
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
  it.each(['mine', 'others'] as const)('ends the %s session list before pagination and errors', (origin) => {
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
    const lastRow = screen.getByRole('button', { name: /^会话 last/ }).closest('div');
    expect(lastRow?.nextElementSibling).toBeNull();
    // 平齐化后末行不再渲染树形末行标识（rail 已全量移除）。
    expect(lastRow?.querySelector('[data-session-tree-rail]')).toBeNull();
    expect(screen.getByRole('button', { name: '加载更多会话' })).toBeInTheDocument();
    expect(screen.getByText('分页失败')).toBeInTheDocument();
  });

  describe('分组展开收起（2026-10-10 需求：我的/好友 Bot 列表）', () => {
    it('点击「我的 Bot」/「好友 Bot」标题收起分组隐藏行列表,再点展开恢复,收起记忆写入 Store', () => {
      // 页面契约:Hook 订阅 Store 并把新快照传回;此处用 rerender + 实时快照模拟(同「origin/scope」用例)。
      const base = sidebarProps();
      const { rerender } = render(<ConversationSidebar {...base} />);
      const syncStore = () => rerender(<ConversationSidebar {...base} store={useConversationStore.getState()} />);
      expect(screen.getByRole('button', { name: '我的 Bot' })).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByRole('button', { name: '好友 Bot' })).toHaveAttribute('aria-expanded', 'true');

      fireEvent.click(screen.getByRole('button', { name: '我的 Bot' }));
      expect(useConversationStore.getState().collapsedBotGroups).toEqual({ managed: true });
      syncStore();
      expect(screen.queryByText('管理 Bot A')).toBeNull();
      expect(screen.getByText('朋友 Bot')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '我的 Bot' })).toHaveAttribute('aria-expanded', 'false');

      fireEvent.click(screen.getByRole('button', { name: '好友 Bot' }));
      syncStore();
      expect(screen.queryByText('朋友 Bot')).toBeNull();
      expect(useConversationStore.getState().collapsedBotGroups).toEqual({ managed: true, friend: true });

      fireEvent.click(screen.getByRole('button', { name: '我的 Bot' }));
      syncStore();
      expect(screen.getByText('管理 Bot A')).toBeInTheDocument();
      expect(screen.queryByText('朋友 Bot')).toBeNull();
      expect(useConversationStore.getState().collapsedBotGroups).toEqual({ friend: true });
    });

    it('收起状态跨挂载保持(移动端 Drawer 关开后重开同一 Store 快照仍收起)', () => {
      const base = sidebarProps();
      const { unmount } = render(<ConversationSidebar {...base} />);
      fireEvent.click(screen.getByRole('button', { name: '我的 Bot' }));
      expect(useConversationStore.getState().collapsedBotGroups).toEqual({ managed: true });
      unmount();
      // 不再经 sidebarProps(其内部 seedStore 会 reset),用实时快照重挂载。
      render(<ConversationSidebar {...base} store={useConversationStore.getState()} />);
      expect(screen.queryByText('管理 Bot A')).toBeNull();
    });

    it('搜索时收起的分组强制展开,匹配行仍可检索;清空关键词后收起记忆仍生效', () => {
      const base = sidebarProps();
      const { rerender } = render(<ConversationSidebar {...base} />);
      fireEvent.click(screen.getByRole('button', { name: '我的 Bot' }));
      fireEvent.click(screen.getByRole('button', { name: '好友 Bot' }));
      rerender(<ConversationSidebar {...base} store={useConversationStore.getState()} />);
      expect(screen.queryByText('管理 Bot A')).toBeNull();

      fireEvent.change(screen.getByLabelText('搜索 Bot'), { target: { value: '管理' } });
      expect(screen.getByText('管理 Bot A')).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText('搜索 Bot'), { target: { value: '朋友' } });
      expect(screen.getByText('朋友 Bot')).toBeInTheDocument();

      // 清空关键词回到非搜索态,收起记忆恢复生效。
      fireEvent.change(screen.getByLabelText('搜索 Bot'), { target: { value: '' } });
      expect(screen.queryByText('管理 Bot A')).toBeNull();
      expect(screen.queryByText('朋友 Bot')).toBeNull();
    });

    it('收起分组只留标题行（2026-10-22 需求变更）:不渲染泊位头像/直达按钮,成员量由标题计数承载', () => {
      const base = sidebarProps({ managedBots: [managedBotA, managedBotB] });
      const { rerender } = render(<ConversationSidebar {...base} />);
      const syncStore = () => rerender(<ConversationSidebar {...base} store={useConversationStore.getState()} />);
      fireEvent.click(screen.getByRole('button', { name: '我的 Bot' }));
      syncStore();
      // 收起即净:行级全名与泊位直达入口都不出现。
      expect(screen.queryByText('管理 Bot A')).toBeNull();
      expect(screen.queryByRole('button', { name: '打开 管理 Bot A' })).toBeNull();
      expect(screen.queryByRole('button', { name: '打开 管理 Bot B' })).toBeNull();
      // 可感知的剩余信息 = 标题 + 成员计数;展开恢复由标题行一键触达。
      expect(screen.getByRole('button', { name: '我的 Bot' })).toHaveAttribute('aria-expanded', 'false');
      const managedGroup = screen.getByRole('group', { name: '我的 Bot' });
      expect(within(managedGroup).getByText('2')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '我的 Bot' }));
      syncStore();
      expect(screen.getByText('管理 Bot A')).toBeInTheDocument();
    });

    it('整栏收起渲染「Bot 泊位」竖轨:选中带当前态,头像直达恢复分组可见并补懒加载,+N 请求反展开整栏', () => {
      window.localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, 'true');
      const sessions = sessionsModel();
      const manyBots = Array.from({ length: 7 }, (_, i) => botOf(`rail-${i}:2088`, `轨道 Bot ${i}`));
      useConversationStore.getState().selectConversation({
        botId: 'rail-0:2088',
        section: 'managed',
        origin: 'mine',
        scope: 'all',
        friendUserId: null,
        sessionId: null,
      });
      render(
        <ConversationSidebar
          {...sidebarProps({
            managedBots: manyBots,
            store: useConversationStore.getState(),
            sessions,
            friendBots: [],
            directory: directoryModel({ managedBots: manyBots, friendBots: [] }),
          })}
        />,
      );
      const aside = screen.getByRole('complementary', { name: '会话侧栏' });
      expect(aside).toHaveAttribute('data-collapsed', 'true');
      // 选中 Bot 在竖轨上留当前态;7 个成员只显 5 个 + 「+2」溢出。
      expect(screen.getByRole('button', { name: '打开 轨道 Bot 0' })).toHaveAttribute('aria-current', 'true');
      expect(screen.queryByRole('button', { name: '打开 轨道 Bot 5' })).not.toBeInTheDocument();
      // 头像直达:恢复分组可见 + 未展开过的 Bot 补懒加载(与展开态行为一致),侧栏保持收起。
      fireEvent.click(screen.getByRole('button', { name: '打开 轨道 Bot 2' }));
      expect(sessions.toggleBot).toHaveBeenCalledWith('rail-2:2088', 'managed');
      expect(useConversationStore.getState().collapsedBotGroups.managed).toBeUndefined();
      expect(aside).toHaveAttribute('data-collapsed', 'true');
      // +N 溢出经 render-prop 拿到 expand():整栏反展开,完整列表回归。
      fireEvent.click(screen.getByRole('button', { name: '展开侧栏，查看我的 Bot其余 2 个' }));
      expect(aside).toHaveAttribute('data-collapsed', 'false');
    });

    it('团队 Bot 分组接入收起(2026-10-22 需求变更):标题即开关,收起只留标题行+计数,记忆写入 Store', () => {
      const base = sidebarProps({
        teamBots: [teamBot],
        directory: directoryModel({ teamBots: [teamBot] }),
        managedBots: [managedBotA],
        friendBots: [friendBot],
      });
      const { rerender } = render(<ConversationSidebar {...base} />);
      const syncStore = () => rerender(<ConversationSidebar {...base} store={useConversationStore.getState()} />);
      // 标题成为开关按钮,与我的/好友 Bot 同契约。
      const teamToggles = screen.getByRole('button', { name: '团队 Bot' });
      expect(teamToggles).toHaveAttribute('aria-expanded', 'true');

      fireEvent.click(teamToggles);
      expect(useConversationStore.getState().collapsedBotGroups).toEqual({ team: true });
      syncStore();
      // 收起即净:团队 Bot 行不渲染,标题计数仍在;搜索时强制展开可检索。
      expect(screen.queryByText('团队空间 Bot')).toBeNull();
      expect(within(screen.getByRole('group', { name: '团队 Bot' })).getByText('1')).toBeInTheDocument();
      expect(teamToggles).toHaveAttribute('aria-expanded', 'false');

      fireEvent.change(screen.getByLabelText('搜索 Bot'), { target: { value: '团队空间' } });
      expect(screen.getByText('团队空间 Bot')).toBeInTheDocument();
      // 清空关键词后收起记忆恢复生效。
      fireEvent.change(screen.getByLabelText('搜索 Bot'), { target: { value: '' } });
      expect(screen.queryByText('团队空间 Bot')).toBeNull();
      fireEvent.click(teamToggles);
      syncStore();
      expect(screen.getByText('团队空间 Bot')).toBeInTheDocument();
    });
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
  expect(screen.queryByText('全部')).not.toBeInTheDocument();
  expect(screen.queryByText('已收藏')).not.toBeInTheDocument();
  if (section !== 'friend') {
    fireEvent.click(screen.getByRole('button', { name: '发起人筛选' }));
  }
  expect(Boolean(screen.queryByRole('button', { name: '我发起' }))).toBe(section !== 'friend');
  expect(Boolean(screen.queryByRole('button', { name: '他人发起' }))).toBe(section !== 'friend');
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
  const managed = screen.getByRole('group', { name: '我的 Bot' });
  const teamGroup = screen.getByRole('group', { name: '团队 Bot' });
  const friend = screen.getByRole('group', { name: '好友 Bot' });
  expect(managed.compareDocumentPosition(teamGroup) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(teamGroup.compareDocumentPosition(friend) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot' }), { target: { value: '团队助手' } });
  fireEvent.click(screen.getByRole('button', { name: '团队助手' }));
  expect(sessions.toggleBot).toHaveBeenCalledWith('shared:entity', 'team');
  expect(screen.queryByText('未找到匹配的 Bot')).not.toBeInTheDocument();
});

it('团队加载结束后空列表保留分组，加载失败仍可单独重试', () => {
  const retry = jest.fn();
  const view = render(
    <ConversationSidebar {...sidebarProps({ directory: directoryModel({ teamLoading: true }) })} />,
  );
  expect(screen.getByRole('group', { name: '团队 Bot' })).toBeInTheDocument();
  expect(screen.queryByText('暂无运行中的团队 Bot')).not.toBeInTheDocument();
  view.rerender(<ConversationSidebar {...sidebarProps({ teamBots: [] })} />);
  expect(screen.getByRole('group', { name: '团队 Bot' })).toBeInTheDocument();
  expect(screen.getByText('暂无运行中的团队 Bot')).toBeInTheDocument();
  // 团队目录独立失败:分组出现并给错误态+重试,不阻塞管理/好友分组。
  view.rerender(
    <ConversationSidebar
      {...sidebarProps({ teamBots: [], directory: directoryModel({ teamError: '团队加载失败', retryTeam: retry }) })}
    />,
  );
  const teamGroup = screen.getByRole('group', { name: '团队 Bot' });
  expect(within(teamGroup).getByText('团队加载失败')).toBeInTheDocument();
  fireEvent.click(within(teamGroup).getByRole('button', { name: '重试' }));
  expect(retry).toHaveBeenCalledTimes(1);
});

it('搜索中团队组无匹配时保留分类标题，不展示目录空态', () => {
  render(
    <ConversationSidebar
      {...sidebarProps({ teamBots: [teamBot], directory: directoryModel({ teamBots: [teamBot] }) })}
    />,
  );
  expect(screen.getByRole('group', { name: '团队 Bot' })).toBeInTheDocument();
  // 关键字只命中我的 Bot，团队组仍保留标题，与其他分类保持一致。
  fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot' }), { target: { value: '管理' } });
  expect(screen.getByRole('group', { name: '团队 Bot' })).toBeInTheDocument();
  expect(screen.queryByText('暂无运行中的团队 Bot')).not.toBeInTheDocument();
});

it('仅团队筛选禁用他人来源，管理 Bot 保持原有可用项', () => {
  const team: ConversationBotView = { ...botOf('team:entity', '团队 Bot'), section: 'team' };
  render(<ConversationSidebar {...sidebarProps({ teamBots: [team] })} />);
  const teamGroup = screen.getByRole('group', { name: '团队 Bot' });
  fireEvent.click(within(teamGroup).getByRole('button', { name: '发起人与会话筛选' }));
  // 气泡内容常驻挂载(forceMount),管理/团队两行弹层都在 document 上,两侧选项同名
  // 「他人发起」,以禁用态区分:团队侧禁用、管理侧可用。点击禁用项不写入 Store。
  const othersOptions = screen
    .getAllByRole('button', { name: '他人发起' })
    .map((node) => node as HTMLButtonElement);
  expect(othersOptions).toHaveLength(2);
  expect(othersOptions.filter((node) => node.disabled)).toHaveLength(1);
  fireEvent.click(othersOptions.find((node) => node.disabled)!);
  expect(useConversationStore.getState().originByManagedBotId['team:entity']).toBeUndefined();
  fireEvent.click(othersOptions.find((node) => !node.disabled)!);
  expect(useConversationStore.getState().originByManagedBotId['bot-a:2088']).toBe('others');
});

describe('ConversationSidebar #2990 还原接线（菜单传导 / 回复中指示 / infoHint）', () => {
  it('onOpenBotManagement 仅经管理/团队行传导:两行出「更多操作」,好友行不出(好友无空间上下文)', () => {
    const onOpenBotManagement = jest.fn();
    const spaceManaged = { ...managedBotA, bot: { ...managedBotA.bot, spaceId: '101' } };
    const spaceTeam = { ...teamBot, bot: { ...teamBot.bot, spaceId: '202' } };
    render(
      <ConversationSidebar
        {...sidebarProps({
          managedBots: [spaceManaged],
          teamBots: [spaceTeam],
          friendBots: [friendBot],
          directory: directoryModel({
            managedBots: [spaceManaged],
            teamBots: [spaceTeam],
            friendBots: [friendBot],
          }),
          onOpenBotManagement,
        })}
      />,
    );
    // 「更多操作」入口仅出现在管理行与团队行;好友行不渲染。
    const entries = screen.getAllByRole('button', { name: '更多操作' });
    expect(entries).toHaveLength(2);
    // 管理行打开菜单点击「Bot 管理」,回调携带所属空间信息。
    fireEvent.click(entries[0]);
    fireEvent.click(screen.getByRole('button', { name: /Bot 管理/ }));
    expect(onOpenBotManagement).toHaveBeenCalledTimes(1);
    expect(onOpenBotManagement).toHaveBeenCalledWith(
      expect.objectContaining({ botId: 'bot-a:2088', spaceId: '101' }),
    );
  });

  it('replyingSessionId 传导至会话行:命中行渲染「Bot 正在回复」,未命中行不渲染', () => {
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
    render(<ConversationSidebar {...sidebarProps({ store, replyingSessionId: 's-a' })} />);
    expect(screen.getAllByRole('status', { name: 'Bot 正在回复' })).toHaveLength(1);
  });

  it('团队 Bot 分组标题携带来源提示 infoHint(#2990 还原),切换按钮可访问名保持纯标题', async () => {
    render(
      <ConversationSidebar
        {...sidebarProps({
          teamBots: [teamBot],
          directory: directoryModel({ teamBots: [teamBot] }),
        })}
      />,
    );
    // 既有契约不回归:标题即开关、可访问名纯「团队 Bot」,计数徽标仍在。
    expect(screen.getByRole('button', { name: '团队 Bot' })).toBeInTheDocument();
    const info = screen.getByLabelText('通过团队成员身份获取的 Bot');
    fireEvent.pointerMove(info, { pointerType: 'mouse' });
    fireEvent.focusIn(info);
    await waitFor(() => expect(screen.getByText('通过团队成员身份获取的 Bot')).toBeInTheDocument());
  });
});

it('空团队目录在整栏收起后仍保留分类入口，点击可展开并查看空态', () => {
  window.localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, 'true');
  render(<ConversationSidebar {...sidebarProps({ teamBots: [] })} />);
  fireEvent.click(screen.getByRole('button', { name: '展开侧栏，添加团队 Bot' }));
  expect(screen.getByRole('group', { name: '团队 Bot' })).toBeInTheDocument();
  expect(screen.getByText('暂无运行中的团队 Bot')).toBeInTheDocument();
});
