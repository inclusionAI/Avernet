import type { IdentityView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';

export interface IdentityLoadResult {
  identities: IdentityView[];
  defaultActiveId: string | null;
}

/**
 * 将 identityService 结果统一写回 Store：保留仍在列表内的 active，列表未变化时不写新引用。
 * 该函数与 loadIdentities 解耦，供 AppShell、useHumanIdentity 和 admin 身份兜底共同使用。
 */
export function applyIdentityLoadResult(result: IdentityLoadResult): void {
  const store = useWorkspaceStore.getState();
  const activeId =
    store.activeIdentityId && result.identities.some((identity) => identity.id === store.activeIdentityId)
      ? store.activeIdentityId
      : result.defaultActiveId;
  const identityChanged =
    store.activeIdentityId !== activeId ||
    store.identities.length !== result.identities.length ||
    store.identities.some((identity, index) => identity.id !== result.identities[index]?.id);
  if (identityChanged) useWorkspaceStore.getState().setIdentities(result.identities, activeId);
}
