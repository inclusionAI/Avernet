import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/**
 * 渠道绑定（钉钉机器人等）OpenAPI 控制器。
 *
 * 后端路由挂载在 `/openapi/v1/collaboration` 下（见 Avernet
 * `bcs-api-http/src/v1/openapi/routes/channel.rs`），四个端点：
 *   POST   /channels/bindings              创建绑定（201, data: ChannelBindingDto）
 *   GET    /channels/bindings/by-target    按 target 查询（200, data: { items: ChannelBindingDto[] }）
 *   PATCH  /channels/bindings/{id}         更新（XOR: {active} 启停 / {config} 全量替换配置；200, data: null）
 *   DELETE /channels/bindings/{id}         删除（200, data: null）
 *
 * 约束：POST/PATCH 均 `deny_unknown_fields`，前端不得传 `env`（后端自用运行时 env，
 * 传了 400）。鉴权由会话承担，`created_by` 由后端注入，前端不传。
 */

const BASE = '/openapi/v1/collaboration/channels/bindings';
const BY_TARGET = `${BASE}/by-target`;

/** 钉钉 config.send_mode：普通文本 / 流式卡片。 */
export type DingTalkSendMode =
  | { mode: 'normal'; message_type: 'markdown' }
  | { mode: 'streaming_card'; card_template_id: string; fallback_message_type: 'markdown' };

/** 钉钉渠道配置（POST/PATCH 的 config 子对象，全 snake_case）。 */
export interface DingTalkConfigPayload {
  robot_code: string;
  client_id: string;
  client_secret: string;
  send_mode: DingTalkSendMode;
}

export type BindingTargetType = 'bot' | 'group';
export type ChannelVisibility = 'full_transcript' | 'lead_only';
export type ChannelGroupChatScope = 'conversation_shared' | 'per_sender';
export type ChannelBindingStatus = 'active' | 'disabled';

export interface BindingTarget {
  group?: { group_id: string };
  bot?: { bot_id: string };
}

/** POST body。严禁 `env`（后端 deny_unknown_fields）。 */
export interface CreateChannelBindingRequest {
  channel_type: 'dingtalk';
  account_ref: string;
  target: BindingTarget;
  group_chat_scope?: ChannelGroupChatScope;
  outbound_visibility: ChannelVisibility;
  config: DingTalkConfigPayload;
}

/** PATCH body：`active` 与 `config` 互斥（恰好传一个）。 */
export interface UpdateChannelBindingRequest {
  active?: boolean;
  config?: DingTalkConfigPayload;
}

/** GET/POST 返回的绑定记录（config 已被后端脱敏，client_secret 回显为 `<redacted>`）。 */
export interface ChannelBindingDto {
  id: string;
  channel_type: string;
  account_ref: string;
  target: BindingTarget;
  group_chat_scope?: ChannelGroupChatScope | null;
  outbound_visibility: ChannelVisibility;
  env: string;
  status: ChannelBindingStatus;
  created_by?: string | null;
  config: {
    robot_code?: string;
    client_id?: string;
    client_secret?: string;
    send_mode?: DingTalkSendMode;
  };
}

export interface ChannelBindingPage {
  items: ChannelBindingDto[];
}

/** 按 target 查询绑定列表。channel_type 省略时后端返回全部渠道。 */
export async function listBindingsByTarget(query: {
  target_type: BindingTargetType;
  target_id: string;
  channel_type?: string;
}) {
  return backendRequest<BackendApiEnvelope<ChannelBindingPage>>(BY_TARGET, {
    method: 'GET',
    params: query as Record<string, unknown>,
    injectUserId: false,
  });
}

/** 创建绑定。 */
export async function createChannelBinding(body: CreateChannelBindingRequest) {
  return backendRequest<BackendApiEnvelope<ChannelBindingDto>>(BASE, {
    method: 'POST',
    data: body,
    injectUserId: false,
  });
}

/** 更新绑定：`{active}` 启停 或 `{config}` 全量替换配置（二者互斥）。 */
export async function updateChannelBinding(id: string, body: UpdateChannelBindingRequest) {
  return backendRequest<BackendApiEnvelope<null>>(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    data: body,
    injectUserId: false,
  });
}

/** 删除绑定。 */
export async function deleteChannelBinding(id: string) {
  return backendRequest<BackendApiEnvelope<null>>(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    injectUserId: false,
  });
}
