export type CommunityActorType = 'human' | 'bot';
export type CommunityTopicStatus = 'open' | 'closed';
export type CommunityScope = 'all' | 'mine';

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
}

export interface CommunityTopicPage {
  items: CommunityTopic[];
  total: number;
}

/** 回帖分页（后端 GET /topics/{id}/posts 返回 Envelope<Page<PostItem>>）。 */
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

export interface CreateCommunityTopicInput {
  title: string;
  body: string;
  authorId: string;
  authorName: string;
  authorAvatarUrl?: string;
}
