import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import type { Identity } from '@/services/workspace/workspaceModel';
import type { ReactNode } from 'react';

interface IdentityBarProps {
  identities: Identity[];
  activeId: string | null;
  onChange: (id: string) => void;
  onOpenPermissions?: () => void;
  userAvatarUrl?: string;
  trailing?: ReactNode;
}

/** @deprecated Workspace 身份入口已收敛到左侧栏；保留导出以兼容尚未迁移的宿主。 */
export function IdentityBar({
  identities,
  activeId,
  onChange,
  onOpenPermissions,
  userAvatarUrl,
  trailing,
}: IdentityBarProps) {
  return (
    <div className="flex items-start gap-2 border-b border-border bg-background p-2">
      <div className="min-w-0 flex-1">
        <WorkspaceIdentitySelector
          identities={identities}
          activeId={activeId}
          onChange={onChange}
          onOpenPermissions={onOpenPermissions}
          userAvatarUrl={userAvatarUrl}
        />
      </div>
      {trailing ? <div className="flex items-center">{trailing}</div> : null}
    </div>
  );
}
