import type { SquareResource } from '@/domain/collaborationSquare/types';

export function getCollaborationSquareErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

export function getCollaborationBotConversationUrl(botId: string, sessionId: string): string {
  return `/workspace?tab=chat&bot=${encodeURIComponent(botId)}&session=${encodeURIComponent(sessionId)}`;
}

/**
 * 协作群会话跳转 URL：走协作群视图（tab=group）。
 * - groupId 已知时带上 group= 以便 workspace 直接选中该群；
 * - 仅 session=（无 group=）时 workspace 会异步反查 groupId（邀请链接等场景）。
 */
export function getCollaborationGroupConversationUrl(groupId: string | null | undefined, sessionId: string): string {
  const params = new URLSearchParams({ tab: 'group', session: sessionId });
  if (groupId) params.set('group', groupId);
  return `/workspace?${params.toString()}`;
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
