import type {
  CollaborationBot,
  CollaborationPrivacyOverview,
  CurrentUserIdentity,
  FriendApprovalConfig,
  OrganizationSearchEntry,
  PendingPublication,
  PublicAudience,
  PublicConfig,
} from '@/domain/collaborationPrivacy/types';

export type DirectSetting = 'collaborationStatus' | 'profilePublic' | 'taskClaimingEnabled' | 'dreamModelEnabled';
export interface DirectSettingCommand {
  botId: string;
  setting: DirectSetting;
  value: boolean | 'online' | 'hidden';
}
export interface PublicationCommand {
  botId: string;
  audience: PublicAudience;
  config: PublicConfig;
  deptEntries?: Array<{ deptNo: string; deptName: string }>;
}
export interface PublicationPendingResult {
  status: 'pending';
  publication: PendingPublication;
}
export interface PublicationCompletedResult {
  status: 'completed';
  config: PublicConfig;
}
export type PublicationResult = PublicationPendingResult | PublicationCompletedResult;
export interface FriendApprovalCommand {
  botId: string;
  config: FriendApprovalConfig;
}

export interface CollaborationPrivacyGateway {
  loadOverview(userId: string, signal?: AbortSignal): Promise<CollaborationPrivacyOverview>;
  refreshManagedBot(botId: string, signal?: AbortSignal): Promise<CollaborationBot>;
  syncDepartment(userId: string, signal?: AbortSignal): Promise<CurrentUserIdentity>;
  /** 按关键词搜索部门，返回匹配的组织路径列表。keyword 必传。 */
  searchDepartments(keyword: string, signal?: AbortSignal): Promise<OrganizationSearchEntry[]>;
  updateDirectSetting(command: DirectSettingCommand, signal?: AbortSignal): Promise<DirectSettingCommand['value']>;
  submitPublication(command: PublicationCommand, signal?: AbortSignal): Promise<PublicationResult>;
  updateFriendApproval(command: FriendApprovalCommand, signal?: AbortSignal): Promise<FriendApprovalConfig>;

  /** 开启任务认领：grant 公共 api-key 给该 Bot 后 PATCH task_claim_mode=true，双写同成同败（PATCH 失败回滚 grant）。 */
  enableTaskClaim(botId: string, signal?: AbortSignal): Promise<CollaborationBot>;
  /** 关闭任务认领：revoke 后 PATCH task_claim_mode=false，双写同成同败（PATCH 失败回滚回 grant）。 */
  disableTaskClaim(botId: string, signal?: AbortSignal): Promise<CollaborationBot>;
}
