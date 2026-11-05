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
  /** Bot 级会话创建在途标记；用于行尾 + 与空态创建按钮共享同一 pending。 */
  isCreatingSession(botId: string): boolean;
  loadMoreSessions(botId: string, section: ConversationBotSection, scope: ConversationSessionScope): Promise<void>;
}
