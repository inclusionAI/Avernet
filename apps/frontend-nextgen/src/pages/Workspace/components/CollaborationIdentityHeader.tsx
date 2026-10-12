import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { IdentityAvatar, IdentityDetails } from '@/components/Workspace/IdentitySelector/IdentitySelectorParts';
import { useWorkspaceIdentitySwitcherModel } from '@/hooks/useWorkspaceIdentitySwitcherModel';

/** 协作群列表顶部的当前身份装配区；身份状态与切换均由共享模型提供。 */
export function CollaborationIdentityHeader({ readOnly = false }: { readOnly?: boolean }) {
  const model = useWorkspaceIdentitySwitcherModel();

  const active = model.identities.find((identity) => identity.id === model.activeIdentityId);
  if (readOnly)
    return (
      <section aria-label="当前协作身份" className="space-y-2 border-b border-border bg-background px-3 py-4">
        <p className="m-0 text-xs font-semibold">当前协作身份</p>
        {active ? (
          <div className="flex items-center gap-2 rounded-lg border border-border px-4 py-2">
            <IdentityAvatar identity={active} size="xs" userAvatarUrl={model.userAvatarUrl} />
            <IdentityDetails identity={active} compact userIdLabel="用户 ID" />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">暂无可用工作身份</p>
        )}
      </section>
    );
  return (
    <section aria-label="当前协作身份" className="border-b border-border bg-background px-3 py-4">
      <WorkspaceIdentitySelector
        identities={model.identities}
        activeId={model.activeIdentityId}
        onChange={model.switchIdentity}
        userAvatarUrl={model.userAvatarUrl}
        layout="collaboration"
        identityStatus={model.humanIdentityStatus}
        identityError={model.humanIdentityError}
        identityListLoading={model.identityListLoading}
        triggerClassName="bg-background hover:bg-accent hover:text-foreground"
      />
    </section>
  );
}
