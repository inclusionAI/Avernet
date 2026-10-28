import type { ConversationBotView } from '@/domain/conversation/types';
import type { ConversationSidebarProps } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import type { ConversationDirectoryModel } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import type { ConversationSessionsModel } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import type { ManagedBotOthersModel } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import { useConversationStore } from '@/stores/conversationStore';
import type { ConversationState } from '@/stores/conversationStoreState';
import { jest } from '@jest/globals';

export const botOf = (botId: string, displayName: string, isFriendBot = false): ConversationBotView => {
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

export const managedBotA = botOf('bot-a:2088', '管理 Bot A');
export const managedBotB = botOf('bot-b:2088', '管理 Bot B');
export const friendBot = botOf('fb-1:327325', '朋友 Bot', true);
export const teamBot = botOf('bot-team:2088', '团队空间 Bot');

export const sessionOf = (sessionId: string, botId = 'bot-a:2088') => ({
  sessionId,
  botId,
  title: `会话 ${sessionId}`,
  messageCount: 1,
  gmtCreate: '',
  gmtModified: '',
});

export const emptyList = () => ({
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
});

export const directoryModel = (over: Partial<ConversationDirectoryModel> = {}): ConversationDirectoryModel => ({
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

export const sessionsModel = (over: Partial<ConversationSessionsModel> = {}): ConversationSessionsModel => ({
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

export function seedStore(apply: (store: ConversationState) => void): ConversationState {
  useConversationStore.getState().reset();
  apply(useConversationStore.getState());
  return useConversationStore.getState();
}

export function sidebarProps(over: Partial<ConversationSidebarProps> = {}): ConversationSidebarProps {
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
    onOpenBotWorkshop: jest.fn(),
    onOpenAgentCodingBot: jest.fn(),
    replyingSessionId: null,
    ...over,
  };
}
