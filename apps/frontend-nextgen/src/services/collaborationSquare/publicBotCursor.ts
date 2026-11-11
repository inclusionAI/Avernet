// BCS 原始 offset 游标 + 可选 Backend metadata 过滤的公开 Bot 拼页服务
// （bcs-standalone-friend-discovery，无 React 依赖）。发现页“发现 → 公开 Bot”由
// CollaborationSquareApiAdapter（Task 3）读取能力后调用本服务：以 BCS 原始 offset 扫描
// 固定上限 100 的候选页，逐条推进原始位置；只有 Backend 已启用时才经共用 metadata 过滤；
// 第 pageSize+1 条合格记录（展示页 24 条时即第 25 条）仅作 hasMore 预读，下一页从它在
// BCS 的原始下标续读——预读项不丢失、展示页不重叠，不能由未过滤的 BCS total 推导 hasMore。
import { mapBcsBotCandidate, type BcsCandidatePage } from '@/domain/collaborationSquare/bcsBotMapper';
import type { BotCandidatePage, PublicBot } from '@/domain/collaborationSquare/types';
import { filterPublicBotsByMetadata, type FetchPublicBotMetadata } from './publicBotMetadata';

/** BCS 上游每批读取条数上限：与扫描页大小一致（每次上游读 ≤100），扫描总量受 BCS total 约束。 */
export const BCS_CANDIDATE_SCAN_PAGE_SIZE = 100;

/** BCS 候选上游读取：由 adapter 封装真实 Controller（listBcsBotCandidates）与信封校验。 */
export type FetchBcsCandidatePage = (
  actorId: string,
  query: { offset: number; limit: number },
  signal?: AbortSignal,
) => Promise<BcsCandidatePage>;

export interface ReadPublicBotPageInput {
  /** BCS 候选视角：human_<user-id> Actor 或当前仍有管理权的物理 Bot 完整身份。 */
  actorId: string;
  /** 登录 Human 的真实 user_id：Backend metadata 接口校验请求者（不可用所选 Bot 身份代替）。 */
  userId: string;
  /** 本次展示页从 BCS 原始偏移处开始读取（上一页返回的 nextOffset）。 */
  offset: number;
  /** 展示页大小；同时预读第 pageSize+1 条合格记录用于 hasMore 与续读游标。 */
  pageSize: number;
  /** 显式的 Backend 启用决策（部署能力）：false 时不发任何 metadata 请求。 */
  backendEnabled: boolean;
  fetchCandidates: FetchBcsCandidatePage;
  fetchMetadata: FetchPublicBotMetadata;
  signal?: AbortSignal;
  /** metadata 增强不可用（网络/5xx）时的一次性提示出口；本次读取后续批次停发 metadata 请求。 */
  onEnhancementUnavailable?: () => void;
}

function createAbortError(): Error {
  return new DOMException('The operation was aborted.', 'AbortError');
}

export async function readPublicBotPage(input: ReadPublicBotPageInput): Promise<BotCandidatePage> {
  const { actorId, userId, offset, pageSize, backendEnabled, fetchCandidates, fetchMetadata, signal } = input;
  const items: PublicBot[] = [];
  let hasMore = false;
  let nextOffset = offset;
  let scanOffset = offset;
  let metadataEnabled = backendEnabled;

  // 增强不可用只重试闸口一次：首次失败后本次读取内不再向已不可用的 metadata 服务发请求。
  const handleEnhancementUnavailable = (): void => {
    metadataEnabled = false;
    input.onEnhancementUnavailable?.();
  };

  while (true) {
    // AbortSignal 限制扫描：进入每批前检查，取消后不再读下一批。
    if (signal?.aborted) throw createAbortError();
    const batch = await fetchCandidates(actorId, { offset: scanOffset, limit: BCS_CANDIDATE_SCAN_PAGE_SIZE }, signal);
    if (!Array.isArray(batch.items) || batch.items.length === 0) break;

    // 每批先映射成 PublicBot，再交给共用 metadata 过滤；候选是否合格按「保留 Bot 引用集合」
    // 判定，原始位置以 BCS offset + 批内下标推进，不按过滤后的数组下标计算。
    // 用普通 for 循环而非循环内闭包（@typescript-eslint/no-loop-func），映射失败的候选跳过。
    const batchEntries: Array<{ bot: PublicBot; rawIndex: number }> = [];
    for (let index = 0; index < batch.items.length; index += 1) {
      const bot = mapBcsBotCandidate(batch.items[index], userId);
      if (bot) batchEntries.push({ bot, rawIndex: scanOffset + index });
    }
    const keptBots = await filterPublicBotsByMetadata({
      bots: batchEntries.map((entry) => entry.bot),
      userId,
      backendEnabled: metadataEnabled,
      fetchMetadata,
      signal,
      onEnhancementUnavailable: handleEnhancementUnavailable,
    });
    // 入列必须使用 filter 返回的对象：owner_name 等增强字段只存在于返回副本上，
    // 原始 batchEntries 仅用于定位 BCS 原始下标（id 唯一，按 id 回取增强副本）。
    const keptBotsById = new Map(keptBots.map((bot) => [bot.id, bot] as const));
    for (const entry of batchEntries) {
      const keptBot = keptBotsById.get(entry.bot.id);
      if (!keptBot) continue;
      if (items.length < pageSize) {
        items.push(keptBot);
        continue;
      }
      // 第 pageSize+1 条合格记录：hasMore 预读与续读游标的唯一依据；下一页从该记录的
      // BCS 原始位置重新读取（不做预读缓存），本页展示不含该项、不发生永久丢失。
      hasMore = true;
      nextOffset = entry.rawIndex;
      break;
    }
    if (hasMore) break;

    // 空页或服务端不前进都会在上游终止；总量由 BCS total 自然约束，不重复扫描同一 offset。
    scanOffset += batch.items.length;
    if (batch.total <= scanOffset) break;
  }

  if (!hasMore) nextOffset = scanOffset;
  return { items, nextOffset, hasMore };
}
