import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { useWorkspaceIdentitySwitcherModel } from '@/hooks/useWorkspaceIdentitySwitcherModel';

/** 工作区一级导航顶部的协作身份入口。身份状态保持全局共享，不依赖对话页二级侧栏。 */
export function WorkspaceIdentitySwitcher({ collapsed = false }: { collapsed?: boolean }) {
  const model = useWorkspaceIdentitySwitcherModel();

  return (
    <WorkspaceIdentitySelector
      identities={model.identities}
      activeId={model.activeIdentityId}
      onChange={model.switchIdentity}
      userAvatarUrl={model.userAvatarUrl}
      layout={collapsed ? 'collapsed' : 'sidebar'}
      identityStatus={model.humanIdentityStatus}
      identityError={model.humanIdentityError}
      identityListLoading={model.identityListLoading}
    />
  );
}
