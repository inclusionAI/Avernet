import type { ReactNode } from 'react';
import type { UseGroupWorkspaceResult } from '../hooks/useGroupWorkspace.types';
import { ScopedWorkspaceUnavailable } from './ScopedWorkspaceUnavailable';

type WorkspaceSelection = Pick<UseGroupWorkspaceResult,
  'selectedGroupId' | 'selectedGroup' | 'isLoadingSelectedGroup' | 'selectedGroupError' |
  'isLoadingGroups' | 'groupsError' | 'retrySelectedGroup' | 'retryGroups'>;

/** Keep the sidebar usable while a deep-link target loads, fails, or is retried. */
export function GroupWorkspaceConversation({ workspace, children }: {
  workspace: WorkspaceSelection;
  children: ReactNode;
}) {
  const unavailable = workspace.selectedGroupId && !workspace.selectedGroup;
  return (
    <div className="relative isolate flex min-h-0 min-w-0 flex-1" data-workspace-conversation="true">
      {unavailable ? (
        <ScopedWorkspaceUnavailable
          loading={workspace.isLoadingSelectedGroup || workspace.isLoadingGroups}
          error={workspace.selectedGroupError ?? workspace.groupsError}
          onRetry={() => {
            workspace.retrySelectedGroup();
            void workspace.retryGroups();
          }}
        />
      ) : children}
    </div>
  );
}
