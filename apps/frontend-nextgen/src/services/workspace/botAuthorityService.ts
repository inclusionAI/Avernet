import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import {
  acceptBotOwnershipTransfer as acceptBotOwnershipTransferApi,
  cancelBotOwnershipTransfer as cancelBotOwnershipTransferApi,
  createBotOwnershipTransfer as createBotOwnershipTransferApi,
  getBotOwnership as getBotOwnershipApi,
  grantBotManager as grantBotManagerApi,
  listBotManagers as listBotManagersApi,
  listBotOwnershipTransfers as listTransfersApi,
  rejectBotOwnershipTransfer as rejectBotOwnershipTransferApi,
  revokeBotManager as revokeBotManagerApi,
  type BotManagerGrantedDto,
  type BotManagerRevokedDto,
  type BotOwnershipTransferDto,
  type ListTransferParams,
} from '@/services/backendApi/collaboration/botOwnershipController';
import { isEnvelopeSuccessAnyDialect, type BackendApiEnvelope } from '@/services/backendApi/types';
import { readBotAccessRelation } from '@/services/collaborationPrivacy/mappers';
import { applyIdentityLoadResult } from './identityStore';
import { identityService, type DomainError, type DomainResult } from './identityService';

export type BotAccessRelation = 'owner' | 'manager';

/** mine owner∪manager 行的 authority 快照（角色与 created_by 仅排障透传）。 */
export interface BotAuthorityView {
  botId: string;
  name?: string;
  accessRelation: BotAccessRelation;
  createdBy?: string;
}

export interface BotOwnershipState {
  botId: string;
  ownerUserId: string;
  ownershipVersion: number;
}

export interface BotManagerView {
  botId: string;
  /** 当前 owner 单独呈现；永不混入 managers。 */
  ownerUserId: string;
  managers: Array<{ userId: string; actorId: string }>;
  total: number;
  offset: number;
  limit: number;
}

export interface BotOwnershipTransferView {
  transferId: string;
  botId: string;
  fromUserId: string;
  toUserId: string;
  status: BotOwnershipTransferDto['status'];
  expectedOwnerVersion: number;
  resultOwnerVersion?: number;
  expiresAt: number;
  decidedBy?: string;
  decidedAt?: number;
  terminalReason?: BotOwnershipTransferDto['terminal_reason'];
  botNameSnapshot: string;
}

// mine Bot 行缺 access_relation = 合同错误：宁失败也不补默认 owner。
export const AUTHORITY_CONTRACT_ERROR = 'AUTHORITY_CONTRACT_INVALID';

export type AuthorityErrorCode =
  | typeof AUTHORITY_CONTRACT_ERROR
  | 'OWNERSHIP_FORBIDDEN'
  | 'OWNERSHIP_CHANGED'
  | 'OWNERSHIP_TRANSFER_PENDING'
  | 'OWNERSHIP_TRANSFER_FAILED'
  | 'OWNERSHIP_LOAD_FAILED'
  | 'MANAGER_GRANT_FAILED'
  | 'MANAGER_REVOKE_FAILED';

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim());
}

/** envelope data 错误体里的机器码（ErrorEnvelope: data.data.error_code）。 */
function readMachineErrorCode(error: unknown): string | undefined {
  const envelope = asRecord((error as { data?: unknown })?.data);
  const errorBody = asRecord(envelope?.data);
  const code = errorBody?.error_code;
  return typeof code === 'string' ? code : undefined;
}

interface OwnershipErrorContext {
  fallbackCode: AuthorityErrorCode;
  fallbackMessage: string;
}

/** 统一错误归类：403 → 权限刷新；409 机器码 → 专属错误；否则 fallback。 */
function toAuthorityError(
  error: unknown,
  { fallbackCode, fallbackMessage }: OwnershipErrorContext,
): { error: DomainError; machineCode?: string; forbidden: boolean; ownershipChanged: boolean } {
  const status = (error as { status?: number })?.status;
  const machineCode = readMachineErrorCode(error);
  if (status === 403) {
    return {
      error: {
        code: 'OWNERSHIP_FORBIDDEN',
        friendlyMessage: '当前身份已无该 Bot 的 ownership/manager 权限，请重新选择身份。',
        canRetry: false,
      },
      machineCode,
      forbidden: true,
      ownershipChanged: false,
    };
  }
  if (status === 409 && machineCode === 'ownership_changed') {
    return {
      error: {
        code: 'OWNERSHIP_CHANGED',
        friendlyMessage: 'ownership 已变更，已刷新当前信息，请重试。',
        canRetry: true,
      },
      machineCode,
      forbidden: false,
      ownershipChanged: true,
    };
  }
  if (status === 409 && machineCode === 'ownership_transfer_pending') {
    return {
      error: {
        code: 'OWNERSHIP_TRANSFER_PENDING',
        friendlyMessage: '该 Bot 已有待确认的转交（每 Bot 仅一个 pending），请先取消原转交。',
        canRetry: false,
      },
      machineCode,
      forbidden: false,
      ownershipChanged: false,
    };
  }
  return {
    error: {
      code: fallbackCode,
      friendlyMessage: fallbackMessage,
      canRetry: true,
    },
    machineCode,
    forbidden: false,
    ownershipChanged: false,
  };
}

function unwrapPage<TDto>(
  page: { items?: TDto[] } | null | undefined,
): TDto[] {
  return Array.isArray(page?.items) ? page.items : [];
}

/**
 * 403（失权）后的统一恢复路径：重新拉 mine → applyIdentityLoadResult。
 * 失效的选中视角由 applyIdentityLoadResult 清理（回落到 default active），
 * 全程不伪造 Bot token/身份。
 */
export async function refreshAfterForbidden(): Promise<void> {
  const identities = await identityService.loadIdentities();
  if (identities.ok) applyIdentityLoadResult(identities.data);
}

function newClientRequestId(): string {
  const cryptoRef = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (typeof cryptoRef?.randomUUID === 'function') return cryptoRef.randomUUID();
  // 可用性兜底（UUID 语义仍由后端校验）。
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}-${Math.random()
    .toString(36)
    .slice(2, 12)}`;
}

function mapTransfer(dto: BotOwnershipTransferDto): BotOwnershipTransferView {
  return {
    transferId: dto.transfer_id,
    botId: dto.bot_id,
    fromUserId: dto.from_user_id,
    toUserId: dto.to_user_id,
    status: dto.status,
    expectedOwnerVersion: dto.expected_owner_version,
    ...(dto.result_owner_version ? { resultOwnerVersion: dto.result_owner_version } : {}),
    expiresAt: dto.expires_at,
    ...(typeof dto.decided_by === 'string' ? { decidedBy: dto.decided_by } : {}),
    ...(typeof dto.decided_at === 'number' ? { decidedAt: dto.decided_at } : {}),
    ...(dto.terminal_reason ? { terminalReason: dto.terminal_reason } : {}),
    botNameSnapshot: dto.bot_name_snapshot,
  };
}

/**
 * BCS Bot ownership/manager 控制面的唯一服务入口（组件只消费这里的结果；
 * UI 不直接调用内部 team endpoints，也不自行推断当前 owner）。
 * 角色与 sources 结果（remaining_team_sources 等）原样保留，不做本地推断。
 */
export const botAuthorityService = {
  /** mine owner∪manager 行的 authority 快照。模块级单飞，resolve 后释放。 */
  loadAuthorities(): Promise<DomainResult<BotAuthorityView[]>> {
    if (!botAuthorityState.inflight) {
      botAuthorityState.inflight = this.doLoadAuthorities().finally(() => {
        botAuthorityState.inflight = null;
      });
    }
    return botAuthorityState.inflight;
  },

  async doLoadAuthorities(): Promise<DomainResult<BotAuthorityView[]>> {
    try {
      const resp = await listMyBots({ offset: 0, limit: 50 });
      if (!isEnvelopeSuccessAnyDialect(resp) || !Array.isArray(resp.data?.items)) {
        return {
          ok: false,
          error: {
            code: 'OWNERSHIP_LOAD_FAILED',
            friendlyMessage: '加载 Bot authority 失败，请稍后重试。',
            canRetry: true,
          },
        };
      }
      const authorities: BotAuthorityView[] = [];
      for (const item of resp.data.items) {
        const record = asRecord(item);
        const botId = [record?.bot_id, record?.id].find(isNonEmptyString)?.trim();
        if (!record || !botId) continue;
        const relation = readBotAccessRelation(record);
        // Bot 行缺 access_relation = mine 合同错误：不默认 owner、不从
        // created_by 推断，直接判定失败。
        if (record.kind !== 'human' && !relation) {
          return {
            ok: false,
            error: {
              code: AUTHORITY_CONTRACT_ERROR,
              friendlyMessage: 'mine 接口缺少 access_relation 角色标签（合同错误），请升级后重试。',
              canRetry: false,
            },
          };
        }
        authorities.push({
          botId,
          ...(isNonEmptyString(record.name) ? { name: record.name.trim() } : {}),
          // human 自显行是兼容投影，应为 owner 标签；Bot 行已在上方合同检查。
          accessRelation: record.kind === 'human' ? 'owner' : relation!,
          ...(isNonEmptyString(record.created_by) ? { createdBy: record.created_by } : {}),
        });
      }
      return { ok: true, data: authorities };
    } catch {
      return {
        ok: false,
        error: {
          code: 'OWNERSHIP_LOAD_FAILED',
          friendlyMessage: '加载 Bot authority 失败，请稍后重试。',
          canRetry: true,
        },
      };
    }
  },

  /** 读当前 owner + ownership_version（initiate 必须用它的快照）。 */
  async getOwnership(botId: string): Promise<DomainResult<BotOwnershipState>> {
    try {
      const resp = await getBotOwnershipApi(botId);
      if (!isEnvelopeSuccessAnyDialect(resp) || !resp.data) {
        return {
          ok: false,
          error: {
            code: 'OWNERSHIP_LOAD_FAILED',
            friendlyMessage: '加载 ownership 失败，请稍后重试。',
            canRetry: true,
          },
        };
      }
      const dto = resp.data;
      return {
        ok: true,
        data: {
          botId: dto.bot_id,
          ownerUserId: dto.owner_user_id,
          ownershipVersion: dto.ownership_version,
        },
      };
    } catch (error) {
      const mapped = toAuthorityError(error, {
        fallbackCode: 'OWNERSHIP_LOAD_FAILED',
        fallbackMessage: '加载 ownership 失败，请稍后重试。',
      });
      if (mapped.forbidden) await refreshAfterForbidden();
      return { ok: false, error: mapped.error };
    }
  },

  /** 显式 manager 列表（owner 独立字段；team/ownership_transfer 来源由后端聚合）。 */
  async listManagers(botId: string): Promise<DomainResult<BotManagerView>> {
    try {
      const resp = await listBotManagersApi(botId, { offset: 0, limit: 50 });
      if (!isEnvelopeSuccessAnyDialect(resp) || !resp.data) {
        return {
          ok: false,
          error: {
            code: 'OWNERSHIP_LOAD_FAILED',
            friendlyMessage: '加载管理者列表失败，请稍后重试。',
            canRetry: true,
          },
        };
      }
      const dto = resp.data;
      return {
        ok: true,
        data: {
          botId: dto.bot_id,
          ownerUserId: dto.owner_user_id,
          managers: (dto.items ?? []).map((entry) => ({
            userId: entry.user_id,
            actorId: entry.actor_id,
          })),
          total: dto.total,
          offset: dto.offset,
          limit: dto.limit,
        },
      };
    } catch (error) {
      const mapped = toAuthorityError(error, {
        fallbackCode: 'OWNERSHIP_LOAD_FAILED',
        fallbackMessage: '加载管理者列表失败，请稍后重试。',
      });
      if (mapped.forbidden) await refreshAfterForbidden();
      return { ok: false, error: mapped.error };
    }
  },

  async grantManager(botId: string, userId: string): Promise<DomainResult<BotManagerGrantedDto>> {
    try {
      const resp = await grantBotManagerApi(botId, userId);
      if (!isEnvelopeSuccessAnyDialect(resp) || !resp.data) {
        return {
          ok: false,
          error: {
            code: 'MANAGER_GRANT_FAILED',
            friendlyMessage: '添加管理者失败，请稍后重试。',
            canRetry: true,
          },
        };
      }
      return { ok: true, data: resp.data };
    } catch (error) {
      const mapped = toAuthorityError(error, {
        fallbackCode: 'MANAGER_GRANT_FAILED',
        fallbackMessage: '添加管理者失败，请稍后重试。',
      });
      if (mapped.forbidden) await refreshAfterForbidden();
      return { ok: false, error: mapped.error };
    }
  },

  /**
   * 幂等 revoke。返回原始结果：revoked=true 不代表总失权——
   * remaining_team_sources 非空时 subject 仍通过团队来源保有 manager 权限；
   * 因此本方法绝不修改身份列表/视角（不本地移除）。
   */
  async revokeManager(botId: string, userId: string): Promise<DomainResult<BotManagerRevokedDto>> {
    try {
      const resp = await revokeBotManagerApi(botId, userId);
      if (!isEnvelopeSuccessAnyDialect(resp) || !resp.data) {
        return {
          ok: false,
          error: {
            code: 'MANAGER_REVOKE_FAILED',
            friendlyMessage: '移除管理者失败，请稍后重试。',
            canRetry: true,
          },
        };
      }
      // remaining_team_sources 原样保留给消费方（不合并、不排序、不删减）。
      return { ok: true, data: resp.data };
    } catch (error) {
      const mapped = toAuthorityError(error, {
        fallbackCode: 'MANAGER_REVOKE_FAILED',
        fallbackMessage: '移除管理者失败，请稍后重试。',
      });
      if (mapped.forbidden) await refreshAfterForbidden();
      return { ok: false, error: mapped.error };
    }
  },

  /**
   * 发起转交。client_request_id 由服务层按 bot 托管：提交成功响应丢失后，
   * 同参重试沿用同一把 key（201-once / 200-replay）；拿到 committed receipt
   * 后 key 用尽，下次全新发起换新 key。409 ownership_changed 时自动重读
   * ownership 快照并归类 OWNERSHIP_CHANGED。
   */
  async createOwnershipTransfer(
    botId: string,
    toUserId: string,
    expectedOwnerVersion: number,
  ): Promise<DomainResult<BotOwnershipTransferView>> {
    let clientRequestId = botAuthorityState.clientRequestIds.get(botId);
    if (!clientRequestId) {
      clientRequestId = newClientRequestId();
      botAuthorityState.clientRequestIds.set(botId, clientRequestId);
    }
    try {
      const resp = await createBotOwnershipTransferApi(botId, {
        to_user_id: toUserId,
        expected_owner_version: expectedOwnerVersion,
        client_request_id: clientRequestId,
      });
      if (!isEnvelopeSuccessAnyDialect(resp) || !resp.data) {
        return {
          ok: false,
          error: {
            code: 'OWNERSHIP_TRANSFER_FAILED',
            friendlyMessage: 'ownership 转交提交失败，请重试',
            canRetry: true,
          },
        };
      }
      // receipt 已 committed：幂等 key 用尽。
      botAuthorityState.clientRequestIds.delete(botId);
      return { ok: true, data: mapTransfer(resp.data) };
    } catch (error) {
      const mapped = toAuthorityError(error, {
        fallbackCode: 'OWNERSHIP_TRANSFER_FAILED',
        fallbackMessage: 'ownership 转交提交失败，请重试',
      });
      // 重试仍用同一把幂等 key（响应丢失 ≠ 服务端未提交）。
      if (mapped.forbidden) {
        botAuthorityState.clientRequestIds.delete(botId);
        await refreshAfterForbidden();
      }
      if (mapped.ownershipChanged) {
        // 版本快照已过期：丢弃该次提交的幂等 key，
        // 由消费方重读 ownership 后重新发起（stale version 不重试）。
        botAuthorityState.clientRequestIds.delete(botId);
      }
      return { ok: false, error: mapped.error };
    }
  },

  /** 收/发件箱（direction=received|sent）。 */
  async listTransfers(
    direction: 'received' | 'sent',
    params: Omit<ListTransferParams, 'direction'> = {},
  ): Promise<DomainResult<BotOwnershipTransferView[]>> {
    try {
      const resp = await listTransfersApi({ direction, ...params });
      if (!isEnvelopeSuccessAnyDialect(resp)) {
        return {
          ok: false,
          error: {
            code: 'OWNERSHIP_LOAD_FAILED',
            friendlyMessage: '加载转交收发件失败，请稍后重试。',
            canRetry: true,
          },
        };
      }
      return { ok: true, data: unwrapPage(resp.data).map(mapTransfer) };
    } catch (error) {
      const mapped = toAuthorityError(error, {
        fallbackCode: 'OWNERSHIP_LOAD_FAILED',
        fallbackMessage: '加载转交收发件失败，请稍后重试。',
      });
      return { ok: false, error: mapped.error };
    }
  },

  /** 接收人确认。成功响应丢失的重试会拿到原始 receipt（服务端幂等）。 */
  async acceptTransfer(transferId: string): Promise<DomainResult<BotOwnershipTransferView>> {
    return this.decideTransfer(() => acceptBotOwnershipTransferApi(transferId), 'OWNERSHIP_TRANSFER_FAILED');
  },

  async rejectTransfer(transferId: string): Promise<DomainResult<BotOwnershipTransferView>> {
    return this.decideTransfer(() => rejectBotOwnershipTransferApi(transferId), 'OWNERSHIP_TRANSFER_FAILED');
  },

  async cancelTransfer(transferId: string): Promise<DomainResult<BotOwnershipTransferView>> {
    return this.decideTransfer(() => cancelBotOwnershipTransferApi(transferId), 'OWNERSHIP_TRANSFER_FAILED');
  },

  async decideTransfer(
    action: () => Promise<BackendApiEnvelope<BotOwnershipTransferDto>>,
    fallbackCode: AuthorityErrorCode,
  ): Promise<DomainResult<BotOwnershipTransferView>> {
    try {
      const resp = await action();
      if (!isEnvelopeSuccessAnyDialect(resp) || !resp.data) {
        return {
          ok: false,
          error: {
            code: fallbackCode,
            friendlyMessage: 'ownership 转交操作失败，请重试。',
            canRetry: true,
          },
        };
      }
      return { ok: true, data: mapTransfer(resp.data) };
    } catch (error) {
      const mapped = toAuthorityError(error, {
        fallbackCode,
        fallbackMessage: 'ownership 转交操作失败，请重试。',
      });
      return { ok: false, error: mapped.error };
    }
  },
};

/** 模块级状态：单飞 + 每 Bot 幂等 key（按既有 service 模式，无全局写状态）。 */
const botAuthorityState: {
  inflight: Promise<DomainResult<BotAuthorityView[]>> | null;
  clientRequestIds: Map<string, string>;
} = {
  inflight: null,
  clientRequestIds: new Map<string, string>(),
};
