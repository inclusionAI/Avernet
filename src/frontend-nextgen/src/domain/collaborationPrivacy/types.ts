export type OrganizationPath = string[];

export interface OrganizationSearchEntry {
  deptNo: string;
  path: OrganizationPath;
}
export type PublicAudience = 'user' | 'bot';
export type PublicScope = 'none' | 'all' | 'restricted';
export type FriendApprovalMode = 'none' | 'all' | 'partial_exempt';
export type CollaborationStatus = 'online' | 'hidden' | 'offline';

export interface CurrentUserIdentity {
  displayName: string;
  employeeNumber: string;
  departmentPath: OrganizationPath;
  lastSyncedAt?: string;
}

export interface PublicConfig {
  scope: PublicScope;
  organizationPaths: OrganizationPath[];
  /** 已解析的部门编码与后端原始完整名称，用于编辑已有范围时安全写回。 */
  organizationEntries?: OrganizationSearchEntry[];
}

export interface FriendApprovalConfig {
  mode: FriendApprovalMode;
  exemptOrganizationPaths: OrganizationPath[];
  /** 后端 no_check_scope_friend_deps；页面展示路径由组织搜索结果提供。 */
  exemptDepartmentNos?: string[];
  /** 已解析的免审批部门条目，用于编辑已有范围时回显并保留部门编码。 */
  exemptOrganizationEntries?: OrganizationSearchEntry[];
}

export interface PendingPublication {
  id: string;
  audience: PublicAudience;
  target: PublicConfig;
  submittedAt: string;
  approvalUrl?: string;
}

export interface CollaborationBot {
  id: string;
  name: string;
  engine: string;
  joinedBcn: boolean;
  collaborationStatus: CollaborationStatus;
  profilePublic: boolean;
  /** BCSFuse 配置查询状态；查询失败时不把 false 误当作已关闭。 */
  profilePublicStatus?: 'ready' | 'unavailable';
  taskClaimingEnabled: boolean;
  /** 任务认领授权态；由 grant·revoke 双写流程更新；缺失=未授权(unauthorized)。 */
  taskClaimStatus?: TaskClaimStatus;
  dreamModelEnabled: boolean;
  publication: Record<PublicAudience, PublicConfig>;
  pendingPublications: Partial<Record<PublicAudience, PendingPublication>>;
  friendApproval: FriendApprovalConfig;
}

export interface CollaborationPrivacyOverview {
  currentUser: CurrentUserIdentity;
  organizationOptions: OrganizationPath[];
  bots: CollaborationBot[];
}

/**
 * 任务认领授权状态机。
 * - unauthorized：未授权（默认，或关闭后）。
 * - granting / revoking：前端 inflight 瞬时态（开关 disabled，不落库）。
 * - authorized：已授权（开关 ON，可被任务派发）。
 * - failed：上次 grant/revoke 失败可重试。
 * - forbidden：secbaas 返回 403（非 Bot owner / 非 api-key 管理员）。
 * - unsupported：Open Core 无内部 grant 注入（开关禁用+开发中）。
 *
 * 表层 grant_status 落库仅 `granted | revoked` 两态（同步链路无 pending）。
 */
export type TaskClaimStatus =
  | 'unauthorized'
  | 'granting'
  | 'revoking'
  | 'authorized'
  | 'failed'
  | 'forbidden'
  | 'unsupported';
