import type { IdentityView } from '@/domain/collaboration';
import { parseWorkspaceRoute, serializeWorkspaceRoutePreserving, type WorkspaceSessionLocationRequest } from '@/domain/workspaceRoute';
import { sessionService } from '@/services/workspace/sessionService';
import { applyResolvedGroupSession, hydrateWorkspaceRoute } from '@/services/workspace/workspaceRouteService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useSearchParams } from '@umijs/max';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export type WorkspaceView = 'chat' | 'group';

export interface UseWorkspacePageResult {
  view: WorkspaceView;
  setView: (v: WorkspaceView) => void;
  activeIdentityId: string | null;
  identities: IdentityView[];
  /** 当前 location.search 已完成 URL → Store 回填；完成前禁止 Store 反向覆盖深链。 */
  urlHydrated: boolean;
  sessionLocationRequest?: WorkspaceSessionLocationRequest;
  hydrationError?: string;
}

/**
 * 协作群页 URL ↔ Store 同步（/workspace/collaboration）：
 * - URL 是刷新、分享和浏览器导航的输入；current= 表示当前身份，group/session/membership 表示群选中和视角；
 * - Store 是 SPA 内交互状态真源，URL hydration 完成后再投影回规范 query；
 * - 旧群链接中的 bot= 仍由 workspaceRoute parser 兼容为身份参数。
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
  const appliedRouteKey = useRef<string | null>(null);
  const projectedRouteKey = useRef<string | null>(null);
  // 自身投影已经对应当前 Store，无需卸载工作区再回填（否则会重连会话历史）。
  const urlHydrated = hydratedRouteKey === routeKey || projectedRouteKey.current === routeKey;
  const requestSequence = useRef(0);
  const [sessionLocationRequest, setSessionLocationRequest] = useState<WorkspaceSessionLocationRequest>();

  const [hydrationFailure, setHydrationFailure] = useState<{ key: string; message: string }>();
  const hydrationError = !urlHydrated && hydrationFailure?.key === routeKey ? hydrationFailure.message : undefined;
  useEffect(() => {
    setHydrationFailure(undefined);
    if (urlHydrated) return;
    const timer = setTimeout(() => setHydrationFailure({ key: routeKey,
      message: '协作区初始化超时，请确认当前登录账号与链接指定身份一致，或刷新页面后重试。' }), 10_000);
    return () => clearTimeout(timer);
  }, [routeKey, urlHydrated]);

  // 外部 query 导航才产生定位意图；自身 Store → URL 投影不得清筛选/重放定位。
  useEffect(() => {
    if (appliedRouteKey.current === routeKey) return;
    if (projectedRouteKey.current === routeKey) {
      projectedRouteKey.current = null;
      appliedRouteKey.current = routeKey;
      setHydratedRouteKey(routeKey);
      return;
    }
    projectedRouteKey.current = null;
    setSessionLocationRequest(undefined);
    let cancelled = false;
    const result = hydrateWorkspaceRoute(route);
    if (result.status === 'waiting_for_identities') return;
    const identityId = useWorkspaceStore.getState().activeIdentityId;
    const finish = () => {
      const current = useWorkspaceStore.getState();
      if (route.sessionId && current.activeIdentityId === identityId && identityId
        && current.selectedGroupId && current.selectedSessionId === route.sessionId) {
        setSessionLocationRequest({ request: ++requestSequence.current, identityId,
          groupId: current.selectedGroupId, sessionId: route.sessionId });
      }
      appliedRouteKey.current = routeKey;
      setHydratedRouteKey(routeKey);
    };
    if (!result.unresolvedGroupSessionId) {
      finish();
      return;
    }

    const sessionId = result.unresolvedGroupSessionId;
    void sessionService.getSessionDetail(sessionId).then((detail) => {
      if (cancelled) return;
      if (detail.ok && useWorkspaceStore.getState().activeIdentityId === identityId) {
        applyResolvedGroupSession(detail.data.groupId, sessionId);
      }
      finish();
    });
    return () => { cancelled = true; };
  }, [identities, route, routeKey]);

  const setView = useCallback((next: WorkspaceView) => {
    useWorkspaceStore.getState().setView(next);
  }, []);

  // 独立协作群页的唯一 Store → URL 投影（不依赖旧混合页的 view 位）。
  useEffect(() => {
    if (!urlHydrated) return;
    setSessionLocationRequest((request) => request && (request.identityId !== activeIdentityId
      || request.groupId !== selectedGroupId || request.sessionId !== selectedSessionId) ? undefined : request);
    const next = serializeWorkspaceRoutePreserving(window.location.search, {
      currentIdentityId: activeIdentityId,
      groupId: selectedGroupId,
      sessionId: selectedSessionId,
      membership,
    });
    if (next === routeKey) return;
    projectedRouteKey.current = next;
    history.replace(`${window.location.pathname}?${next}${window.location.hash}`);
  }, [activeIdentityId, membership, routeKey, selectedGroupId, selectedSessionId, urlHydrated]);

  return useMemo(
    () => ({ view, setView, activeIdentityId, identities, urlHydrated, sessionLocationRequest, hydrationError }),
    [view, setView, activeIdentityId, identities, urlHydrated, sessionLocationRequest, hydrationError],
  );
}
