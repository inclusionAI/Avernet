export type CommunityActorType = 'human' | 'bot';
export type CommunityTopicStatus = 'open' | 'closed';
export type CommunityTopicType = 'discussion' | 'share' | 'poll' | 'help' | 'notice';
export type CommunityTopicFilter = 'all' | Exclude<CommunityTopicType, 'notice'>;
export type CommunityPublishTopicType = Exclude<CommunityTopicType, 'notice'>;
export type CommunityPollSelection = 'single' | 'multi';
export type CommunityScope = 'all' | 'mine';

export interface CommunityPoll {
  selection: CommunityPollSelection;
  options: string[];
  deadlineAt?: string;
}

export interface CommunityHelp {
  expectedTime?: string;
}

export interface CommunityPollStatsOption {
  label: string;
  votes: number;
  percent: number;
}

export interface CommunityPollStats {
  totalVotes: number;
  participantCount: number;
  options: CommunityPollStatsOption[];
  updatedAt: string;
}

export interface CommunityTopicConfig {
  poll?: CommunityPoll;
  help?: CommunityHelp;
}

export interface CommunityTopicSummary {
  content: string;
}

export interface CommunityTopicViewer {
  hasVoted: boolean;
  votePostId?: string;
  pollSelection?: number[];
}

export interface CommunityTopicAnalysis {
  summary?: CommunityTopicSummary;
  tags?: string[];
  pollStats?: CommunityPollStats;
}

export interface CommunityAuthor {
  type: CommunityActorType;
  id: string;
  displayName: string;
  avatarUrl?: string;
}

export interface CommunityTopic {
  id: string;
  title: string;
  body: string;
  status: CommunityTopicStatus;
  topicType: CommunityTopicType;
  topicConfig?: CommunityTopicConfig;
  analysis?: CommunityTopicAnalysis;
  viewer?: CommunityTopicViewer;
  author: CommunityAuthor;
  replyCount?: number;
  likeCount?: number;
  latestActivityAt?: string;
  createdAt: string;
  isMine: boolean;
  canClose: boolean;
  canEdit: boolean;
  canDelete: boolean;
}

export interface CommunityReply {
  id: string;
  topicId: string;
  body: string;
  author: CommunityAuthor;
  createdAt: string;
  /** Stable chronological number for a first-level reply. */
  floorNo?: number;
  /** HELP-only marker controlled by the Topic author. */
  helpful: boolean;
  /** Optional Post being referenced. The list remains flat; this is not a tree edge. */
  replyToPostId?: string;
  /** Structured vote indexes for POLL replies. */
  pollSelection?: number[];
  /** Read-only floor number derived by the backend from first-level reply order. */
  replyToFloorNo?: number;
  /**
   * Presentation-only replies shown inside this first-level floor.
   * The write contract remains a single `reply_to_post_id`.
   */
  childReplies?: CommunityReply[];
}

export interface CommunityTopicPage {
  items: CommunityTopic[];
  total: number;
}

/** 回帖分页；total 只计数一级楼层，children 跟随当前页所属楼层返回。 */
export interface CommunityReplyPage {
  items: CommunityReply[];
  total: number;
}

/** 回帖分页查询参数（1-based page / page_size）。 */
export interface CommunityReplyPager {
  page?: number;
  pageSize?: number;
}

export interface CommunityTopicQuery {
  search?: string;
  scope?: CommunityScope;
  topicType?: CommunityTopicFilter;
  /** 作者过滤（HUMAN 工号 / BOT id）；scope=mine 时由调用方填入本人工号，下发 openapi §2.4 author_id 真分页。 */
  authorId?: string;
  offset?: number;
  limit?: number;
}

export interface CreateCommunityPollInput {
  selection: CommunityPollSelection;
  options: string[];
  deadlineAt?: string;
}

export interface CreateCommunityHelpInput {
  expectedTime?: string;
}

export interface CreateCommunityTopicDraft {
  title: string;
  body: string;
  topicType?: CommunityPublishTopicType;
  poll?: CreateCommunityPollInput;
  help?: CreateCommunityHelpInput;
}

export interface CreateCommunityTopicInput extends CreateCommunityTopicDraft {
  authorId: string;
  authorName: string;
  authorAvatarUrl?: string;
}

export interface UpdateCommunityTopicInput {
  topicId: string;
  authorId: string;
  title: string;
  body: string;
}

export interface CreateCommunityReplyInput {
  topicId: string;
  body: string;
  authorId: string;
  replyToPostId?: string;
  authorName?: string;
  authorAvatarUrl?: string;
  pollSelection?: number[];
}

export type CreateCommunityReplyDraft = Pick<
  CreateCommunityReplyInput,
  'body' | 'pollSelection' | 'replyToPostId'
>;

export type InteractionActorType = 'HUMAN' | 'BOT';
export type InteractionActionType = 'VIEW' | 'LIKE' | 'FAVORITE' | 'FOLLOW' | 'ACCEPT';
export type InteractionTargetType = 'TOPIC' | 'POST' | 'HUMAN' | 'BOT';
export type InteractionSummaryScope = 'ACTOR' | 'TARGET' | 'ACTOR_TARGET';
export type PersonalCenterActorScope = 'HUMAN' | 'BOT' | 'ALL';
/** Personal Center exposes only the five concrete activity tabs; ALL is a backend-only aggregate. */
export type PersonalCenterActivityType =
  | 'AUTHORED'
  | 'FAVORITED'
  | 'VIEWED'
  | 'LIKED'
  | 'REPLIED';
export type PersonalCenterTargetFilter = 'ALL' | 'TOPIC' | 'POST';

export interface ForumActorRef {
  actorType: InteractionActorType;
  id: string;
}

export interface ForumTargetRef {
  targetType: InteractionTargetType;
  id: string;
}

export interface InteractionSummaryQuery {
  actor?: ForumActorRef;
  target?: ForumTargetRef;
}

export interface InteractionStats {
  viewCount: number;
  likeCount: number;
  favoriteCount: number;
  followCount: number;
  acceptCount: number;
  topicCount: number;
  replyCount: number;
  acceptedReplyCount: number;
  replyParticipantCount: number;
  helpfulReplyCount: number;
}

export interface InteractionSummary extends InteractionStats {
  scope: InteractionSummaryScope;
  actor?: ForumActorRef;
  target?: ForumTargetRef;
}

export interface RecordInteractionInput {
  actor: ForumActorRef;
  actionType: InteractionActionType;
  target: ForumTargetRef;
}

export interface PersonalCenterActivity {
  activityType: PersonalCenterActivityType;
  actor: ForumActorRef;
  target: ForumTargetRef;
  topicId: string;
  postId?: string;
  happenedAt: string;
  bodyPreview: string;
}

export interface PersonalCenterActivityPage {
  items: PersonalCenterActivity[];
  total: number;
}

export interface PersonalCenterActivityQuery {
  ownerUserId: string;
  actorScope: PersonalCenterActorScope;
  activityType: PersonalCenterActivityType;
  targetType: PersonalCenterTargetFilter;
  page: number;
  pageSize: number;
}
