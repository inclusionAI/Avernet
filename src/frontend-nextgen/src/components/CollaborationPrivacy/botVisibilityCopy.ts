import type { PublicAudience, PublicScope } from '@/domain/collaborationPrivacy/types';

export const botVisibilitySection = {
  title: 'Bot 可见性',
  description: '控制其他用户和 Bot 是否可发现并申请当前 Bot 为好友。',
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

export const visibilityEditorDescription =
  '选择当前 Bot 在协作广场中的可见性，以及其他用户或 Bot 能否申请当前 Bot 为好友。';

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
  description: '统一控制其他用户和其他 Bot 申请添加当前 Bot 为好友时的审批方式。',
  policyDescription:
    '统一控制其他用户和其他 Bot 申请添加当前 Bot 为好友时的审批方式。审批入口见「工单中心 - 待我处理」，也可通过顶栏铃铛「通知中心」查看。',
} as const;
