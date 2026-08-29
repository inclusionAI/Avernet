import type {
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
  loadOverview(signal?: AbortSignal): Promise<CollaborationPrivacyOverview>;
  syncDepartment(signal?: AbortSignal): Promise<CurrentUserIdentity>;
  /** 按关键词搜索部门，返回匹配的组织路径列表。keyword 必传。 */
  searchDepartments(keyword: string, signal?: AbortSignal): Promise<OrganizationSearchEntry[]>;
  updateDirectSetting(command: DirectSettingCommand, signal?: AbortSignal): Promise<DirectSettingCommand['value']>;
  submitPublication(command: PublicationCommand, signal?: AbortSignal): Promise<PublicationResult>;
  updateFriendApproval(command: FriendApprovalCommand, signal?: AbortSignal): Promise<FriendApprovalConfig>;
}
