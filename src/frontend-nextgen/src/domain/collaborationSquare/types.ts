export type BotRelationshipStatus = 'none' | 'applying' | 'friend';
export type SquareResource = 'bot' | 'group' | 'task';
export type BotSearchMode = 'name' | 'smart';

/**
 * 任务广场（只读浏览）四种公开状态。来源语义为 BBS 接力（求助）发布到广场的公开任务，
 * 本档只消费广场只读四态，不建模任务生命周期。
 */
export type PlazaTaskStatus = 'pending_claim' | 'claimed' | 'reviewing' | 'completed';
/** 状态筛选：`'all'` 表示不限。 */
export type TaskStatusFilter = 'all' | PlazaTaskStatus;
/** 状态徽标语义 tone，配合文案双通道呈现，避免仅依赖颜色。 */
export type TaskStatusTone = 'warning' | 'brand' | 'info' | 'success';

export interface PublicTask {
  id: string;
  name: string;
  goal: string;
  acceptanceCriteria: string[];
  status: PlazaTaskStatus;
  /** 发布者展示名。BBS 端点 publisher 为 null（系统任务等）时为 undefined。 */
  publisherBotName?: string;
  /** 发布者原始 Bot ID（详情页用于「id（name）」展示）。 */
  publisher?: string;
  /** 发布者展示名（来自后端 `publisher_name`，优先于 bots/query 反查）；缺失时为 undefined。 */
  publisherName?: string;
  publishedAt: string;
  claimedBotName?: string;
  claimedAt?: string;
  completedAt?: string;
  /** 任务输出内容（来自 BBS `extend_props.output`）；无输出时为 undefined。 */
  output?: string;
}

export interface PublicTaskSearchQuery {
  /** 命中任务名称或任务目标，大小写不敏感。 */
  search?: string;
  /** 状态筛选，`'all'` 表示不限。 */
  status?: TaskStatusFilter;
  offset?: number;
  limit?: number;
}

export interface PublicTaskPage {
  items: PublicTask[];
  total: number;
}

/** 任务广场状态文案与语义 tone 的固定映射（文字 + 语义徽标双通道）。 */
export const TASK_STATUS_CONFIG: Record<PlazaTaskStatus, { label: string; tone: TaskStatusTone }> = {
  pending_claim: { label: '待认领', tone: 'warning' },
  claimed: { label: '已认领', tone: 'brand' },
  reviewing: { label: '待验收', tone: 'info' },
  completed: { label: '已完成', tone: 'success' },
};

/** 返回广场任务状态的展示信息（label + tone），对齐既有 {@link getPublicBotTargetId} 纯函数范式。 */
export function getPublicTaskStatusPresentation(status: PlazaTaskStatus): { label: string; tone: TaskStatusTone } {
  return TASK_STATUS_CONFIG[status];
}

/**
 * Catalog 检索时的当前身份（read-time viewer）。跟随当前角色 tab：
 * human → viewerActorId 取 humanIdentity.userId；bot → viewerActorId 取当前 bot 身份 id。
 */
export interface BotCatalogViewer {
  viewerActorType: 'human' | 'bot';
  viewerActorId: string;
}

/** 好友申请的发起方身份（from_actor）。协作广场默认登录人类；对话协作跟随当前角色 tab。 */
export interface FriendRequestActor {
  type: 'human' | 'bot';
  id: string;
}
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
  /** Catalog 检索的当前身份（read-time viewer）；下发给 bot-catalog 的 viewer_actor_type。 */
  viewerActorType?: BotCatalogViewer['viewerActorType'];
  /** Catalog 检索的当前身份 id；下发给 bot-catalog 的 viewer_actor_id。 */
  viewerActorId?: string;
}

export interface PublicBotDiscoveryQuery {
  keyword: string;
  topK?: number;
  minScore?: number;
  runtimeState?: 'online';
  /** 智能发现同样以当前身份作为 viewer 过滤可见 Bot。 */
  viewerActorType?: BotCatalogViewer['viewerActorType'];
  viewerActorId?: string;
}

export interface PublicGroupSearchQuery {
  search?: string;
  offset?: number;
  limit?: number;
}

export interface CollaborationSquarePage<T> {
  items: T[];
  total: number;
}

export interface PublicBot {
  /** 公开 Bot Catalog 的 canonical target；有 bot_uuid 时使用复合值，缺失时回退 Bot ID。 */
  id: string;
  /** 兼容缺少 bot_uuid 的历史数据时使用的好友申请 actor id。 */
  friendRequestBotId?: string;
  name: string;
  ownerName: string;
  description: string;
  capabilities: string[];
  relationshipStatus: BotRelationshipStatus;
  /** entity_id 与当前 human user_id 一致时，表示当前用户拥有该公开 Bot，可直接创建会话。 */
  isOwnedByViewer?: boolean;
  /** 智能搜索 Discovery 响应的推荐理由简述（recommendation.short_profile），名称搜索无此字段。 */
  shortProfile?: string;
}

/** Return the stable identity used to isolate friend-request state for a public Bot card. */
export function getPublicBotTargetId(bot: Pick<PublicBot, 'id' | 'friendRequestBotId'>): string {
  return bot.friendRequestBotId?.trim() || bot.id;
}

/** 当前查看者可直接创建公开 Bot 会话（已是好友或 Bot 归当前用户管理）。 */
export function canStartPublicBotConversation(bot: Pick<PublicBot, 'relationshipStatus' | 'isOwnedByViewer'>): boolean {
  return bot.isOwnedByViewer === true || bot.relationshipStatus === 'friend';
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
  /** 群主 Bot 的 driver_bot_uuid。公开群目录响应无 participants，需用此 uuid 经 bots/query 反查群主名。 */
  driverBotUuid?: string;
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

/** 创建公开协作群会话的表单入参（OpenAPI POST /groups/{group_id}/sessions body）。 */
export interface CreateGroupSessionPayload {
  /** 会话名称（接口 body.title）。 */
  title: string;
  /** 协作目标（接口 body.input.query）。 */
  query: string;
}

export interface CreateSessionResult {
  /** 新建会话 ID（用于跳转选中）。 */
  sessionId: string;
  /** Only present when the backend explicitly returns the caller's effective role. */
  defaultRole?: string;
  /** Kept optional until the group-session contract returns membership source. */
  memberSource?: 'session_temp';
}

export interface SquareDeepLink {
  resource: SquareResource;
  id: string;
  /** Bot 分享链接附带的公开名称，仅作为 Catalog Search 提示；最终仍按 id 精确匹配。 */
  searchHint?: string;
}

/**
 * 公开 Bot 面板（协作广场 / 添加好友弹窗）展示层 view model 契约。
 * 两个调用方各自产出该结构（广场用全局 store hook，弹窗用本地 state hook），
 * 由公共展示组件 {@link PublicBotCatalogPanel} 消费。
 */
export interface BotCatalogViewModel {
  bots: PublicBot[];
  busyKeys: string[];
  query: string;
  mode: BotSearchMode;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  loadMoreError: string | null;
  setQuery: (query: string) => void;
  setMode: (mode: BotSearchMode) => void;
  reload: () => void;
  loadMore: () => void;
  primaryAction: (bot: PublicBot) => void;
  share: (bot: PublicBot) => void;
  openProfile: (bot: PublicBot) => void;
  closeProfile: () => void;
  selectedBotId: string | null;
  botProfile: PublicBotProfile | null;
  detailLoading: boolean;
  copyBotId: (id: string) => void;
}
