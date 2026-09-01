import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type SessionKind = 'chat' | 'service_invocation';
export type SessionStatus = 'running' | 'completed';
export type SessionParticipantMode = 'auto' | 'muted' | 'absent' | 'present';
export interface SessionParticipantDto {
  actor_id: string;
  actor_kind: 'human' | 'bot';
  name?: string;
  role: 'driver' | 'consultant' | 'manager' | 'worker' | 'observer';
  mode: SessionParticipantMode;
  joined_at?: number;
}
export interface SessionDetailData {
  session_id: string;
  version: number;
  group_id: string;
  title?: string;
  status: SessionStatus;
  participants: SessionParticipantDto[];
  /** 会话成员总数（列表接口返回）。 */
  participant_count?: number;
  input?: { query?: string };
  created_at: number;
  updated_at: number;
  kind?: SessionKind;
  collected?: boolean;
  /** 会话创建者 actor_id（bot_id 或 user_id）。 */
  created_by?: string;
  /** 发起调用的主体标识（区分身份维度）。 */
  caller_principal?: string;
}
export interface SessionMessageData {
  id: string;
  timestamp: number;
  /** 发送者标识（bot_id / user_id）。 */
  sender: string;
  /** 消息内容（支持 Markdown）。 */
  content: string;
  /** 发送者类型：bot / human / system。 */
  message_type: 'bot' | 'human' | 'system';
  /** Bot 名称（仅 message_type=bot 时存在，用于展示头像与名称）。 */
  bot_name?: string;
  /** 消息角色（与 SDK ChatMessage.role 对齐）。 */
  role: 'assistant' | 'user' | 'system';
  /** 关联的运行 ID（同一 run_id 的 assistant 消息属于同一轮次）。 */
  run_id?: string;
  /** 附件列表（BCS image 形态：{attachment_id, type, file_name, mime_type, size, url, expires_at}）。 */
  attachments?: SessionMessageAttachment[];
}
/** BCS 图片附件 wire 形态（与 GET messages 返回一致）。 */
export interface SessionMessageAttachment {
  attachment_id: string;
  type: 'image';
  file_name: string;
  mime_type?: string;
  size?: number;
  url: string;
  expires_at?: number;
}
export interface SessionTokenData {
  token: string;
  expires_at: number;
}
export interface SessionCollectData {
  collected: boolean;
}
export interface SessionParticipantDeletedData {
  deleted: boolean;
}

// 查询协作会话详情。
export async function getSession(session_id: string) {
  return backendRequest<BackendApiEnvelope<SessionDetailData>>(`/openapi/v1/collaboration/sessions/${session_id}`, {
    method: 'GET',
    injectUserId: false,
  });
}

// 更新协作会话。
export async function updateSession(session_id: string, body: { title?: string }) {
  return backendRequest<BackendApiEnvelope<SessionDetailData>>(`/openapi/v1/collaboration/sessions/${session_id}`, {
    method: 'PATCH',
    data: body,
    injectUserId: false,
  });
}

// 删除协作会话。
export async function deleteSession(session_id: string) {
  return backendRequest<BackendApiEnvelope<{ deleted: boolean }>>(`/openapi/v1/collaboration/sessions/${session_id}`, {
    method: 'DELETE',
    injectUserId: false,
  });
}

// 收藏协作会话。
export async function collectSession(session_id: string, body: { participant?: string } = {}) {
  return backendRequest<BackendApiEnvelope<SessionCollectData>>(
    `/openapi/v1/collaboration/sessions/${session_id}/collect`,
    { method: 'POST', data: body, injectUserId: false },
  );
}

// 取消收藏协作会话。
export async function uncollectSession(session_id: string, params: { participant?: string } = {}) {
  return backendRequest<BackendApiEnvelope<SessionCollectData>>(
    `/openapi/v1/collaboration/sessions/${session_id}/collect`,
    { method: 'DELETE', params: params as Record<string, unknown>, injectUserId: false },
  );
}

// 查询会话消息列表。data 为扁平消息数组（新→旧，降序）。
// view_bot_id：当前身份对应的 bot/human 的 bot_id。
export async function listSessionMessages(
  session_id: string,
  params: { before?: string; limit?: number; view_bot_id?: string },
) {
  return backendRequest<BackendApiEnvelope<SessionMessageData[]>>(
    `/openapi/v1/collaboration/sessions/${session_id}/messages`,
    { method: 'GET', params: params as Record<string, unknown>, injectUserId: false },
  );
}

// 更新会话成员模式。
export async function updateSessionMemberMode(
  session_id: string,
  actor_id: string,
  body: { mode: SessionParticipantMode },
) {
  return backendRequest<BackendApiEnvelope<SessionParticipantDto>>(
    `/openapi/v1/collaboration/sessions/${session_id}/participants/${actor_id}`,
    { method: 'PATCH', data: body, injectUserId: false },
  );
}

// 在会话中新增成员。
export async function addSessionParticipant(session_id: string, bot_uuid: string) {
  return backendRequest<BackendApiEnvelope<SessionParticipantDto>>(
    `/openapi/v1/collaboration/sessions/${session_id}/participants`,
    { method: 'POST', data: { bot_uuid }, injectUserId: false },
  );
}

// 删除会话成员。
export async function deleteSessionParticipant(session_id: string, bot_uuid: string) {
  return backendRequest<BackendApiEnvelope<SessionParticipantDeletedData>>(
    `/openapi/v1/collaboration/sessions/${session_id}/participants/${bot_uuid}`,
    { method: 'DELETE', injectUserId: false },
  );
}

// 获取会话连接令牌。
export async function createSessionToken(session_id: string): Promise<BackendApiEnvelope<SessionTokenData>> {
  return backendRequest<BackendApiEnvelope<SessionTokenData>>(
    `/openapi/v1/collaboration/sessions/${session_id}/token`,
    { method: 'POST', injectUserId: false },
  );
}

// 供 group 协作嵌套的辅助：会话列表 / 创建
export async function listGroupSessions(
  group_id: string,
  params: { view_bot_id?: string; offset?: number; limit?: number; status?: 'running' | 'completed' } = {},
) {
  return backendRequest<
    BackendApiEnvelope<{ items: SessionDetailData[]; offset: number; limit: number; total: number }>
  >(`/openapi/v1/collaboration/groups/${group_id}/sessions`, {
    method: 'GET',
    params: params as Record<string, unknown>,
    injectUserId: false,
  });
}

export interface CreateSessionRequest {
  title?: string;
  kind?: SessionKind;
  acting_bot_id?: string;
  creator_role?: Exclude<SessionParticipantDto['role'], 'driver'>;
  input?: { query?: string; [key: string]: unknown };
}

export async function createSession(group_id: string, body: CreateSessionRequest, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<SessionDetailData>>(
    `/openapi/v1/collaboration/groups/${group_id}/sessions`,
    { method: 'POST', data: body, injectUserId: false, ...(signal ? { signal } : {}) },
  );
}
