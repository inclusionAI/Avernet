export type IdentityStatus = 'online' | 'hidden';
export type IdentityReachability = 'reachable' | 'unreachable';
export type IdentityView = {
  id: string;
  kind: 'user' | 'bot';
  displayName: string;
  avatarUrl?: string;
  online: boolean;
  /** Bot 实例运行状态：online→在线，hidden→不在线。human 项无此字段。 */
  status?: IdentityStatus;
  /** Bot 所使用的引擎类型；后端未返回时保持缺省。 */
  engine?: string;
  /** Bot 类型原始枚举值：personal / service / desktop。 */
  botType?: string;
  /** Bot 群聊链路可达性；与运行状态分开表达。human 项无此字段。 */
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
export interface GroupInitialRun {
  runId: string;
  botUuid: string;
  activityKind: 'group_bootstrap';
  state: 'running' | 'failed';
  startedAt: string;
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
  /** 创建群时后端同步生成的初始会话 ID；仅在创建响应链路保留。 */
  initialSessionId?: string;
  joinedRole?: ParticipantRole;
  membership?: 'direct' | 'session_only';
  isPublic: boolean;
  publicJoinRole?: ParticipantRole;
  deliveryPolicy: DeliveryPolicy;
  sidePanelConfig?: SidePanelConfig;
  /** 仅创建响应返回，用于立即展示 Driver/Manager 启动状态。 */
  initialRun?: GroupInitialRun;
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
  /** 会话创建者 actor_id（bot_id 或 user_id），用于权限判定（creator 可删除会话）。 */
  createdBy?: string;
  /** 发起调用的主体标识（区分身份维度）。 */
  callerPrincipal?: string;
}
export interface GroupSessionPage {
  items: SessionView[];
  offset: number;
  limit: number;
  total: number;
  hasMore: boolean;
}
export interface InvitationView {
  token: string;
  groupId: string;
  groupName?: string;
  expiresAt?: number;
}
