/** Shared interaction lifecycle: scoped frames, ACKs, idempotency, reset and terminal fencing. */
import {
  parseGroupQuestionResolveResponse,
  type GroupQuestionEventIdentity,
  type GroupQuestionInteractionStatus,
  type ParsedGroupQuestionResolveResponse,
} from './groupQuestionFrames';

/** 等待服务端 resolve 响应的超时（毫秒）。 */
const RESOLVE_TIMEOUT_MS = 10_000;

export interface GroupQuestionServiceConfig {
  /** 当前群（connect 的 group_id），入站帧严格相等校验。 */
  groupId: string;
  /** 当前 BCS 会话 ID，入站帧严格相等校验。 */
  bcsSessionId: string;
  /** 出站 interaction.resolve 帧发送（包装层注入的 transport.send）。 */
  send: (frame: unknown) => Promise<unknown>;
  /** 当前连接是否可用（包装层注入的真实连接状态）。 */
  isConnected: () => boolean;
}

/** 快照通知；reason 区分终态移除与连接重置，供 hook 决定草稿去留。 */
export interface GroupInteractionSnapshot<T> {
  reason: 'update' | 'terminal' | 'reset';
  interactions: T[];
}

/** resolve 失败的结构化错误：code 兼容服务端 error.code 与本地 TIMEOUT/SEND_FAILED/DISCONNECTED。 */
export class GroupQuestionResolveError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  /** 失败响应携带的 interaction 状态；本地失败（超时/发送失败）为 null。 */
  readonly interactionStatus: GroupQuestionInteractionStatus | null;

  constructor(fields: {
    code: string;
    message: string;
    retryable: boolean;
    interactionStatus?: GroupQuestionInteractionStatus | null;
  }) {
    super(fields.message);
    this.name = 'GroupQuestionResolveError';
    this.code = fields.code;
    this.retryable = fields.retryable;
    this.interactionStatus = fields.interactionStatus ?? null;
  }
}

interface ResolveRequestWait {
  /** 所属交互 key，用于终态竞态时定位等待。 */
  key: string;
  requestId: string;
  timer: ReturnType<typeof setTimeout>;
  resolve: () => void;
  reject: (error: GroupQuestionResolveError) => void;
}

/** 生成出站 id：requestId/idempotencyKey 全局唯一即可，格式不参与协议。 */
function newFrameId(prefix: string): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return `${prefix}-${cryptoApi.randomUUID()}`;
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isOwnResponseFrame(frame: unknown): frame is { id: unknown } {
  return (
    typeof frame === 'object' &&
    frame !== null &&
    (frame as Record<string, unknown>).type === 'res' &&
    typeof (frame as Record<string, unknown>).id === 'string'
  );
}

interface DeferredOutcome {
  promise: Promise<void>;
  resolve: () => void;
  reject: (error: GroupQuestionResolveError) => void;
}

function createDeferredOutcome(): DeferredOutcome {
  let resolve!: () => void;
  let reject!: (error: GroupQuestionResolveError) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = (error) => rej(error);
  });
  return { promise, resolve, reject };
}

export interface InteractionIdentity {
  key: string;
  runId: string;
  botId: string;
  interactionId: string;
}

export type GroupInteractionEvent<T> =
  | { phase: 'requested'; key: string; interaction: T }
  | { phase: 'accepted' | 'resolved' | 'invalidated'; key: string };

export interface GroupInteractionAdapter<T, R> {
  parse(frame: unknown, identity: GroupQuestionEventIdentity): GroupInteractionEvent<T> | null;
  fingerprint(interaction: T, resolution: R): string;
  validate?(interaction: T, resolution: R): void;
}

export class GroupInteractionService<T extends InteractionIdentity, R> {
  private readonly config: GroupQuestionServiceConfig;
  private readonly identity: GroupQuestionEventIdentity;
  private readonly listeners = new Set<(snapshot: GroupInteractionSnapshot<T>) => void>();
  private readonly pendingInteractions = new Map<string, T>();
  /** 当前连接周期内的终态标记：阻止迟到 requested 复活；resetConnection 清空。 */
  private readonly terminalTombstones = new Set<string>();
  private readonly requestWaits = new Map<string, ResolveRequestWait>();
  /** 交互 key → 在飞 resolve promise：提交期间禁止同交互重复提交。 */
  private readonly inFlightByInteraction = new Map<string, Promise<void>>();
  /** (交互 key, 回答指纹) → 幂等键：跨连接周期保留，直至 service 释放。 */
  private readonly idempotencyKeys = new Map<string, string>();

  constructor(config: GroupQuestionServiceConfig, private readonly adapter: GroupInteractionAdapter<T, R>) {
    this.config = config;
    this.identity = { groupId: config.groupId, bcsSessionId: config.bcsSessionId };
  }

  /** 订阅快照；立即回放当前状态，退订函数由调用方持有。 */
  subscribe(listener: (snapshot: GroupInteractionSnapshot<T>) => void): () => void {
    this.listeners.add(listener);
    listener({ reason: 'update', interactions: this.currentInteractions() });
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * 尝试消费入站帧。仅适配器认可的当前群/会话事件与等待中请求的响应返回 true；
   * 其余帧（普通聊天、其他 kind、陌生响应）一律 false，交回原有 SDK 链路。
   */
  handleFrame(frame: unknown): boolean {
    const event = this.adapter.parse(frame, this.identity);
    if (event) {
      this.applyEvent(event);
      return true;
    }
    if (isOwnResponseFrame(frame) && this.requestWaits.has(frame.id as string)) {
      const response = parseGroupQuestionResolveResponse(frame);
      if (!response) return false;
      this.applyResolveResponse(response);
      return true;
    }
    return false;
  }

  /**
   * 提交回答。返回的 promise 在匹配 ACK、终态事件到达时成功；失败拒绝结构化
   * GroupQuestionResolveError（可重试失败保留待处理状态）。同一交互在飞期间
   * 的并发调用共享同一次尝试结果。
   */
  async resolve(key: string, resolution: R): Promise<void> {
    const inFlight = this.inFlightByInteraction.get(key);
    if (inFlight) return inFlight;
    const attempt = this.startResolve(key, resolution).finally(() => {
      if (this.inFlightByInteraction.get(key) === attempt) {
        this.inFlightByInteraction.delete(key);
      }
    });
    this.inFlightByInteraction.set(key, attempt);
    return attempt;
  }

  /**
   * 连接周期重置（断开/替换连接、重连握手开始时调用）：
   * 立即结束全部响应等待并清理定时器，清空待处理与 tombstone；
   * 幂等缓存保留，供服务端 replay 重建后复用相同回答的幂等键。
   */
  resetConnection(): void {
    for (const wait of this.requestWaits.values()) {
      this.finishWait(wait);
      wait.reject(
        new GroupQuestionResolveError({
          code: 'DISCONNECTED',
          message: '连接已断开，请重连后重试',
          retryable: true,
        }),
      );
    }
    this.requestWaits.clear();
    this.inFlightByInteraction.clear();
    this.pendingInteractions.clear();
    this.terminalTombstones.clear();
    this.notify({ reason: 'reset', interactions: [] });
  }

  private startResolve(key: string, resolution: R): Promise<void> {
    const interaction = this.pendingInteractions.get(key);
    if (!interaction) {
      return Promise.reject(
        new GroupQuestionResolveError({
          code: 'UNKNOWN_INTERACTION',
          message: '交互不存在或已结束',
          retryable: false,
        }),
      );
    }
    this.adapter.validate?.(interaction, resolution);
    if (!this.config.isConnected()) {
      return Promise.reject(
        new GroupQuestionResolveError({
          code: 'DISCONNECTED',
          message: '连接不可用，请稍后重试',
          retryable: true,
        }),
      );
    }

    // 幂等键按（交互 key, 回答指纹）缓存：相同载荷重试复用（含 cancel 重试），
    // 回答内容变化或改为取消时指纹变化自然换新键，避免旧成功记录被当作新答案。
    const cacheId = JSON.stringify([key, this.adapter.fingerprint(interaction, resolution)]);
    let idempotencyKey = this.idempotencyKeys.get(cacheId);
    if (!idempotencyKey) {
      idempotencyKey = newFrameId('idem');
      this.idempotencyKeys.set(cacheId, idempotencyKey);
    }

    const requestId = newFrameId('req');
    const deferred = createDeferredOutcome();
    const frame = {
      type: 'req',
      id: requestId,
      method: 'interaction.resolve',
      params: {
        bcsRunId: interaction.runId,
        interactionId: interaction.interactionId,
        idempotencyKey,
        ...resolution,
      } as Record<string, unknown>,
    };

    let wait: ResolveRequestWait;
    const timer = setTimeout(() => {
      this.settleWaitFailure(
        wait,
        new GroupQuestionResolveError({
          code: 'TIMEOUT',
          message: '提交超时，请重试',
          retryable: true,
        }),
      );
    }, RESOLVE_TIMEOUT_MS);
    wait = { key, requestId, timer, resolve: deferred.resolve, reject: deferred.reject };
    this.requestWaits.set(requestId, wait);

    // 发送失败不等于提交失败：保留待处理状态，允许稍后重试
    const sendFailure = () => {
      this.settleWaitFailure(
        wait,
        new GroupQuestionResolveError({
          code: 'SEND_FAILED',
          message: '发送失败，请检查连接后重试',
          retryable: true,
        }),
      );
    };
    try {
      void this.config.send(frame).catch(sendFailure);
    } catch {
      sendFailure();
    }
    return deferred.promise;
  }

  private applyEvent(event: GroupInteractionEvent<T>): void {
    const { key } = event;
    if (event.phase === 'requested') {
      if (this.terminalTombstones.has(key) || this.pendingInteractions.has(key)) return;
      this.pendingInteractions.set(key, event.interaction);
      this.notify({ reason: 'update', interactions: this.currentInteractions() });
      return;
    }
    // 终态（accepted/resolved/invalidated）一到即关闭；原 SSE resolved 可能先于
    // 本次响应到达，BCN 将该竞态合并为成功，因此同步结束该交互的在飞等待。
    this.settleInFlightAsTerminal(key);
    this.closeInteractionAtTerminal(key);
  }

  private applyResolveResponse(response: ParsedGroupQuestionResolveResponse): void {
    const wait = this.requestWaits.get(response.requestId);
    if (!wait) return;
    if (response.ok) {
      this.finishWait(wait);
      wait.resolve();
      if (response.interactionStatus && response.interactionStatus !== 'pending') {
        this.closeInteractionAtTerminal(wait.key);
      }
      return;
    }
    // ok:false 时 parse 保证携带顶层 error（code/message 已通过结构校验）
    this.settleWaitFailure(
      wait,
      new GroupQuestionResolveError({
        code: response.error.code,
        message: response.error.message,
        retryable: response.error.retryable,
        interactionStatus: response.interactionStatus,
      }),
    );
    // 失败但携带终态：按该状态结束待处理展示（如他人已处理、运行已失效）
    if (response.interactionStatus && response.interactionStatus !== 'pending') {
      this.closeInteractionAtTerminal(wait.key);
    }
  }

  /** 终态关闭：写 tombstone、移除待处理；移除成功才通知 terminal。 */
  private closeInteractionAtTerminal(key: string): void {
    this.terminalTombstones.add(key);
    if (this.pendingInteractions.delete(key)) {
      this.notify({ reason: 'terminal', interactions: this.currentInteractions() });
    }
  }

  /** 该交互的全部在飞等待按成功合并结束（终态竞态，见协议 §7.2）。 */
  private settleInFlightAsTerminal(key: string): void {
    for (const wait of this.requestWaits.values()) {
      if (wait.key !== key) continue;
      this.finishWait(wait);
      wait.resolve();
    }
  }

  private settleWaitFailure(wait: ResolveRequestWait, error: GroupQuestionResolveError): void {
    if (this.requestWaits.get(wait.requestId) !== wait) return;
    this.finishWait(wait);
    wait.reject(error);
  }

  private finishWait(wait: ResolveRequestWait): void {
    clearTimeout(wait.timer);
    this.requestWaits.delete(wait.requestId);
  }

  private currentInteractions(): T[] {
    return Array.from(this.pendingInteractions.values());
  }

  private notify(snapshot: GroupInteractionSnapshot<T>): void {
    for (const listener of Array.from(this.listeners)) {
      listener(snapshot);
    }
  }
}
