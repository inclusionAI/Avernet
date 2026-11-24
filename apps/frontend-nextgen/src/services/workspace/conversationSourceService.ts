import type { ConversationSessionRequestRange } from '@/domain/conversation/types';
import { getCapabilities } from '@/capabilities';
import { sortConversationSessions } from '@/domain/conversation/sessionOrder';
import { isCollabCodingSessionId } from '@/domain/conversation/codingSessionOwnership';
import { listBotSessions } from '@/services/backendApi/bots/privateBotSessionController';
import {
  botSessionService, mapBotSessionSummary, resolveChatRuntimeStage, resolveUserId,
  type BotSessionPageView, type ChatBotView,
} from './botSessionService';
import type { DomainResult } from './identityService';

/** 仅管理/协作 Bot 使用；好友 Bot 继续走原 f_user_id 链路，不混用 source。 */
export async function listConversationSource(
  bot: ChatBotView, userId: string, source: 'mine' | 'others', page: number, pageSize: number,
  sessionKey: string, range?: ConversationSessionRequestRange, cursor?: string | null,
): Promise<DomainResult<BotSessionPageView>> {
  // “我发起”直接按当前 Human 的 conversation subject 查询。Caller Bot 由后端
  // 路由到独立实例，owner-mode Bot 保持共享运行时；无需 caller-context 探测。
  if (source === 'mine') {
    const result = await botSessionService.listSessionsPage(
      bot, userId, page, pageSize, sessionKey, range,
    );
    if (!result.ok) return result;
    const hidden = bot.isAgentCodingBot
      ? getCapabilities().getCodingBotConversationHost().value?.isSessionHidden
      : undefined;
    return { ok: true, data: {
      ...result.data,
      items: sortConversationSessions(result.data.items.filter(
        (item) => !hidden?.(item) && !(bot.isAgentCodingBot && isCollabCodingSessionId(item.sessionId)),
      )),
    } };
  }
  try {
    const response = await listBotSessions(bot.realBotId, {
      user_id: resolveUserId(userId), owner_id: bot.ownerId,
      source, page: 1, page_size: pageSize,
      stage: range?.stage ?? resolveChatRuntimeStage(bot),
      ...(cursor ? { source_cursor: cursor } : {}),
      ...(sessionKey ? { session_key: sessionKey } : {}),
      ...(range ? { device_uuid: range.deviceUuid } : {}),
    });
    const data = response.data;
    // 旧后端忽略 source 时不能把未过滤列表显示成本人/他人；要求先部署新接口。
    if (!data || data.source !== source || typeof data.has_more !== 'boolean' ||
        (data.has_more && !data.next_cursor)) {
      return { ok: false, error: {
        code: 'SESSION_SOURCE_UNSUPPORTED', friendlyMessage: '当前服务暂不支持会话来源筛选，请升级后端后重试。', canRetry: true,
      } };
    }
    const hidden = bot.isAgentCodingBot ? getCapabilities().getCodingBotConversationHost().value?.isSessionHidden : undefined;
    return { ok: true, data: {
      items: sortConversationSessions(data.items.map((item) => ({ ...mapBotSessionSummary(item), botId: bot.botId }))
        .filter((item) => !hidden?.(item) && !(bot.isAgentCodingBot && isCollabCodingSessionId(item.sessionId)))),
      total: data.total, hasMore: data.has_more, nextCursor: data.next_cursor, scanLimited: data.scan_limited,
    } };
  } catch {
    return { ok: false, error: {
      code: 'SESSION_SOURCE_LOAD_FAILED',
      friendlyMessage: cursor
        ? '继续读取会话失败，可能已达到扫描上限或请求超时；可重试或按会话 ID 搜索。'
        : '加载会话失败，请稍后重试。',
      canRetry: true,
    } };
  }
}
