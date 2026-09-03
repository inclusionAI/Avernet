import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import type { IdentityView } from '@/domain/collaboration';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import { listBots as listOwnedBotsApi, type OwnedBotDto } from '@/services/backendApi/bots/botController';
import {
  createBotSession,
  deleteBotSession,
  deleteBotSessionMessages,
  favoriteBotSession,
  getBotSession,
  listBotModels,
  listBotSessionMessages,
  listBotSessions,
  listFavoriteSessions,
  unfavoriteBotSession,
  updateBotSession,
  type BotMessageDto,
  type BotModelDto,
} from '@/services/backendApi/bots/privateBotSessionController';
import type { ChatMessage } from '@tc-chat/core';
import type { BotIamTokenStage } from '../backendApi/privateChat/iamTokenController';
import { mapBotSessionMessages } from './botSessionMessageMapper';
import type { DomainError, DomainResult } from './identityService';

export interface ChatBotView {
  botId: string; // mine 原始 bot_id(可能复合)
  realBotId: string; // 拆分 head
  ownerId?: string; // 拆分 tail(staffNo)
  displayName: string;
  avatarUrl?: string;
  online: boolean;
  reachability?: 'reachable' | 'unreachable';
  chatable: boolean;
  engine?: string;
  botType?: string;
  isAgentCodingBot?: boolean;
  templateType?: string;
  templateName?: string;
  spaceId?: string;
  spaceName?: string;
  /** 当前对话所连接的运行阶段；普通工作台会话缺省使用 online。 */
  runtimeStage?: BotIamTokenStage;
}

export interface BotChatSessionView {
  sessionId: string;
  botId: string;
  title: string;
  messageCount: number;
  gmtModified: string;
  gmtCreate: string;
  model?: string;
  favorite?: boolean;
}

export interface BotSessionPageView {
  items: BotChatSessionView[];
  total: number;
}

export const BOT_SESSION_PAGE_SIZE = 10;

export interface BotModelView {
  modelId: string;
  name: string;
  provider: string;
}

const COMPOUND_ID_RE = /^.+:.+$/;

export function splitBotId(botId: string): { realBotId: string; ownerId?: string } {
  const idx = botId.indexOf(':');
  if (idx < 0) return { realBotId: botId, ownerId: undefined };
  return { realBotId: botId.slice(0, idx), ownerId: botId.slice(idx + 1) };
}

/** user_id 规则:human 身份 id 含冒号取尾段,否则原值。 */
/** mine 返回的 human 身份 bot_id 形如 "human_327325",工号取 "human_" 之后的部分;
 *  兼容旧的 "{head}:{staffNo}" 复合写法(取首个冒号之后)。结果只保留工号本身。
 *  导出供 botChatProvider 等同样调用 /openapi/v1/bots/* 的地方复用,避免漏归一化。 */
export function resolveUserId(userId: string): string {
  return resolveOpenApiUserId(userId);
}

function toDomainError(e: unknown): DomainError {
  const msg = e instanceof Error ? e.message : 'Bot 单聊请求失败';
  return { code: 'BOT_SESSION_FAILED', friendlyMessage: msg, canRetry: true };
}

export function listChatBots(identityViews: IdentityView[]): ChatBotView[] {
  return identityViews
    .filter((i) => i.kind === 'bot')
    .map((i) => {
      const { realBotId, ownerId } = splitBotId(i.id);
      return {
        botId: i.id,
        realBotId,
        ownerId,
        displayName: i.displayName,
        avatarUrl: i.avatarUrl,
        online: i.online,
        reachability: i.reachability,
        chatable: COMPOUND_ID_RE.test(i.id),
        engine: i.engine,
      };
    });
}

function toSessionView(
  s: {
    session_id: string;
    title: string;
    message_count: number;
    gmt_modified: string;
    gmt_create: string;
    model?: string;
  },
  botId: string,
  favorite = false,
): BotChatSessionView {
  // 后端返回的 title 有时拼接了 `_${session_id}` 后缀（更新标题后尤其明显），
  // 这里剥离掉该后缀，仅展示用户可见的标题；为空时回退为「新会话」（与 open-claw 一致）。
  const rawTitle = s.title ?? '';
  const sessionIdSuffix = `_${s.session_id}`;
  const trimmedTitle =
    rawTitle && rawTitle.endsWith(sessionIdSuffix)
      ? rawTitle.slice(0, -sessionIdSuffix.length).trim()
      : rawTitle.trim();
  return {
    sessionId: s.session_id,
    botId,
    title: trimmedTitle || '新会话',
    messageCount: s.message_count,
    gmtModified: s.gmt_modified,
    gmtCreate: s.gmt_create,
    model: s.model,
    ...(favorite ? { favorite: true } : {}),
  };
}

function toModelView(s: BotModelDto): BotModelView {
  return { modelId: s.model_id, name: s.name, provider: s.provider };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function readTemplateType(dto: OwnedBotDto): string | undefined {
  const engineProperties = asRecord(dto.engine_properties);
  const templateConfig = asRecord(engineProperties.template_config ?? dto.template_config);
  const value =
    dto.template_type ?? engineProperties.template_type ?? templateConfig.template_type ?? templateConfig.template_key;
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function readTemplateName(dto: OwnedBotDto, templateType?: string): string | undefined {
  const engineProperties = asRecord(dto.engine_properties);
  const templateConfig = asRecord(engineProperties.template_config ?? dto.template_config);
  const botTemplateConfig = asRecord(templateConfig.bot_template_config ?? dto.bot_template_config);
  const value = [
    (dto as OwnedBotDto & { template_name?: unknown }).template_name,
    engineProperties.template_name,
    templateConfig.template_name,
    botTemplateConfig.template_name,
  ].find((item) => typeof item === 'string' && item.trim());
  if (typeof value === 'string') return value.trim();

  const normalized = templateType?.toLowerCase().replace(/[\s_-]/g, '');
  if (normalized === 'applicationcoding') return '应用 Bot';
  if (normalized === 'personalcoding') return '个人 Coding Bot';
  return undefined;
}

function toOwnedBotView(dto: OwnedBotDto): ChatBotView {
  const botId = dto.bot_id.includes(':') || !dto.owner_entity_id ? dto.bot_id : `${dto.bot_id}:${dto.owner_entity_id}`;
  const { realBotId, ownerId } = splitBotId(botId);
  const templateType = readTemplateType(dto);
  const templateName = readTemplateName(dto, templateType);
  const runtime = resolveBotRuntime({
    engine: dto.engine_type ?? dto.engine,
    templateType,
    templateName,
    botType: dto.bot_type,
    botId,
  });
  return {
    botId,
    realBotId,
    ownerId,
    displayName: dto.bot_name || dto.bot_id,
    online: dto.status === 'ACTIVE' || dto.status === 'online',
    reachability: 'reachable',
    chatable: COMPOUND_ID_RE.test(botId),
    engine: dto.engine_type ?? dto.engine,
    botType: dto.bot_type,
    isAgentCodingBot: runtime.isAgentCodingBot,
    templateType: runtime.templateType,
    templateName: runtime.templateName,
    spaceId: dto.space_id === undefined ? undefined : String(dto.space_id),
    spaceName: dto.space_name,
  };
}

export const botSessionService = {
  async listOwnedBots(userId: string): Promise<DomainResult<ChatBotView[]>> {
    try {
      const resp = await listOwnedBotsApi({ user_id: resolveUserId(userId), page: 1, page_size: 100 });
      const items = (resp.data?.items ?? []).map(toOwnedBotView);
      return { ok: true, data: items };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async listSessionsPage(
    bot: ChatBotView,
    userId: string,
    page = 1,
    pageSize = BOT_SESSION_PAGE_SIZE,
  ): Promise<DomainResult<BotSessionPageView>> {
    try {
      const params = { user_id: resolveUserId(userId), owner_id: bot.ownerId, page, page_size: pageSize };
      const resp = await listBotSessions(bot.realBotId, params);
      const items = (resp.data?.items ?? []).map((s) => toSessionView(s, bot.botId));
      return { ok: true, data: { items, total: resp.data?.total ?? items.length } };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async listSessions(bot: ChatBotView, userId: string): Promise<DomainResult<BotChatSessionView[]>> {
    try {
      const params = { user_id: resolveUserId(userId), owner_id: bot.ownerId, page: 1, page_size: 50 };
      const resp = await listBotSessions(bot.realBotId, params);
      const items = (resp.data?.items ?? []).map((s) => toSessionView(s, bot.botId));
      return { ok: true, data: items };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async createSession(bot: ChatBotView, userId: string, title?: string): Promise<DomainResult<BotChatSessionView>> {
    try {
      const params = { user_id: resolveUserId(userId), owner_id: bot.ownerId };
      const resp = await createBotSession(bot.realBotId, params, { title });
      const s = resp.data;
      if (!s) throw new Error('创建会话失败');
      return { ok: true, data: toSessionView(s, bot.botId) };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async getSessionDetail(
    bot: ChatBotView,
    userId: string,
    sessionId: string,
  ): Promise<DomainResult<BotChatSessionView>> {
    try {
      const params = { user_id: resolveUserId(userId), owner_id: bot.ownerId };
      const resp = await getBotSession(bot.realBotId, sessionId, params);
      const s = resp.data;
      if (!s) throw new Error('查询会话详情失败');
      return { ok: true, data: toSessionView(s, bot.botId) };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async deleteSession(bot: ChatBotView, userId: string, sessionId: string): Promise<DomainResult<null>> {
    try {
      await deleteBotSession(bot.realBotId, sessionId, { user_id: resolveUserId(userId), owner_id: bot.ownerId });
      return { ok: true, data: null };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async updateSessionTitle(
    bot: ChatBotView,
    userId: string,
    sessionId: string,
    title: string,
  ): Promise<DomainResult<BotChatSessionView>> {
    try {
      const params = { user_id: resolveUserId(userId) };
      const resp = await updateBotSession(bot.realBotId, sessionId, params, { title });
      if (!resp.data) throw new Error('更新会话标题失败');
      return { ok: true, data: toSessionView(resp.data, bot.botId) };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async clearContext(bot: ChatBotView, userId: string, sessionId: string): Promise<DomainResult<null>> {
    try {
      await deleteBotSessionMessages(bot.realBotId, sessionId, { user_id: resolveUserId(userId) });
      return { ok: true, data: null };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async toggleFavorite(
    bot: ChatBotView,
    userId: string,
    sessionId: string,
    favorite: boolean,
  ): Promise<DomainResult<boolean>> {
    try {
      const params = { user_id: resolveUserId(userId) };
      const resp = favorite
        ? await favoriteBotSession(bot.realBotId, sessionId, params)
        : await unfavoriteBotSession(bot.realBotId, sessionId, params);
      return { ok: true, data: resp.data?.favorited ?? favorite };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async listFavoriteSessionsPage(
    bot: ChatBotView,
    userId: string,
    page = 1,
    pageSize = BOT_SESSION_PAGE_SIZE,
  ): Promise<DomainResult<BotSessionPageView>> {
    try {
      const params = { user_id: resolveUserId(userId), owner_id: bot.ownerId, page, page_size: pageSize };
      const resp = await listFavoriteSessions(bot.realBotId, params);
      const items = (resp.data?.items ?? []).map((s) => toSessionView(s, bot.botId, true));
      return { ok: true, data: { items, total: resp.data?.total ?? items.length } };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async listFavoriteSessions(bot: ChatBotView, userId: string): Promise<DomainResult<BotChatSessionView[]>> {
    const result = await this.listFavoriteSessionsPage(bot, userId, 1, 50);
    return result.ok ? { ok: true, data: result.data.items } : result;
  },
  async listMessages(bot: ChatBotView, userId: string, sessionId: string): Promise<ChatMessage[]> {
    const resp = await listBotSessionMessages(bot.realBotId, sessionId, {
      user_id: resolveUserId(userId),
      owner_id: bot.ownerId,
      page: 1,
      page_size: 50,
    });
    const items = (resp.data?.items ?? []) as BotMessageDto[];
    // Mapper 内部按 gmt_create 升序(旧→新)稳定排序(同时间戳保持入参页内顺序),
    // 因此这里直接透传 items,不做额外反转,避免翻转破坏同时间戳的页内升序。
    return mapBotSessionMessages(items);
  },

  async listModels(bot: ChatBotView, userId: string): Promise<DomainResult<BotModelView[]>> {
    try {
      const params = { user_id: resolveUserId(userId), owner_id: bot.ownerId, page: 1, page_size: 50 };
      const resp = await listBotModels(bot.realBotId, params);
      return { ok: true, data: (resp.data?.items ?? []).map(toModelView) };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },

  async updateSessionModel(
    bot: ChatBotView,
    userId: string,
    sessionId: string,
    model: string,
  ): Promise<DomainResult<BotChatSessionView>> {
    try {
      const params = { user_id: resolveUserId(userId) };
      const resp = await updateBotSession(bot.realBotId, sessionId, params, { model });
      if (!resp.data) throw new Error('更新会话模型失败');
      return { ok: true, data: toSessionView(resp.data, bot.botId) };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
};
