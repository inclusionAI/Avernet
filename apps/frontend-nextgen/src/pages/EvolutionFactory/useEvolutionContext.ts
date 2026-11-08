import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { initSpaceContext, useSpaceContext } from '@/hooks/useSpaceContext';
import { evolutionApi } from '@/services/evolution/evolutionService';
import { useEffect } from 'react';

/** Module-local readiness only; never changes the shared request or space stores. */
export function useEvolutionContext() {
  const identity = useBotWorkshopRequestIdentity();
  const state = useSpaceContext((s) => s);
  useEffect(() => {
    if (identity.ready) void initSpaceContext();
  }, [identity.ready]);
  return {
    api: evolutionApi,
    ...identity,
    spaceId: state.currentSpaceId,
    key: `${identity.userId ?? ''}:${state.currentSpaceId ?? ''}`,
    ready: identity.ready && state.initialized && !state.loading && !state.error && !!state.currentSpace,
    loading: identity.loading || (identity.ready && (state.loading || !state.initialized)),
    error: identity.error || state.error || (state.initialized && !state.currentSpace ? '请先选择可访问的空间' : ''),
  };
}
