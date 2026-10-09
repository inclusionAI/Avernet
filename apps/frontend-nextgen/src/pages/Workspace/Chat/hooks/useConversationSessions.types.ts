import type { ConversationBotSection, ConversationSessionScope } from '@/domain/conversation/types';
import type { ConversationFavoritesModel } from './useConversationFavorites';

export interface ConversationSessionsModel {
  favorites: ConversationFavoritesModel;
  openBotIds: Record<string, true>;
  toggleBot(botId: string, section: ConversationBotSection): void;
  selectMineSession(botId: string, sessionId: string): void;
  selectFriendBotSession(botId: string, sessionId: string): void;
  createSession(botId: string): Promise<void>;
  loadMoreSessions(botId: string, section: ConversationBotSection, scope: ConversationSessionScope): Promise<void>;
}
