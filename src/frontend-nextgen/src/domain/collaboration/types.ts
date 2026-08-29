export type IdentityStatus = 'online' | 'hidden';
export type IdentityReachability = 'reachable' | 'unreachable';
export type IdentityView = {
  id: string;
  kind: 'user' | 'bot';
  displayName: string;
  avatarUrl?: string;
  online: boolean;
  /** bot 可聊天状态：online→可聊天，hidden→不可聊天。human 项无此字段。 */
  status?: IdentityStatus;
  /** bot 可达性：reachable→绿点，unreachable→红点。human 项无此字段。 */
  reachability?: IdentityReachability;
};
export type GroupKind = 'free_chat' | 'task_master_slave' | 'task_dag';
export type SessionKind = 'chat' | 'service_invocation';
export type GroupStatus = 'active' | 'dissolved';
export type SessionStatus = 'running' | 'completed';
export type SenderKind = 'human' | 'bot' | 'system';
export type ParticipantRole = 'owner' | 'driver' | 'manager' | 'member';
export type ParticipantMode = 'auto' | 'muted' | 'present' | 'absent';
export type DeliveryPolicy = 'send_to_driver' | 'inject_observers';

export interface ParticipantView {
  actorId: string;
  kind: 'human' | 'bot';
  name: string;
  avatarUrl?: string;
  role: ParticipantRole;
  mode: ParticipantMode;
  online?: boolean;
}
export interface SidePanelConfig {
  initializeSidePanel?: boolean;
  sidePanelId?: string;
  sidePanelName?: string;
  componentName?: string;
  cdnUrl?: string;
}
export interface GroupView {
  groupId: string;
  name: string;
  kind: GroupKind;
  status: GroupStatus;
  participants: ParticipantView[];
  sessions: SessionView[];
  lastMessageAt: number;
  createdAt: number;
  participantCount: number;
  ownerUserId?: string;
  joinedRole?: ParticipantRole;
  membership?: 'direct' | 'session_only';
  isPublic: boolean;
  publicJoinRole?: ParticipantRole;
  deliveryPolicy: DeliveryPolicy;
  sidePanelConfig?: SidePanelConfig;
}
export interface SessionView {
  sessionId: string;
  groupId: string;
  title: string;
  kind: SessionKind;
  status: SessionStatus;
  participants: ParticipantView[];
  /** 会话成员总数（列表接口返回；未返回时可用 participants.length 兜底）。 */
  participantCount?: number;
  lastMessageAt: number;
  createdAt: number;
  favorite: boolean;
  contextQuery?: string;
}
export interface InvitationView {
  token: string;
  groupId: string;
  groupName?: string;
  expiresAt?: number;
}
