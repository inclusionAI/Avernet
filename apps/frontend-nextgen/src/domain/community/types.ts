export type CommunityActorType = 'human' | 'bot';
export type CommunityTopicStatus = 'open' | 'closed';
export type CommunityTopicType = 'discussion' | 'poll' | 'notice';
export type CommunityPollSelection = 'single' | 'multi';
export type CommunityScope = 'all' | 'mine';

export interface CommunityPoll {
  selection: CommunityPollSelection;
  options: string[];
  deadlineAt?: string;
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
  latestActivityAt?: string;
  createdAt: string;
  isMine: boolean;
  canClose: boolean;
}

export interface CommunityReply {
  id: string;
  topicId: string;
  body: string;
  author: CommunityAuthor;
  createdAt: string;
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

export interface CreateCommunityTopicDraft {
  title: string;
  body: string;
  topicType?: Exclude<CommunityTopicType, 'notice'>;
  poll?: CreateCommunityPollInput;
}

export interface CreateCommunityTopicInput extends CreateCommunityTopicDraft {
  authorId: string;
  authorName: string;
  authorAvatarUrl?: string;
}

export interface CreateCommunityReplyInput {
  topicId: string;
  body: string;
  authorId: string;
  authorName?: string;
  authorAvatarUrl?: string;
  pollSelection?: number[];
}
