import { validateCollaborationRoute } from '@/domain/collaborationPresentation';
import { parseWorkspaceRoute } from '@/domain/workspaceRoute';
import { collaborationScopeService, type CollaborationScope } from '@/services/workspace/collaborationScopeService';
import { workspaceService } from '@/services/workspace/workspaceService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useMemo, useState, type MutableRefObject } from 'react';

/** 受限入口先验证身份和资源，再挂载聊天；旧 URL/身份的异步响应不可激活 Store。 */
export function useCollaborationScope(
  mode: 'group' | 'session',
  query: string,
  projectedQuery?: MutableRefObject<string | null>,
) {
  const identities = useWorkspaceStore((s) => s.identities);
  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const route = useMemo(() => parseWorkspaceRoute(query), [query]);
  const identityId = route.currentIdentityId ?? route.legacyGroupIdentityId ?? activeIdentityId;
  const key = JSON.stringify([mode, query, identityId]);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; scope: CollaborationScope | null; error: string | null }>({
    key: '',
    scope: null,
    error: null,
  });
  const ownProjection =
    mode === 'group' &&
    projectedQuery?.current === query.replace(/^\?/, '') &&
    state.scope?.group.groupId === route.groupId &&
    state.scope?.identityId === identityId;
  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    if (mode === 'group' && projectedQuery?.current === query.replace(/^\?/, '')) {
      if (projectedQuery) projectedQuery.current = null;
      setState((current) => ({ ...current, key }));
      return;
    }
    let cancelled = false;
    const fail = (error: string) => {
      if (!cancelled) setState({ key, scope: null, error });
    };
    setState({ key, scope: null, error: null });
    const validation = validateCollaborationRoute(mode, route);
    if (validation) {
      fail(validation);
      return;
    }
    if (!identities.length) {
      void workspaceService
        .initWorkspace()
        .then((result) => {
          if (!result.ok) fail(result.error.friendlyMessage);
          else if (!useWorkspaceStore.getState().identities.length) fail('暂无可用工作身份，请重试。');
        })
        .catch(() => fail('加载工作身份失败，请重试。'));
      return () => {
        cancelled = true;
      };
    }
    if (!identityId || !identities.some((identity) => identity.id === identityId)) {
      fail('指定工作身份不存在或不可用，请返回完整协作区选择身份。');
      return;
    }
    void collaborationScopeService
      .load(route.groupId!, identityId, route.sessionId)
      .then((result) => {
        if (cancelled) return;
        if (!result.ok) {
          fail(result.error.friendlyMessage);
          return;
        }
        const scope = { ...result.data, identityId };
        collaborationScopeService.activate(scope);
        setState({ key, scope, error: null });
      })
      .catch(() => fail('加载指定协作区失败，请重试。'));
    return () => {
      cancelled = true;
    };
  }, [attempt, identities, identityId, key, mode, route, query, projectedQuery]);

  const current = state.key === key || ownProjection;
  const identityChanged = current && state.scope && state.scope.identityId !== activeIdentityId;
  return {
    scope: current && !identityChanged ? state.scope : null,
    error: identityChanged ? '当前工作身份已变化，请重试或返回完整协作区。' : current ? state.error : null,
    retry,
  };
}
