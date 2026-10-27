import { getCapabilities } from '@/capabilities';
import type {
  CommunityReplyDto,
  CommunityTopicDto,
} from '@/domain/community/mapper';
import type {
  CommunityReplyPager,
  CommunityTopicFilter,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
} from '@/domain/community/types';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface CommunityTopicPageDto {
  items: CommunityTopicDto[];
  total: number;
}

export interface CommunityReplyPageDto {
  items: CommunityReplyDto[];
  total: number;
}

export type CommunityEnvelope<T> = BackendApiEnvelope<T>;

export interface CommunityCreatedTopicDto {
  topic_id: string;
  topic_type?: string;
}

export interface CommunityCreatedReplyDto {
  post_id?: string;
}

export interface CommunityPostHelpfulDto {
  post_id?: string;
  helpful?: boolean;
}

const DEFAULT_PAGE_SIZE = 20;

function bbsApiBase(): string {
  return getCapabilities().getBbsApiBase().value ?? '/openapi/v1/collaboration/tasks/bbs/topics';
}

function backendTopicType(topicType: CommunityTopicFilter | undefined): string | undefined {
  if (!topicType || topicType === 'all') return undefined;
  return topicType.toUpperCase();
}

let clientRequestIdCounter = 0;
function makeClientRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  clientRequestIdCounter += 1;
  return `tc-${Date.now()}-${clientRequestIdCounter}`;
}

export function listCommunityTopics(query: CommunityTopicQuery = {}, signal?: AbortSignal) {
  const base = bbsApiBase();
  const offset = Math.max(0, query.offset ?? 0);
  const limit = query.limit ?? DEFAULT_PAGE_SIZE;
  const params: Record<string, unknown> = {
    page: Math.floor(offset / Math.max(1, limit)) + 1,
    page_size: limit,
  };
  if (query.search?.trim()) params.keyword = query.search.trim();
  if (query.authorId?.trim()) params.author_id = query.authorId.trim();
  const topicType = backendTopicType(query.topicType);
  if (topicType) params.topic_type = topicType;
  return backendRequest<CommunityEnvelope<CommunityTopicPageDto>>(base, {
    method: 'GET',
    params,
    signal,
  });
}

export function getCommunityTopic(
  topicId: string,
  options: {
    viewerAuthorId?: string;
    viewerAuthorType?: 'HUMAN' | 'BOT';
  } = {},
  signal?: AbortSignal,
) {
  const base = bbsApiBase();
  const params: Record<string, unknown> = {};
  if (options.viewerAuthorId?.trim()) params.viewer_author_id = options.viewerAuthorId.trim();
  if (options.viewerAuthorType?.trim()) params.viewer_author_type = options.viewerAuthorType.trim();
  return backendRequest<CommunityEnvelope<CommunityTopicDto>>(`${base}/${encodeURIComponent(topicId)}`, {
    method: 'GET',
    params,
    signal,
  });
}

export function listCommunityReplies(topicId: string, pager: CommunityReplyPager = {}, signal?: AbortSignal) {
  const base = bbsApiBase();
  const page = Math.max(1, pager.page ?? 1);
  const page_size = Math.max(1, pager.pageSize ?? DEFAULT_PAGE_SIZE);
  return backendRequest<CommunityEnvelope<CommunityReplyPageDto>>(`${base}/${encodeURIComponent(topicId)}/posts`, {
    method: 'GET',
    params: { page, page_size },
    signal,
  });
}

export function createCommunityTopic(input: CreateCommunityTopicInput, signal?: AbortSignal) {
  const topicType = input.topicType ?? 'discussion';
  const data: {
    client_request_id: string;
    author_type: 'HUMAN';
    author_id: string;
    title: string;
    body: string;
    topic_type: 'DISCUSSION' | 'SHARE' | 'POLL' | 'HELP';
    topic_config?: {
      poll?: {
        selection: 'SINGLE' | 'MULTI';
        options: string[];
        deadline_at?: string;
      };
      help?: { expected_time?: string };
    };
    author_display_name?: string;
    author_avatar_url?: string;
  } = {
    client_request_id: makeClientRequestId(),
    author_type: 'HUMAN',
    author_id: input.authorId,
    title: input.title,
    body: input.body,
    topic_type: topicType.toUpperCase() as 'DISCUSSION' | 'SHARE' | 'POLL' | 'HELP',
  };
  if (topicType === 'poll' && input.poll) {
    data.topic_config = {
      poll: {
        selection: input.poll.selection === 'multi' ? 'MULTI' : 'SINGLE',
        options: input.poll.options,
        ...(input.poll.deadlineAt ? { deadline_at: input.poll.deadlineAt } : {}),
      },
    };
  } else if (topicType === 'help' && input.help) {
    data.topic_config = {
      help: input.help.expectedTime ? { expected_time: input.help.expectedTime } : {},
    };
  }
  const displayName = input.authorName?.trim();
  if (displayName) data.author_display_name = displayName;
  if (input.authorAvatarUrl) data.author_avatar_url = input.authorAvatarUrl;
  return backendRequest<CommunityEnvelope<CommunityCreatedTopicDto>>(bbsApiBase(), {
    method: 'POST',
    data,
    signal,
  });
}

export function createCommunityReply(input: CreateCommunityReplyInput, signal?: AbortSignal) {
  const displayName = input.authorName?.trim();
  const data: {
    client_request_id: string;
    author_type: 'HUMAN';
    author_id: string;
    body: string;
    poll_selection?: { option_indices: number[] };
    author_display_name?: string;
    author_avatar_url?: string;
  } = {
    client_request_id: makeClientRequestId(),
    author_type: 'HUMAN',
    author_id: input.authorId,
    body: input.body,
  };
  if (input.pollSelection) data.poll_selection = { option_indices: [...input.pollSelection] };
  if (displayName) data.author_display_name = displayName;
  if (input.authorAvatarUrl) data.author_avatar_url = input.authorAvatarUrl;

  return backendRequest<CommunityEnvelope<CommunityCreatedReplyDto>>(
    `${bbsApiBase()}/${encodeURIComponent(input.topicId)}/posts`,
    { method: 'POST', data, signal },
  );
}

export function setCommunityPostHelpful(
  topicId: string,
  postId: string,
  authorType: 'HUMAN' | 'BOT',
  authorId: string,
  helpful: boolean,
  signal?: AbortSignal,
) {
  return backendRequest<CommunityEnvelope<CommunityPostHelpfulDto>>(
    `${bbsApiBase()}/${encodeURIComponent(topicId)}/posts/${encodeURIComponent(postId)}/helpful`,
    {
      method: 'PUT',
      data: {
        author_type: authorType,
        author_id: authorId,
        helpful,
      },
      signal,
    },
  );
}

export function closeCommunityTopic(topicId: string, authorId: string, signal?: AbortSignal) {
  return backendRequest<CommunityEnvelope<null | { topic_id?: string; status?: string }>>(
    `${bbsApiBase()}/${encodeURIComponent(topicId)}/close`,
    { method: 'POST', data: { author_type: 'HUMAN', author_id: authorId }, signal },
  );
}
