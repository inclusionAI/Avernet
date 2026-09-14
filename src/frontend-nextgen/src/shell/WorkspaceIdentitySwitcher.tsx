import { getCapabilities } from '@/capabilities';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { resolveAuthenticatedDisplayName } from '@/domain/userIdentity';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { mapIdentityViewToIdentity } from '@/hooks/workspaceIdentityMapper';
import { workspaceService } from '@/services/workspace/workspaceService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useMemo } from 'react';

function useWorkspaceIdentitySwitcherModel() {
  const identityViews = useWorkspaceStore((state) => state.identities);
  const activeIdentityId = useWorkspaceStore((state) => state.activeIdentityId);
  const { identity: humanIdentity, status: humanIdentityStatus, error: humanIdentityError } = useHumanIdentity();
  const userProfilePresentation = getCapabilities().getUserProfilePresentation().value;
  const identities = useMemo(() => {
    const authenticatedUser = humanIdentity
      ? { userId: humanIdentity.userId, name: humanIdentity.displayName || humanIdentity.userId }
      : null;
    return identityViews.map((view) => {
      const identity = mapIdentityViewToIdentity(view);
      if (!userProfilePresentation.preferAuthenticatedUserProfile || identity.kind !== 'user') return identity;
      const name = resolveAuthenticatedDisplayName(
        { id: identity.id, kind: 'user', name: identity.name },
        authenticatedUser,
      );
      return name && name !== identity.name ? { ...identity, name } : identity;
    });
  }, [
    humanIdentity?.displayName,
    humanIdentity?.userId,
    identityViews,
    userProfilePresentation.preferAuthenticatedUserProfile,
  ]);
  const switchIdentity = useCallback((identityId: string) => workspaceService.switchIdentity(identityId), []);

  return {
    identities,
    activeIdentityId,
    switchIdentity,
    userAvatarUrl: humanIdentity?.avatarUrl,
    humanIdentityStatus,
    humanIdentityError,
  };
}

/** 工作区一级导航顶部的协作身份入口。身份状态保持全局共享，不依赖对话页二级侧栏。 */
export function WorkspaceIdentitySwitcher() {
  const model = useWorkspaceIdentitySwitcherModel();
  const identityListLoading = useWorkspaceStore((state) => state.isIdentityListLoading);

  return (
    <WorkspaceIdentitySelector
      identities={model.identities}
      activeId={model.activeIdentityId}
      onChange={model.switchIdentity}
      userAvatarUrl={model.userAvatarUrl}
      layout="sidebar"
      identityStatus={model.humanIdentityStatus}
      identityError={model.humanIdentityError}
      identityListLoading={identityListLoading}
    />
  );
}
