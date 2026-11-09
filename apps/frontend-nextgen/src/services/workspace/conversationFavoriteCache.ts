import type { ConversationSessionRequestRange } from '@/domain/conversation/types';
import { botSessionService, type ChatBotView } from './botSessionService';

type PageResult = Awaited<ReturnType<typeof botSessionService.listFavoriteSessionsPage>>;
interface FavoriteContext {
  version: number;
  pages: Map<string, { promise: Promise<PageResult>; expiresAt: number }>;
}
const contexts = new Map<string, FavoriteContext>();
const CACHE_TTL_MS = 30_000;
const MAX_CONTEXTS = 32;
const MAX_PAGES = 8;
let actor: string | null = null;

/** 页面级内存缓存，不保存凭证；切换请求用户后清空，失败不缓存。 */
export function clearConversationFavoriteCache(): void {
  contexts.clear();
  actor = null;
}

export function favoriteContext(bot: ChatBotView, userId: string): FavoriteContext {
  if (actor !== userId) { clearConversationFavoriteCache(); actor = userId; }
  const key = JSON.stringify([userId, bot.botId, bot.realBotId, bot.ownerId, bot.engine,
    bot.botType, bot.runtimeStage, bot.isTeamBot, bot.isFriendBot]);
  let context = contexts.get(key);
  if (!context) {
    if (contexts.size >= MAX_CONTEXTS) contexts.delete(contexts.keys().next().value as string);
    context = { version: 0, pages: new Map() };
    contexts.set(key, context);
  }
  return context;
}

export function invalidateConversationFavorites(bot: ChatBotView, userId: string): void {
  const context = favoriteContext(bot, userId);
  context.version += 1;
  context.pages.clear();
}

export function isFavoriteContextCurrent(context: FavoriteContext): boolean {
  return [...contexts.values()].includes(context);
}

/** 同页在途请求合并；实例范围属于页 key，收藏写入使该 Bot 所有实例页失效。 */
export function readFavoritePage(
  context: FavoriteContext, bot: ChatBotView, userId: string, page: number, pageSize: number,
  range?: ConversationSessionRequestRange,
): Promise<PageResult> {
  const key = JSON.stringify([page, pageSize, range?.deviceUuid, range?.stage]);
  const cached = context.pages.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.promise;
  const args: [] | [ConversationSessionRequestRange] = range ? [range] : [];
  const entry = {
    promise: botSessionService.listFavoriteSessionsPage(bot, userId, page, pageSize, ...args),
    expiresAt: Infinity,
  };
  entry.promise = entry.promise.then((result) => {
    if (context.pages.get(key) === entry) {
      if (result.ok) entry.expiresAt = Date.now() + CACHE_TTL_MS;
      else context.pages.delete(key);
    }
    return result;
  }, (error: unknown) => {
    if (context.pages.get(key) === entry) context.pages.delete(key);
    throw error;
  });
  context.pages.delete(key);
  if (context.pages.size >= MAX_PAGES) context.pages.delete(context.pages.keys().next().value as string);
  context.pages.set(key, entry);
  return entry.promise;
}
