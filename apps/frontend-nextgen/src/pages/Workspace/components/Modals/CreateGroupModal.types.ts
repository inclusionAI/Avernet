import type { GroupView, IdentityView } from '@/domain/collaboration';

export interface CreateGroupModalProps {
  open: boolean;
  /** 当前对话协作身份；决定好友列表与可协作 Bot 列表的查询视角。 */
  activeIdentity?: IdentityView | null;
  authenticatedUserId?: string | null;
  authenticatedUserName?: string | null;
  onClose: () => void;
  /** 创建成功后回传群详情及初始 Manager run。 */
  onCreated: (group: GroupView) => void;
}
