import type { CollaborationScope } from '@/services/workspace/collaborationScopeService';
// Task 8 拆页后本区域只服务协作群页(/workspace/collaboration):
// 跨模块视图切换收口到 App Shell 一级导航,view/视图切换 props 已删除。
export interface GroupWorkspaceAreaProps {
  scope?: CollaborationScope;
  sessionOnly?: boolean;
  userAvatarUrl?: string;
  userIdentityId?: string | null;
  userIdentityName?: string | null;
  /** <lg 二级协作群列表抽屉开关。 */
  mobileListOpen: boolean;
  onCloseMobileList: () => void;
  /** <lg 打开二级协作群列表抽屉。 */
  onOpenMobileList?: () => void;
}
