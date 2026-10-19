import { createEvolutionApi, requestEvolution } from '@/services/backendApi/clawevolve/controller';
import type { BackendRequestOptions } from '@/services/backendApi/httpClient';
import { useSpaceContextStore } from '@/stores/spaceContextStore';

// Space readiness and late-response isolation are application policy, not transport.
async function spaceRequest<T>(url: string, options: BackendRequestOptions = {}): Promise<T> {
  const snapshot = useSpaceContextStore.getState();
  const spaceId = snapshot.currentSpaceId;
  if (!snapshot.initialized || snapshot.loading || snapshot.error || !spaceId || !snapshot.currentSpace)
    throw new Error('当前空间尚未就绪');
  let switched = false;
  const unsubscribe = useSpaceContextStore.subscribe((current) => {
    if (current.currentSpaceId !== spaceId) switched = true;
  });
  try {
    const value = await requestEvolution<T>(url, {
      ...options,
      params: { ...options.params, space_id: String(spaceId) },
    });
    const current = useSpaceContextStore.getState();
    if (switched || current.currentSpaceId !== spaceId || !current.currentSpace)
      throw new Error(
        options.method && options.method !== 'GET'
          ? '空间已切换；原操作可能已经完成，请切回原空间确认，勿重复提交。'
          : '空间已切换，请在当前空间重新查看',
      );
    return value;
  } finally {
    unsubscribe();
  }
}

export const evolutionApi = createEvolutionApi(spaceRequest);
