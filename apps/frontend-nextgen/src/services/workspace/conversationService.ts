// 对话工作台目录与会话 Service(设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md)。
// 组合 botSessionService / collaborationCandidateService 的既有映射,不重复协议转换。
// 由调用方(Hook)传入已鉴权的 Human ID,本 Service 不读取任何 Store / 路由。
import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import { getCapabilities } from '@/capabilities';
import { supportsBotSessionFavorites } from '@/domain/botEngine';
import { codingSessionOriginOf } from '@/domain/conversation/codingSessionOwnership';
import type {
  ConversationOrigin,
  ConversationSessionRequestRange,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import {
  BOT_SESSION_PAGE_SIZE,
  botSessionService,
  splitBotId,
  type BotSessionPageView,
  type ChatBotView,
} from './botSessionService';
import { collaborationCandidateService, type CollaborationBotView } from './collaborationCandidateService';
import type { DomainResult } from './identityService';

export interface ConversationDirectoryResult {
  /** 当前用户管理的 Bot;包含 AgentCoding Bot,行点击走专用 coding-chat 入口。 */
  managedBots: ChatBotView[];
  /** Human→Bot 好友 Bot,统一带 isFriendBot 标记。 */
  friendBots: ChatBotView[];
}

function toConversationFriendBotView(view: CollaborationBotView): ChatBotView {
  const { realBotId, ownerId } = splitBotId(view.id);
  const runtime = resolveBotRuntime({
    botId: realBotId,
    engine: view.engine,
    botType: view.botType,
    templateType: view.templateType,
    templateName: view.templateName,
  });
  return {
    botId: view.id,
    realBotId,
    ownerId,
    displayName: view.name,
    avatarUrl: view.avatarUrl,
    online: view.online,
    reachability: view.reachability,
    chatable: view.detailsResolved !== false && !(view.botType === 'service' && view.onlineAvailable === false),
    engine: view.engine,
    botType: view.botType,
    onlineAvailable: view.onlineAvailable,
    isFriendBot: true,
    isAgentCodingBot: runtime.isAgentCodingBot,
  };
}

const CODING_SESSION_SCAN_PAGE_SIZE = 100;
const CODING_SESSION_MAX_SCAN_PAGES = 20;

/**
 * Relay 的 Coding Session 列表暂不支持按创建者过滤。与 AIXCoding 路由页一致，
 * TeamClaw 从 sessionId 提取创建者并在前端分流。为了避免原始一页恰好全是另一
 * 个来源时出现假空态，这里持续扫描，直到凑满当前展示页或到达数据尾部。
 */
async function listCodingSessionsByOrigin(
  bot: ChatBotView,
  userId: string,
  scope: ConversationSessionScope,
  origin: ConversationOrigin,
  page: number,
  pageSize: number,
  sessionKey: string,
  range?: ConversationSessionRequestRange,
): Promise<DomainResult<BotSessionPageView>> {
  const matching: BotSessionPageView['items'] = [];
  const targetEnd = page * pageSize;
  const isSessionHidden = getCapabilities().getCodingBotConversationHost().value?.isSessionHidden;
  let rawPage = 1;
  let exhausted = false;

  while (matching.length < targetEnd && rawPage <= CODING_SESSION_MAX_SCAN_PAGES) {
    const rangeArgs: [] | [ConversationSessionRequestRange] = range ? [range] : [];
    const result =
      scope === 'favorite' && supportsBotSessionFavorites(bot.engine)
        ? await botSessionService.listFavoriteSessionsPage(
            bot,
            userId,
            rawPage,
            CODING_SESSION_SCAN_PAGE_SIZE,
            ...rangeArgs,
          )
        : await botSessionService.listSessionsPage(
            bot,
            userId,
            rawPage,
            CODING_SESSION_SCAN_PAGE_SIZE,
            sessionKey,
            ...rangeArgs,
          );
    if (!result.ok) return result;

    matching.push(
      ...result.data.items.filter(
        (session) => !isSessionHidden?.(session) && codingSessionOriginOf(session.sessionId, userId) === origin,
      ),
    );
    exhausted =
      result.data.items.length < CODING_SESSION_SCAN_PAGE_SIZE ||
      rawPage * CODING_SESSION_SCAN_PAGE_SIZE >= result.data.total;
    if (exhausted) break;
    rawPage += 1;
  }

  const items = matching.slice((page - 1) * pageSize, targetEnd);
  // 未扫到尾时只暴露“至少还有一页”的有界 total；真正到尾后再给精确过滤总数。
  const total = exhausted ? matching.length : Math.max(targetEnd + 1, matching.length + 1);
  const data = { items, total };
  return { ok: true, data };
}

export const conversationService = {
  /** 会话目录:管理 Bot 走自有 Bot 接口,好友 Bot 走 Friend Connections + metadata 补详情。 */
  async listDirectory(userId: string): Promise<DomainResult<ConversationDirectoryResult>> {
    const [ownedResult, friendsResult] = await Promise.all([
      botSessionService.listOwnedBotsWithMeta(userId),
      collaborationCandidateService.listFriends(userId, {
        actorType: 'human',
        offset: 0,
        limit: 100,
      }),
    ]);
    if (!ownedResult.ok) return { ok: false, error: ownedResult.error };
    // 好友 Bot 加载失败降级为空列表,管理 Bot 目录仍可用;失败提示由 Hook 层负责。
    return {
      ok: true,
      data: {
        managedBots: ownedResult.data.bots,
        friendBots: friendsResult.ok ? friendsResult.data.items.map(toConversationFriendBotView) : [],
      },
    };
  },

  /** 管理 Bot(origin=mine)会话分页;scope=favorite 走收藏会话接口(不携带 session_key)。 */
  async listManagedSessions(
    bot: ChatBotView,
    userId: string,
    scope: ConversationSessionScope,
    page: number = 1,
    pageSize: number = BOT_SESSION_PAGE_SIZE,
    sessionKey: string = '',
    range?: ConversationSessionRequestRange,
    origin: ConversationOrigin = 'mine',
  ): Promise<DomainResult<BotSessionPageView>> {
    if (bot.isAgentCodingBot) {
      return listCodingSessionsByOrigin(bot, userId, scope, origin, page, pageSize, sessionKey, range);
    }
    const rangeArgs: [] | [ConversationSessionRequestRange] = range ? [range] : [];
    if (scope === 'favorite' && supportsBotSessionFavorites(bot.engine)) {
      return botSessionService.listFavoriteSessionsPage(bot, userId, page, pageSize, ...rangeArgs);
    }
    const result = await botSessionService.listSessionsPage(bot, userId, page, pageSize, sessionKey, ...rangeArgs);
    return result;
  },

  /** Human→Bot 好友 Bot 会话分页:鉴权 Human 既作 user_id 也作 f_user_id;
   *  scope=favorite 走收藏接口,supportsBotSessionFavorites 保留引擎能力边界(TEClaw 回退普通列表)。 */
  async listFriendBotSessions(
    bot: ChatBotView,
    userId: string,
    page: number = 1,
    pageSize: number = BOT_SESSION_PAGE_SIZE,
    scope: ConversationSessionScope = 'all',
    sessionKey: string = '',
    range?: ConversationSessionRequestRange,
  ): Promise<DomainResult<BotSessionPageView>> {
    const friendBot = { ...bot, isFriendBot: true };
    const rangeArgs: [] | [ConversationSessionRequestRange] = range ? [range] : [];
    if (scope === 'favorite' && supportsBotSessionFavorites(bot.engine)) {
      return botSessionService.listFavoriteSessionsPage(friendBot, userId, page, pageSize, ...rangeArgs);
    }
    const result = await botSessionService.listSessionsPage(
      friendBot,
      userId,
      page,
      pageSize,
      sessionKey,
      ...rangeArgs,
    );
    return result;
  },
};
