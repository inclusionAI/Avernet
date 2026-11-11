// 协作群路由(/workspace/collaboration)URL 契约的纯函数 parse/serialize:
//   current={identityId}&group={groupId}&session={sessionId}&membership={direct|session_only}
// current 可按现有工作身份语义省略;旧群链接中的 bot= 仅作一次性 legacy 身份输入,
// 不再序列化输出。对话页 URL 契约见 src/domain/conversation/route.ts。
// 不依赖 React / Store / Router。
export type WorkspaceRouteMembership = 'direct' | 'session_only';

export interface WorkspaceRouteState {
  explicit: boolean;
  currentIdentityId?: string;
  /** current= 上线前，群链接曾复用 bot= 表示当前身份。 */
  legacyGroupIdentityId?: string;
  groupId?: string;
  sessionId?: string;
  membership?: WorkspaceRouteMembership;
}

export interface WorkspaceRouteProjection {
  currentIdentityId?: string | null;
  groupId?: string | null;
  sessionId?: string | null;
  membership?: WorkspaceRouteMembership;
}

function nonEmpty(value: string | null): string | undefined {
  const normalized = value?.trim();
  return normalized || undefined;
}

export function parseWorkspaceRoute(input: URLSearchParams | string): WorkspaceRouteState {
  const params = typeof input === 'string' ? new URLSearchParams(input) : input;
  const currentIdentityId = nonEmpty(params.get('current'));
  const botParam = nonEmpty(params.get('bot'));
  const groupId = nonEmpty(params.get('group'));
  const sessionId = nonEmpty(params.get('session'));
  const membershipParam = params.get('membership');
  const membership = membershipParam === 'direct' || membershipParam === 'session_only' ? membershipParam : undefined;
  const explicit = ['current', 'bot', 'group', 'session', 'membership'].some((key) => params.has(key));

  return {
    explicit,
    currentIdentityId,
    legacyGroupIdentityId: currentIdentityId ? undefined : botParam,
    groupId,
    sessionId,
    membership,
  };
}

export function serializeWorkspaceRoute(route: WorkspaceRouteProjection): string {
  const params = new URLSearchParams();
  if (route.currentIdentityId) params.set('current', route.currentIdentityId);
  if (route.groupId) params.set('group', route.groupId);
  if (route.sessionId) params.set('session', route.sessionId);
  if (route.membership) params.set('membership', route.membership);
  return params.toString();
}
