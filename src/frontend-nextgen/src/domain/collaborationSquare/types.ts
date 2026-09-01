export type BotRelationshipStatus = 'none' | 'applying' | 'friend';
export type SquareResource = 'bot' | 'group';
export type BotSearchMode = 'name' | 'smart';

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
