import type { WorkspaceView } from '@/domain/collaboration/availableViews';

export type WorkspaceRouteMembership = 'direct' | 'session_only';

export interface WorkspaceRouteState {
  explicit: boolean;
  view?: WorkspaceView;
  currentIdentityId?: string;
  /** current= 上线前，群链接曾复用 bot= 表示当前身份。 */
  legacyGroupIdentityId?: string;
  /** 仅 Human→Bot 单聊使用；群聊中的旧 bot= 不会映射到该字段。 */
  targetBotId?: string;
  /** 仅 Bot→Human 只读单聊使用；统一保存不带 human_ 前缀的用户 ID。 */
  targetHumanId?: string;
  groupId?: string;
  sessionId?: string;
  membership?: WorkspaceRouteMembership;
}

export interface WorkspaceRouteProjection {
  view: WorkspaceView;
  currentIdentityId?: string | null;
  targetBotId?: string | null;
  targetHumanId?: string | null;
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
  const tab = params.get('tab');
  const currentIdentityId = nonEmpty(params.get('current'));
  const botParam = nonEmpty(params.get('bot'));
  const humanParam = nonEmpty(params.get('human'));
  const groupId = nonEmpty(params.get('group'));
  const sessionId = nonEmpty(params.get('session'));
  const membershipParam = params.get('membership');
  const membership = membershipParam === 'direct' || membershipParam === 'session_only' ? membershipParam : undefined;
  const explicit = ['tab', 'current', 'bot', 'human', 'group', 'session', 'membership'].some((key) => params.has(key));

  let view: WorkspaceView | undefined;
  if (tab === 'chat' || tab === 'group') view = tab;
  else if (groupId) view = 'group';
  else if (botParam || humanParam) view = 'chat';
  else if (sessionId) view = 'group';

  const isGroupRoute = view === 'group';
  return {
    explicit,
    view,
    currentIdentityId,
    legacyGroupIdentityId: isGroupRoute && !currentIdentityId ? botParam : undefined,
    targetBotId: view === 'chat' ? botParam : undefined,
    targetHumanId: view === 'chat' ? humanParam : undefined,
    groupId,
    sessionId,
    membership,
  };
}

export function serializeWorkspaceRoute(route: WorkspaceRouteProjection): string {
  const params = new URLSearchParams();
  params.set('tab', route.view);
  if (route.currentIdentityId) params.set('current', route.currentIdentityId);
  if (route.view === 'chat') {
    if (route.targetBotId) params.set('bot', route.targetBotId);
    else if (route.targetHumanId) params.set('human', route.targetHumanId);
    if ((route.targetBotId || route.targetHumanId) && route.sessionId) params.set('session', route.sessionId);
  } else {
    if (route.groupId) params.set('group', route.groupId);
    if (route.sessionId) params.set('session', route.sessionId);
    if (route.membership) params.set('membership', route.membership);
  }
  return params.toString();
}
