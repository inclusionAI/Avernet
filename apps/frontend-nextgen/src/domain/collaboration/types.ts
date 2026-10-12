export type IdentityStatus = 'online' | 'hidden';
export type IdentityReachability = 'reachable' | 'unreachable';
export type IdentityView = {
  id: string;
  kind: 'user' | 'bot';
  displayName: string;
  avatarUrl?: string;
  online: boolean;
  /** Bot 实例运行状态：online→在线，hidden→离线。human 项无此字段。 */
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
export type ParticipantRole = 'owner' | 'driver' | 'manager' | 'worker' | 'member';
export type ParticipantMode = 'auto' | 'muted' | 'present' | 'absent';
/** 消息可见域：full=完整视角；participant=参与者视角（仅公共/与自己相关/自己的消息）。 */
export type MessageViewScope = 'full' | 'participant';
export type DeliveryPolicy = 'send_to_driver' | 'inject_observers';

export interface ParticipantView {
  actorId: string;
  kind: 'human' | 'bot';
  name: string;
  avatarUrl?: string;
  role: ParticipantRole;
  mode: ParticipantMode;
  online?: boolean;
  /** 消息可见域回显（只读；bot 恒为 full）。 */
  messageViewScope?: MessageViewScope;
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
  /** Driver / Manager Bot ID；群列表无 participants 时用于权限判定。 */
  driverBotUuid?: string;
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

// ── 消息投递（拥塞控制）领域类型 ──────────────────────────────────────────────

/** 消息投递状态机（与后端 MessageDeliveryStatus 对齐）。 */
export type DeliveryStatus =
  | 'queued'
  | 'dispatching'
  | 'running'
  | 'unknown'
  | 'cancelling'
  | 'cancel_unknown'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'expired'
  | 'rejected_capacity'
  | 'pending_context'
  | 'bound'
  | 'consumed'
  | 'discarded_context';

/** 排队等待原因（与后端 DeliveryWaitReason 对齐）。 */
export type DeliveryWaitReason =
  | 'prior_message_running'
  | 'bot_capacity'
  | 'rate_limited'
  | 'bot_offline'
  | 'retry_backoff'
  | 'paused';

/** 投递流类型（与后端 DeliveryFlowKind 对齐）。 */
export type DeliveryFlowKind = 'group' | 'direct_a2a' | 'task' | 'system' | 'state_machine';

/** 投递类型（与后端 DeliveryType 对齐）。 */
export type DeliveryType = 'send' | 'inject';

/** 后端推送的投递状态视图（message.delivery.updated 载荷）。 */
export interface DeliveryStatusView {
  delivery_id: string;
  message_id: string;
  target_bot_id: string;
  flow_kind: DeliveryFlowKind;
  kind: DeliveryType;
  status: DeliveryStatus;
  state_version: number;
  run_id: string | null;
  wait_reason: DeliveryWaitReason | null;
  admission_error: string | null;
  /** 消息正文预览（截断至前 200 字符），供前端排队列表展示。 */
  content_preview?: string | null;
}

/** 判断投递是否处于活跃（非终态）。 */
export function isActiveDelivery(status: DeliveryStatus): boolean {
  return ![
    'completed',
    'failed',
    'cancelled',
    'expired',
    'rejected_capacity',
    'consumed',
    'discarded_context',
  ].includes(status);
}

/** 判断投递是否处于排队中（可取消）。 */
export function isQueuedDelivery(status: DeliveryStatus): boolean {
  return status === 'queued' || status === 'dispatching';
}

/** 判断投递是否处于处理中（可终止）。 */
export function isProcessingDelivery(status: DeliveryStatus): boolean {
  return status === 'running';
}
