import type { CommunityAuthor, CommunityReply, CommunityTopic } from './types';

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
  };
}
