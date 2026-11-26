import { botSessionService, type ChatBotView } from './botSessionService';

const MODEL_CACHE_TTL_MS = 60_000;
const MAX_MODEL_CACHE_ENTRIES = 32;
type ModelResult = Awaited<ReturnType<typeof botSessionService.listModels>>;
const entries = new Map<string, { promise: Promise<ModelResult>; expiresAt: number }>();
let currentUserId: string | null = null;

/** 不持久化凭证/模型；切换登录用户即清空，失败不缓存，在途同上下文请求合并。 */
export function clearBotModelCache(): void {
  entries.clear();
  currentUserId = null;
}

export function botModelContextKey(bot: ChatBotView | null, userId: string | null): string {
  return JSON.stringify([userId, bot?.botId, bot?.realBotId, bot?.ownerId, bot?.engine,
    bot?.botType, bot?.runtimeStage, bot?.isTeamBot, bot?.isFriendBot]);
}

export function loadCachedBotModels(bot: ChatBotView, userId: string): Promise<ModelResult> {
  if (currentUserId !== userId) {
    clearBotModelCache();
    currentUserId = userId;
  }
  const key = botModelContextKey(bot, userId);
  const cached = entries.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.promise;
  const entry = { promise: botSessionService.listModels(bot, userId), expiresAt: Infinity };
  entry.promise = entry.promise.then((result) => {
    if (entries.get(key) === entry) {
      if (result.ok) entry.expiresAt = Date.now() + MODEL_CACHE_TTL_MS;
      else entries.delete(key);
    }
    return result;
  }, (error: unknown) => {
    if (entries.get(key) === entry) entries.delete(key);
    throw error;
  });
  entries.delete(key);
  if (entries.size >= MAX_MODEL_CACHE_ENTRIES) entries.delete(entries.keys().next().value as string);
  entries.set(key, entry);
  return entry.promise;
}
