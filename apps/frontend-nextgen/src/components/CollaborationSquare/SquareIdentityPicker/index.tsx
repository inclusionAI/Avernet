import { getCapabilities } from '@/capabilities';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSquareIdentity } from '@/hooks/useSquareIdentity';
import { mapIdentityViewsToDisplayIdentities } from '@/hooks/workspaceIdentityMapper';
import { useMemo } from 'react';

/**
 * 公开Bot Tab 模块级身份选择器（docs/specs/2026-09-23-discovery-tab-identity/spec.md）。
 *
 * 复用 WorkspaceIdentitySelector 的能力与入口形态（触发按钮 + Popover 身份列表），
 * 但身份状态来自协作广场模块级 store（useSquareIdentity）：
 * - 默认登录用户身份；可选 Bot 身份；刷新恢复、失效回退由 store/hook 承担；
 * - 选择用户身份项 → 回到默认（无持久化）；选择 Bot → 持久化其 id；
 * - 不写 workspaceStore.activeIdentityId，不影响左上角全局工作身份与工作区会话。
 */
export function SquareIdentityPicker() {
  const { identities, listStatus, selectedIdentity, selectIdentity, retryLoadIdentities } = useSquareIdentity();
  const { identity: humanIdentity, error: humanIdentityError } = useHumanIdentity();
  const userProfilePresentation = getCapabilities().getUserProfilePresentation().value;
  const displayIdentities = useMemo(
    () =>
      mapIdentityViewsToDisplayIdentities(
        identities,
        humanIdentity
          ? { userId: humanIdentity.userId, name: humanIdentity.displayName || humanIdentity.userId }
          : null,
        userProfilePresentation.preferAuthenticatedUserProfile,
      ),
    [
      identities,
      humanIdentity?.displayName,
      humanIdentity?.userId,
      userProfilePresentation.preferAuthenticatedUserProfile,
    ],
  );

  const handleChange = (id: string) => {
    const target = identities.find((identity) => identity.id === id);
    // 用户身份项 → 默认态（null，无持久化）；Bot 身份 → 持久化其 id。
    selectIdentity(target?.kind === 'bot' ? id : null);
  };

  return (
    // 发现页使用单行身份选择器；身份来源与切换仍由发现模块独立维护。
    <div className="w-full max-w-[280px] shrink-0">
      <WorkspaceIdentitySelector
        identities={displayIdentities}
        activeId={selectedIdentity?.id ?? null}
        onChange={handleChange}
        userAvatarUrl={humanIdentity?.avatarUrl}
        layout="inline"
        identityStatus={listStatus}
        identityError={listStatus === 'error' ? humanIdentityError : undefined}
        onRetry={() => void retryLoadIdentities()}
        // 弹层文案写模块级身份在本 Tab 的实际用途(句式与协作群对齐);默认「全局生效」
        // 与模块级行为不符;说明图标与副提示重复,一并隐藏。副提示控制在 20 字内防 320px 弹层截断。
        popoverTitle="切换协作身份"
        popoverHint="选择浏览公开Bot与发起好友申请的身份"
        hidePopoverInfo
        // 使用语义背景色，保持与发现页背景的对比。
        triggerClassName="bg-background hover:bg-accent hover:text-foreground"
      />
    </div>
  );
}
