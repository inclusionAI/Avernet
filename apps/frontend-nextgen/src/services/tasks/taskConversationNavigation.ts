import { parseConversationRoute } from '@/domain/conversation';
import { parseWorkspaceRoute } from '@/domain/workspaceRoute';
import {
  useTaskConversationNavigationStore,
  type TaskConversationNavigation,
  type TaskConversationOrigin,
  type TaskConversationPathname,
} from '@/stores/taskConversationNavigationStore';
import type { TaskNavTarget } from '@/stores/taskSidePanelStore';

const CHAT_PATHNAME: TaskConversationPathname = '/workspace/chat';
const GROUP_PATHNAME: TaskConversationPathname = '/workspace/collaboration';

function currentLocation(): Pick<Location, 'pathname' | 'search' | 'hash'> | null {
  return typeof window === 'undefined' ? null : window.location;
}

export function getCurrentRelativeUrl(): string {
  const location = currentLocation();
  return location ? `${location.pathname}${location.search}${location.hash}` : '';
}

export function resolveCurrentTaskConversationOrigin(): TaskConversationOrigin | null {
  const location = currentLocation();
  if (!location) return null;
  if (location.pathname === CHAT_PATHNAME) {
    const route = parseConversationRoute(location.search);
    if (!route.sessionId) return null;
    return {
      routeUrl: getCurrentRelativeUrl(),
      sessionId: route.sessionId,
      kind: route.section === 'friend' ? 'friend' : 'bot',
      botId: route.botId ?? null,
    };
  }
  if (location.pathname === GROUP_PATHNAME) {
    const route = parseWorkspaceRoute(location.search);
    if (!route.sessionId) return null;
    return {
      routeUrl: getCurrentRelativeUrl(),
      sessionId: route.sessionId,
      kind: 'group',
      groupId: route.groupId ?? null,
    };
  }
  return null;
}

export function isCurrentTaskConversationOrigin(origin: TaskConversationOrigin): boolean {
  const current = resolveCurrentTaskConversationOrigin();
  if (!current || current.sessionId !== origin.sessionId) return false;
  return (current.kind === 'group') === (origin.kind === 'group');
}

export function resolveTaskNavigationPathname(target: TaskNavTarget): TaskConversationPathname {
  if (target.routeUrl) {
    const parsed = new URL(target.routeUrl, 'http://localhost');
    if (parsed.pathname === GROUP_PATHNAME) return GROUP_PATHNAME;
    return CHAT_PATHNAME;
  }
  return target.kind === 'group' || target.sessionId.startsWith('bcs_grp_') ? GROUP_PATHNAME : CHAT_PATHNAME;
}

export function isTaskNavigationAtCurrentRoute(navigation: TaskConversationNavigation): boolean {
  if (!navigation.routeCommitted) return false;
  const origin = resolveCurrentTaskConversationOrigin();
  return origin?.sessionId === navigation.sessionId && window.location.pathname === navigation.pathname;
}

export function markTaskNavigationRouteHydrated(
  sessionId: string | null,
  pathname: TaskConversationPathname,
): void {
  if (!sessionId) return;
  const navigation = useTaskConversationNavigationStore.getState().navigation;
  if (
    navigation &&
    navigation.routeCommitted &&
    navigation.sessionId === sessionId &&
    navigation.pathname === pathname &&
    isTaskNavigationAtCurrentRoute(navigation)
  ) {
    useTaskConversationNavigationStore.getState().markRouteHydrated(navigation.id);
  }
}

export function isTaskNavigationHydratedAtCurrentRoute(navigation: TaskConversationNavigation): boolean {
  return navigation.routeHydrated && isTaskNavigationAtCurrentRoute(navigation);
}

export function shouldPreserveTaskPanelForSession(
  sessionId: string | null,
  pathname: TaskConversationPathname,
): boolean {
  if (!sessionId) return false;
  const navigation = useTaskConversationNavigationStore.getState().navigation;
  if (!navigation || navigation.sessionId !== sessionId || navigation.pathname !== pathname) return false;
  // Destination hydration may render before the URL/session effects settle. During that handoff window,
  // the navigation lease itself is enough to keep the already-mounted TaskPanel open.
  return !navigation.routeHydrated || isTaskNavigationAtCurrentRoute(navigation);
}

/**
 * Keep the persistent TaskPanel alive for the complete task-driven route handoff.
 *
 * Before destination hydration, source cleanup and destination mount may run while window.location still
 * points at either route, so preservation must not depend on the target URL or a serialized tab snapshot.
 * After hydration, only the actual target route keeps the lease; an ordinary later navigation releases it.
 */
export function shouldPreserveTaskPanelDuringNavigation(): boolean {
  const navigation = useTaskConversationNavigationStore.getState().navigation;
  if (!navigation) return false;
  return !navigation.routeHydrated || isTaskNavigationAtCurrentRoute(navigation);
}
