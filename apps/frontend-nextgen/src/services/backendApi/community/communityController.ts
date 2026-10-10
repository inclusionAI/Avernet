import { getCapabilities } from '@/capabilities';
import type { CommunityReplyDto, CommunityTopicDto } from '@/domain/community/mapper';
import type { CommunityReplyPager, CommunityTopicQuery, CreateCommunityTopicInput } from '@/domain/community/types';
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

/** 默认每页条数（列表首屏、加载更多、回帖分页统一）。 */
const DEFAULT_PAGE_SIZE = 20;

/**
 * BBS API 路径前缀（list/get/posts/create/close 共用）：由 capability getBbsApiBase 注入。
 * 预发阶段统一走内部 /api Unified 面 `/api/v1/bbs/topics`（agentclawengine-pre 已发布；dev 经 /api/v1/bbs
 * 代理直连 engine，契约同 spec §2.1–§2.6：写口 body 携 author_type+author_id、list 支持 author_id 过滤）。
 * 与 taskController 同范式：capability 缺省回退内部 /api 面。请求期调用确保 capability 已装填。
 */
function bbsApiBase(): string {
  return getCapabilities().getBbsApiBase().value ?? '/api/v1/bbs/topics';
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

export function getCommunityTopic(topicId: string, signal?: AbortSignal) {
  const base = bbsApiBase();
  return backendRequest<CommunityEnvelope<CommunityTopicDto>>(`${base}/${encodeURIComponent(topicId)}`, {
    method: 'GET',
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
  // 内部 /api Unified 面 §2.1 写契约（snake_case，与 openapi §2.1 等同）：基础写体
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
    author_display_name?: string;
    author_avatar_url?: string;
  } = {
    client_request_id: makeClientRequestId(),
    author_type: 'HUMAN',
    author_id: input.authorId,
    title: input.title,
    body: input.body,
  };
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

export function closeCommunityTopic(topicId: string, authorId: string, signal?: AbortSignal) {
  // 内部 /api Unified 面 §2.3 结帖契约：body 携带 `{author_type, author_id}`，后端校验声明作者=主题存储作者 → 不符 403。
  // 产品端结帖仅对「本人（Human）发布的开放主题」开放（前端按 author_id 推导 canClose），故 author_type 固定 'HUMAN'。
  return backendRequest<CommunityEnvelope<null | { topic_id?: string; status?: string }>>(
    `${bbsApiBase()}/${encodeURIComponent(topicId)}/close`,
    { method: 'POST', data: { author_type: 'HUMAN', author_id: authorId }, signal },
  );
}
