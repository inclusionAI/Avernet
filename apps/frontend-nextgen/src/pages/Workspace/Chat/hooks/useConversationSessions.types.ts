import type { ConversationBotSection, ConversationSessionScope } from '@/domain/conversation/types';
import type { ConversationFavoritesModel } from './useConversationFavorites';
import type { ConversationSessionActionsModel } from './useConversationSessionActions';

export interface ConversationSessionsModel {
  favorites: ConversationFavoritesModel;
  actions: ConversationSessionActionsModel;
  openBotIds: Record<string, true>;
  toggleBot(botId: string, section: ConversationBotSection): void;
  selectMineSession(botId: string, sessionId: string): void;
  selectFriendBotSession(botId: string, sessionId: string): void;
  retrySessions(botId: string, section: ConversationBotSection): void;
  createSession(botId: string): Promise<void>;
  loadMoreSessions(botId: string, section: ConversationBotSection, scope: ConversationSessionScope): Promise<void>;
}
