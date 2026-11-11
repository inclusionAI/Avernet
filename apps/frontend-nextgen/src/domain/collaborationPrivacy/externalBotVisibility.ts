/**
 * 外部 Bot 对 Bot 可见性的领域语义（bcs-frontend-external-bot-config）。
 *
 * 外部 Bot 只通过 PATCH /openapi/v1/collaboration/bots/{botId} 直改可见性，不走 publish 审批链路：
 * - public 只提交 visibility，后端可能保留陈旧的 view_scope_agent_friend_deps，展示必须忽略并按全部可见回显；
 * - protected 的部门范围是组织完整名称字符串（BCS 按精确/前缀匹配），读写都按名称原样处理，绝不用部门编码；
 * - 外部 Bot 没有审批态：展示时清空 pendingPublications，避免遗留审批字段渲染链接或拦截保存。
 */
import type { CollaborationBot, PublicConfig } from './types';

/** 外部 Bot 可见性的后端枚举（与 CollaborationBotDto.visibility 同形，供领域层独立演化）。 */
export type ExternalBotVisibility = 'public' | 'protected' | 'private';

export interface ExternalBotVisibilitySource {
  visibility?: ExternalBotVisibility;
  friend_ext?: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 读取 friend_ext.view_scope_agent_friend_deps 的部门完整名称（字符串原样；历史对象按 deptName 兼容）。 */
export function readExternalViewScopeNames(friendExt: unknown): string[] {
  if (!isRecord(friendExt) || !Array.isArray(friendExt.view_scope_agent_friend_deps)) return [];
  const names = friendExt.view_scope_agent_friend_deps
    .map((item) => {
      if (typeof item === 'string') return item.trim();
      if (isRecord(item)) return String(item.deptName ?? item.dept_name ?? item.name ?? '').trim();
      return '';
    })
    .filter(Boolean);
  return [...new Set(names)];
}

/**
 * 把外部 Bot 的 visibility + friend_ext 归一为协作权限域的对 Bot 可见性配置。
 * public 忽略陈旧部门范围（切回全部可见时后端可能保留旧 key）；protected 空范围按全部可见回显。
 */
export function normalizeExternalViewScopeConfig(
  visibility: ExternalBotVisibility | undefined,
  friendExt: unknown,
): PublicConfig {
  if (visibility === 'public') return { scope: 'all', organizationPaths: [] };
  if (visibility !== 'protected') return { scope: 'none', organizationPaths: [] };
  const names = readExternalViewScopeNames(friendExt);
  if (names.length === 0) return { scope: 'all', organizationPaths: [] };
  return { scope: 'restricted', organizationPaths: names.map((name) => [name]) };
}

/**
 * friend_ext 在后端是顶层整体替换语义。先克隆当前对象，再只替换部门范围键，
 * 避免 public_agent_approval、no_check_scope_friend_deps 等未知键被丢弃。
 */
export function buildExternalViewScopeFriendExt(currentFriendExt: unknown, names: string[]): Record<string, unknown> {
  const nextFriendExt: Record<string, unknown> = isRecord(currentFriendExt)
    ? (structuredClone(currentFriendExt) as Record<string, unknown>)
    : {};
  nextFriendExt.view_scope_agent_friend_deps = names.map((name) => name.trim()).filter(Boolean);
  return nextFriendExt;
}

/** 应用外部 Bot 语义到协作域 Bot：覆写对 Bot 可见性，并清空审批态（外部 Bot 无审批流）。 */
export function applyExternalViewScopeToBot(
  bot: CollaborationBot,
  source: ExternalBotVisibilitySource,
): CollaborationBot {
  return {
    ...bot,
    publication: {
      ...bot.publication,
      bot: normalizeExternalViewScopeConfig(source.visibility, source.friend_ext),
    },
    pendingPublications: {},
  };
}