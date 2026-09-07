import { getCapabilities } from '@/capabilities';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { mapIdentityViewToIdentity } from '@/hooks/workspaceIdentityMapper';
import { workspaceService } from '@/services/workspace/workspaceService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useMemo } from 'react';

function useWorkspaceIdentitySwitcherModel() {
  const identityViews = useWorkspaceStore((state) => state.identities);
  const activeIdentityId = useWorkspaceStore((state) => state.activeIdentityId);
  const { identity: humanIdentity } = useHumanIdentity();
  const userProfilePresentation = getCapabilities().getUserProfilePresentation().value;
  const identities = useMemo(() => {
    const authenticatedName = humanIdentity?.displayName.trim() || humanIdentity?.userId.trim();
    return identityViews.map((view) => {
      const identity = mapIdentityViewToIdentity(view);
      if (!userProfilePresentation.preferAuthenticatedUserProfile || identity.kind !== 'user' || !authenticatedName) {
        return identity;
      }
      return { ...identity, name: authenticatedName };
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
  };
}

/** 工作区一级导航顶部的协作身份入口。身份状态保持全局共享，不依赖对话页二级侧栏。 */
export function WorkspaceIdentitySwitcher() {
  const model = useWorkspaceIdentitySwitcherModel();

  return (
    <WorkspaceIdentitySelector
      identities={model.identities}
      activeId={model.activeIdentityId}
      onChange={model.switchIdentity}
      userAvatarUrl={model.userAvatarUrl}
      layout="sidebar"
    />
  );
}
