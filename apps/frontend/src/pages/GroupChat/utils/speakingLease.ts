/**
 * Speaking-lease protocol helper for the authenticated Workbench WebSocket.
 *
 * The lease holder is always resolved by BCS from the authenticated Human
 * connection; the frontend only carries the lease id/epoch returned by BCS.
 */

export interface SpeakingLease {
  leaseId: string;
  epoch: number;
  expiresAtMs: number;
  holderActorId?: string;
}

export type LeaseCommandStatus =
  | 'granted'
  | 'self_held'
  | 'occupied'
  | 'held'
  | 'none'
  | 'released';

export interface LeaseCommandResult {
  status: LeaseCommandStatus;
  lease?: SpeakingLease;
  holderActorId?: string;
}

export interface LeaseResponseFrame {
  type?: string;
  id?: string;
  ok?: boolean;
  payload?: Record<string, any>;
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}

interface LeaseTransport {
  send(frame: Record<string, any>): Promise<void>;
}

interface GroupChatLeaseClientOptions {
  generateId?: () => string;
  timeoutMs?: number;
}

const LEASE_REFRESH_LEAD_MS = 5_000;
const DEFAULT_TIMEOUT_MS = 10_000;

export class LeaseCommandError extends Error {
  readonly code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = 'LeaseCommandError';
    this.code = code;
  }
}

function defaultGenerateId(): string {
  const randomUUID = (globalThis as any)?.crypto?.randomUUID;
  if (typeof randomUUID === 'function')
    return randomUUID.call((globalThis as any).crypto);
  return `lease-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function normalizePayload(
  payload: Record<string, any> | undefined,
): LeaseCommandResult {
  const status = payload?.status as LeaseCommandStatus | undefined;
  if (!status) throw new LeaseCommandError('lease response has no status');
  const leaseId = payload?.leaseId ?? payload?.lease_id;
  const epoch = Number(payload?.epoch ?? 0);
  const expiresAtMs = Number(
    payload?.expiresAt_ms ?? payload?.expiresAtMs ?? 0,
  );
  const holderActorId = payload?.holderActorId ?? payload?.holder_actor_id;
  const lease =
    typeof leaseId === 'string' &&
    leaseId &&
    Number.isFinite(epoch) &&
    epoch > 0
      ? { leaseId, epoch, expiresAtMs, holderActorId }
      : undefined;
  return { status, lease, holderActorId };
}

export function getChatSendLeaseParams(
  lease: SpeakingLease | undefined,
  nowMs: number,
): { leaseId: string; leaseEpoch: number } | undefined {
  if (!lease || lease.expiresAtMs <= nowMs + LEASE_REFRESH_LEAD_MS)
    return undefined;
  return { leaseId: lease.leaseId, leaseEpoch: lease.epoch };
}

export function isLeaseUsable(
  lease: SpeakingLease | undefined,
  nowMs: number,
): boolean {
  return getChatSendLeaseParams(lease, nowMs) !== undefined;
}

export class GroupChatLeaseClient {
  private readonly transport: LeaseTransport;
  private readonly generateId: () => string;
  private readonly timeoutMs: number;
  private readonly pending = new Map<
    string,
    (frame: LeaseResponseFrame) => void
  >();

  constructor(
    transport: LeaseTransport,
    options: GroupChatLeaseClientOptions = {},
  ) {
    this.transport = transport;
    this.generateId = options.generateId ?? defaultGenerateId;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  handleFrame(frame: LeaseResponseFrame): boolean {
    if (frame?.type !== 'res' || typeof frame.id !== 'string') return false;
    const resolve = this.pending.get(frame.id);
    if (!resolve) return false;
    this.pending.delete(frame.id);
    resolve(frame);
    return true;
  }

  async acquire(groupId: string): Promise<LeaseCommandResult> {
    return this.command('lease.acquire', { groupId });
  }

  async preempt(groupId: string): Promise<LeaseCommandResult> {
    return this.command('lease.preempt', { groupId });
  }

  async current(groupId: string): Promise<LeaseCommandResult> {
    return this.command('lease.current', { groupId });
  }

  async release(
    lease: SpeakingLease,
    groupId: string,
  ): Promise<LeaseCommandResult> {
    return this.command('lease.release', {
      groupId,
      leaseId: lease.leaseId,
      leaseEpoch: lease.epoch,
    });
  }

  private async command(
    method: string,
    params: Record<string, any>,
  ): Promise<LeaseCommandResult> {
    const id = this.generateId();
    let settleFrame!: (frame: LeaseResponseFrame) => void;
    let rejectFrame!: (error: unknown) => void;
    let timeout!: ReturnType<typeof setTimeout>;

    const responsePromise = new Promise<LeaseResponseFrame>(
      (resolve, reject) => {
        settleFrame = resolve;
        rejectFrame = reject;
        timeout = setTimeout(() => {
          if (this.pending.delete(id)) {
            reject(
              new LeaseCommandError('lease request timeout', 'lease_timeout'),
            );
          }
        }, this.timeoutMs);
        this.pending.set(id, (frame) => {
          clearTimeout(timeout);
          this.pending.delete(id);
          settleFrame(frame);
        });
      },
    );

    try {
      await this.transport.send({ type: 'req', id, method, params });
    } catch (error) {
      clearTimeout(timeout);
      this.pending.delete(id);
      rejectFrame(error);
    }

    const frame = await responsePromise;
    if (frame.ok === false) {
      const error = frame.error ?? {};
      const message = error.code ?? error.message ?? 'lease request failed';
      throw new LeaseCommandError(message, error.code);
    }
    return normalizePayload(frame.payload);
  }
}
