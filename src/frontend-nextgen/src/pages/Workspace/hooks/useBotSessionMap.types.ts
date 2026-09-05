import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';

export interface BotSessionPageMeta {
  total: number;
  hasMore: boolean;
  nextPage: number;
  isLoadingMore: boolean;
  error?: string;
  loadMoreError?: string;
}

export interface UseBotSessionMapResult {
  rawByBotId: Record<string, BotChatSessionView[]>;
  favoriteByBotId: Record<string, BotChatSessionView[]>;
  pageMetaByBotId: Record<string, BotSessionPageMeta>;
  favoritePageMetaByBotId: Record<string, BotSessionPageMeta>;
  isLoading: boolean;
  updateBotSessions: (botId: string, fn: (list: BotChatSessionView[]) => BotChatSessionView[]) => void;
  updateBotFavoriteSessions: (botId: string, fn: (list: BotChatSessionView[]) => BotChatSessionView[]) => void;
  reloadBot: (bot: ChatBotView, userId: string) => Promise<void>;
  loadFavoriteSessions: (bot: ChatBotView, userId: string) => Promise<void>;
  loadMoreSessions: (bot: ChatBotView, userId: string, mode: 'all' | 'favorite') => Promise<void>;
  /** 按 section 展开 bot，记录归属 section 并懒加载会话。 */
  toggleBotExpanded: (botId: string, sectionKey?: string) => void;
}
