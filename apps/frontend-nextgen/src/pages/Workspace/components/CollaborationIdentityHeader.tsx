import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { useWorkspaceIdentitySwitcherModel } from '@/hooks/useWorkspaceIdentitySwitcherModel';

/** 协作群列表顶部的当前身份装配区；身份状态与切换均由共享模型提供。 */
export function CollaborationIdentityHeader() {
  const model = useWorkspaceIdentitySwitcherModel();

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
