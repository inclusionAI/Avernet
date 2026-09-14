import type { PublicAudience, PublicScope } from '@/domain/collaborationPrivacy/types';

export const botVisibilitySection = {
  title: 'Bot 可见性',
  description:
    '分别控制其他用户和其他 Bot 能否在协作广场看到当前 Bot，并决定其是否可以发起好友申请。两个对象的可见性可单独设置。',
  disabledFriendApprovalReason: '至少开启一种 Bot 可见性后，才能修改好友审批策略',
} as const;

export const visibilityAudience: Record<
  PublicAudience,
  {
    label: string;
    description: string;
    editorTitle: string;
    showcaseDescription: string;
  }
> = {
  user: {
    label: '对用户可见性',
    description: '其他用户以个人身份，在协作广场可见当前 Bot 并申请好友。',
    editorTitle: 'Bot 可见性：对用户',
    showcaseDescription: '其他用户以个人身份在协作广场可见当前 Bot，',
  },
  bot: {
    label: '对 Bot 可见性',
    description: '以 Bot 工作身份，在协作广场可见当前 Bot 并申请好友。',
    editorTitle: 'Bot 可见性：对 Bot',
    showcaseDescription: '以 Bot 工作身份在协作广场可见当前 Bot，',
  },
} as const;

export const visibilityScopeLabels: Record<PublicScope, string> = {
  none: '不可见',
  all: '全部可见',
  restricted: '限定组织可申请',
} as const;

export const visibilityEditorDescription = {
  leading: '选择当前 Bot 在',
  trailing: '中的可见性，以及其他用户或 Bot 能否申请当前 Bot 为好友。',
} as const;

export const organizationScopeCopy = {
  editorTitle: '选择组织范围',
  searchPlaceholder: '请输入组织范围',
  searchAriaLabel: '搜索组织范围',
  notFound: '未找到匹配的组织范围',
  selectedTitle: '已选组织范围',
  emptyHelp: '可搜索组织范围，并连续添加多个范围。',
} as const;

export const botFriendApprovalSection = {
  title: 'Bot 好友审批',
  descriptionLeading: '在其他用户或其他 Bot 发起好友申请后，统一控制是否需要审批。待审批的申请可前往「',
  approvalEntryLabel: '管理后台 / 通知中心 / 待我处理',
  approvalEntryPath: '/admin?tab=work-orders',
  descriptionTrailing: '」处理。',
} as const;
