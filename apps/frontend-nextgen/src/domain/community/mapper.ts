import type {
  CommunityAuthor,
  CommunityPoll,
  CommunityPollSelection,
  CommunityPollStats,
  CommunityPollStatsOption,
  CommunityReply,
  CommunityTopic,
  CommunityTopicType,
} from './types';

export interface CommunityAuthorDto {
  actor_type?: string;
  actor_id?: string;
  display_name?: string;
  avatar_url?: string | null;
}

export interface CommunityTopicDto {
  topic_id?: string;
  title?: string;
  body?: string;
  body_preview?: string;
  body_truncated?: boolean;
  status?: string;
  topic_type?: string;
  poll?: CommunityPollDto | null;
  poll_stats?: CommunityPollStatsDto | null;
  tags?: string[] | null;
  author_type?: string;
  author_id?: string;
  author?: CommunityAuthorDto;
  reply_count?: number;
  latest_activity_at?: string;
  updated_at?: string;
  created_at?: string;
  is_mine?: boolean;
  can_close?: boolean;
  [key: string]: unknown;
}

export interface CommunityReplyDto {
  post_id?: string;
  topic_id?: string;
  body?: string;
  reply_to_post_id?: string | null;
  reply_to_floor_no?: number | null;
  children?: CommunityReplyDto[] | null;
  author_type?: string;
  author_id?: string;
  author?: CommunityAuthorDto;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

function asAuthorDto(dto: {
  author?: CommunityAuthorDto;
  author_type?: string;
  author_id?: string;
  display_name?: string;
  avatar_url?: string | null;
}): CommunityAuthorDto {
  if (dto.author) return dto.author;
  // openapi §4.1 列表项把 author_type/author_id/display_name/avatar_url 平铺（详情/楼层接口无此平铺）；
  // 透传 display_name/avatar_url，避免后端 enrich 后前端拿不到展示名/头像（mapAuthor 兜底 id/匿名）。
  return {
    actor_type: dto.author_type,
    actor_id: dto.author_id,
    display_name: dto.display_name,
    avatar_url: dto.avatar_url,
  };
}

export interface CommunityPollStatsOptionDto {
  label?: string | null;
  votes?: number | null;
  percent?: number | null;
  [key: string]: unknown;
}

export interface CommunityPollStatsDto {
  total_votes?: number | null;
  participant_count?: number | null;
  options?: CommunityPollStatsOptionDto[] | null;
  updated_at?: string | null;
  [key: string]: unknown;
}

export interface CommunityPollDto {
  selection?: string;
  options?: string[] | null;
  deadline_at?: string | null;
  [key: string]: unknown;
}

function mapTopicType(value: string | undefined): CommunityTopicType {
  const normalized = value?.trim().toLowerCase();
  if (normalized === 'poll') return 'poll';
  if (normalized === 'notice') return 'notice';
  return 'discussion';
}

function mapPoll(value: CommunityPollDto | null | undefined): CommunityPoll | undefined {
  if (!value) return undefined;
  const selection: CommunityPollSelection =
    value.selection?.trim().toLowerCase() === 'multi' ? 'multi' : 'single';
  const options = (Array.isArray(value.options) ? value.options : [])
    .map((option) => option.trim())
    .filter(Boolean);
  if (!options.length) return undefined;
  return {
    selection,
    options,
    deadlineAt: value.deadline_at?.trim() || undefined,
  };
}

function mapPollStats(
  value: CommunityPollStatsDto | null | undefined,
): CommunityPollStats | undefined {
  if (!value) return undefined;
  if (typeof value.total_votes !== 'number' || typeof value.participant_count !== 'number') {
    return undefined;
  }
  const rawOptions = Array.isArray(value.options) ? value.options : [];
  const options: CommunityPollStatsOption[] = rawOptions.flatMap((option) => {
    if (
      typeof option.label !== 'string' ||
      !option.label.trim() ||
      typeof option.votes !== 'number' ||
      typeof option.percent !== 'number'
    ) {
      return [];
    }
    return [{ label: option.label.trim(), votes: option.votes, percent: option.percent }];
  });
  if (!options.length) return undefined;
  return {
    totalVotes: value.total_votes,
    participantCount: value.participant_count,
    options,
    updatedAt: value.updated_at?.trim() || '',
  };
}

function mapTags(value: string[] | null | undefined): string[] {
  return (Array.isArray(value) ? value : [])
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function mapAuthor(author: CommunityAuthorDto | undefined): CommunityAuthor {
  const trimmedId = author?.actor_id?.trim() || '';
  const trimmedName = author?.display_name?.trim();
  const displayName = trimmedName || trimmedId || '匿名';
  return {
    type: author?.actor_type?.toUpperCase() === 'BOT' ? 'bot' : 'human',
    id: trimmedId || 'unknown',
    displayName,
    avatarUrl: author?.avatar_url?.trim() || undefined,
  };
}

export function mapCommunityTopicDto(dto: CommunityTopicDto): CommunityTopic {
  const body = dto.body ?? dto.body_preview ?? '';
  const latestActivityAt = dto.latest_activity_at ?? dto.updated_at ?? dto.created_at ?? undefined;
  return {
    id: dto.topic_id?.trim() || '',
    title: dto.title?.trim() || '未命名主题',
    body,
    status: dto.status?.toUpperCase() === 'OPEN' ? 'open' : 'closed',
    topicType: mapTopicType(dto.topic_type),
    poll: mapPoll(dto.poll),
    pollStats: mapPollStats(dto.poll_stats),
    tags: mapTags(dto.tags),
    author: mapAuthor(asAuthorDto(dto)),
    replyCount: typeof dto.reply_count === 'number' ? dto.reply_count : undefined,
    latestActivityAt,
    createdAt: dto.created_at ?? '',
    isMine: dto.is_mine === true,
    canClose: dto.can_close === true,
  };
}

export function mapCommunityReplyDto(dto: CommunityReplyDto): CommunityReply {
  return {
    id: dto.post_id?.trim() || '',
    topicId: dto.topic_id?.trim() || '',
    body: dto.body ?? '',
    author: mapAuthor(asAuthorDto(dto)),
    createdAt: dto.created_at ?? '',
    replyToPostId: dto.reply_to_post_id?.trim() || undefined,
    replyToFloorNo: typeof dto.reply_to_floor_no === 'number' ? dto.reply_to_floor_no : undefined,
    childReplies: (dto.children ?? []).map(mapCommunityReplyDto),
  };
}
