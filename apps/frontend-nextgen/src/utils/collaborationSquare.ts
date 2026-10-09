import type { SquareResource } from '@/domain/collaborationSquare/types';
import type { ConversationRouteState } from '@/domain/conversation';
import { serializeConversationRoute } from '@/domain/conversation';
import { serializeWorkspaceRoute } from '@/domain/workspaceRoute';

export function getCollaborationSquareErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

/**
 * 单 Bot 会话跳转 URL（/workspace/chat）。bot= 仅透传 Bot 目标；
 * section/origin 由对话页按目录归属解析（管理 Bot/好友 Bot 均可命中）。
 */
export function getCollaborationBotConversationUrl(botId: string, sessionId: string): string {
  const route: ConversationRouteState = { botId, sessionId };
  return `/workspace/chat?${serializeConversationRoute(route)}`;
}

/**
 * 协作群会话跳转 URL（/workspace/collaboration）。
 * - groupId 已知时带上 group= 以便协作群页直接选中该群；
 * - 仅 session=（无 group=）时协作群页会异步反查 groupId（邀请链接等场景）。
 */
export function getCollaborationGroupConversationUrl(
  groupId: string | null | undefined,
  sessionId: string,
  currentIdentityId?: string,
): string {
  return `/workspace/collaboration?${serializeWorkspaceRoute({
    currentIdentityId,
    groupId,
    sessionId,
  })}`;
}

export function getCollaborationSquareShareUrl(
  origin: string,
  resource: SquareResource,
  id: string,
  searchHint?: string,
): string {
  const pathname = resource === 'bot' ? '/collaboration-square/bots' : '/collaboration-square/groups';
  const params = new URLSearchParams({ resource, id });
  const normalizedSearchHint = searchHint?.trim();
  if (resource === 'bot' && normalizedSearchHint) params.set('name', normalizedSearchHint);
  return `${origin}${pathname}?${params.toString()}`;
}

export function clearCollaborationSquareTargetingSearch(resource: SquareResource, id: string): void {
  if (typeof window === 'undefined') return;
  const params = new URLSearchParams(window.location.search);
  if (params.get('resource') !== resource || params.get('id') !== id) return;
  params.delete('resource');
  params.delete('id');
  params.delete('name');
  const search = params.toString();
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`,
  );
}
