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
}

export interface FriendApprovalConfig {
  mode: FriendApprovalMode;
  exemptOrganizationPaths: OrganizationPath[];
}

export interface PendingPublication {
  id: string;
  audience: PublicAudience;
  target: PublicConfig;
  submittedAt: string;
}

export interface CollaborationBot {
  id: string;
  name: string;
  engine: string;
  joinedBcn: boolean;
  collaborationStatus: CollaborationStatus;
  profilePublic: boolean;
  taskClaimingEnabled: boolean;
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
