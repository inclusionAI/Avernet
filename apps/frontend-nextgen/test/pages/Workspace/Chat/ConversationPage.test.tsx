/** @jest-environment jsdom */
// Task 8 页面边界测试:
// - ConversationPage 组合 Task 4-7 Hook / Task 6 侧栏组件,使用登录 Human 身份
//   (不调 useWorkspace),页内不出现二级身份/视图切换 tab;
// - 选中 origin=others 时渲染真实只读面板,不渲染交互舞台(Task 7 review 约定);
// - CollaborationPage 承接旧协作群分支,GroupWorkspaceArea 保持挂载。
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { conversationInitialState, useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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

// 交互舞台只替换接收端(保持传参装配的真实性);只读面板走真实组件。
jest.mock('@/pages/Workspace/Chat/ConversationInteractiveStage', () => ({
  ConversationInteractiveStage: () => <div data-testid="interactive-stage" />,
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
jest.mock('@/pages/Workspace/hooks/useGroupChat', () => ({ useGroupChat: () => ({}) }));
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
    // Human 登录身份用于目录分组标题(而非旧工作身份)。
    await screen.findByText('张三管理的 Bot');
  });

  it('点击 AgentCoding Bot 直接进入专用 coding-chat', async () => {
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

    expect(history.push).toHaveBeenCalledTimes(1);
    expect(history.push).toHaveBeenCalledWith(
      '/coding/coding-chat?botId=coding-bot%3A2088&space_id=73&space_name=%E6%B5%8B%E8%AF%95%E7%A9%BA%E9%97%B4',
    );
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

    expect(await screen.findByTestId('interactive-stage')).toBeInTheDocument();
    expect(screen.queryByText('请选择一个好友用户会话')).not.toBeInTheDocument();
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

    // useBotChat(实时连接装配入口)接收到 null 目标:真实 Hook 对 null bot/session 不建连。
    expect(mockUseBotChat).toHaveBeenCalledWith(null, null, expect.anything(), undefined, '101');

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
