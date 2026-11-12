// BCS v1 公开 Bot 候选列表与智能搜索的传输 Controller。
// 契约权威：apps/bcs/api-contracts/v1/openapi/bots.yaml（list_bot_candidates / search_bot_candidates，
// 后者经 internal.yaml 挂载到 /api/v1/collaboration/bots/{bot_id}/candidates/search），信封结构见
// 同目录 domain-models.yaml（BotCandidatePageEnvelope / BotCandidateSearchEnvelope，成功 code 恒为 20000）。
//
// 请求统一走 backendRequest：名称不代表目标服务，仅指复用其同源 Cookie（credentials 'include'）、
// 登录跳转与取消语义（401→统一登录处置、AbortSignal 原样透传），只把目标 URL 换成 BCS 同源路径。
// 响应在传输边界严格校验：code===20000、data.items 数组、total/offset/limit 数值、候选项结构，
// 401 / 错误信封 / 无效结构一律上抛，绝不伪装成空列表。
import type {
  BcsCandidateItem,
  BcsCandidatePage,
  BcsCandidateSearchItem,
  BcsCandidateSearchMode,
  BcsCandidateSearchResult,
} from '@/domain/collaborationSquare/bcsBotMapper';
import { normalizeOpenApiUserId } from '@/domain/userIdentity';
import { BackendRequestError, backendRequest } from '@/services/backendApi/httpClient';

/** BCS 契约的唯一成功信封码（domain-models.yaml BotCandidatePageEnvelope code const 20000）。 */
const BCS_SUCCESS_CODE = 20000;

/** 公开 v1 候选列表（GET）。actorId 为当前用户的 human_<user-id> Actor 或其此刻仍有管理权的物理 Bot。 */
export const BCS_CANDIDATES_PATH = (actorId: string) =>
  `/openapi/v1/collaboration/bots/${encodeURIComponent(actorId)}/candidates`;

/** 内部 v1 智能候选搜索（GET），经 /api/v1/collaboration/** 同源反代；与普通列表共用同一视角授权。 */
export const BCS_CANDIDATE_SEARCH_PATH = (actorId: string) =>
  `/api/v1/collaboration/bots/${encodeURIComponent(actorId)}/candidates/search`;

/**
 * Human 查询路径的视角 actor：从 `humanBotContext.userId` 规范生成 canonical `human_<user-id>`
 * （与 BCS `bots/mine` human 项的 bot_id 同构）。经 `normalizeOpenApiUserId` 复用既有 user_id
 * 规范化：剥 `human_` 前缀保证幂等；`me` 展示占位身份映射为空并直接拒绝（其绝不能作为真实
 * OpenAPI user_id）。Bot 路径不经过本函数，直接使用所选 `viewerActorId`（bots/mine 提供的
 * 完整身份）；权限仍由 BCS 校验。
 */
export function resolveHumanBcsActorId(userId: string): string {
  const normalized = normalizeOpenApiUserId(userId);
  if (!normalized) throw new Error('无法从空 userId 生成 BCS Human Actor');
  return `human_${normalized}`;
}

/** 校验通过的 BCS 列表信封。data 为结构校验后的 `BotCandidatePage`。 */
export interface BcsCandidatePageEnvelope {
  code: 20000;
  message: string;
  data: BcsCandidatePage;
  request_id?: string;
}

/** 校验通过的 BCS 搜索信封。data 为结构校验后的 `BotCandidateSearchResult`。 */
export interface BcsCandidateSearchEnvelope {
  code: 20000;
  message: string;
  data: BcsCandidateSearchResult;
  request_id?: string;
}

/** 普通列表查询参数：只带 purpose=discovery 与名称/分页，不注入 user_id（视角授权由 BCS 校验）。 */
export interface BcsListCandidateQuery {
  /** 名称子串过滤；空白视同缺省不上送（服务端统一 trim、大小写不敏感匹配）。 */
  name?: string;
  offset?: number;
  limit?: number;
}

/** 智能搜索查询参数。 */
export interface BcsSearchCandidateQuery {
  /** 搜索关键词；空白按契约 empty_query 语义本地短路，不发请求。 */
  q: string;
  /** 契约默认 discovery；collaboration 保留给既有调用方语义。 */
  purpose?: 'discovery' | 'collaboration';
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function readEnvelopeMessage(envelope: Record<string, unknown> | null): string {
  return typeof envelope?.message === 'string' && envelope.message.trim() ? envelope.message : '';
}

function readEnvelopeRequestId(envelope: Record<string, unknown>): string | undefined {
  return typeof envelope.request_id === 'string' ? envelope.request_id : undefined;
}

/** 视角 actor 校验：空白时直接拒绝（fail fast），不发无效请求。 */
function requireCandidateActorId(actorId: string): string {
  const normalized = actorId.trim();
  if (!normalized) {
    throw new Error('BCS 候选查询视角为空：需提供 human_<user-id> Actor 或所选 Bot 完整身份');
  }
  return normalized;
}

function isIntegerAtLeast(value: unknown, minimum: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= minimum;
}

function isCandidateVisibility(value: unknown): value is 'public' | 'protected' | 'private' {
  return value === 'public' || value === 'protected' || value === 'private';
}

/**
 * 候选项结构校验（PhysicalBot 投影的消费字段 + is_friend）。
 * 契约必填字段缺失或类型不符即整体拒绝，绝不把畸形项映射为伪造身份。
 */
function isBcsCandidateItem(value: unknown): value is BcsCandidateItem {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  const bot = asRecord(candidate.bot);
  if (!bot) return false;
  const { bot_id, name, avatar_url, created_by, descriptor, visibility, user_visibility, status } = bot;
  const descriptorRecord = asRecord(descriptor);
  return (
    typeof bot_id === 'string' &&
    bot_id.trim() !== '' &&
    typeof name === 'string' &&
    (avatar_url === null || typeof avatar_url === 'string') &&
    (created_by === undefined || typeof created_by === 'string') &&
    descriptorRecord !== null &&
    typeof descriptorRecord.summary === 'string' &&
    isCandidateVisibility(visibility) &&
    isCandidateVisibility(user_visibility) &&
    (status === 'online' || status === 'hidden') &&
    typeof candidate.is_friend === 'boolean'
  );
}

function isBcsCandidateSearchItem(value: unknown): value is BcsCandidateSearchItem {
  if (!isBcsCandidateItem(value)) return false;
  const { tags, score, short_profile } = value as unknown as Record<string, unknown>;
  return (
    (tags === undefined || (typeof tags === 'object' && tags !== null)) &&
    (score === undefined || typeof score === 'number') &&
    (short_profile === undefined || typeof short_profile === 'string')
  );
}

function isBcsCandidateSearchMode(value: unknown): value is BcsCandidateSearchMode {
  return value === 'empty_query' || value === 'semantic' || value === 'name_fallback';
}

/** 列表信封解析：非 20000 / data 结构异常 / 候选项畸形均抛错，不返回部分成功。 */
function parseBcsCandidatePageEnvelope(response: unknown, path: string): BcsCandidatePageEnvelope {
  const envelope = asRecord(response);
  if (!envelope || envelope.code !== BCS_SUCCESS_CODE) {
    throw new BackendRequestError(readEnvelopeMessage(envelope) || 'BCS 候选列表响应不是成功信封', {
      apiPath: path,
      data: response,
    });
  }
  const data = asRecord(envelope.data);
  const { items, total, offset, limit } = data ?? {};
  if (
    !data ||
    !Array.isArray(items) ||
    !isIntegerAtLeast(total, 0) ||
    !isIntegerAtLeast(offset, 0) ||
    !isIntegerAtLeast(limit, 1) ||
    !items.every(isBcsCandidateItem)
  ) {
    throw new BackendRequestError('BCS 候选列表响应结构无效', { apiPath: path, data: response });
  }
  return {
    code: BCS_SUCCESS_CODE,
    message: readEnvelopeMessage(envelope),
    data: { items: items as BcsCandidateItem[], total, offset, limit },
    request_id: readEnvelopeRequestId(envelope),
  };
}

/** 搜索信封解析：search_mode / 候选项结构校验同列表口径。 */
function parseBcsCandidateSearchEnvelope(response: unknown, path: string): BcsCandidateSearchEnvelope {
  const envelope = asRecord(response);
  if (!envelope || envelope.code !== BCS_SUCCESS_CODE) {
    throw new BackendRequestError(readEnvelopeMessage(envelope) || 'BCS 候选搜索响应不是成功信封', {
      apiPath: path,
      data: response,
    });
  }
  const data = asRecord(envelope.data);
  const { items, search_mode: searchMode } = data ?? {};
  if (
    !data ||
    !Array.isArray(items) ||
    !isBcsCandidateSearchMode(searchMode) ||
    !items.every(isBcsCandidateSearchItem)
  ) {
    throw new BackendRequestError('BCS 候选搜索响应结构无效', { apiPath: path, data: response });
  }
  return {
    code: BCS_SUCCESS_CODE,
    message: readEnvelopeMessage(envelope),
    data: { items: items as BcsCandidateSearchItem[], search_mode: searchMode },
    request_id: readEnvelopeRequestId(envelope),
  };
}

/**
 * 普通候选列表：purpose 固定 discovery，只带 name/offset/limit，
 * 不注入 user_id（injectUserId: false），AbortSignal 原样透传。
 */
export async function listBcsBotCandidates(
  actorId: string,
  query: BcsListCandidateQuery = {},
  signal?: AbortSignal,
): Promise<BcsCandidatePageEnvelope> {
  const path = BCS_CANDIDATES_PATH(requireCandidateActorId(actorId));
  const response = await backendRequest<unknown>(path, {
    method: 'GET',
    params: {
      purpose: 'discovery',
      name: query.name?.trim() || undefined,
      offset: query.offset,
      limit: query.limit,
    },
    injectUserId: false,
    signal,
  });
  return parseBcsCandidatePageEnvelope(response, path);
}

/**
 * 智能候选搜索：空关键词按契约 empty_query 语义本地短路（等价于服务端收到空/空白 q 的行为，
 * 不发请求）；其余走内部 v1 搜索路径，semantic 与 name_fallback 的 `search_mode`、
 * `score`、`short_profile` 由响应原样保留。
 */
export async function searchBcsCandidates(
  actorId: string,
  query: BcsSearchCandidateQuery,
  signal?: AbortSignal,
): Promise<BcsCandidateSearchEnvelope> {
  const actor = requireCandidateActorId(actorId);
  const keyword = query.q.trim();
  if (!keyword) {
    return {
      code: BCS_SUCCESS_CODE,
      message: '',
      data: { items: [], search_mode: 'empty_query' },
    };
  }
  const path = BCS_CANDIDATE_SEARCH_PATH(actor);
  const response = await backendRequest<unknown>(path, {
    method: 'GET',
    params: { q: keyword, purpose: query.purpose ?? 'discovery' },
    injectUserId: false,
    signal,
  });
  return parseBcsCandidateSearchEnvelope(response, path);
}
