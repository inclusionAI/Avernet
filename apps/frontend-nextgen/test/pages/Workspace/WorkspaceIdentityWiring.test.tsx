/** @jest-environment jsdom */
import WorkspacePage from '@/pages/Workspace';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

let mockComposerDeps: {
  beforeSend?: (content: string) => Promise<void> | void;
} | null = null;

const mockWorkspace = {
  isTestUser: false,
  activeIdentityId: 'human_user-101',
  activeIdentity: { id: 'human_user-101', kind: 'user', displayName: '旧身份名称', online: true },
  availableViews: ['chat', 'group'],
  currentUserId: 'user-101',
  currentUserDisplayName: '认证用户',
  currentUserAvatarUrl: 'https://example.test/avatar.png',
  botSessions: {
    selectedSession: null as BotChatSessionView | null,
    renameSessionOnFirstMessage: jest.fn(),
  },
  botChatTarget: null,
  botFriendConversation: {
    humanFriends: [],
    botFriends: [],
    humanLoading: false,
    botLoading: false,
    humanError: null,
    botError: null,
    settled: true,
    reloadHuman: jest.fn(),
    reloadBot: jest.fn(),
    selectedFriend: null,
    sessions: {
      expandedFriendUserId: null,
      sessions: [],
      selectedSession: null,
      loading: false,
      error: null,
      hasMore: false,
      isLoadingMore: false,
      loadMoreError: null,
      toggleFriend: jest.fn(),
      selectSession: jest.fn(),
      retry: jest.fn(),
      loadMore: jest.fn(),
    },
    history: {
      messages: [],
      loading: false,
      error: null,
      hasMore: false,
      isLoadingMore: false,
      loadMoreError: null,
      retry: jest.fn(),
      loadMore: jest.fn(),
    },
  },
  botChat: { chat: { messages: [], isRequesting: false, isDefaultMessagesRequesting: false, retryCount: 0 } },
  expandedBotIds: {},
  panelRef: { current: null },
  chatBots: [] as ChatBotView[],
  friendBots: [] as ChatBotView[],
  draft: '',
  setDraft: jest.fn(),
};

jest.mock('@/hooks/useWorkspace', () => ({ useWorkspace: () => mockWorkspace }));
jest.mock('@/pages/Workspace/hooks/useWorkspacePage', () => ({
  useWorkspacePage: () => ({ view: 'chat', setView: () => {} }),
}));
jest.mock('@/hooks/useMediaQuery', () => ({ useMinWidth: () => true }));
jest.mock('react-router-dom', () => ({ useNavigate: () => () => {} }));
jest.mock('@/hooks/useTaskExecution', () => ({ useTaskExecution: () => ({}) }));
jest.mock('@/hooks/useTaskExecuteFromCard', () => ({ useTaskExecuteFromCard: () => {} }));
jest.mock('@/hooks/useComposerSend', () => ({
  useComposerSend: (_taskExecution: unknown, deps: typeof mockComposerDeps) => {
    mockComposerDeps = deps;
    return () => {};
  },
}));
jest.mock('@/pages/Workspace/hooks/useBotSessionFilesFeature', () => ({ useBotSessionFilesFeature: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useChatUrlSync', () => ({ useChatUrlSync: () => {} }));
jest.mock('@/pages/Workspace/hooks/useBotFriendChatUrlSync', () => ({ useBotFriendChatUrlSync: () => {} }));
jest.mock('@/services/workspace', () => ({ buildAgentCodingChatPath: () => '/workspace' }));
jest.mock('@/services/workspace/botSessionService', () => ({ resolveUserId: (id: string) => id }));
jest.mock('@/components/Workspace/ChatPanel/BotModelSelector', () => ({ BotModelSelectorContainer: () => null }));
jest.mock('@/components/Workspace/TaskComposerMenu', () => ({ ComposerCapabilitiesMenu: () => null }));
jest.mock('@/pages/Workspace/components/AgentCodingGuide', () => ({ AgentCodingGuide: () => null }));
jest.mock('@/pages/Workspace/components/ChatSessionSidebarSlot', () => ({ ChatSessionSidebarSlot: () => null }));
jest.mock('@/pages/Workspace/BotFriendWorkspaceArea', () => ({
  BotFriendWorkspaceArea: () => <div data-testid="bot-friend-workspace-area" />,
}));
jest.mock('@/pages/Workspace/GroupWorkspaceArea', () => ({ GroupWorkspaceArea: () => null }));
// 只替换接收端，不替换页面的真实分支和参数装配逻辑。
jest.mock('@/components/Workspace/ChatPanel', () => ({
  ChatPanel: (props: { authenticatedUserId?: string; authenticatedUserName?: string; mode?: string }) => (
    <div
      data-testid="page-chat-panel"
      data-user-id={props.authenticatedUserId}
      data-user-name={props.authenticatedUserName}
      data-mode={props.mode}
    />
  ),
}));

beforeEach(() => {
  mockComposerDeps = null;
  mockWorkspace.isTestUser = false;
  mockWorkspace.activeIdentityId = 'human_user-101';
  mockWorkspace.activeIdentity = { id: 'human_user-101', kind: 'user', displayName: '旧身份名称', online: true };
  mockWorkspace.currentUserDisplayName = '认证用户';
  mockWorkspace.chatBots = [];
  mockWorkspace.friendBots = [];
  mockWorkspace.botSessions.selectedSession = null;
  mockWorkspace.botSessions.renameSessionOnFirstMessage.mockReset();
});

it.each([
  { isTestUser: false, mode: 'bot' },
  { isTestUser: true, mode: 'support' },
])('$mode 页面分支向 ChatPanel 传递认证身份而非旧工作身份名称', ({ isTestUser, mode }) => {
  mockWorkspace.isTestUser = isTestUser;

  render(<WorkspacePage />);

  expect(screen.getByTestId('page-chat-panel')).toHaveAttribute('data-mode', mode);
  expect(screen.getByTestId('page-chat-panel')).toHaveAttribute('data-user-id', 'user-101');
  expect(screen.getByTestId('page-chat-panel')).toHaveAttribute('data-user-name', '认证用户');
});

it('真实用户认证名称更新后重新传递给 Bot 单聊组件', () => {
  const { rerender } = render(<WorkspacePage />);
  mockWorkspace.currentUserDisplayName = '更新后的认证用户';

  rerender(<WorkspacePage />);

  expect(screen.getByTestId('page-chat-panel')).toHaveAttribute('data-user-name', '更新后的认证用户');
});

it('Bot 身份对话渲染独立的只读好友会话区域', () => {
  mockWorkspace.activeIdentityId = 'bot-a:327325';
  mockWorkspace.activeIdentity = { id: 'bot-a:327325', kind: 'bot', displayName: '皮皮虾', online: true };

  render(<WorkspacePage />);

  expect(screen.getByTestId('bot-friend-workspace-area')).toBeInTheDocument();
  expect(screen.queryByTestId('page-chat-panel')).not.toBeInTheDocument();
});

it('用户身份单聊把原始首条正文接入会话自动重命名 beforeSend', async () => {
  const bot = {
    botId: 'bot-a:327325',
    realBotId: 'bot-a',
    ownerId: '327325',
    displayName: '皮皮虾',
    online: true,
    chatable: true,
  };
  const session = {
    sessionId: 'session-1',
    botId: bot.botId,
    title: '新会话',
    messageCount: 0,
    gmtModified: '',
    gmtCreate: '',
  };
  mockWorkspace.chatBots = [bot];
  mockWorkspace.botSessions.selectedSession = session;
  mockWorkspace.botSessions.renameSessionOnFirstMessage.mockResolvedValue(true);

  render(<WorkspacePage />);
  await mockComposerDeps?.beforeSend?.('  第一条原始消息  ');

  expect(mockWorkspace.botSessions.renameSessionOnFirstMessage).toHaveBeenCalledWith(
    bot,
    session,
    '  第一条原始消息  ',
  );
});
