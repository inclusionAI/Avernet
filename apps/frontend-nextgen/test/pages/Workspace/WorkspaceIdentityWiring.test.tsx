/** @jest-environment jsdom */
// 页面边界身份接线测试。
// Task 10 退休旧 /workspace 混合页(含 Bot 身份只读好友分支)后,本文件以新页面边界为准:
// - /workspace/chat 固定登录 Human 视角:即便协作群侧的全局工作身份是 Bot,
//   对话页目录仍按登录用户 userId 加载,不再依赖 activeIdentity.kind 分支;
// - /workspace/collaboration 保留既有身份语义:legacy `current=`(旧 bot= 群链接)
//   身份照常回填全局身份 Store,协作群区域挂载。
import { conversationInitialState, useConversationStore } from '@/stores/conversationStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, waitFor } from '@testing-library/react';

const humanIdentity = { userId: '101', displayName: '张三', online: true };
const managedBot = {
  botId: 'bot-a:1',
  realBotId: 'bot-a',
  ownerId: '1',
  displayName: '管理 Bot',
  online: true,
  chatable: true,
};

let mockedSearchParams = new URLSearchParams();

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({ identity: humanIdentity, status: 'ready' }),
}));

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn(), push: jest.fn() },
  useLocation: () => ({
    search: mockedSearchParams.toString(),
    pathname: '/workspace/chat',
  }),
  useSearchParams: () => [mockedSearchParams, jest.fn()],
}));

jest.mock('@/hooks/useMediaQuery', () => ({ useMinWidth: () => true }));

const mockListDirectory = jest.fn().mockResolvedValue({
  ok: true,
  data: { managedBots: [managedBot], friendBots: [] },
});

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: {
    listDirectory: (...args: unknown[]) => mockListDirectory(...(args as [])) as unknown,
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
jest.mock('@/pages/Workspace/hooks/useBotChat', () => ({
  useBotChat: () => ({
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
  }),
}));

// 交互舞台只替换接收端;身份接线断言不依赖其内部。
jest.mock('@/pages/Workspace/Chat/ConversationInteractiveStage', () => ({
  ConversationInteractiveStage: () => <div data-testid="interactive-stage" />,
}));

// 协作群区域替换为接收端:断言页面按 Human 身份装配并保持挂载。
jest.mock('@/pages/Workspace/GroupWorkspaceArea', () => ({
  GroupWorkspaceArea: ({ userIdentityName }: { userIdentityName?: string }) => (
    <div data-testid="group-workspace-area" data-user-name={userIdentityName} />
  ),
}));

jest.mock('@/services/workspace/sessionService', () => ({
  sessionService: { getSessionDetail: jest.fn() },
}));

const ConversationPage = (
  require('@/pages/Workspace/Chat/ConversationPage') as typeof import('@/pages/Workspace/Chat/ConversationPage')
).default;
const CollaborationPage = (
  require('@/pages/Workspace/Collaboration') as typeof import('@/pages/Workspace/Collaboration')
).default;

beforeEach(() => {
  mockListDirectory.mockClear();
  mockedSearchParams = new URLSearchParams();
  // 身份 Store 是模块级单例,测试间复位,避免工作身份串号。
  useWorkspaceStore.getState().resetWorkspace();
  useConversationStore.setState({ ...conversationInitialState });
});

it('全局工作身份是 Bot 时,/workspace/chat 仍用登录 Human 目录(不再有 Bot 只读分支)', async () => {
  // 协作群侧把工作身份切到 Bot;对话页不得读取该身份。
  useWorkspaceStore.getState().setIdentities(
    [
      { id: 'user-101', kind: 'user', displayName: '张三', online: true },
      { id: 'b1', kind: 'bot', displayName: '皮皮虾', online: true },
    ],
    'b1',
  );

  render(<ConversationPage />);

  // 目录按登录用户 userId 加载(Human 固定视角),而非 Bot 工作身份。
  await waitFor(() => expect(mockListDirectory).toHaveBeenCalledWith('101'));
  expect(await screen.findByText('张三管理的 Bot')).toBeInTheDocument();
});

it('全局工作身份是 Bot 时,/workspace/chat 不渲染 Bot 身份只读好友区域', async () => {
  useWorkspaceStore.getState().setIdentities(
    [
      { id: 'user-101', kind: 'user', displayName: '张三', online: true },
      { id: 'b1', kind: 'bot', displayName: '皮皮虾', online: true },
    ],
    'b1',
  );

  render(<ConversationPage />);

  expect(await screen.findByText('张三管理的 Bot')).toBeInTheDocument();
  expect(screen.queryByTestId('bot-friend-workspace-area')).not.toBeInTheDocument();
  expect(screen.queryByText('请选择一个好友用户会话')).not.toBeInTheDocument();
});

it('/workspace/collaboration 保留既有身份语义:legacy bot= 身份照常回填 Store', async () => {
  mockedSearchParams = new URLSearchParams('current=b1');
  useWorkspaceStore.getState().setIdentities(
    [
      { id: 'user-101', kind: 'user', displayName: '张三', online: true },
      { id: 'b1', kind: 'bot', displayName: '皮皮虾', online: true },
    ],
    'user-101',
  );

  render(<CollaborationPage />);

  // 协作页沿用 useWorkspacePage 的 current= 身份 hydration(旧群链接兼容面)。
  await waitFor(() => expect(useWorkspaceStore.getState().activeIdentityId).toBe('b1'));
  // 页面挂载协作群区域,且登录 Human 展示信息照常透传。
  expect(screen.getByTestId('group-workspace-area')).toBeInTheDocument();
  expect(screen.getByTestId('group-workspace-area')).toHaveAttribute('data-user-name', '张三');
});
