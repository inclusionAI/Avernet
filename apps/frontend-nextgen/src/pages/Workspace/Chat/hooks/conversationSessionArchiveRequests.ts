// Coding Bot 归档请求粘合层：统一组装 Agentcoding 归档接口的公共输入，
// 并处理普通 session 首页刷新与已恢复会话详情查询。
import type {
  CodingBotSessionArchiveHost,
  CodingBotSessionArchiveListInput,
} from '@/capabilities';
import { getCapabilities } from '@/capabilities';
import type {
  CodingBotRestoredSessionItem,
  ConversationBotView,
  ConversationOrigin,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { BOT_SESSION_PAGE_SIZE, resolveChatRuntimeStage } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { emptySessions, firstPageOf, writeSessionState } from './conversationSessionCache';

export function archiveHost(): CodingBotSessionArchiveHost | null {
  return getCapabilities().getCodingBotConversationHost().value?.sessionArchive ?? null;
}

export function validateTarget(
  botId: string,
  origin: ConversationOrigin,
  revision: number,
): boolean {
  const store = useConversationStore.getState();
  return (
    (store.originByManagedBotId[botId] ?? 'mine') === origin &&
    (store.effectiveScopeByManagedBotId[botId] ?? 'all') === 'archived' &&
    (store.sessionFilterRevisionByBotId[botId] ?? 0) === revision
  );
}

export function baseInput(bot: ChatBotView, userId: string): CodingBotSessionArchiveListInput {
  return {
    botId: bot.realBotId,
    ...(bot.ownerId ? { ownerId: bot.ownerId } : {}),
    userId,
    stage: resolveChatRuntimeStage(bot) === 'online' ? 'online' : 'verify',
  };
}

export async function refreshNormalSessions(
  view: ConversationBotView,
  origin: ConversationOrigin,
  userId: string | null,
): Promise<void> {
  if (!userId) return;
  const result = await conversationService.listManagedSessions(
    view.bot,
    userId,
    'all',
    1,
    BOT_SESSION_PAGE_SIZE,
    '',
  );
  if (!result.ok || !isManagedConversationSection(view.section)) return;
  const store = useConversationStore.getState();
  if ((store.originByManagedBotId[view.bot.botId] ?? 'mine') !== origin) return;
  writeSessionState(view.bot.botId, view.section, 'all', firstPageOf(result.data.items, result.data.total), origin);
}

export async function restoredSessionsOf(
  host: CodingBotSessionArchiveHost,
  view: { bot: ChatBotView },
  userId: string | null,
  sessionKeys: string[],
): Promise<CodingBotRestoredSessionItem[]> {
  if (!userId || !sessionKeys.length) return [];
  try {
    const result = await host.listRestored(baseInput(view.bot, userId));
    if (!result.ok) return [];
    const requested = new Set(sessionKeys);
    return result.items.filter((item) => requested.has(item.sessionKey));
  } catch {
    return [];
  }
}

export { emptySessions };
