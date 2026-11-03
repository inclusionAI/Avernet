/**
 * 发言权（speaking lease）生命周期 service：本 Provider 实例私有
 * （快照/等待随实例隔离）。组件只经 subscribeToSpeakingLease /
 * acquireSpeakingLease 等包装层方法消费，不接触 transport 或协议帧。
 *
 * 与 ask_user 的 GroupInteractionService 不同，lease.* 是纯请求/应答指令：
 * 无事件推送（无 lease.revoked），收敛靠 held/occupied 状态下的 10s
 * lease.current 轮询；租约只由「服务端成功接受的新 Human 消息」续期，
 * 到期前 LEASE_REFRESH_LEAD_MS 即视为不可用（不注入 chat.send）。
 * 状态映射沿用 legacy 语义：holderActorId 判定只有认证连接的服务端可作，
 * 前端只回传服务端给出的 leaseId/epoch。
 */
import {
  LEASE_COMMAND_TIMEOUT_MS,
  LEASE_REFRESH_LEAD_MS,
  LeaseCommandError,
  getChatSendLeaseParams,
  normalizeLeasePayload,
  type LeaseCommandResult,
  type LeaseResponseFrame,
  type SpeakingLease,
  type SpeakingLeaseStatus,
  type SpeakingMode,
} from '@/domain/collaboration/speakingLease';

/** held/occupied 状态下 lease.current 轮询间隔（毫秒）。 */
const LEASE_POLL_INTERVAL_MS = 10_000;

/** 封装层注入的依赖，与 GroupInteractionService 的 send/isConnected 同构。 */
export interface GroupSpeakingLeaseServiceConfig {
  /** 当前群（lease.* params 的 groupId）。 */
  groupId: string;
  /** 本地认证 Human 的业务 actor id（human_{userId}），与服务端 holderActorId 同构。 */
  humanActorId: string;
  /** 出站 lease.* 帧发送（包装层注入的 transport.send）；断线下在发送处拒绝。 */
  send: (frame: unknown) => Promise<unknown>;
  /** 当前连接是否可用（真实连接状态，非 UI 展示状态）。 */
  isConnected: () => boolean;
}

/** 发言权快照：speakingMode 为 connect 应答快照，仅用于 UI 条件渲染。 */
export interface SpeakingLeaseSnapshot {
  /** connect 应答解析的会话发言模式；连接应答到达前为 undefined。 */
  speakingMode: SpeakingMode | undefined;
  /** 当前持有的租约（仅 holder 为本地 Human 时存在）；到期/释放/被抢占后清除。 */
  lease: SpeakingLease | undefined;
  /** UI 四态：unheld / held / occupied / error。 */
  status: SpeakingLeaseStatus;
  /** 是否有 acquire/release 指令在途（按钮 loading）。 */
  requesting: boolean;
}

function newLeaseFrameId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return `lease-${cryptoApi.randomUUID()}`;
  }
  return `lease-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export class GroupSpeakingLeaseService {
  private readonly config: GroupSpeakingLeaseServiceConfig;
  private readonly listeners = new Set<(snapshot: SpeakingLeaseSnapshot) => void>();
  /** 出站 lease 指令 → 等待应答的回调表（按请求 id 路由 res 帧）。 */
  private readonly pending = new Map<string, (frame: LeaseResponseFrame) => void>();
  private state: SpeakingLeaseSnapshot = {
    speakingMode: undefined,
    lease: undefined,
    status: 'unheld',
    requesting: false,
  };
  /** held 状态的到期清零定时器（提前 LEASE_REFRESH_LEAD_MS 到点即丢弃）。 */
  private expiryTimer?: ReturnType<typeof setTimeout>;
  /** held/occupied 状态下的 lease.current 轮询。 */
  private pollTimer?: ReturnType<typeof setInterval>;

  constructor(config: GroupSpeakingLeaseServiceConfig) {
    this.config = config;
  }

  /** 订阅发言权快照；订阅即回放当前状态。 */
  subscribe(listener: (snapshot: SpeakingLeaseSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** connect 应答快照喂入；应答模式经 parseConnectSpeakingMode 归一后才可进来。 */
  setSpeakingMode(mode: SpeakingMode): void {
    if (this.state.speakingMode === mode) return;
    this.state = { ...this.state, speakingMode: mode };
    this.notify();
  }

  /**
   * 连接周期结束（disconnect / 底层断线）时收敛：停轮询与到期定时器，并立结
   * 在途请求（回复 lease_disconnected，各等待自行走 catch 路径）。避免断开的
   * 连接周期继续驱动定时器、在途请求超时后才误报。
   */
  resetConnection(): void {
    if (this.expiryTimer) {
      clearTimeout(this.expiryTimer);
      this.expiryTimer = undefined;
    }
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = undefined;
    }
    for (const [id, resolve] of this.pending) {
      this.pending.delete(id);
      resolve({
        type: 'res',
        id,
        ok: false,
        error: { code: 'lease_disconnected', retryable: true },
      });
    }
  }

  /**
   * 入站帧路由：仅吃 type==='res' 且 id 命中 pending 的租约应答；
   * 其余帧返回 false 由包装层继续既有链路。
   */
  handleFrame(frame: unknown): boolean {
    if (typeof frame !== 'object' || frame === null) return false;
    const candidate = frame as LeaseResponseFrame;
    if (candidate.type !== 'res' || typeof candidate.id !== 'string') return false;
    const resolve = this.pending.get(candidate.id);
    if (!resolve) return false;
    this.pending.delete(candidate.id);
    resolve(candidate);
    return true;
  }

  /** chat.send 可注入的租约参数；lease 缺失或临期（≤5s）返回 undefined。 */
  usableLeaseParams(nowMs: number): { leaseId: string; leaseEpoch: number } | undefined {
    return getChatSendLeaseParams(this.state.lease, nowMs);
  }

  /** 获取（preempt=false）或抢占（preempt=true）发言权；成功返回 true。 */
  async acquire(preempt = false): Promise<boolean> {
    if (!this.config.isConnected()) return false;
    this.setRequesting(true);
    try {
      const result = preempt
        ? await this.command('lease.preempt', { groupId: this.config.groupId })
        : await this.command('lease.acquire', { groupId: this.config.groupId });
      return this.applyLeaseResult(result);
    } catch (error) {
      // 指令失败（发送/超时/服务端拒绝）落 error 态，等待 UI 重试；具体提示由 hook 层负责。
      this.clearLease('error');
      throw error;
    } finally {
      this.setRequesting(false);
    }
  }

  /** 释放当前持有的租约；失败抛错由调用方提示（此刻不清 UI 状态）。 */
  async release(): Promise<void> {
    const lease = this.state.lease;
    if (!this.config.isConnected() || !lease) return;
    this.setRequesting(true);
    try {
      await this.command('lease.release', {
        groupId: this.config.groupId,
        leaseId: lease.leaseId,
        leaseEpoch: lease.epoch,
      });
    } finally {
      this.setRequesting(false);
    }
    this.clearLease('unheld');
  }

  /** lease.current 补拍快照；失败落 error 态（轮询期间同样收敛于此）。 */
  async refresh(): Promise<void> {
    if (!this.config.isConnected()) return;
    try {
      const result = await this.command('lease.current', {
        groupId: this.config.groupId,
      });
      this.applyLeaseResult(result);
    } catch (error) {
      this.clearLease('error');
      console.error('[GroupSpeakingLeaseService] refresh failed:', error);
    }
  }

  private async command(
    method: string,
    params: Record<string, unknown>,
  ): Promise<LeaseCommandResult> {
    const id = newLeaseFrameId();
    let settleFrame!: (frame: LeaseResponseFrame) => void;
    let rejectFrame!: (error: unknown) => void;
    let timeout!: ReturnType<typeof setTimeout>;

    const responsePromise = new Promise<LeaseResponseFrame>((resolve, reject) => {
      settleFrame = resolve;
      rejectFrame = reject;
      timeout = setTimeout(() => {
        if (this.pending.delete(id)) {
          reject(new LeaseCommandError('lease request timeout', 'lease_timeout'));
        }
      }, LEASE_COMMAND_TIMEOUT_MS);
      this.pending.set(id, (frame) => {
        clearTimeout(timeout);
        this.pending.delete(id);
        settleFrame(frame);
      });
    });

    try {
      await this.config.send({ type: 'req', id, method, params });
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
    return normalizeLeasePayload(frame.payload);
  }

  /**
   * 协议结果 → UI 四态：occupied 顶层持有者占位；holder 为本地 Human →
   * held；none/released/无 lease → unheld；其余（有 holder 但非本人）→
   * occupied。返回是否已由本人持有。
   */
  private applyLeaseResult(result: LeaseCommandResult): boolean {
    if (result.status === 'occupied') {
      this.clearLease('occupied');
      return false;
    }
    const lease = result.lease;
    if (lease && lease.holderActorId === this.config.humanActorId) {
      this.state = { ...this.state, lease, status: 'held' };
      this.scheduleExpiry(lease);
      this.updatePoll();
      this.notify();
      return true;
    }
    if (result.status === 'none' || result.status === 'released' || !lease) {
      this.clearLease('unheld');
      return false;
    }
    this.clearLease('occupied');
    return false;
  }

  /** 清租约并落指定状态；负责停轮询、清到期定时器。 */
  private clearLease(status: SpeakingLeaseStatus): void {
    this.state = { ...this.state, lease: undefined, status };
    if (this.expiryTimer) {
      clearTimeout(this.expiryTimer);
      this.expiryTimer = undefined;
    }
    this.updatePoll();
    this.notify();
  }

  /** held 提前量到点自动丢弃：临期租约不再注入 chat.send。 */
  private scheduleExpiry(lease: SpeakingLease): void {
    if (this.expiryTimer) clearTimeout(this.expiryTimer);
    const delay = Math.max(0, lease.expiresAtMs - LEASE_REFRESH_LEAD_MS - Date.now()) + 1;
    this.expiryTimer = setTimeout(() => {
      this.expiryTimer = undefined;
      this.clearLease('unheld');
    }, delay);
  }

  /** 仅 held/occupied 期间轮询；无 WS 推送时以此收敛被抢占等变化。 */
  private updatePoll(): void {
    const shouldPoll =
      this.state.status === 'held' || this.state.status === 'occupied';
    if (shouldPoll && !this.pollTimer) {
      this.pollTimer = setInterval(() => {
        // 断线时跳过本轮（指令会被发送处拒绝），不断言错误态。
        if (this.config.isConnected()) void this.refresh();
      }, LEASE_POLL_INTERVAL_MS);
    } else if (!shouldPoll && this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = undefined;
    }
  }

  private setRequesting(requesting: boolean): void {
    if (this.state.requesting === requesting) return;
    this.state = { ...this.state, requesting };
    this.notify();
  }

  private notify(): void {
    this.state = { ...this.state };
    this.listeners.forEach((listener) => listener(this.state));
  }
}