import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { IdentityTriggerButton } from '@/components/Workspace/IdentitySelector/IdentitySelectorParts';
import { useWorkspaceIdentitySwitcherModel } from '@/hooks/useWorkspaceIdentitySwitcherModel';

/**
 * 协作群列表顶部的当前身份装配区；身份状态与切换均由共享模型提供。
 * v2 重构（dmore artboard-010 Frame 1912056221）：身份行换 sidebarChip 紧凑形态
 * （16px 头像 + 名称 + (短ID) + 用户|Bot 分段 + 设置入口，整行打开切换 Popover）；
 * readOnly（锁定范围的单群入口）复用同款 chip 行样式，仅禁用切换，
 * 与其他协作页面保持一致的当前身份展示。
 */
export function CollaborationIdentityHeader({ readOnly = false }: { readOnly?: boolean }) {
  const model = useWorkspaceIdentitySwitcherModel();

  const active = model.identities.find((identity) => identity.id === model.activeIdentityId);
  if (readOnly)
    return (
      <section aria-label="当前协作身份" className="flex h-12 shrink-0 items-center border-b border-divider px-3">
        {active ? (
          <IdentityTriggerButton
            identity={active}
            open={false}
            layout="sidebarChip"
            userAvatarUrl={model.userAvatarUrl}
            disabled
            className="disabled:cursor-default disabled:opacity-100"
          />
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