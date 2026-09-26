import { BOT_SESSION_PAGE_SIZE } from '@/services/workspace/botSessionService';
import type { BotSessionPageMeta } from './useBotSessionMap.types';

export function hasMoreForPage(itemCount: number, total: number, page: number): boolean {
  return itemCount > 0 && page * BOT_SESSION_PAGE_SIZE < total;
}

export function successBotPageMeta(total: number, hasMore: boolean, nextPage: number): BotSessionPageMeta {
  return { total, hasMore, nextPage, isLoadingMore: false, error: undefined, loadMoreError: undefined };
}

export function errorBotPageMeta(
  current: { total?: number; nextPage?: number } | undefined,
  error: string,
): BotSessionPageMeta {
  return { total: current?.total ?? 0, hasMore: false, nextPage: current?.nextPage ?? 1, isLoadingMore: false, error };
}
