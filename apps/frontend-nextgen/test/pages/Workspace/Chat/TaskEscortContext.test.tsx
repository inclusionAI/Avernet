/** @jest-environment jsdom */
import type { HumanIdentity } from '@/capabilities';
import type { TaskEscortPanelProps } from '@/components/Workspace/TaskEscortPanel';
import { StandardConversationStageContainer } from '@/pages/Workspace/Chat/StandardConversationStageContainer';
import type { ConversationSessionsModel } from '@/pages/Workspace/Chat/hooks/useConversationSessions.types';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';

const mockExecution = jest.fn();
const mockPanel = jest.fn<void, [TaskEscortPanelProps]>();
const mockBotChat = {
  chat: { isRequesting: false, messages: [], onRequest: jest.fn() }, historyReady: true,
  send: jest.fn(), reloadHistory: jest.fn(), connectionRuntime: { resolved: true, engine: 'openclaw' },
};
const mockFileFeature = { senderRef: { current: null }, fileDrawerOpen: false, openFileDrawer: jest.fn(), closeFileDrawer: jest.fn() };

jest.mock('@/hooks/useTaskExecution', () => ({ useTaskExecution: (options: unknown) => { mockExecution(options); return {}; } }));
jest.mock('@/hooks/useTaskExecuteFromCard', () => ({ useTaskExecuteFromCard: () => {} }));
jest.mock('@/hooks/useTaskPreflightAssistant', () => ({ useTaskPreflightAssistant: () => ({}) }));
jest.mock('@/hooks/useComposerSend', () => ({ useComposerSend: () => jest.fn() }));
jest.mock('@/pages/Workspace/hooks/useBotChat', () => ({ useBotChat: () => mockBotChat }));
jest.mock('@/pages/Workspace/hooks/useBotSessionFilesFeature', () => ({ useBotSessionFilesFeature: () => mockFileFeature }));
jest.mock('@/services/workspace/chatBridge', () => ({ chatBridge: {} }));
jest.mock('@tc-chat/adapters', () => ({ useChatBridge: () => {} }));
jest.mock('@/pages/Workspace/Chat/hooks/useChatEngineVersionNotice', () => ({ useChatEngineVersionNotice: () => ({ notice: null, dialogs: null }) }));
jest.mock('@/pages/Workspace/Chat/hooks/useConversationSessionEdits', () => ({ useConversationSessionEdits: () => ({}) }));
jest.mock('@/pages/Workspace/Chat/hooks/useSessionSwitchReset', () => ({ useSessionSwitchReset: () => {} }));
jest.mock('@/pages/Workspace/Chat/hooks/useChatHeaderPanels', () => ({
  useChatHeaderPanels: () => ({ openPanel: 'escort', favorites: {}, closePanel: jest.fn() }),
}));
jest.mock('@/pages/Workspace/Chat/components/ChatHeaderActions', () => ({ ChatHeaderActions: () => null }));
jest.mock('@/pages/Workspace/Chat/components/ChatHeaderRightPanel', () => ({ ChatHeaderRightPanel: () => null, SessionDetailPanel: () => null }));
jest.mock('@/pages/Workspace/Chat/components/SessionHistorySearchPanel', () => ({ SessionHistorySearchPanel: () => null }));
jest.mock('@/pages/Workspace/Chat/ConversationInteractiveStage', () => ({
  ConversationInteractiveStage: ({ headerRightPanels }: { headerRightPanels: ReactNode }) => <>{headerRightPanels}</>,
}));
jest.mock('@/components/Workspace/TaskEscortPanel', () => ({ TaskEscortPanel: (props: TaskEscortPanelProps) => { mockPanel(props); return null; } }));

const bot = {
  botId: 'collaborator-bot:asset-owner-202', realBotId: 'collaborator-bot', ownerId: 'asset-owner-202',
  displayName: '协作 Bot', engine: 'openclaw', online: true, chatable: true,
} as ChatBotView;
const session: BotChatSessionView = {
  sessionId: 'task-session', botId: bot.botId, title: '当前任务会话', messageCount: 0,
  gmtModified: '', gmtCreate: '', favorite: false,
};
const human = { userId: 'viewer-101', displayName: '查看者', online: true } as HumanIdentity;
const sessions = { favorites: { toggleFavorite: jest.fn(), isPending: () => false } } as unknown as ConversationSessionsModel;

function stage(viewer: HumanIdentity | null) {
  return <StandardConversationStageContainer bot={bot} session={session} human={viewer} section="managed" chatBots={[bot]} sessions={sessions} onOpenSessionList={() => {}} />;
}

beforeEach(() => { mockExecution.mockClear(); mockPanel.mockClear(); });

it('passes the exact same context object through interactiveModel and HeaderPanels as the + task composer', () => {
  window.history.replaceState(null, '', '/workspace/chat?ownerId=forged&userId=forged&botId=forged');
  const { rerender } = render(stage(human));
  const composerContext = (mockExecution.mock.calls.at(-1)?.[0] as { context: TaskComposerContext | null }).context;
  expect(composerContext).toEqual({
    sourceType: 'bot', ownerUserId: 'viewer-101', ownerBotId: 'collaborator-bot:asset-owner-202',
    mainSessionId: 'task-session', mainSessionName: '当前任务会话', parentTaskId: null,
  });
  expect(mockPanel.mock.calls.at(-1)?.[0].context).toBe(composerContext);
  expect(mockPanel.mock.calls.at(-1)?.[0].open).toBe(true);
  rerender(stage({ ...human, userId: 'viewer-303' }));
  const nextContext = (mockExecution.mock.calls.at(-1)?.[0] as { context: TaskComposerContext | null }).context;
  expect(nextContext?.ownerUserId).toBe('viewer-303');
  expect(nextContext?.ownerBotId).toBe(bot.botId);
  expect(mockPanel.mock.calls.at(-1)?.[0].context).toBe(nextContext);
  expect(nextContext).not.toBe(composerContext);
  rerender(stage(null));
  expect((mockExecution.mock.calls.at(-1)?.[0] as { context: TaskComposerContext | null }).context).toBeNull();
  expect(mockPanel.mock.calls.at(-1)?.[0].context).toBeNull();
});
