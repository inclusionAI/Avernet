import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { IdentityAvatar, IdentityDetails } from '@/components/Workspace/IdentitySelector/IdentitySelectorParts';
import { useWorkspaceIdentitySwitcherModel } from '@/hooks/useWorkspaceIdentitySwitcherModel';

/**
 * 协作群列表顶部的当前身份装配区；身份状态与切换均由共享模型提供。
 * v2 重构（dmore artboard-010 Frame 1912056221）：身份行换 sidebarChip 紧凑形态
 * （16px 头像 + 名称 + (短ID) + 用户|Bot 分段 + 设置入口，整行打开切换 Popover），
 * readOnly 单群视图保持只读卡片不变。
 */
export function CollaborationIdentityHeader({ readOnly = false }: { readOnly?: boolean }) {
  const model = useWorkspaceIdentitySwitcherModel();

  const active = model.identities.find((identity) => identity.id === model.activeIdentityId);
  if (readOnly)
    return (
      <section aria-label="当前协作身份" className="space-y-2 border-b border-divider bg-background px-3 py-4">
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
    <section aria-label="当前协作身份" className="flex h-12 shrink-0 items-center border-b border-divider px-3">
      <WorkspaceIdentitySelector
        identities={model.identities}
        activeId={model.activeIdentityId}
        onChange={model.switchIdentity}
        userAvatarUrl={model.userAvatarUrl}
        layout="sidebarChip"
        identityStatus={model.humanIdentityStatus}
        identityError={model.humanIdentityError}
        identityListLoading={model.identityListLoading}
        popoverTitle="切换协作身份"
        popoverHint="选择参与协作群会话的身份"
        hidePopoverInfo
      />
    </section>
  );
}