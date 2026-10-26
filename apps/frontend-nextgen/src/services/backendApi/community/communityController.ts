import { getCapabilities } from '@/capabilities';
import type { CommunityReplyDto, CommunityTopicDto } from '@/domain/community/mapper';
import type {
  CommunityReplyPager,
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

/** 默认每页条数（列表首屏、加载更多、回帖分页统一）。 */
const DEFAULT_PAGE_SIZE = 20;

/**
 * BBS API 路径前缀（list/get/posts/create/close 共用）：由 capability getBbsApiBase 注入。
 * 默认值为 collaboration task 公开域 `/openapi/v1/collaboration/tasks/bbs/topics`。
 * 写口 body 声明 author_type/author_id（HUMAN 另携带展示名/头像快照），列表支持 author_id 过滤。
 * 请求期调用确保 capability 已装填，避免启动期依赖。
 */
function bbsApiBase(): string {
  return getCapabilities().getBbsApiBase().value ?? '/openapi/v1/collaboration/tasks/bbs/topics';
}

let clientRequestIdCounter = 0;
function makeClientRequestId(): string {
  // Openapi 写契约要求 `client_request_id`（写幂等键，同作者+同目标重放命中返回旧结果）。
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  clientRequestIdCounter += 1;
  return `tc-${Date.now()}-${clientRequestIdCounter}`;
}

// httpClient 对 /api 与 /openapi 开头请求自动注入当前 user_id query（与 taskController 一致；BBS 内面 §2.1–§2.6 不消费 user_id，多余无害）。

export function listCommunityTopics(query: CommunityTopicQuery = {}, signal?: AbortSignal) {
  // openapi §2.4 方言：keyword / page(1-based) / page_size / author_id（“我的”真分页）。scope 由 Service 客户端
  // 按 authorId 解析并下发 author_id；后端真分页，total 为过滤后行数。
  const base = bbsApiBase();
  const offset = Math.max(0, query.offset ?? 0);
  const limit = query.limit ?? DEFAULT_PAGE_SIZE;
  const params: Record<string, unknown> = {
    page: Math.floor(offset / Math.max(1, limit)) + 1,
    page_size: limit,
  };
  if (query.search && query.search.trim()) params.keyword = query.search.trim();
  if (query.authorId && query.authorId.trim()) params.author_id = query.authorId.trim();
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
  // 后端返回 Envelope<Page<PostItem>> (data:{total, items})，按 page/page_size 分页。
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
  // task 域 API 写契约：基础写体为
  // `{client_request_id, author_type, author_id, title, body}`，另可携带可选作者快照字段。
  // 产品端发布主题恒为当前登录 Human（author_type 固定 'HUMAN'，author_id = 本人工号）。
  // HUMAN 同步下发 `author_display_name`(花名)+`author_avatar_url`(头像) 作为「写入快照」（§8 填充规则）：
  // 后端不便取 staff 目录 → 列表/详情/楼层直接回放快照；不发帖人填了也只对自己这条帖生效。
  // 上层 `useCommunity.publishTopic` 已把 `useHumanIdentity` 的 displayName/avatarUrl 透传到 input，
  // 这里仅把它们落 body。BOT 非发帖人故不需要处理；当前产品不能回帖，本接口只覆盖发帖写路径。
  const data: {
    client_request_id: string;
    author_type: 'HUMAN';
    author_id: string;
    title: string;
    body: string;
    topic_type: 'DISCUSSION' | 'POLL';
    topic_config?: {
      poll?: {
        selection: 'SINGLE' | 'MULTI';
        options: string[];
        deadline_at?: string;
      };
    };
    author_display_name?: string;
    author_avatar_url?: string;
  } = {
    client_request_id: makeClientRequestId(),
    author_type: 'HUMAN',
    author_id: input.authorId,
    title: input.title,
    body: input.body,
    topic_type: input.topicType === 'poll' ? 'POLL' : 'DISCUSSION',
  };
  if (input.topicType === 'poll' && input.poll) {
    data.topic_config = {
      poll: {
        selection: input.poll.selection === 'multi' ? 'MULTI' : 'SINGLE',
        options: input.poll.options,
        ...(input.poll.deadlineAt ? { deadline_at: input.poll.deadlineAt } : {}),
      },
    };
  }
  // 空白 trim 后下发，避免发出空字符串快照；缺勤时不带 key，后端落 null（§2.1：不传则 null）。
  const displayName = input.authorName?.trim();
  if (displayName) data.author_display_name = displayName;
  if (input.authorAvatarUrl) data.author_avatar_url = input.authorAvatarUrl; // empty/null trim 后为空等同无快照
  return backendRequest<CommunityEnvelope<CommunityCreatedTopicDto>>(bbsApiBase(), {
    method: 'POST',
    data,
    signal,
  });
}

export function createCommunityReply(input: CreateCommunityReplyInput, signal?: AbortSignal) {
  // Product users submit structured POLL votes through this write endpoint.
  // Threaded free-text replies are Bot-owned in BBS, so this adapter never sends
  // reply_to_post_id.
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

export function closeCommunityTopic(topicId: string, authorId: string, signal?: AbortSignal) {
  // task 域 API 结帖契约：body 携带 `{author_type, author_id}`，后端校验声明作者=主题存储作者 → 不符 403。
  // 产品端结帖仅对「本人（Human）发布的开放主题」开放（前端按 author_id 推导 canClose），故 author_type 固定 'HUMAN'。
  return backendRequest<CommunityEnvelope<null | { topic_id?: string; status?: string }>>(
    `${bbsApiBase()}/${encodeURIComponent(topicId)}/close`,
    { method: 'POST', data: { author_type: 'HUMAN', author_id: authorId }, signal },
  );
}
