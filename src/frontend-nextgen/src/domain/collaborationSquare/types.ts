export type BotRelationshipStatus = 'none' | 'applying' | 'friend';
export type SquareResource = 'bot' | 'group';
export type BotSearchMode = 'name' | 'smart';
export type MemberListVisibility = 'visible' | 'count_only';
export type PublicMemberType = 'human' | 'bot';
export type CollaborationSquareErrorCode =
  | 'unauthenticated'
  | 'forbidden'
  | 'protocol_error'
  | 'network'
  | 'scope_not_matched'
  | 'target_invalid'
  | 'duplicate_action'
  | 'unsupported'
  | 'unknown';

export interface PublicBotSearchQuery {
  search?: string;
  page?: number;
  pageSize?: number;
}

export interface PublicBotDiscoveryQuery {
  keyword: string;
  topK?: number;
  minScore?: number;
  runtimeState?: 'online';
}

export interface PublicGroupSearchQuery {
  search?: string;
  offset?: number;
  limit?: number;
}

export interface PublicBot {
  id: string;
  name: string;
  ownerName: string;
  description: string;
  capabilities: string[];
  relationshipStatus: BotRelationshipStatus;
}

export interface PublicBotProfile extends Omit<PublicBot, 'relationshipStatus' | 'capabilities'> {
  engine?: string;
  capabilities: Array<{ id: string; name: string }>;
}

export interface PublicGroup {
  id: string;
  name: string;
  ownerBotName: string;
  ownerUserName: string;
  typeLabel: string;
  memberCount: number;
  goal: string;
  memberListVisibility: MemberListVisibility;
  canCreateSession: boolean;
}

export interface PublicGroupMember {
  id: string;
  displayName: string;
  type: PublicMemberType;
  role: string;
}

export interface HumanBotActionContext {
  /** Collaboration actor path parameter, for example human_327325. */
  actorId: string;
  /** Normalized OpenAPI user_id, for example 327325. */
  userId: string;
}

export interface FriendRequestResult {
  status: BotRelationshipStatus;
}

export interface OpenBotConversationResult {
  sessionId: string;
}

export interface CreateSessionResult {
  sessionId: string;
  memberSource: 'session_temp';
  defaultRole: string;
}

export interface SquareDeepLink {
  resource: SquareResource;
  id: string;
}
