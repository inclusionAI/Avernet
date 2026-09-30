import type { WorkspaceView } from '@/domain/collaboration/availableViews';

export interface GroupWorkspaceAreaProps {
  view: 'chat' | 'group';
  onViewChange: (v: 'chat' | 'group') => void;
  availableViews: WorkspaceView[];
  userAvatarUrl?: string;
  userIdentityId?: string | null;
  userIdentityName?: string | null;
  /** <lg 二级协作群列表抽屉开关。 */
  mobileListOpen: boolean;
  onCloseMobileList: () => void;
  /** <lg 打开二级协作群列表抽屉（聊天/协作群视图共用同一开关）。 */
  onOpenMobileList?: () => void;
}
