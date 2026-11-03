/** @jest-environment jsdom */
// Task 8 页面边界测试:
// - ConversationPage 组合 Task 4-7 Hook / Task 6 侧栏组件,使用登录 Human 身份
//   (不调 useWorkspace),页内不出现二级身份/视图切换 tab;
// - 选中 origin=others 时渲染真实只读面板,不渲染交互舞台(Task 7 review 约定);
// - CollaborationPage 承接旧协作群分支,GroupWorkspaceArea 保持挂载。
import * as botController from '@/services/backendApi/bots/botController';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { conversationInitialState, useConversationStore } from '@/stores/conversationStore';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

const managedBot: ChatBotView = {
  botId: 'bot-a:1',
  realBotId: 'bot-a',
  ownerId: '1',
  displayName: '管理 Bot',
  online: true,
  chatable: true,
};

const agentCodingBot: ChatBotView = {
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
};
const emptyList = {
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
};


jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: '101', displayName: '张三', online: true },
    status: 'ready',
  }),
}));

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn(), push: jest.fn() },
  useLocation: () => ({ search: '', pathname: '/workspace/chat' }),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}));

jest.mock('@/services/workspace/teamBotConversationService', () => ({
  teamBotConversationService: { listBots: jest.fn().mockResolvedValue({ ok: true, data: [] }) },
}));

jest.mock('@/hooks/useMediaQuery', () => ({ useMinWidth: () => true }));

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: {
    listDirectory: jest.fn().mockResolvedValue({
      ok: true,
      data: { managedBots: [managedBot], friendBots: [] },
    }),
    listManagedSessions: jest.fn().mockResolvedValue({ ok: true, data: { items: [], total: 0, page: 1 } }),
    listFriendBotSessions: jest.fn().mockResolvedValue({ ok: true, data: { items: [], total: 0, page: 1 } }),
  },
}));

jest.mock('@/services/workspace/managedBotConversationService', () => ({
  managedBotConversationService: {
    loadFriendUsers: jest.fn().mockResolvedValue({ ok: true, data: [] }),
    listOtherSessions: jest.fn().mockResolvedValue({ ok: true, data: { items: [], total: 0, page: 1 } }),
    listOtherMessages: jest.fn().mockResolvedValue({
      ok: true,
      data: { messages: [], page: 1, total: 0, rawCount: 0, hasMore: false },
    }),
  },
}));

jest.mock('@/services/workspace/botSessionService', () => ({
  splitBotId: (id: string) => {
    const index = id.indexOf(':');
    return index < 0
      ? { realBotId: id }
      : { realBotId: id.slice(0, index), ownerId: id.slice(index + 1) };
  },
  botSessionService: {
    updateSessionTitle: jest.fn().mockResolvedValue({ ok: true, data: { sessionId: 's1', title: 't' } }),
    clearContext: jest.fn().mockResolvedValue({ ok: true, data: null }),
    createSession: jest.fn(),
    toggleFavorite: jest.fn(),
  },
  resolveUserId: (id: string) => id,
}));

// botChat 是既有产品(useBotChat);页边界测试只关心装配,内部会话通道用桩件。
// 以可 spy 的 jest.fn 包住桩件回传(同一实例),供只读分支「零连接/零发送」断言。
const mockBotChat = {
  chat: { onRequest: jest.fn(), messages: [], isRequesting: false },
  send: jest.fn(),
  stop: jest.fn(),
  reconnect: jest.fn(),
  reloadHistory: jest.fn(),
  loadMoreHistory: jest.fn(),
  hasMoreHistory: false,
  isLoadingMoreHistory: false,
  connectionStatus: 'disconnected',
  supportState: { phase: 'idle' },
};
const mockUseBotChat = jest.fn(() => mockBotChat);
jest.mock('@/pages/Workspace/hooks/useBotChat', () => ({
  useBotChat: (...args: unknown[]) => mockUseBotChat(...(args as [])),
}));

// Coding Bot 主工作区只验证互斥边界;Embedded/publish 合同由 CodingBotStageBoundary 单测覆盖。
jest.mock('@/pages/Workspace/Chat/CodingBotStageBoundary', () => ({
  CodingBotStageBoundary: ({ bot, session }: { bot: { realBotId: string }; session: { sessionId: string } }) => (
    <div data-testid="coding-bot-stage" data-bot-id={bot.realBotId} data-session-id={session.sessionId} />
  ),
}));

// 交互舞台只替换接收端(保持传参装配的真实性);只读面板走真实组件。
jest.mock('@/pages/Workspace/Chat/ConversationInteractiveStage', () => ({
  ConversationInteractiveStage: ({ headerActions }: { headerActions?: ReactNode }) => (
    <div data-testid="interactive-stage">{headerActions}</div>
  ),
}));

// GroupWorkspaceArea 的重 Hook/叶子桩件(沿用 GroupWorkspaceAreaDrawer.test 的桩件面)。
jest.mock('@/services/workspace/sessionService', () => ({
  sessionService: { getSessionDetail: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { info: jest.fn(), error: jest.fn(), success: jest.fn() } }));
jest.mock('@/pages/Workspace/hooks/useGroupWorkspace', () => ({
  useGroupWorkspace: () => ({
    groups: [],
    expandedGroupIds: {} as Record<string, true>,
    selectedGroupId: null,
    selectedGroup: null,
    canManageGroup: false,
    isLoadingGroups: false,
    groupSearchText: '',
    setGroupText: jest.fn(),
    setGroupSearchText: jest.fn(),
    kindFilter: 'all',
    setKindFilter: jest.fn(),
    membership: 'direct',
    setMembership: jest.fn(),
    sortMode: 'createdAt',
    setSortMode: jest.fn(),
    toggleGroupExpanded: jest.fn(),
    onSelectGroup: jest.fn(),
    dissolveGroup: jest.fn(),
    refreshGroups: jest.fn(),
    retryGroups: jest.fn(),
    reloadSelectedGroup: jest.fn(),
    activeIdentity: null,
    identities: [],
  }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupSessions', () => ({
  useGroupSessions: () => ({
    sessionsByGroupId: {},
    selectedSession: null,
    selectedSessionId: null,
    selectedGroupId: null,
    openSession: jest.fn(),
    createSessionIn: jest.fn(),
    leaveSession: jest.fn(),
    favoriteSessionIds: [],
    sessionSearchText: '',
    setSessionSearchText: jest.fn(),
    toggleFavorite: jest.fn(),
    renameSession: jest.fn(),
    deleteSession: jest.fn(),
  }),
}));
// GroupWorkspaceArea 直接读 chat.chat.messages / chat.panelRef（头部图标域编排），
// 桩件需提供最小 chat 契约（其余 chat.* 字段仅作为 props 透传给已 mock 的 GroupChatPane）。
jest.mock('@/pages/Workspace/hooks/useGroupChat', () => ({
  useGroupChat: () => ({ chat: { messages: [] }, panelRef: { current: null } }),
}));
jest.mock('@/pages/Workspace/hooks/useGroupManagement', () => ({ useGroupManagement: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useSessionManagement', () => ({
  useSessionManagement: () => ({}),
}));
jest.mock('@/pages/Workspace/hooks/useGroupCreateDialog', () => ({
  useGroupCreateDialog: () => ({ open: false, openModal: jest.fn(), closeModal: jest.fn(), handleCreated: jest.fn() }),
}));
jest.mock('@/pages/Workspace/hooks/useOpenDefaultGroupSession', () => ({
  useOpenDefaultGroupSession: () => jest.fn(),
}));
jest.mock('@/pages/Workspace/components/GroupChatPane', () => ({
  GroupChatPane: () => <div data-testid="group-chat-pane" />,
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/SessionFilesSidebar', () => ({
  SessionFilesSidebar: () => null,
}));
jest.mock('@/pages/Workspace/components/Modals/CreateGroupModal', () => ({
  CreateGroupModal: () => null,
}));
jest.mock('@/pages/Workspace/components/WorkspaceManagePanels', () => ({
  WorkspaceManagePanels: () => null,
}));

const ConversationPage = (
  require('@/pages/Workspace/Chat/ConversationPage') as typeof import('@/pages/Workspace/Chat/ConversationPage')
).default;
const CollaborationPage = (
  require('@/pages/Workspace/Collaboration') as typeof import('@/pages/Workspace/Collaboration')
).default;

afterEach(() => {
  extendCapabilities(defaultCapabilities);
});

beforeEach(() => {
  // 会话 Store 是模块级单例,测试间复位,避免选择/缓存串号。
  useConversationStore.setState({ ...conversationInitialState });
  mockUseBotChat.mockClear();
  for (const fn of [
    mockBotChat.chat.onRequest,
    mockBotChat.send,
    mockBotChat.stop,
    mockBotChat.reconnect,
    mockBotChat.reloadHistory,
    mockBotChat.loadMoreHistory,
  ]) {
    (fn as jest.Mock).mockClear();
  }
});

describe('ConversationPage(Task 8 组合页)', () => {
  it('ConversationPage uses Human identity and renders no Workspace identity tabs', async () => {
    render(<ConversationPage />);
    expect(screen.queryByRole('tab', { name: '协作群' })).not.toBeInTheDocument();
    // Human 登录身份用于目录加载(而非旧工作身份);组名按 v2 设计稿固定为「我的 Bot」。
    await screen.findByText('我的 Bot');
  });

  it('点击 AgentCoding Bot 展开普通会话目录，不再进入专用 coding-chat', async () => {
    const conversationServiceMock = require('@/services/workspace/conversationService').conversationService as {
      listDirectory: jest.Mock;
    };
    const { history } = require('@umijs/max') as { history: { push: jest.Mock; replace: jest.Mock } };
    history.push.mockClear();
    conversationServiceMock.listDirectory.mockResolvedValueOnce({
      ok: true,
      data: { managedBots: [agentCodingBot], friendBots: [] },
    });

    render(<ConversationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'AgentCoding Bot' }));

    expect(await screen.findByText('AgentCoding Bot 暂无会话')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建会话' })).toBeInTheDocument();
    expect(history.push).not.toHaveBeenCalled();
  });

  it('origin=mine 选中会话渲染交互舞台(不渲染只读面板)', async () => {
    useConversationStore.setState({
      ...conversationInitialState,
      expandedBotIds: { 'bot-a:1': true },
      originByManagedBotId: { 'bot-a:1': 'mine' },
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedScope: 'all',
      selectedSessionId: 's1',
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'mine',
          scope: 'all',
          sessions: {
            ...emptyList,
            items: [
              {
                sessionId: 's1',
                botId: 'bot-a:1',
                title: '我的会话',
                messageCount: 3,
                gmtModified: '',
                gmtCreate: '',
              },
            ],
          },
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        },
      },
    });

    render(<ConversationPage />);

    const stage = await screen.findByTestId('interactive-stage');
    expect(stage).toBeInTheDocument();
    expect(mockUseBotChat).toHaveBeenCalledWith(
      expect.objectContaining({ botId: 'bot-a:1' }),
      expect.objectContaining({ sessionId: 's1' }),
      expect.anything(),
      undefined,
      '101',
    );
    // 主舞台 wrapper 是侧栏右侧的横向 flex item，必须允许收缩；否则普通会话的
    // 固有宽度会把 ChatLayout 连同 420px TaskPanel 推出可视区，初次只剩窄条可见。
    expect(stage.parentElement).toHaveClass('min-w-0');
    expect(screen.queryByText('请选择一个好友用户会话')).not.toBeInTheDocument();
  });

  it('识别出 Coding Bot 后，目标 Session 未就绪也先触发 Embedded 宿主预载', async () => {
    const preload = jest.fn();
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage: () => <div />, preload },
      }),
    });
    const conversationServiceMock = require('@/services/workspace/conversationService').conversationService as {
      listDirectory: jest.Mock;
    };
    conversationServiceMock.listDirectory.mockResolvedValueOnce({
      ok: true,
      data: { managedBots: [agentCodingBot], friendBots: [] },
    });

    render(<ConversationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'AgentCoding Bot' }));

    expect(await screen.findByText('AgentCoding Bot 暂无会话')).toBeInTheDocument();
    expect(preload).toHaveBeenCalledTimes(1);
  });

  it('Coding Bot 选中会话时挂载 Embedded 边界且不初始化普通 Chat Runtime', async () => {
    const conversationServiceMock = require('@/services/workspace/conversationService').conversationService as {
      listDirectory: jest.Mock;
    };
    conversationServiceMock.listDirectory.mockResolvedValueOnce({
      ok: true,
      data: { managedBots: [agentCodingBot], friendBots: [] },
    });
    useConversationStore.setState({
      ...conversationInitialState,
      expandedBotIds: { 'coding-bot:2088': true },
      originByManagedBotId: { 'coding-bot:2088': 'mine' },
      selectedBotId: 'coding-bot:2088',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedScope: 'all',
      selectedSessionId: 'coding-s1',
      sessionsByBotId: {
        'coding-bot:2088': {
          botId: 'coding-bot:2088',
          origin: 'mine',
          scope: 'all',
          sessions: {
            ...emptyList,
            items: [
              {
                sessionId: 'coding-s1',
                botId: 'coding-bot:2088',
                title: 'Coding 会话',
                messageCount: 1,
                gmtModified: '',
                gmtCreate: '',
              },
            ],
          },
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        },
      },
    });

    render(<ConversationPage />);

    const stage = await screen.findByTestId('coding-bot-stage');
    expect(stage).toHaveAttribute('data-bot-id', 'coding-bot');
    expect(stage).toHaveAttribute('data-session-id', 'coding-s1');
    expect(screen.queryByTestId('interactive-stage')).not.toBeInTheDocument();
    expect(mockUseBotChat).not.toHaveBeenCalled();
    expect(mockBotChat.chat.onRequest).not.toHaveBeenCalled();
    expect(mockBotChat.send).not.toHaveBeenCalled();
  });

  it('对当前在线 managed 单聊读取版本，关闭提示后切换会话重新检查', async () => {
    const eligibleBot: ChatBotView = {
      ...managedBot,
      engine: 'openclaw',
      actions: ['restart'],
    };
    const getVersion = jest.spyOn(botController, 'getBotEngineVersion').mockResolvedValue({
      code: 200000,
      data: {
        detection: { status: 'known' },
        current: { raw_version: '1.2.0' },
        release_target: { status: 'available', release_id: 'REL-2' },
        comparison: 'older',
        restart_hint: 'recommended',
        context: { bot_id: 'bot-a', stage: 'draft', checked_at: '2026-10-20T00:00:00Z' },
      },
    } as never);
    const conversationServiceMock = require('@/services/workspace/conversationService').conversationService as {
      listDirectory: jest.Mock;
    };
    conversationServiceMock.listDirectory.mockResolvedValueOnce({
      ok: true,
      data: { managedBots: [eligibleBot], friendBots: [] },
    });
    useConversationStore.setState({
      ...conversationInitialState,
      expandedBotIds: { 'bot-a:1': true },
      originByManagedBotId: { 'bot-a:1': 'mine' },
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedScope: 'all',
      selectedSessionId: 's1',
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'mine',
          scope: 'all',
          sessions: {
            ...emptyList,
            items: ['s1', 's2'].map((sessionId) => ({
              sessionId,
              botId: 'bot-a:1',
              title: sessionId,
              messageCount: 1,
              gmtModified: '',
              gmtCreate: '',
            })),
          },
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        },
      },
    });

    const view = render(<ConversationPage />);
    try {
      expect(await screen.findByRole('button', { name: '引擎有更新' })).toBeInTheDocument();
      expect(getVersion).toHaveBeenCalledWith(
        'bot-a',
        expect.objectContaining({
          userId: '101',
          ownerId: '1',
          stage: 'draft',
        }),
      );
      fireEvent.click(screen.getByRole('button', { name: '关闭当前会话的引擎更新提示' }));
      expect(screen.queryByTestId('chat-engine-version-notice')).not.toBeInTheDocument();

      act(() => useConversationStore.setState({ selectedSessionId: 's2' }));
      expect(await screen.findByRole('button', { name: '引擎有更新' })).toBeInTheDocument();
      await waitFor(() => expect(getVersion).toHaveBeenCalledTimes(2));
    } finally {
      view.unmount();
      getVersion.mockRestore();
    }
  });

  it.each(['managed', 'team'] as const)('%s origin=others 选中好友会话:只读、零写接口和实时连接', async (section) => {
    if (section === 'team') {
      require('@/services/workspace/conversationService').conversationService.listDirectory.mockResolvedValueOnce({
        ok: true,
        data: { managedBots: [], friendBots: [] },
      });
      require('@/services/workspace/teamBotConversationService').teamBotConversationService.listBots.mockResolvedValueOnce(
        { ok: true, data: [managedBot] },
      );
    }
    useConversationStore.setState({
      ...conversationInitialState,
      expandedBotIds: { 'bot-a:1': true },
      originByManagedBotId: { 'bot-a:1': 'others' },
      selectedBotId: 'bot-a:1',
      selectedSection: section,
      selectedOrigin: 'others',
      selectedScope: 'all',
      selectedFriendUserId: 'f9',
      selectedSessionId: 's-o',
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'others',
          scope: 'all',
          sessions: emptyList,
          friendDirectory: {
            items: [{ userId: 'f9', displayName: '小明' }],
            loading: false,
            error: null,
          },
          friendGroups: {
            f9: {
              friend: { userId: 'f9', displayName: '小明' },
              state: 'loaded',
              sessions: {
                ...emptyList,
                items: [
                  {
                    sessionId: 's-o',
                    botId: 'bot-a:1',
                    title: '他人会话',
                    messageCount: 3,
                    gmtModified: '',
                    gmtCreate: '',
                  },
                ],
              },
              expanded: true,
            },
          },
        },
      },
    });

    render(<ConversationPage />);

    // 只读面板分支接管(真实只读面板渲染选中会话头),交互舞台未挂载。
    expect(await screen.findByText('他人会话')).toBeInTheDocument();
    expect(screen.queryByTestId('interactive-stage')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /发送|消息|输入/ })).not.toBeInTheDocument();

    // 只读分支不挂载普通 Runtime,即使目录尚在解析也不能初始化实时连接装配入口。
    expect(mockUseBotChat).not.toHaveBeenCalled();

    // 发送/重连/首条消息请求链路零调用。
    expect(mockBotChat.send).not.toHaveBeenCalled();
    expect(mockBotChat.chat.onRequest).not.toHaveBeenCalled();
    expect(mockBotChat.reconnect).not.toHaveBeenCalled();

    // 只读分支不触发任何会话写接口(创建/重命名/清上下文/收藏)。
    const botSessionServiceMock = require('@/services/workspace/botSessionService').botSessionService as Record<
      string,
      jest.Mock
    >;
    expect(botSessionServiceMock.createSession).not.toHaveBeenCalled();
    expect(botSessionServiceMock.updateSessionTitle).not.toHaveBeenCalled();
    expect(botSessionServiceMock.clearContext).not.toHaveBeenCalled();
    expect(botSessionServiceMock.toggleFavorite).not.toHaveBeenCalled();
  });

  it('origin=others 真实页渲染只读面板,不出交互舞台', async () => {
    useConversationStore.setState({
      ...conversationInitialState,
      expandedBotIds: { 'bot-a:1': true },
      originByManagedBotId: { 'bot-a:1': 'others' },
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'others',
      selectedScope: 'all',
      selectedFriendUserId: 'f9',
      selectedSessionId: null,
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'others',
          scope: 'all',
          sessions: emptyList,
          friendDirectory: {
            items: [{ userId: 'f9', displayName: '小明' }],
            loading: false,
            error: null,
          },
          friendGroups: {
            f9: {
              friend: { userId: 'f9', displayName: '小明' },
              state: 'unloaded',
              sessions: emptyList,
              expanded: true,
            },
          },
        },
      },
    });

    render(<ConversationPage />);

    // 只读面板(真实组件)接管:暂无选中可读会话的空态;交互舞台的 ChatPanel 不出现。
    expect(await screen.findByText('请选择一个好友用户会话')).toBeInTheDocument();
    expect(screen.queryByTestId('interactive-stage')).not.toBeInTheDocument();
  });
});

describe('CollaborationPage(Task 8 组合页)', () => {
  it('CollaborationPage keeps GroupWorkspaceArea mounted', () => {
    render(<CollaborationPage />);
    expect(screen.getByLabelText('协作群列表')).toBeInTheDocument();
    expect(screen.getByTestId('group-chat-pane')).toBeInTheDocument();
  });
});

it('expanding a Bot with no sessions replaces the chat stage with its creation prompt', async () => {
  render(<ConversationPage />);
  fireEvent.click(await screen.findByRole('button', { name: '管理 Bot' }));
  expect(await screen.findByText('管理 Bot 暂无会话')).toBeInTheDocument();
  expect(screen.queryByTestId('interactive-stage')).not.toBeInTheDocument();
  expect(mockUseBotChat).toHaveBeenLastCalledWith(null, null, expect.anything(), undefined, '101');
  const service = require('@/services/workspace/botSessionService').botSessionService as { createSession: jest.Mock };
  service.createSession.mockResolvedValueOnce({
    ok: true,
    data: {
      sessionId: 'new-s',
      botId: 'bot-a:1',
      title: '新会话',
      messageCount: 0,
      gmtCreate: '',
      gmtModified: '',
    },
  });
  fireEvent.click(screen.getByRole('button', { name: '创建会话' }));
  await waitFor(() => expect(screen.getByTestId('interactive-stage')).toBeInTheDocument());
  expect(useConversationStore.getState().selectedSessionId).toBe('new-s');
});
