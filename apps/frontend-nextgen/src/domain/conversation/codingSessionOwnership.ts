import type { ConversationOrigin } from './types';

/**
 * Coding Session 的创建者编码在 session id 中。这里保持与 AIXCoding 路由模式一致：
 * - user:{userId}:session:...:agent:...
 * - agent:...:session:...:user:{userId}
 * - agent:...:direct:{userId}
 */
export function extractCodingSessionCreatorUserId(sessionId: string): string | null {
  if (!sessionId) return null;
  const codingMatch = sessionId.match(/^user:([^:]+):/);
  if (codingMatch) return codingMatch[1];
  const legacyMatch = sessionId.match(/(?:user|direct):([^:]+)$/);
  return legacyMatch ? legacyMatch[1] : null;
}

/** 仅用于会话归属比较；数字工号忽略前导零，非数字身份按小写比较。 */
/** 与 AIXCoding 路由页一致：他人会话在列表标题前展示创建者 userId。 */
export function formatCodingOtherSessionTitle(sessionId: string, title: string): string {
  const creatorUserId = extractCodingSessionCreatorUserId(sessionId);
  return creatorUserId ? `${creatorUserId}: ${title}` : title;
}

export function normalizeCodingSessionUserId(userId?: string | number | null): string {
  const value = String(userId ?? '')
    .trim()
    .toLowerCase();
  if (!value) return '';
  return /^\d+$/.test(value) ? value.replace(/^0+/, '') || '0' : value;
}

export function isSameCodingSessionUserId(left?: string | number | null, right?: string | number | null): boolean {
  const normalizedLeft = normalizeCodingSessionUserId(left);
  const normalizedRight = normalizeCodingSessionUserId(right);
  return Boolean(normalizedLeft && normalizedRight && normalizedLeft === normalizedRight);
}

/**
 * 列表归类沿用 AIXCoding 的兼容策略：无法从旧 session id 提取创建者时归入 mine，
 * 避免历史会话升级后从用户列表中消失。
 */
export function codingSessionOriginOf(sessionId: string, currentUserId: string): ConversationOrigin {
  const creatorUserId = extractCodingSessionCreatorUserId(sessionId);
  return !creatorUserId || isSameCodingSessionUserId(creatorUserId, currentUserId) ? 'mine' : 'others';
}

/** 深链归类无法判断时返回 null，由调用方保留 URL/当前筛选。 */
export function resolveCodingSessionOrigin(
  sessionId?: string | null,
  currentUserId?: string | number | null,
): ConversationOrigin | null {
  const creatorUserId = extractCodingSessionCreatorUserId(sessionId ?? '');
  const normalizedCurrentUserId = normalizeCodingSessionUserId(currentUserId);
  if (!creatorUserId || !normalizedCurrentUserId || normalizedCurrentUserId === 'anonymous') return null;
  return isSameCodingSessionUserId(creatorUserId, currentUserId) ? 'mine' : 'others';
}
