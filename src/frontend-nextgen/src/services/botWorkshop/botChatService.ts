import type { BotChatContext, BotChatDetail, BotChatFilters, BotChatRelationScope } from '@/domain/botChats';
import { getBotChat, listBotChats, type BotChatListParams } from '@/services/backendApi/bots/botChatController';
import { getGroupBotTrace } from '@/services/backendApi/bots/botLogController';
import { useBotChatStore } from '@/stores/botChatStore';
import { mapBotChatDetail, mapBotChatPage } from './botChatMapper';

const optional = (value: string) => value.trim() || undefined;
const isoDate = (value: string) => (value ? new Date(value).toISOString() : undefined);

function buildParams(context: BotChatContext, filters: BotChatFilters, page: number, limit: number): BotChatListParams {
  return {
    user_id: context.userId,
    owner_id: context.ownerId && context.ownerId !== context.userId ? context.ownerId : undefined,
    trace_id: optional(filters.traceId),
    session_id: optional(filters.sessionId),
    session_key: optional(filters.sessionKey),
    query: optional(filters.keyword),
    biz_scene: optional(filters.bizScene),
    biz_task_id: optional(filters.bizTaskId),
    group_id: optional(filters.groupId),
    match_mode: filters.keyword ? 'contains' : 'exact',
    include_output_match: Boolean(filters.keyword),
    from_date: isoDate(filters.fromDate),
    to_date: isoDate(filters.toDate),
    page,
    limit,
  };
}

const messageOf = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);
const mergeTracePages = (current: ReturnType<typeof mapBotChatPage>, next: ReturnType<typeof mapBotChatPage>) => {
  const seen = new Set<string>();
  return {
    ...next,
    items: [...current.items, ...next.items].filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    }),
  };
};
let listSequence = 0;
let detailSequence = 0;
let relatedSequence = 0;

export const botChatService = {
  async list(context: BotChatContext, filters: BotChatFilters, page = 1, limit = 20) {
    const sequence = ++listSequence;
    useBotChatStore.getState().setListState({ loading: true, error: undefined });
    try {
      const response = await listBotChats(context.botId, buildParams(context, filters, page, limit));
      if (!response.data) throw new Error(response.message || '日志列表为空');
      const result = mapBotChatPage(response.data);
      if (sequence === listSequence && useBotChatStore.getState().open) {
        useBotChatStore.getState().setListState({ page: result, loading: false, error: undefined });
      }
      return result;
    } catch (error) {
      if (sequence === listSequence)
        useBotChatStore.getState().setListState({ loading: false, error: messageOf(error, '日志加载失败') });
      throw error;
    }
  },

  async detail(context: BotChatContext, traceId: string, groupId?: string) {
    const sequence = ++detailSequence;
    useBotChatStore.getState().setDetailState({ detailLoading: true, error: undefined });
    if (!groupId) {
      useBotChatStore.getState().setRelatedState({ related: undefined, relatedLoading: false, error: undefined });
    }
    try {
      const response = groupId
        ? await getGroupBotTrace(traceId, {
            bot_id: context.botId,
            group_id: groupId,
            user_id: context.userId,
            owner_id: context.ownerId && context.ownerId !== context.userId ? context.ownerId : undefined,
          })
        : await getBotChat(context.botId, traceId, {
            user_id: context.userId,
            owner_id: context.ownerId && context.ownerId !== context.userId ? context.ownerId : undefined,
          });
      if (!response.data) throw new Error(response.message || '日志详情为空');
      const detail = mapBotChatDetail(response.data);
      if (sequence === detailSequence && useBotChatStore.getState().open) {
        useBotChatStore.getState().setDetailState({ detail, detailLoading: false, error: undefined });
      }
      return detail;
    } catch (error) {
      if (sequence === detailSequence)
        useBotChatStore
          .getState()
          .setDetailState({ detailLoading: false, error: messageOf(error, '日志详情加载失败') });
      throw error;
    }
  },

  async related(context: BotChatContext, detail: BotChatDetail, scope: BotChatRelationScope, page = 1, append = false) {
    const sequence = ++relatedSequence;
    useBotChatStore.getState().setRelatedState({
      relationScope: scope,
      related: append ? useBotChatStore.getState().related : undefined,
      relatedLoading: true,
      error: undefined,
    });
    const relation: Partial<BotChatListParams> =
      scope === 'session'
        ? { session_key: detail.sessionKey, session_id: detail.sessionKey ? undefined : detail.sessionId }
        : scope === 'task'
        ? { biz_scene: detail.bizScene, biz_task_id: detail.bizTaskId }
        : { group_id: detail.groupId };
    try {
      const response = await listBotChats(context.botId, {
        user_id: context.userId,
        owner_id: context.ownerId && context.ownerId !== context.userId ? context.ownerId : undefined,
        ...relation,
        match_mode: 'exact',
        ...(scope === 'group' && detail.groupId ? { time_scope: 'all' } : {}),
        page,
        limit: 100,
      });
      if (!response.data) throw new Error(response.message || '关联日志为空');
      const result = mapBotChatPage(response.data);
      if (sequence === relatedSequence && useBotChatStore.getState().open) {
        const current = useBotChatStore.getState().related;
        const merged = append && current ? mergeTracePages(current, result) : result;
        useBotChatStore.getState().setRelatedState({ related: merged, relatedLoading: false, error: undefined });
        return merged;
      }
      return result;
    } catch (error) {
      if (sequence === relatedSequence)
        useBotChatStore
          .getState()
          .setRelatedState({ relatedLoading: false, error: messageOf(error, '关联日志加载失败') });
      throw error;
    }
  },
};
