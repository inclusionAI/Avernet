import type { BotRelationshipStatus, PublicBot } from './types';
import { getPublicBotTargetId } from './types';

/**
 * 好友关系回写：applying 时写入/更新后端新建的 pending 申请单号（未透传时保留旧值），
 * 其他状态清除已失效的旧单号，避免「重新申请」后续取消到过期申请单。
 */
export function applyBotRelationshipUpdate(
  bot: PublicBot,
  targetId: string,
  status: BotRelationshipStatus,
  pendingRequestId?: string,
): PublicBot {
  if (getPublicBotTargetId(bot) !== targetId) return bot;
  if (status === 'applying') {
    return {
      ...bot,
      relationshipStatus: status,
      ...(pendingRequestId ? { pendingFriendRequestId: pendingRequestId } : {}),
    };
  }
  return { ...bot, relationshipStatus: status, pendingFriendRequestId: undefined };
}
