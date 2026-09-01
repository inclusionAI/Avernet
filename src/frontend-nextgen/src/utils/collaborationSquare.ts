import type { SquareResource } from '@/domain/collaborationSquare/types';

export function getCollaborationSquareErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

export function getCollaborationBotConversationUrl(botId: string, sessionId: string): string {
  return `/workspace?tab=chat&bot=${encodeURIComponent(botId)}&session=${encodeURIComponent(sessionId)}`;
}

export function clearCollaborationSquareTargetingSearch(resource: SquareResource, id: string): void {
  if (typeof window === 'undefined') return;
  const params = new URLSearchParams(window.location.search);
  if (params.get('resource') !== resource || params.get('id') !== id) return;
  params.delete('resource');
  params.delete('id');
  const search = params.toString();
  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`,
  );
}
