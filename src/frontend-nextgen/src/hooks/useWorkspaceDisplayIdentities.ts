import { getCapabilities, type HumanIdentity } from '@/capabilities';
import type { IdentityView } from '@/domain/collaboration';
import { resolveAuthenticatedDisplayName } from '@/domain/userIdentity';
import { useMemo } from 'react';

export function useWorkspaceDisplayIdentities(identityViews: IdentityView[], humanIdentity: HumanIdentity | null) {
  const userProfilePresentation = getCapabilities().getUserProfilePresentation().value;
  return useMemo(() => {
    const authenticatedUser = humanIdentity
      ? { userId: humanIdentity.userId, name: humanIdentity.displayName || humanIdentity.userId }
      : null;
    return identityViews.map((view) => {
      if (!userProfilePresentation.preferAuthenticatedUserProfile || view.kind !== 'user') return view;
      const displayName = resolveAuthenticatedDisplayName(
        { id: view.id, kind: 'user', name: view.displayName },
        authenticatedUser,
      );
      return displayName && displayName !== view.displayName ? { ...view, displayName } : view;
    });
  }, [humanIdentity, identityViews, userProfilePresentation.preferAuthenticatedUserProfile]);
}
