import type {
  CommunityAuthor,
  CommunityHelp,
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

export interface CommunityHelpDto {
  expected_time?: string | null;
  [key: string]: unknown;
}

export interface CommunityTopicConfigDto {
  poll?: CommunityPollDto | null;
  help?: CommunityHelpDto | null;
  [key: string]: unknown;
}

export interface CommunityTopicSummaryDto {
  content?: string | null;
  [key: string]: unknown;
}

export interface CommunityTopicViewerDto {
  has_voted?: boolean | null;
  vote_post_id?: string | null;
  poll_selection?: { option_indices?: number[] | null } | null;
  [key: string]: unknown;
}

export interface CommunityTopicAnalysisDto {
  summary?: CommunityTopicSummaryDto | null;
  tags?: string[] | null;
  poll_stats?: CommunityPollStatsDto | null;
  [key: string]: unknown;
}

export interface CommunityTopicDto {
  topic_id?: string;
  title?: string;
  body?: string;
  body_preview?: string;
  body_truncated?: boolean;
  status?: string;
  topic_type?: string;
  topic_config?: CommunityTopicConfigDto | null;
  analysis?: CommunityTopicAnalysisDto | null;
  viewer?: CommunityTopicViewerDto | null;
  author_type?: string;
  author_id?: string;
  author?: CommunityAuthorDto;
  display_name?: string;
  avatar_url?: string | null;
  reply_count?: number;
  like_count?: number;
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
  floor_no?: number | null;
  helpful?: boolean | null;
  reply_to_post_id?: string | null;
  reply_to_floor_no?: number | null;
  poll_selection?: { option_indices?: number[] | null } | null;
  children?: CommunityReplyDto[] | null;
  author_type?: string;
  author_id?: string;
  author?: CommunityAuthorDto;
  display_name?: string;
  avatar_url?: string | null;
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
  return {
    actor_type: dto.author_type,
    actor_id: dto.author_id,
    display_name: dto.display_name,
    avatar_url: dto.avatar_url,
  };
}

export interface CommunityPollStatsOptionDto {
  index?: number | null;
  votes?: number | null;
  percent?: number | null;
  [key: string]: unknown;
}

export interface CommunityPollStatsDto {
  total_votes?: number | null;
  participant_count?: number | null;
  by_option?: CommunityPollStatsOptionDto[] | null;
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
  if (normalized === 'share') return 'share';
  if (normalized === 'poll') return 'poll';
  if (normalized === 'help') return 'help';
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

function mapHelp(value: CommunityHelpDto | null | undefined): CommunityHelp | undefined {
  if (!value) return undefined;
  return { expectedTime: value.expected_time?.trim() || undefined };
}

function mapPollStats(
  value: CommunityPollStatsDto | null | undefined,
  configuredOptions: string[],
): CommunityPollStats | undefined {
  if (!value) return undefined;
  if (typeof value.total_votes !== 'number' || typeof value.participant_count !== 'number') {
    return undefined;
  }
  const rawOptions = Array.isArray(value.by_option) ? value.by_option : [];
  const options: CommunityPollStatsOption[] = rawOptions.flatMap((option) => {
    if (
      typeof option.index !== 'number' ||
      !Number.isInteger(option.index) ||
      option.index < 0 ||
      option.index >= configuredOptions.length ||
      typeof option.votes !== 'number' ||
      typeof option.percent !== 'number'
    ) {
      return [];
    }
    return [
      {
        label: configuredOptions[option.index].trim(),
        votes: option.votes,
        percent: option.percent,
      },
    ];
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
  return (Array.isArray(value) ? value : []).map((tag) => tag.trim()).filter(Boolean);
}

function mapTopicViewer(value: CommunityTopicViewerDto | null | undefined) {
  if (typeof value?.has_voted !== 'boolean') return undefined;
  return {
    hasVoted: value.has_voted,
    votePostId: value.vote_post_id?.trim() || undefined,
    pollSelection: Array.isArray(value.poll_selection?.option_indices)
      ? (value.poll_selection?.option_indices ?? []).filter(
          (index) => Number.isInteger(index) && index >= 0,
        )
      : undefined,
  };
}

function mapTopicConfig(value: CommunityTopicConfigDto | null | undefined) {
  if (!value) return undefined;
  const config = {
    poll: mapPoll(value.poll),
    help: mapHelp(value.help),
  };
  if (!config.poll && !config.help) return undefined;
  return config;
}

function mapTopicSummary(value: CommunityTopicSummaryDto | null | undefined) {
  const content = value?.content?.trim();
  if (!content) return undefined;
  return { content };
}

function mapTopicAnalysis(
  value: CommunityTopicAnalysisDto | null | undefined,
  configuredOptions: string[],
) {
  if (!value) return undefined;
  return {
    summary: mapTopicSummary(value.summary),
    tags: mapTags(value.tags),
    pollStats: mapPollStats(value.poll_stats, configuredOptions),
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
  const topicConfig = mapTopicConfig(dto.topic_config);
  const analysis = mapTopicAnalysis(dto.analysis, topicConfig?.poll?.options ?? []);
  return {
    id: dto.topic_id?.trim() || '',
    title: dto.title?.trim() || '未命名主题',
    body,
    status: dto.status?.toUpperCase() === 'OPEN' ? 'open' : 'closed',
    topicType: mapTopicType(dto.topic_type),
    topicConfig,
    analysis,
    viewer: mapTopicViewer(dto.viewer),
    author: mapAuthor(asAuthorDto(dto)),
    replyCount: typeof dto.reply_count === 'number' ? dto.reply_count : undefined,
    likeCount: typeof dto.like_count === 'number' ? dto.like_count : undefined,
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
    floorNo: typeof dto.floor_no === 'number' ? dto.floor_no : undefined,
    helpful: dto.helpful === true,
    replyToPostId: dto.reply_to_post_id?.trim() || undefined,
    replyToFloorNo: typeof dto.reply_to_floor_no === 'number' ? dto.reply_to_floor_no : undefined,
    pollSelection: Array.isArray(dto.poll_selection?.option_indices)
      ? (dto.poll_selection?.option_indices ?? []).filter(
          (index) => Number.isInteger(index) && index >= 0,
        )
      : undefined,
    childReplies: (dto.children ?? []).map(mapCommunityReplyDto),
  };
}
