import { fullCollaborationUrl, validateCollaborationRoute } from './collaborationPresentation';
import { serializeWorkspaceRoute } from './workspaceRoute';

interface AcceptedInvitationTarget {
  currentIdentityId: string;
  targetType?: 'group' | 'session' | null;
  targetId?: string;
  groupId?: string;
}

/** 展示选项不参与目标、工作身份或权限解析，仅使用服务端核验后的邀请结果。 */
export function buildInvitationDestination(
  search: string,
  { currentIdentityId, targetType, targetId, groupId }: AcceptedInvitationTarget,
): string {
  if (!targetType || !targetId) return fullCollaborationUrl({});

  const route = targetType === 'group'
    ? { currentIdentityId, groupId: targetId }
    : { currentIdentityId, groupId, sessionId: targetId };
  const options = new URLSearchParams(search);
  const view = options.get('view');
  const focused = view === 'group' || (view === 'session' && targetType === 'session');
  if (!focused || validateCollaborationRoute(view, route)) return fullCollaborationUrl(route);

  const query = new URLSearchParams(serializeWorkspaceRoute(route));
  const showReturn = options.get('showReturn');
  if (showReturn === 'true' || showReturn === 'false') query.set('showReturn', showReturn);
  return `/workspace/collaboration/${view}?${query.toString()}`;
}
