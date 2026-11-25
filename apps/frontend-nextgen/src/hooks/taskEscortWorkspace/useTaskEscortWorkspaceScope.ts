import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { identityService } from '@/services/workspace/identityService';
import { applyIdentityLoadResult } from '@/services/workspace/identityStore';
import { useCallback, useEffect, useRef, useState } from 'react';

/** Request identity stays in the existing adapter; scope isolates the workspace's UI memory. */
export function useTaskEscortWorkspaceScope() {
  const identity = useBotWorkshopRequestIdentity();
  const spaceId = useSpaceContext((state) => state.currentSpaceId);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string>();
  const mounted = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const retry = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRetrying(true);
    setRetryError(undefined);
    void identityService
      .loadIdentities()
      .then((result) => {
        if (!mounted.current) return;
        if (result.ok) applyIdentityLoadResult(result.data);
        else setRetryError(result.error.friendlyMessage);
      })
      .catch(() => {
        if (mounted.current) setRetryError('当前用户身份加载失败，请稍后重试');
      })
      .finally(() => {
        inFlight.current = false;
        if (mounted.current) setRetrying(false);
      });
  }, []);
  return {
    ...identity,
    ready: identity.ready && !retrying,
    loading: identity.loading || retrying,
    error: identity.ready ? undefined : retryError ?? identity.error,
    key: `${identity.userId ?? 'unresolved'}:${spaceId ?? 'default'}`,
    retry,
  };
}
