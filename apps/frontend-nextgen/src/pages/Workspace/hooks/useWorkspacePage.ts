import type { IdentityView } from '@/domain/collaboration';
import { parseWorkspaceRoute, serializeWorkspaceRoute } from '@/domain/workspaceRoute';
import { sessionService } from '@/services/workspace/sessionService';
import { applyResolvedGroupSession, hydrateWorkspaceRoute } from '@/services/workspace/workspaceRouteService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useSearchParams } from '@umijs/max';
import { useCallback, useEffect, useMemo, useState } from 'react';

export type WorkspaceView = 'chat' | 'group';

export interface UseWorkspacePageResult {
  view: WorkspaceView;
  setView: (v: WorkspaceView) => void;
  activeIdentityId: string | null;
  identities: IdentityView[];
  /** 当前 location.search 已完成 URL → Store 回填；完成前禁止 Store 反向覆盖深链。 */
  urlHydrated: boolean;
}

/**
 * Workspace URL ↔ Store 同步：
 * - URL 是刷新、分享和浏览器导航的输入；current= 表示当前身份，bot= 表示 Human→Bot 目标，human= 表示 Bot→Human 目标；
 * - Store 是 SPA 内交互状态真源，URL hydration 完成后再投影回规范 query；
 * - 群聊旧链接中的 bot= 仍由 workspaceRoute parser 兼容为身份参数。
 */
export function useWorkspacePage(): UseWorkspacePageResult {
  const [searchParams] = useSearchParams();
  const activeIdentityId = useWorkspaceStore((state) => state.activeIdentityId);
  const identities = useWorkspaceStore((state) => state.identities);
  const selectedGroupId = useWorkspaceStore((state) => state.selectedGroupId);
  const selectedSessionId = useWorkspaceStore((state) => state.selectedSessionId);
  const membership = useWorkspaceStore((state) => state.membership);
  const view = useWorkspaceStore((state) => state.view);

  const routeKey = searchParams.toString();
  const route = useMemo(() => parseWorkspaceRoute(routeKey), [routeKey]);
  const [hydratedRouteKey, setHydratedRouteKey] = useState<string | null>(null);
  const urlHydrated = hydratedRouteKey === routeKey;

  // 每次 location.search 变化都重新回填，支持浏览器前进/后退和同路由 query 导航。
  useEffect(() => {
    let cancelled = false;
    const result = hydrateWorkspaceRoute(route);
    if (result.status === 'waiting_for_identities') return;
    if (!result.unresolvedGroupSessionId) {
      setHydratedRouteKey(routeKey);
      return;
    }

    const sessionId = result.unresolvedGroupSessionId;
    void sessionService.getSessionDetail(sessionId).then((detail) => {
      if (cancelled) return;
      if (detail.ok) applyResolvedGroupSession(detail.data.groupId, sessionId);
      setHydratedRouteKey(routeKey);
    });
    return () => {
      cancelled = true;
    };
  }, [identities, route, routeKey]);

  const setView = useCallback((next: WorkspaceView) => {
    useWorkspaceStore.getState().setView(next);
  }, []);

  // 群聊 Store → URL 投影。单聊投影由 useChatUrlSync 复用同一个 serializer。
  useEffect(() => {
    if (!urlHydrated || view !== 'group') return;
    const next = serializeWorkspaceRoute({
      view: 'group',
      currentIdentityId: activeIdentityId,
      groupId: selectedGroupId,
      sessionId: selectedSessionId,
      membership,
    });
    if (next === routeKey) return;
    history.replace(`${window.location.pathname}?${next}${window.location.hash}`);
  }, [activeIdentityId, membership, routeKey, selectedGroupId, selectedSessionId, urlHydrated, view]);

  return useMemo(
    () => ({ view, setView, activeIdentityId, identities, urlHydrated }),
    [view, setView, activeIdentityId, identities, urlHydrated],
  );
}
