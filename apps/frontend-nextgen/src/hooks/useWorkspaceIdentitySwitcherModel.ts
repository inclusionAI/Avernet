import { getCapabilities } from '@/capabilities';
import { useHumanIdentity, type HumanIdentityStatus } from '@/hooks/useHumanIdentity';
import { mapIdentityViewsToDisplayIdentities } from '@/hooks/workspaceIdentityMapper';
import type { Identity } from '@/services/workspace/workspaceModel';
import { workspaceService } from '@/services/workspace/workspaceService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useMemo } from 'react';

export interface WorkspaceIdentitySwitcherModel {
  identities: Identity[];
  activeIdentityId: string | null;
  switchIdentity: (identityId: string) => void;
  userAvatarUrl?: string;
  humanIdentityStatus: HumanIdentityStatus;
  humanIdentityError?: string;
  identityListLoading: boolean;
}

export function useWorkspaceIdentitySwitcherModel(): WorkspaceIdentitySwitcherModel {
  const identityViews = useWorkspaceStore((state) => state.identities);
  const activeIdentityId = useWorkspaceStore((state) => state.activeIdentityId);
  const identityListLoading = useWorkspaceStore((state) => state.isIdentityListLoading);
  const { identity: humanIdentity, status: humanIdentityStatus, error: humanIdentityError } = useHumanIdentity();
  const preferAuthenticatedUserProfile =
    getCapabilities().getUserProfilePresentation().value.preferAuthenticatedUserProfile;
  const identities = useMemo(() => {
    const authenticatedUser = humanIdentity
      ? { userId: humanIdentity.userId, name: humanIdentity.displayName || humanIdentity.userId }
      : null;
    return mapIdentityViewsToDisplayIdentities(identityViews, authenticatedUser, preferAuthenticatedUserProfile);
  }, [humanIdentity?.displayName, humanIdentity?.userId, identityViews, preferAuthenticatedUserProfile]);
  const switchIdentity = useCallback((identityId: string) => workspaceService.switchIdentity(identityId), []);

  return {
    identities,
    activeIdentityId,
    switchIdentity,
    userAvatarUrl: humanIdentity?.avatarUrl,
    humanIdentityStatus,
    humanIdentityError,
    identityListLoading,
  };
}
