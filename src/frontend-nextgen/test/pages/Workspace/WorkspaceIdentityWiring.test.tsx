/** @jest-environment jsdom */
import WorkspacePage from '@/pages/Workspace';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const mockWorkspace = {
  isTestUser: false,
  activeIdentityId: 'human_user-101',
  activeIdentity: { id: 'human_user-101', kind: 'user', displayName: '旧身份名称', online: true },
  availableViews: ['chat', 'group'],
  currentUserId: 'user-101',
  currentUserDisplayName: '认证用户',
  currentUserAvatarUrl: 'https://example.test/avatar.png',
  botSessions: { selectedSession: null },
  botChatTarget: null,
  botChat: { chat: { messages: [], isRequesting: false, isDefaultMessagesRequesting: false, retryCount: 0 } },
  expandedBotIds: {},
  panelRef: { current: null },
  chatBots: [],
  friendBots: [],
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
jest.mock('@/hooks/useComposerSend', () => ({ useComposerSend: () => () => {} }));
jest.mock('@/pages/Workspace/hooks/useBotSessionFilesFeature', () => ({ useBotSessionFilesFeature: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useChatUrlSync', () => ({ useChatUrlSync: () => {} }));
jest.mock('@/services/workspace', () => ({ buildAgentCodingChatPath: () => '/workspace' }));
jest.mock('@/services/workspace/botSessionService', () => ({ resolveUserId: (id: string) => id }));
jest.mock('@/components/Workspace/ChatPanel/BotModelSelector', () => ({ BotModelSelectorContainer: () => null }));
jest.mock('@/components/Workspace/TaskComposerMenu', () => ({ ComposerCapabilitiesMenu: () => null }));
jest.mock('@/pages/Workspace/components/AgentCodingGuide', () => ({ AgentCodingGuide: () => null }));
jest.mock('@/pages/Workspace/components/ChatSessionSidebarSlot', () => ({ ChatSessionSidebarSlot: () => null }));
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
  mockWorkspace.isTestUser = false;
  mockWorkspace.currentUserDisplayName = '认证用户';
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
