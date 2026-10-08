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
    // sidebar 布局：触发按钮为紧凑单行（头像+名称+用户/BOT 徽标），限宽容器内不折行；
    // default 布局的按钮含多行详情（引擎/工号副行），窄容器下会折行散乱（页面反馈）。
    <div className="w-full max-w-[280px] shrink-0">
      <WorkspaceIdentitySelector
        identities={displayIdentities}
        activeId={selectedIdentity?.id ?? null}
        onChange={handleChange}
        userAvatarUrl={humanIdentity?.avatarUrl}
        layout="sidebar"
        identityStatus={listStatus}
        identityError={listStatus === 'error' ? humanIdentityError : undefined}
        headerLabel="为 Ta 加好友："
        headerTooltip="每个身份（用户或 Bot）都拥有各自独立的好友关系。"
        onRetry={() => void retryLoadIdentities()}
        hideBotRegistration
        // 公开Bot 页面底色为 bg-muted，默认触发按钮 bg-muted/40 会与背景融合；
        // 覆盖为白底（bg-background）+ 边框，与页面卡片（Card）同对比方式。
        triggerClassName="bg-background hover:bg-accent hover:text-foreground"
      />
    </div>
  );
}
