import { serializeWorkspaceRoute, type WorkspaceRouteProjection, type WorkspaceRouteState } from './workspaceRoute';

export type CollaborationPresentation = 'full' | 'only' | 'group' | 'session';

export function getCollaborationPresentation(pathname: string): CollaborationPresentation {
  if (pathname === '/workspace/collaboration-only') return 'only';
  if (pathname === '/workspace/collaboration/group') return 'group';
  if (pathname === '/workspace/collaboration/session') return 'session';
  return 'full';
}

export function validateCollaborationRoute(
  mode: CollaborationPresentation,
  route: Pick<WorkspaceRouteState, 'groupId' | 'sessionId'>,
): string | null {
  if ((mode === 'group' || mode === 'session') && !route.groupId?.trim()) return '链接缺少必填参数 group。';
  if (mode === 'session' && !route.sessionId?.trim()) return '链接缺少必填参数 session。';
  return null;
}

export function fullCollaborationUrl(route: WorkspaceRouteProjection): string {
  const query = serializeWorkspaceRoute(route);
  return `/workspace/collaboration${query ? `?${query}` : ''}`;
}
