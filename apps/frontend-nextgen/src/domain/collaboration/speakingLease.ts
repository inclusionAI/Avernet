/**
 * BCS 群聊发言权（speaking lease）纯领域：协议类型、帧解析与可用性判定。
 *
 * 纯领域逻辑，无 I/O：租约持有者由 BCS 从认证 Human 连接解析，前端只回传
 * 服务端给出的 leaseId/epoch；chat.send 注入与 UI 状态映射均基于这里的纯函数。
 */

/** 服务端返回的租约快照；holderActorId 为业务 actor id（如 human_1）。 */
export interface SpeakingLease {
  leaseId: string;
  epoch: number;
  expiresAtMs: number;
  holderActorId?: string;
}

/** lease.* 应答的协议六态。 */
export type LeaseCommandStatus =
  | 'granted'
  | 'self_held'
  | 'occupied'
  | 'held'
  | 'none'
  | 'released';

/** 一次 lease 指令的归一化结果。 */
export interface LeaseCommandResult {
  status: LeaseCommandStatus;
  lease?: SpeakingLease;
  holderActorId?: string;
}

/** Workbench WS 应答帧（租约指令只按请求 id 路由 res 帧）。 */
export interface LeaseResponseFrame {
  type?: string;
  id?: string;
  ok?: boolean;
  payload?: Record<string, unknown>;
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}

/** connect 应答携带的会话发言模式；仅用于 UI 条件渲染，消息提交仍由服务端逐条判定。 */
export type SpeakingMode = 'plain' | 'lease_required';

/** 发言权面板的 UI 四态（由 LeaseCommandResult 映射而来）。 */
export type SpeakingLeaseStatus = 'unheld' | 'held' | 'occupied' | 'error';

/** 租约到期前的提前失效量：剩余 ≤ 该窗口即视为不可用，不注入 chat.send。 */
export const LEASE_REFRESH_LEAD_MS = 5_000;

/** lease.* 指令等待应答超时（毫秒）。 */
export const LEASE_COMMAND_TIMEOUT_MS = 10_000;

/** lease 指令失败：code 兼容服务端 error.code 与本地 lease_timeout。 */
export class LeaseCommandError extends Error {
  readonly code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = 'LeaseCommandError';
    this.code = code;
  }
}

/**
 * 归一化 lease 应答 payload。后端字段刻意混拼（leaseId/epoch/holderActorId 为
 * camelCase，expiresAt_ms 为 snake_case），这里双兼容全部拼写；leaseId 非空且
 * epoch > 0 才构造 lease，否则 lease 为 undefined（occupied 分支仅有顶层
 * holderActorId/epoch/expiresAt_ms）。
 */
export function normalizeLeasePayload(
  payload: Record<string, unknown> | undefined,
): LeaseCommandResult {
  const status = payload?.status as LeaseCommandStatus | undefined;
  if (!status) throw new LeaseCommandError('lease response has no status');
  const leaseId = (payload?.leaseId ?? payload?.lease_id) as unknown;
  const epoch = Number(payload?.epoch ?? 0);
  const expiresAtMs = Number(
    payload?.expiresAt_ms ?? payload?.expiresAtMs ?? 0,
  );
  const holderActorId = (payload?.holderActorId ?? payload?.holder_actor_id) as
    | string
    | undefined;
  const lease =
    typeof leaseId === 'string' &&
    leaseId &&
    Number.isFinite(epoch) &&
    epoch > 0
      ? { leaseId, epoch, expiresAtMs, holderActorId }
      : undefined;
  return { status, lease, holderActorId };
}

/** 可注入 chat.send 的租约参数；lease 缺失或临期（≤5s）返回 undefined。 */
export function getChatSendLeaseParams(
  lease: SpeakingLease | undefined,
  nowMs: number,
): { leaseId: string; leaseEpoch: number } | undefined {
  if (!lease || lease.expiresAtMs <= nowMs + LEASE_REFRESH_LEAD_MS)
    return undefined;
  return { leaseId: lease.leaseId, leaseEpoch: lease.epoch };
}

/** 距过期 > 提前量即视为可用（等于注入条件）。 */
export function isLeaseUsable(
  lease: SpeakingLease | undefined,
  nowMs: number,
): boolean {
  return getChatSendLeaseParams(lease, nowMs) !== undefined;
}

/**
 * 解析 connect 应答的 speaking_mode；非 'lease_required' 一律归一为 'plain'，
 * 缺省（旧服务端）也按 plain 处理——面板不渲染。
 */
export function parseConnectSpeakingMode(
  payload: Record<string, unknown> | undefined,
): SpeakingMode {
  return payload?.speaking_mode === 'lease_required'
    ? 'lease_required'
    : 'plain';
}