// BCS `/bots/{bot_id}/candidates` 系列响应 → 公开 Bot 页面模型的安全映射。
// 与 `PublicBotCatalogTransport` 在 mapper.ts 的定位一致：domain 层声明自己消费的
// 传输字段（最小交集），不反向依赖 services 层 Controller；字段权威见
// apps/bcs/api-contracts/v1/domain-models.yaml（PhysicalBot / BotCandidate / BotCandidateSearchItem）。
import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import type { PublicBot } from './types';

/** Bot→Bot 协作可见性（BCS visibility）与人类侧可见性（BCS user_visibility）共用枚举。 */
export type BcsCandidateVisibility = 'public' | 'protected' | 'private';

/**
 * BCS PhysicalBot 候选投影的安全公开字段。
 * env/reachability/friend_ext 等内部字段不在消费范围，不得透出到页面模型。
 */
export interface BcsCandidateBot {
  /** BCN canonical 身份；好友申请的唯一目标（不另设 friendRequestBotId）。 */
  bot_id: string;
  name: string;
  avatar_url: string | null;
  /** 创建者 staff_no（旧数据可能缺省）；仅用于「是否本人管理」比对，不是 Owner 展示名。 */
  created_by?: string;
  descriptor: { summary: string };
  visibility: BcsCandidateVisibility;
  user_visibility: BcsCandidateVisibility;
  /** BCS 原始持久化状态（BotStatus：online|hidden）；与 Backend Catalog 生命周期 status 语义无关。 */
  status: 'online' | 'hidden';
}

/** BCS `BotCandidate`：候选项 = 物理 Bot 投影 + 当前视角已批准好友标记。 */
export interface BcsCandidateItem {
  bot: BcsCandidateBot;
  is_friend: boolean;
}

/** BCS `BotCandidatePage`：分页字段沿用 BCS 原始 offset/limit 游标语义。 */
export interface BcsCandidatePage {
  items: BcsCandidateItem[];
  total: number;
  offset: number;
  limit: number;
}

export type BcsCandidateSearchMode = 'empty_query' | 'semantic' | 'name_fallback';

/** BCS `BotCandidateSearchItem`：候选超集 + 语义推荐附加字段（tags 为内部标签，页面模型不消费）。 */
export interface BcsCandidateSearchItem extends BcsCandidateItem {
  tags?: Record<string, unknown>;
  /** 语义相关度；empty_query 与 name_fallback 结果缺省。 */
  score?: number;
  /** 语义推荐短画像；empty_query 与 name_fallback 结果缺省。 */
  short_profile?: string;
}

export interface BcsCandidateSearchResult {
  items: BcsCandidateSearchItem[];
  search_mode: BcsCandidateSearchMode;
}

/**
 * 将 BCS 候选项映射为 `PublicBot` 页面模型：
 * - 申请目标唯一来自 `bot.bot_id`（不另设 friendRequestBotId，`getPublicBotTargetId` 即命中同一身份）；
 * - `descriptor.skills` 是未公开审核的技能、`friend_ext` 是内部好友策略字段，一律不透出（capabilities 恒为空）；
 * - `status=online|hidden` 只写入独立 `bcsStatus`，不写 Backend Catalog 生命周期 `status`、不伪造 `ACTIVE`；
 * - 智能搜索项的 `score`/`short_profile`（仅 semantic 命中携带）映射为 `score`/`shortProfile`；
 * - 头像/名称/摘要缺失按既有约定回退（dicebear 头像 / 未命名 Bot / 空摘要），Owner 展示名缺失明确降级为「未公开」；
 * - `bot_id` 缺失（无法确立申请身份）时返回 null，由调用方丢弃，绝不映射为伪造身份。
 */
export function mapBcsBotCandidate(candidate: BcsCandidateSearchItem, viewerUserId?: string): PublicBot | null {
  const bot = typeof candidate === 'object' && candidate !== null ? candidate.bot : undefined;
  const botId = typeof bot?.bot_id === 'string' ? bot.bot_id.trim() : '';
  if (!bot || !botId) return null;
  const userPublic = bot.user_visibility === 'public';
  const botPublic = bot.visibility === 'public';
  // 两维独立公开设置派生分享链接受众（与 Catalog mapper 同口径，保持分享链接行为一致）。
  const audience = userPublic && !botPublic ? ('user' as const) : botPublic && !userPublic ? ('bot' as const) : undefined;
  const isOwnedByLoggedInUser = Boolean(viewerUserId?.trim() && bot.created_by?.trim() === viewerUserId.trim());
  const bcsStatus = bot.status === 'online' || bot.status === 'hidden' ? bot.status : undefined;
  const score = typeof candidate.score === 'number' ? candidate.score : undefined;
  const shortProfile = candidate.short_profile?.trim() || undefined;
  return {
    id: botId,
    name: bot.name?.trim() || '未命名 Bot',
    avatarUrl: resolveBotAvatarUrl(bot.avatar_url, botId),
    ownerName: '未公开',
    description: bot.descriptor?.summary?.trim() ?? '',
    capabilities: [],
    relationshipStatus: candidate.is_friend === true ? 'friend' : 'none',
    ...(bcsStatus ? { bcsStatus } : {}),
    ...(audience ? { audience } : {}),
    ...(score !== undefined ? { score } : {}),
    ...(shortProfile ? { shortProfile } : {}),
    ...(isOwnedByLoggedInUser ? { isOwnedByLoggedInUser: true } : {}),
  };
}
