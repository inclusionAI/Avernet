import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage } from '../types';

/**
 * BCS Bot ownership/manager 控制面契约（Human 控制；见 api-contracts/v1 bots.yaml）：
 * - get：单一有效 owner + optimistic-concurrency ownership_version；
 * - managers：owner 独立字段 + 显式 manager 分页；grant/revoke 幂等且不动 team/* 来源；
 * - ownership-transfers：确认式转交 receipt（历史记录，永不命名 current_owner）。
 */

export type BotOwnershipTransferStatus =
  | 'pending'
  | 'accepted'
  | 'rejected'
  | 'cancelled'
  | 'expired'
  | 'invalidated';

export interface BotOwnershipDto {
  bot_id: string;
  owner_user_id: string;
  ownership_version: number;
}

export interface BotManagerEntryDto {
  user_id: string;
  actor_id: string;
  role: 'manager';
}

export interface BotManagerPageDto {
  bot_id: string;
  owner_user_id: string;
  items: BotManagerEntryDto[];
  total: number;
  offset: number;
  limit: number;
}

export interface BotManagerGrantedDto {
  bot_id: string;
  user_id: string;
  role: 'manager';
  changed: boolean;
}

export interface BotManagerRevokedDto {
  bot_id: string;
  user_id: string;
  revoked: boolean;
  /** 直接 DELETE 不触碰的 team/* 来源；非空 = 仍保留 manager 权限。 */
  remaining_team_sources: string[];
}

export interface CreateOwnershipTransferBody {
  to_user_id: string;
  expected_owner_version: number;
  /** UUID 幂等键：首次 201、同键同载荷重放 200（同键不同载荷 409）。 */
  client_request_id: string;
}

export interface BotOwnershipTransferDto {
  transfer_id: string;
  bot_id: string;
  from_user_id: string;
  to_user_id: string;
  status: BotOwnershipTransferStatus;
  expected_owner_version: number;
  result_owner_version?: number;
  expires_at: number;
  decided_by?: string;
  decided_at?: number;
  terminal_reason?: 'owner_changed' | 'bot_deleted' | 'actor_unavailable';
  bot_name_snapshot: string;
  gmt_create: number;
  gmt_modified: number;
}

export interface ListTransferParams {
  direction?: 'received' | 'sent';
  status?: BotOwnershipTransferStatus;
  offset?: number;
  limit?: number;
}

export const BOT_OWNERSHIP_ENDPOINTS = {
  ownership: (bot_id: string) => `/openapi/v1/collaboration/bots/${bot_id}/ownership`,
  managers: (bot_id: string) => `/openapi/v1/collaboration/bots/${bot_id}/managers`,
  manager: (bot_id: string, user_id: string) =>
    `/openapi/v1/collaboration/bots/${bot_id}/managers/${user_id}`,
  botTransfers: (bot_id: string) =>
    `/openapi/v1/collaboration/bots/${bot_id}/ownership-transfers`,
  transfers: () => '/openapi/v1/collaboration/ownership-transfers',
  transfer: (transfer_id: string) =>
    `/openapi/v1/collaboration/ownership-transfers/${transfer_id}`,
  transferAction: (transfer_id: string, action: 'accept' | 'reject' | 'cancel') =>
    `/openapi/v1/collaboration/ownership-transfers/${transfer_id}/${action}`,
};

// 读当前 owner + ownership_version；未初始化返回 409 ownership_not_initialized。
export async function getBotOwnership(bot_id: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotOwnershipDto>>(
    BOT_OWNERSHIP_ENDPOINTS.ownership(bot_id),
    { method: 'GET', injectUserId: false, signal },
  );
}

// 显式 manager 分页：owner 单独在 owner_user_id，owner 永不混入 items。
export async function listBotManagers(
  bot_id: string,
  params: { offset?: number; limit?: number } = {},
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BotManagerPageDto>>(
    BOT_OWNERSHIP_ENDPOINTS.managers(bot_id),
    {
      method: 'GET',
      params: params as Record<string, unknown>,
      injectUserId: false,
      signal,
    },
  );
}

// 幂等 DIRECT-lane grant（无业务 body；team 参数被契约拒绝）。
export async function grantBotManager(bot_id: string, user_id: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotManagerGrantedDto>>(
    BOT_OWNERSHIP_ENDPOINTS.manager(bot_id, user_id),
    { method: 'PUT', injectUserId: false, signal },
  );
}

// 幂等 revoke：只撤 direct/ownership_transfer 来源，team/* 来源原样保留并回传。
export async function revokeBotManager(bot_id: string, user_id: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotManagerRevokedDto>>(
    BOT_OWNERSHIP_ENDPOINTS.manager(bot_id, user_id),
    { method: 'DELETE', injectUserId: false, signal },
  );
}

// 发起转交：carrier 幂等键 + 刚读到的 ownership_version 快照。
export async function createBotOwnershipTransfer(
  bot_id: string,
  body: CreateOwnershipTransferBody,
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BotOwnershipTransferDto>>(
    BOT_OWNERSHIP_ENDPOINTS.botTransfers(bot_id),
    { method: 'POST', data: body, injectUserId: false, signal },
  );
}

// 收发件箱：direction=received（默认）| sent；expired 过滤为期限感知。
export async function listBotOwnershipTransfers(
  params: ListTransferParams = {},
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotOwnershipTransferDto>>>(
    BOT_OWNERSHIP_ENDPOINTS.transfers(),
    {
      method: 'GET',
      params: params as Record<string, unknown>,
      injectUserId: false,
      signal,
    },
  );
}

// 以下三个 bodyless 动作：空 JSON 对象即契约（任何业务 body 被 400 拒绝）。
export async function acceptBotOwnershipTransfer(transfer_id: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotOwnershipTransferDto>>(
    BOT_OWNERSHIP_ENDPOINTS.transferAction(transfer_id, 'accept'),
    { method: 'POST', data: {}, injectUserId: false, signal },
  );
}

export async function rejectBotOwnershipTransfer(transfer_id: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotOwnershipTransferDto>>(
    BOT_OWNERSHIP_ENDPOINTS.transferAction(transfer_id, 'reject'),
    { method: 'POST', data: {}, injectUserId: false, signal },
  );
}

export async function cancelBotOwnershipTransfer(transfer_id: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotOwnershipTransferDto>>(
    BOT_OWNERSHIP_ENDPOINTS.transferAction(transfer_id, 'cancel'),
    { method: 'POST', data: {}, injectUserId: false, signal },
  );
}
