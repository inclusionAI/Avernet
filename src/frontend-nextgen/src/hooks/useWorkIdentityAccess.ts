import type { IdentityView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useMemo } from 'react';

export interface WorkIdentityAccess {
  activeIdentity: IdentityView | null;
  activeIdentityKind: 'user' | 'bot' | null;
  canViewPublicGroups: boolean;
}

export function getWorkIdentityAccess(activeIdentity: IdentityView | null): WorkIdentityAccess {
  const activeIdentityKind = activeIdentity?.kind ?? null;
  const isBot = activeIdentityKind === 'bot';
  return {
    activeIdentity,
    activeIdentityKind,
    canViewPublicGroups: !isBot,
  };
}

export function getWorkIdentityRedirect(pathname: string, access: WorkIdentityAccess): string | null {
  if (
    !access.canViewPublicGroups &&
    (pathname === '/collaboration-square/groups' || pathname.startsWith('/collaboration-square/groups/'))
  ) {
    return '/collaboration-square/bots';
  }
  return null;
}

export function useWorkIdentityAccess(): WorkIdentityAccess {
  const activeIdentity = useWorkspaceStore((state) =>
    state.activeIdentityId ? state.identities.find((identity) => identity.id === state.activeIdentityId) ?? null : null,
  );

  return useMemo(() => getWorkIdentityAccess(activeIdentity), [activeIdentity]);
}
