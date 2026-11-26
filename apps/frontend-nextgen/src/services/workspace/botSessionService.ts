import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import type { BotModelBadge } from '@/capabilities';
import type { IdentityView } from '@/domain/collaboration';
import type { ConversationSessionRequestRange } from '@/domain/conversation/types';
import type { BotLifecycle } from '@/domain/botWorkshop';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import { AceLoginRedirectError, BackendRequestError } from '@/services/backendApi/httpClient';
import { extractFriendlyErrorMessage } from '@/utils/requestErrorHandler';
import {
  listBots as listOwnedBotsApi,
  type OwnedBotDto,
} from '@/services/backendApi/bots/botController';
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
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import type { ChatMessage } from '@tc-chat/core';
import type { BotIamTokenStage } from '../backendApi/privateChat/iamTokenController';
import { mapBotActionPermissions } from './botActionPermissionMapper';
import { mapBotSessionMessages } from './botSessionMessageMapper';
import type { DomainError, DomainResult } from './identityService';

export interface ChatBotView {
  /** Runtime APIs address the owning user; team chat ownerId may be a record entity. */
  engineOwnerId?: string;
  botId: string; // mine 原始 bot_id(可能复合)
  realBotId: string; // 拆分 head
  ownerId?: string; // 拆分 tail(staffNo)
  displayName: string;
  avatarUrl?: string;
  online: boolean;
  /** bots 接口返回的原始状态(PENDING/ACTIVE/FAILED 等);视图侧对非可用态整行禁用,缺省视为可用。 */
  status?: string;
  reachability?: 'reachable' | 'unreachable';
  chatable: boolean;
  engine?: string;
  actions?: string[];
  disabledActions?: Record<string, string>;
  botType?: string;
  /** 服务 Bot 是否有可用的 online 发布；旧接口缺省不触发禁用。 */
  onlineAvailable?: boolean | null;
  /** Inventory-backed lifecycle for service engine checks; request stage is not lifecycle proof. */
  serviceLifecycle?: BotLifecycle;
  isAgentCodingBot?: boolean;
  templateType?: string;
  templateName?: string;
  spaceId?: string;
  spaceName?: string;
  /** 归属空间类型（后端 space.kind）；缺失时由 spaceId 与个人空间比对兜底。 */
  spaceKind?: 'personal' | 'team';
  /** 会话运行阶段；对话页目录中的服务 Bot 使用 online，个人 Bot 使用 draft；调试页可显式选择阶段。 */
  runtimeStage?: BotIamTokenStage;
  /** 标记来自当前用户好友关系，而非当前用户管理的 Bot。 */
  isFriendBot?: boolean;
  /** 团队空间 Bot 写接口必须显式携带实体 owner，不能由登录用户推断。 */
  isTeamBot?: boolean;
  /** 目录 DTO 的 call_type 透传(历史兼容):新版 openapi_v1 目录接口族不下发该字段,
   *  恒 undefined;前端不再以此字段作任何判据,保留仅为类型兼容。 */
  callType?: string;
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
  hasMore?: boolean;
  nextCursor?: string | null;
  scanLimited?: boolean;
}

export const BOT_SESSION_PAGE_SIZE = 10;
export const BOT_MESSAGE_PAGE_SIZE = 50;

export interface BotMessagePageView {
  messages: ChatMessage[];
  total: number;
  rawCount: number;
}

export interface BotModelView {
  modelId: string;
  name: string;
  provider: string;
  /** 运营展示徽标（capability 注入，如「推荐」）；缺省不渲染。 */
  badge?: BotModelBadge;
}

export interface BotSessionScopeOptions {
  /** “对话”本人会话使用 subject scope；Bot 工坊运维链路显式关闭。 */
  subjectScoped?: boolean;
}

export interface BotSessionCreateOptions extends BotSessionScopeOptions {
  friendlyErrors?: boolean;
  /** Caller 首次分配已受理时，在同一幂等创建链路内等待运行时就绪。 */
  waitForCallerRuntime?: boolean;
  signal?: AbortSignal;
}

const COMPOUND_ID_RE = /^.+:.+$/;

export function splitBotId(botId: string): { realBotId: string; ownerId?: string } {
  const idx = botId.indexOf(':');
  if (idx < 0) return { realBotId: botId, ownerId: undefined };
  return { realBotId: botId.slice(0, idx), ownerId: botId.slice(idx + 1) };
}

/** user_id 规则:human 身份 id 含冒号取尾段,否则原值。 */
/** mine 返回的 human 身份 bot_id 形如 "human_900003",工号取 "human_" 之后的部分;
 *  兼容旧的 "{head}:{staffNo}" 复合写法(取首个冒号之后)。结果只保留工号本身。
 *  导出供 botChatProvider 等同样调用 /openapi/v1/bots/* 的地方复用,避免漏归一化。 */
export function resolveUserId(userId: string): string {
  return resolveOpenApiUserId(userId);
}

/** 连接和会话请求共用运行环境，避免 Token 与实际实例选址不一致。 */
export function resolveChatRuntimeStage(bot: ChatBotView): BotIamTokenStage {
  if (bot.botType === 'service' && (bot.isTeamBot || bot.isFriendBot)) return 'online';
  if (bot.isFriendBot || bot.botType === 'personal') return 'draft';
  return bot.runtimeStage ?? (bot.botType === 'service' ? 'online' : 'draft');
}

/** 支持“按实例取数”的 Bot：服务 Bot、非好友、非 TEClaw、非已知 Caller 模式。 */
export function supportsConversationInstanceScope(bot: ChatBotView): boolean {
  return (
    bot.botType?.toLowerCase() === 'service' &&
    !bot.isFriendBot &&
    bot.callType?.toLowerCase() !== 'caller' &&
    bot.engine?.toLowerCase() !== 'teclaw'
  );
}

/** 由已选实例构造会话请求范围（device_uuid + 对应 runtime stage）。 */
export function buildConversationSessionRequestRange(
  bot: ChatBotView,
  deviceUuid: string,
): ConversationSessionRequestRange {
  return { deviceUuid, stage: resolveChatRuntimeStage(bot) };
}

/**
 * 对话栏目本人会话的统一请求范围。
 * user_id 是经网关校验的操作者；f_user_id 是会话主体。当前产品不开放接管，
 * 声明会话主体时，两者均指向同一登录 Human。自有个人 Bot 只有自身的草稿实例，
 * 直接走受鉴权保护的管理会话接口，避免老版本将其误路由到好友会话。
 */
export function withConversationSubjectParams<T extends { user_id: string; owner_id?: string; stage?: BotIamTokenStage }>(
  bot: ChatBotView,
  userId: string,
  params: T,
): Omit<T, 'stage'> & { stage?: BotIamTokenStage; f_user_id?: string } {
  const { stage: requestedStage, ...baseParams } = params;
  const stage =
    bot.isFriendBot || bot.isTeamBot || bot.runtimeStage === 'online'
      ? resolveChatRuntimeStage(bot)
      : requestedStage ?? resolveChatRuntimeStage(bot);
  const subjectUserId = resolveUserId(userId);
  const isOwnedPersonalRuntime =
    bot.botType === 'personal' &&
    !bot.isFriendBot &&
    !bot.isTeamBot &&
    bot.callType?.toLowerCase() !== 'caller' &&
    Boolean(subjectUserId) &&
    bot.engineOwnerId === subjectUserId;
  return {
    ...baseParams,
    ...(bot.botType === 'service' ? { stage } : {}),
    ...((bot.isFriendBot || bot.isTeamBot) && bot.ownerId ? { owner_id: bot.ownerId } : {}),
    ...(!isOwnedPersonalRuntime ? { f_user_id: subjectUserId } : {}),
  };
}

/** 按产品入口选择运行时参数；关闭 subject scope 时仍保留服务 Bot 的阶段寻址。 */
export function withBotSessionScope<T extends { user_id: string; owner_id?: string; stage?: BotIamTokenStage }>(
  bot: ChatBotView,
  userId: string,
  params: T,
  subjectScoped = true,
): Omit<T, 'stage'> & { stage?: BotIamTokenStage; f_user_id?: string } {
  if (subjectScoped) return withConversationSubjectParams(bot, userId, params);
  const { stage: requestedStage, ...baseParams } = params;
  return {
    ...baseParams,
    ...(bot.botType === 'service'
      ? { stage: requestedStage ?? resolveChatRuntimeStage(bot) }
      : {}),
  };
}

const CALLER_RUNTIME_PREPARING_CODE = 409113;
const CALLER_RUNTIME_POLL_INTERVAL_MS = 3_000;
const CALLER_RUNTIME_POLL_TIMEOUT_MS = 150_000;

function isCallerRuntimePreparing(error: unknown): error is BackendRequestError {
  if (!(error instanceof BackendRequestError) || error.status !== 409) return false;
  const code = (error.data as { code?: unknown } | null)?.code;
  return Number(code) === CALLER_RUNTIME_PREPARING_CODE;
}

function waitForCallerRuntime(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout>;
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, CALLER_RUNTIME_POLL_INTERVAL_MS);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
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
        avatarUrl: resolveBotAvatarUrl(i.avatarUrl, realBotId),
        online: i.online,
        reachability: i.reachability,
        chatable: COMPOUND_ID_RE.test(i.id),
        engine: i.engine,
      };
    });
}

export interface BotSessionSummaryView {
  sessionId: string;
  title: string;
  messageCount: number;
  gmtModified: string;
  gmtCreate: string;
  model?: string;
}

/** 被锚定剥除信任的最短尾巴长度：短于它的「本会话标识」宁可漏剥也不参与判据。 */
const ANCHORED_SUFFIX_MIN_LENGTH = 8;

/**
 * 从 session id 提取其 uuid 段，解析语义对齐后端 adapter `_session_label_suffix`
 * （create 写引擎 label 的后缀正是这个裸 uuid）。已知形态：
 * - `session:<uuid>:user:<id>`（裸引擎 key）
 * - `agent:<provider>:session:<uuid>:user:<id>`（canonical 完整 AID）
 * 未知形态返回 null——宁漏剥不误剥（漏剥后续由后端出站剥除兜底）。
 */
export function extractSessionUuidFromSessionId(
  sessionId?: string | null,
): string | null {
  if (!sessionId) return null;
  const parts = sessionId.split(':');
  // session:<uuid>:user:<id>
  if (parts.length >= 4 && parts[0] === 'session' && parts[2] === 'user') {
    return parts[1] || null;
  }
  // agent:<provider>:session:<uuid>:user:<id>
  if (parts.length >= 5 && parts[0] === 'agent' && parts[2] === 'session') {
    return parts[3] || null;
  }
  return null;
}

export function mapBotSessionSummary(s: {
  session_id: string;
  title: string;
  message_count: number;
  gmt_modified: string;
  gmt_create: string;
  model?: string;
}): BotSessionSummaryView {
  // 后端 adapter 写引擎 label 时可能拼两种后缀（同一写点不同入参）：
  // - create：`标题_<本会话裸uuid>`；
  // - update：`标题_<完整 session_id>`（更新标题后尤其明显）。
  // 剥除判据全部锚定本会话自身标识（精确 endsWith），禁止无差别正则——
  // 人类标题自带的下划线尾巴（如 `技能回放-ODPS_APPROVAL-100008`）不得误剥；
  // 解析不到已知形态的 uuid 时宁可漏剥。为空时回退为「新会话」（与 open-claw 一致）。
  const rawTitle = s.title ?? '';
  const sessionIdSuffix = `_${s.session_id}`;
  let trimmedTitle =
    rawTitle && rawTitle.endsWith(sessionIdSuffix)
      ? rawTitle.slice(0, -sessionIdSuffix.length).trim()
      : rawTitle.trim();
  const sid = extractSessionUuidFromSessionId(s.session_id);
  if (
    sid &&
    sid.length >= ANCHORED_SUFFIX_MIN_LENGTH &&
    trimmedTitle &&
    trimmedTitle.endsWith(`_${sid}`)
  ) {
    trimmedTitle = trimmedTitle
      .slice(0, trimmedTitle.length - sid.length - 1)
      .trim();
  }
  return {
    sessionId: s.session_id,
    title: trimmedTitle || '新会话',
    messageCount: s.message_count,
    gmtModified: s.gmt_modified,
    gmtCreate: s.gmt_create,
    model: s.model,
  };
}

function toSessionView(
  s: Parameters<typeof mapBotSessionSummary>[0],
  botId: string,
  favorite = false,
): BotChatSessionView {
  return {
    ...mapBotSessionSummary(s),
    botId,
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

/** 归一后端 space.kind（大小写/命名差异）到前端空间类型；缺失返回 undefined 由调用侧兜底。 */
function normalizeSpaceKind(kind: string | undefined): 'personal' | 'team' | undefined {
  if (!kind) return undefined;
  return kind.toUpperCase() === 'TEAM' ? 'team' : 'personal';
}

function toOwnedBotView(dto: OwnedBotDto): ChatBotView {
  const botId = dto.bcn_bot_id || (dto.bot_id.includes(':') || !dto.owner_entity_id ? dto.bot_id : `${dto.bot_id}:${dto.owner_entity_id}`);
  const { realBotId, ownerId: legacyLocator } = splitBotId(botId);
  const ownerId = dto.entity_id || legacyLocator;
  const templateType = readTemplateType(dto);
  const templateName = readTemplateName(dto, templateType);
  const permissions = mapBotActionPermissions(dto.actions, dto.disabled_actions);
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
    engineOwnerId: dto.owner_entity_id,
    displayName: dto.bot_name || dto.bot_id,
    avatarUrl: resolveBotAvatarUrl(dto.avatar_url, dto.bot_id),
    online: dto.status === 'ACTIVE' || dto.status === 'online',
    status: dto.status,
    reachability: 'reachable',
    chatable: COMPOUND_ID_RE.test(botId),
    engine: dto.engine_type ?? dto.engine,
    ...permissions,
    botType: dto.bot_type,
    runtimeStage: dto.bot_type === 'service' ? 'online' : 'draft',
    isAgentCodingBot: runtime.isAgentCodingBot,
    templateType: runtime.templateType,
    templateName: runtime.templateName,
    // Caller 模式标记透传(openspec add-iam-caller-runtime-target);缺失保持 undefined。
    callType: dto.call_type,
    spaceId: dto.space?.space_id,
    spaceName: dto.space?.name,
    spaceKind: normalizeSpaceKind(dto.space?.kind),
  };
}

async function fetchOwnedBots(
  userId: string,
): Promise<DomainResult<{ bots: ChatBotView[]; hasAgentCodingBots: boolean }>> {
  try {
    const resp = await listOwnedBotsApi({ user_id: resolveUserId(userId), page: 1, page_size: 100, stage: 'online' });
    // 对话页的「已管理 Bot」目录保留全部可用 Bot。AgentCoding Bot 的行点击
    // 由对话页直接进入 coding-chat；普通 CC 仍由运行时分类保留，不误判。
    const owned = resp.data?.items ?? [];
    // Engine metadata is resolved only for the selected conversation.
    const items = owned.map(toOwnedBotView);
    return {
      ok: true,
      data: {
        bots: items,
        hasAgentCodingBots: items.some((bot) => bot.isAgentCodingBot),
      },
    };
  } catch (e) {
    return { ok: false, error: toDomainError(e) };
  }
}

export const botSessionService = {
  async listOwnedBots(userId: string): Promise<DomainResult<ChatBotView[]>> {
    const result = await fetchOwnedBots(userId);
    return result.ok ? { ok: true, data: result.data.bots } : result;
  },
  async listOwnedBotsWithMeta(
    userId: string,
  ): Promise<DomainResult<{ bots: ChatBotView[]; hasAgentCodingBots: boolean }>> {
    return fetchOwnedBots(userId);
  },
  async listSessionsPage(
    bot: ChatBotView,
    userId: string,
    page = 1,
    pageSize = BOT_SESSION_PAGE_SIZE,
    sessionKey = '',
    range?: ConversationSessionRequestRange,
    subjectScoped = true,
  ): Promise<DomainResult<BotSessionPageView>> {
    try {
      // session_key 原样透传(空条件省略),由 backendRequest 负责查询串编码。
      const baseParams = {
        user_id: resolveUserId(userId),
        owner_id: bot.ownerId,
        page,
        page_size: pageSize,
        ...(sessionKey ? { session_key: sessionKey } : {}),
        ...(range ? { device_uuid: range.deviceUuid, ...(range.stage ? { stage: range.stage } : {}) } : {}),
      };
      const params = subjectScoped
        ? withConversationSubjectParams(bot, userId, baseParams)
        : baseParams;
      const resp = await listBotSessions(bot.realBotId, params);
      const items = (resp.data?.items ?? []).map((s) => toSessionView(s, bot.botId));
      const total = resp.data?.total ?? items.length;
      return { ok: true, data: { items, total, hasMore: page * pageSize < total } };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async listSessions(
    bot: ChatBotView,
    userId: string,
    subjectScoped = true,
  ): Promise<DomainResult<BotChatSessionView[]>> {
    try {
      const params = withBotSessionScope(bot, userId, {
        user_id: resolveUserId(userId),
        owner_id: bot.ownerId,
        page: 1,
        page_size: 50,
      }, subjectScoped);
      const resp = await listBotSessions(bot.realBotId, params);
      const items = (resp.data?.items ?? []).map((s) => toSessionView(s, bot.botId));
      return { ok: true, data: items };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async createSession(
    bot: ChatBotView,
    userId: string,
    title: string = '新会话',
    options?: BotSessionCreateOptions,
  ): Promise<DomainResult<BotChatSessionView>> {
    try {
      const params = withBotSessionScope(bot, userId, {
        user_id: resolveUserId(userId),
        owner_id: bot.ownerId,
      }, options?.subjectScoped !== false);
      const startedAt = Date.now();
      let resp;
      while (true) {
        try {
          resp = options?.friendlyErrors || options?.signal || options?.waitForCallerRuntime
            ? await createBotSession(
                bot.realBotId,
                params,
                { title },
                options?.signal,
                options?.friendlyErrors ? '创建会话失败，请稍后重试' : undefined,
              )
            : await createBotSession(bot.realBotId, params, { title });
          break;
        } catch (error) {
          const canPoll = options?.waitForCallerRuntime === true
            && isCallerRuntimePreparing(error)
            && Date.now() - startedAt + CALLER_RUNTIME_POLL_INTERVAL_MS <= CALLER_RUNTIME_POLL_TIMEOUT_MS;
          if (!canPoll) throw error;
          if (error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
          await waitForCallerRuntime(options.signal);
        }
      }
      const s = resp.data;
      if (!s) throw new Error('创建会话失败');
      return { ok: true, data: toSessionView(s, bot.botId) };
    } catch (e) {
      if (!options?.friendlyErrors) return { ok: false, error: toDomainError(e) };
      return {
        ok: false,
        error: {
          code: 'BOT_SESSION_FAILED',
          friendlyMessage: e instanceof BackendRequestError ? e.message : extractFriendlyErrorMessage(e),
          canRetry: true,
          alreadyHandled: e instanceof AceLoginRedirectError ||
            (e instanceof BackendRequestError && Boolean(e.alreadyHandled || e.silent)),
        },
      };
    }
  },
  async getSessionDetail(
    bot: ChatBotView,
    userId: string,
    sessionId: string,
    range?: ConversationSessionRequestRange,
    options?: { silentNotFound?: boolean },
  ): Promise<DomainResult<BotChatSessionView>> {
    try {
      const params = withConversationSubjectParams(bot, userId, {
        user_id: resolveUserId(userId),
        owner_id: bot.ownerId,
        ...(range ? {
          device_uuid: range.deviceUuid,
          ...(range.stage ? { stage: range.stage } : {}),
        } : {}),
      });
      const resp = await getBotSession(bot.realBotId, sessionId, params);
      const s = resp.data;
      if (!s) throw new Error('查询会话详情失败');
      return { ok: true, data: toSessionView(s, bot.botId) };
    } catch (e) {
      // An instance lookup miss is expected; ordinary detail requests retain default notifications.
      if (options?.silentNotFound && range?.deviceUuid && e instanceof BackendRequestError && e.status === 404) {
        if (e.toastKey) useErrorNotifyStore.getState().cancel(e.toastKey);
        return { ok: false, error: { code: 'NOT_FOUND', friendlyMessage: '目标会话未找到', canRetry: true } };
      }
      return { ok: false, error: toDomainError(e) };
    }
  },
  async deleteSession(bot: ChatBotView, userId: string, sessionId: string): Promise<DomainResult<null>> {
    try {
      await deleteBotSession(
        bot.realBotId,
        sessionId,
        withConversationSubjectParams(bot, userId, {
          user_id: resolveUserId(userId),
          owner_id: bot.ownerId,
        }),
      );
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
      const params = withConversationSubjectParams(bot, userId, { user_id: resolveUserId(userId) });
      const resp = await updateBotSession(bot.realBotId, sessionId, params, { title });
      if (!resp.data) throw new Error('更新会话标题失败');
      return { ok: true, data: toSessionView(resp.data, bot.botId) };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
  async clearContext(
    bot: ChatBotView,
    userId: string,
    sessionId: string,
    subjectScoped = true,
  ): Promise<DomainResult<null>> {
    try {
      await deleteBotSessionMessages(
        bot.realBotId,
        sessionId,
        withBotSessionScope(bot, userId, { user_id: resolveUserId(userId) }, subjectScoped),
      );
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
      const params = withConversationSubjectParams(bot, userId, { user_id: resolveUserId(userId) });
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
    range?: ConversationSessionRequestRange,
    subjectScoped = true,
  ): Promise<DomainResult<BotSessionPageView>> {
    try {
      const baseParams = {
        user_id: resolveUserId(userId),
        owner_id: bot.ownerId,
        page,
        page_size: pageSize,
        ...(range ? { device_uuid: range.deviceUuid, ...(range.stage ? { stage: range.stage } : {}) } : {}),
      };
      const params = subjectScoped
        ? withConversationSubjectParams(bot, userId, baseParams)
        : baseParams;
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
  async listMessagesPage(
    bot: ChatBotView,
    userId: string,
    sessionId: string,
    page = 1,
    pageSize = BOT_MESSAGE_PAGE_SIZE,
    range?: ConversationSessionRequestRange,
    subjectScoped = true,
  ): Promise<BotMessagePageView> {
    const baseParams = {
      user_id: resolveUserId(userId),
      owner_id: bot.ownerId,
      page,
      page_size: pageSize,
      ...(range ? {
        device_uuid: range.deviceUuid,
        ...(range.stage ? { stage: range.stage } : {}),
      } : {}),
    };
    const resp = await listBotSessionMessages(
      bot.realBotId,
      sessionId,
      subjectScoped ? withConversationSubjectParams(bot, userId, baseParams) : baseParams,
    );
    const items = (resp.data?.items ?? []) as BotMessageDto[];
    return {
      messages: mapBotSessionMessages(items),
      total: resp.data?.total ?? items.length,
      rawCount: items.length,
    };
  },
  async listMessages(bot: ChatBotView, userId: string, sessionId: string): Promise<ChatMessage[]> {
    const page = await this.listMessagesPage(bot, userId, sessionId);
    return page.messages;
  },

  async listModels(
    bot: ChatBotView,
    userId: string,
    subjectScoped = true,
  ): Promise<DomainResult<BotModelView[]>> {
    try {
      const params = withBotSessionScope(bot, userId, {
        user_id: resolveUserId(userId),
        owner_id: bot.ownerId,
        page: 1,
        page_size: 50,
      }, subjectScoped);
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
    subjectScoped = true,
  ): Promise<DomainResult<BotChatSessionView>> {
    try {
      const params = withBotSessionScope(
        bot, userId, { user_id: resolveUserId(userId) }, subjectScoped,
      );
      const resp = await updateBotSession(bot.realBotId, sessionId, params, { model });
      if (!resp.data) throw new Error('更新会话模型失败');
      return { ok: true, data: toSessionView(resp.data, bot.botId) };
    } catch (e) {
      return { ok: false, error: toDomainError(e) };
    }
  },
};
