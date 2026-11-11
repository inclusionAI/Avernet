// BCS 公开 Bot 候选与可选 Backend metadata 的共用关联与过滤（bcs-standalone-friend-discovery）。
// 普通候选列表、智能语义结果与智能名称回退共用本过滤：Backend 已启用时以登录 Human 的真实
// user_id 分批（每批 ≤100 组精确 Bot/Owner 身份对）查询 metadata/queries，只有「查到 Backend
// 记录且 bot_type=service、online_available !== true」的候选才被排除；未匹配记录的 BCS 原生
// Bot 保留（不得把无元数据当作 unavailable），非 service 的 online_available:null 不参与过滤。
// 认证错误（401/403）与 AbortError 原样上抛；metadata 服务网络/5xx 不可用保留 BCS 列表并经
// onEnhancementUnavailable 提示增强不可用——不得把 Backend 403 当作「未部署」。
import type { PublicBot } from '@/domain/collaborationSquare/types';
import type { BotMetadataDto, BotMetadataQueryItem } from '@/services/backendApi/bots/botController';
import { BackendRequestError, type AceLoginRedirectError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { isAceLoginRedirectError } from './collaborationSquareApiHelpers';

/** metadata/queries 每批身份对上限（page_size 由 adapter 封装时覆盖整批）。 */
export const BOT_METADATA_QUERY_CHUNK_SIZE = 100;

/**
 * metadata 读取回调：由 adapter 封装真实 Controller（listBotMetadata）与信封检查，
 * 不在本模块散落传输协议细节。
 */
export type FetchPublicBotMetadata = (
  userId: string,
  pairs: BotMetadataQueryItem[],
  signal?: AbortSignal,
) => Promise<BotMetadataDto[]>;

export interface FilterPublicBotsByMetadataInput<T extends PublicBot> {
  /** 本批候选映射后的完整对象：顺序与泛型附带字段（score/shortProfile/bcsStatus 等）原样保留。 */
  bots: T[];
  /** 登录 Human 的真实 user_id；metadata 接口校验请求者身份，不可用所选 Bot 身份代替。 */
  userId: string;
  /** 显式的 Backend 启用决策（部署能力）；false 时不发任何请求。 */
  backendEnabled: boolean;
  fetchMetadata: FetchPublicBotMetadata;
  signal?: AbortSignal;
  /** 增强（metadata 关联）不可用时的一次性提示出口；由调用方接到用户提示层。 */
  onEnhancementUnavailable?: () => void;
}

function isAbortError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: unknown }).name === 'AbortError';
}

/**
 * 认证/授权错误沿既有错误策略上抛（映射由上层 adapter 完成），不降级、不吞错误。
 * 除 BackendRequestError 401/403 外，oauth-provider 策略下 httpClient 对 401 统一抛
 * AceLoginRedirectError（登录跳转信号已在协议层登记）——同样按鉴权失败原样上抛，
 * 不得落入「增强不可用」分支（会话过期必须走既有鉴权路径）。
 */
function isAuthFailure(error: unknown): error is BackendRequestError | AceLoginRedirectError {
  if (error instanceof BackendRequestError && (error.status === 401 || error.status === 403)) return true;
  // 复用 adapter helpers 的鸭子类型兜底检测（moduleNameMapper 稳健性），不另写第三份判定。
  return isAceLoginRedirectError(error);
}

/** 拆复合引用（`<bot_id>:<owner_id>`）为 metadata 身份对；裸 UUID/残缺引用不可解析，保留不查询。 */
function toBotMetadataPair(botId: string): BotMetadataQueryItem | null {
  const separator = botId.indexOf(':');
  if (separator <= 0) return null;
  const bot_id = botId.slice(0, separator).trim();
  const owner_id = botId.slice(separator + 1).trim();
  return bot_id && owner_id ? { bot_id, owner_id } : null;
}

/** 身份对的规范化顺序键（Bot/Owner 重组），与响应复合引用同构。 */
function botOwnerKey(bot_id: string, owner_id: string): string {
  return `${bot_id}:${owner_id}`;
}

/** 只有查到 Backend 记录且为 service Bot、无可用 online 发布才排除；online_available 表示发布可用性，不代表实时健康或权限。 */
function isServiceUnavailableRecord(record: BotMetadataDto): boolean {
  return record.bot_type === 'service' && record.online_available !== true;
}

export async function filterPublicBotsByMetadata<T extends PublicBot>(
  input: FilterPublicBotsByMetadataInput<T>,
): Promise<T[]> {
  const { bots, userId, backendEnabled, fetchMetadata, signal } = input;
  if (!backendEnabled || bots.length === 0) return bots;

  // 只有可解析 Bot/Owner 身份对的候选参与查询；裸 UUID 视为 Backend 未收录，直接保留。
  const pairByKey = new Map<string, BotMetadataQueryItem>();
  for (const bot of bots) {
    const pair = toBotMetadataPair(bot.id);
    if (pair) pairByKey.set(botOwnerKey(pair.bot_id, pair.owner_id), pair);
  }
  if (pairByKey.size === 0) return bots;

  // 逐批 ≤100 组精确身份对；响应按稳定引用（bcn_bot_id，缺失时回退 bot_id:owner_id 复合引用）
  // 匹配，不按响应数组顺序拼接。匹配到记录的候选回填 owner_name 到卡片 ownerName，
  // 未匹配记录或 owner_name 空白的候选保持「未公开」。
  const unavailableReferences = new Set<string>();
  const ownerNameByReference = new Map<string, string>();
  try {
    const pairs = [...pairByKey.values()];
    for (let start = 0; start < pairs.length; start += BOT_METADATA_QUERY_CHUNK_SIZE) {
      const chunk = pairs.slice(start, start + BOT_METADATA_QUERY_CHUNK_SIZE);
      const records = await fetchMetadata(userId, chunk, signal);
      for (const record of Array.isArray(records) ? records : []) {
        const bcnBotId = record.bcn_bot_id?.trim();
        const botId = record.bot_id?.trim();
        const ownerId = record.owner_id?.trim();
        const reference = bcnBotId || (botId && ownerId ? botOwnerKey(botId, ownerId) : botId) || '';
        if (!reference) continue;
        const ownerName = record.owner_name?.trim();
        if (ownerName) ownerNameByReference.set(reference, ownerName);
        if (isServiceUnavailableRecord(record)) unavailableReferences.add(reference);
      }
    }
  } catch (error) {
    if (isAbortError(error) || isAuthFailure(error)) throw error;
    // 已配置的 metadata 服务不可用（网络/5xx 等）：保留 BCS 列表（全量候选不过滤），
    // 经回调提示增强不可用；本次调用内不再继续后续批次。取消 transport 层已入队的
    // 默认失败提示（fallbackBotProfile 同法），避免在降级提示之上再叠加一条接口失败 toast。
    if (error instanceof BackendRequestError && error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
    input.onEnhancementUnavailable?.();
    return bots;
  }

  // 候选以其完整对象参与筛选：保留原顺序与输入引用，不改写 bcsStatus/推荐分数/短画像等字段。
  return bots
    .filter((bot) => {
      const pair = toBotMetadataPair(bot.id);
      if (pair && unavailableReferences.has(botOwnerKey(pair.bot_id, pair.owner_id))) return false;
      return !unavailableReferences.has(bot.id.trim());
    })
    .map((bot) => {
      const pair = toBotMetadataPair(bot.id);
      const ownerName =
        (pair ? ownerNameByReference.get(botOwnerKey(pair.bot_id, pair.owner_id)) : undefined) ??
        ownerNameByReference.get(bot.id.trim());
      // 仅命中非空 owner_name 时生成新对象；其余候选保留原引用与「未公开」默认值。
      return ownerName ? { ...bot, ownerName } : bot;
    });
}
