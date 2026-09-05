import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { useBotSessionMap } from './useBotSessionMap';

export interface UseBotSessionsResult {
  sessionsByBotId: Record<string, BotChatSessionView[]>;
  favoriteSessionsByBotId: Record<string, BotChatSessionView[]>;
  sessionPageMetaByBotId: ReturnType<typeof useBotSessionMap>['pageMetaByBotId'];
  favoriteSessionPageMetaByBotId: ReturnType<typeof useBotSessionMap>['favoritePageMetaByBotId'];
  isSessionsLoading: boolean;
  selectedBotSessionId: string | null;
  selectedSession: BotChatSessionView | null;
  selectSession: (id: string | null) => void;
  openSession: (botId: string, sessionId: string) => void;
  createSession: (bot: ChatBotView, title?: string) => Promise<BotChatSessionView | null>;
  deleteSession: (bot: ChatBotView, sessionId: string) => Promise<boolean>;
  renameSession: (bot: ChatBotView, sessionId: string, title: string) => Promise<boolean>;
  clearContext: (bot: ChatBotView, sessionId: string) => Promise<boolean>;
  toggleFavorite: (botId: string, sessionId: string) => Promise<boolean>;
  loadFavoriteSessions: (botId: string) => Promise<void>;
  loadMoreSessions: (botId: string, mode: 'all' | 'favorite') => Promise<void>;
  updateSessionModel: (botId: string, sessionId: string, model: string) => void;
  reloadBot: (botId: string) => Promise<void>;
  toggleBotExpanded: (botId: string, sectionKey?: string) => void;
}
