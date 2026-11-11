import type { GroupKind, GroupView, IdentityView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';

export interface UseGroupWorkspaceResult {
  identities: IdentityView[];
  activeIdentityId: string | null;
  activeIdentity: IdentityView | null;
  groups: GroupView[];
  isLoadingGroups: boolean;
  groupsError: string | null;
  selectedGroupId: string | null;
  selectedGroup: GroupView | null;
  groupSearchText: string;
  setGroupSearchText: (v: string) => void;
  kindFilter: 'all' | GroupKind;
  setKindFilter: (k: 'all' | GroupKind) => void;
  sortMode: 'lastActivity' | 'createdAt';
  setSortMode: (m: 'lastActivity' | 'createdAt') => void;
  membership: 'direct' | 'session_only';
  setMembership: (m: 'direct' | 'session_only') => void;
  expandedGroupIds: Record<string, true>;
  toggleGroupExpanded: (groupId: string) => void;
  onSelectGroup: (groupId: string) => void;
  refreshGroups: () => Promise<void>;
  retryGroups: () => Promise<void>;
  /** 重拉群详情并写回本地 groups。管理面板打开/更新后就地刷新；可传 groupId 直接刷新指定群
   *  （默认刷新当前选中群）。群详情含 participants/owner/driver，仅查看/编辑时按需调用。 */
  reloadSelectedGroup: (groupId?: string) => Promise<void>;
  dissolveGroup: (groupId: string) => Promise<void>;
  /** Service 政策结果：当前选中群 + 当前身份是否有管理权限（policy 计算归 Hook，组件只消费）。 */
  canManageGroup: PolicyResult;
  /** Service 政策结果：当前选中群 + 当前身份是否可解散（policy 计算归 Hook，组件只消费）。 */
  canDissolveGroup: PolicyResult;
}
