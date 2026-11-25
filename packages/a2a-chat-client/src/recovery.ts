/**
 * B11 — 恢复预算 + 旧任务恢复 runner（spec stage2 §4.4，首版默认值）。
 * ============================================================================
 *
 * 预算律（spec 引文钉死，全部具名常量）：
 *   · 总量：max 5 attempts AND 总 60s，先到即停；
 *   · 退避：第 1 次立即；此后 sleep ∈ [cap/2, cap] 抖动，cap 序列
 *     [1000, 2000, 4000, 8000]（按 attempt 序取，越界取末位）；预算耗尽
 *     即截断本次 sleep；
 *   · 单连接/GetTask 10s 上限（不越过剩余预算）：**活跃 SSE 不继承 10s**
 *     ——replay 首事件到达即视为活跃（backlog 先回放后 live，生成器本身就是
 *     live 续流），活跃后事件等待不受 per-call 限；SubscribeToTask 是空
 *     backlog 的"竞态补看"分支，不适用活跃豁免（防止"等一个永不到来的
 *     final"），每个 next() 都受 10s 限；
 *   · GetTask→SubscribeToTask 竞态**共享同一预算**（无分支重计）：
 *     一次 attempt = 一次 replay，或（拒绝分诊后）GetTask + 可能的
 *     subscribeFuture 整链——计 1；
 *   · 预算耗尽 → 'budget-exhausted'（调用方据此发"恢复已停止"信号 +
 *     手工恢复/放弃入口）；known taskId + 未消费任何 seq → 重放游标
 *     sinceSeq=0（永不为 undefined）。
 *
 * 拒绝分诊规则（replay RPC 拒绝 reason = cache_limit / not_found_or_expired）：
 *   GetTask 三态分诊——
 *     found + 终态   → 'resumed-incomplete'（带上 state/latestText；恢复层不
 *                      伪造 wire 事件，终态帧合成归 provider）；
 *     found + 活跃   → subscribeFuture 补看；流静默收尾/超时/断流 = 本次
 *                      attempt 失败，进入下一轮退避；
 *     found:false    → 'unknown'（结果未知）。
 *
 * 失败归类（typed failure）：
 *   transport / 其他 rpc → 可重试（attempt 失败）；
 *   rpc reason cache_limit / not_found_or_expired → 拒绝分诊；
 *   auth / protocol（LOCK_CANDIDATE_KINDS 同集）→ **非可重试终局 'fatal'**
 *     （B14/F4：鉴权拒绝/协议破约不会因退避自愈，重试只是空烧预算——带原始
 *     failure 早退，宿主硬失败播报与 locked-v3 锁检测归 provider 尾端收口）；
 *   超时（per-call race 打断）→ typed transport 'call_timeout' → 可重试。
 *
 * 测试可同步观察性：预算/退避全走注入 clock（now/sleep），backoff 抖动走
 * clock.random（缺省 Math.random）；per-call 超时 race 用真实 setTimeout
 * （测试用小 perCallTimeoutMs 实测，勿用 jest fake timers——会连累注入
 * clock 的语义）。
 */
import type { WireEvent } from './stream';
import { LOCK_CANDIDATE_KINDS, type WireFailure } from './rpc';

export interface RecoveryEngine {
  /** ReplayEvents 重放（拒绝对 typed 抛出；backlog 先回放后 live 续流） */
  replay(taskId: string, sinceSeq: number): AsyncGenerator<WireEvent>;
  /** 引擎单信封 GetTask；not_found_or_expired → {found:false} */
  getTask(taskId: string): Promise<{ found: boolean; state?: string; latestText?: string }>;
  /** SubscribeToTask RPC 流（GetTask 竞态的未来订阅分支，空 backlog） */
  subscribeFuture(taskId: string): AsyncGenerator<WireEvent>;
}

export interface RecoveryConfig {
  maxAttempts?: number;
  totalBudgetMs?: number;
  backoffCapsMs?: number[];
  perCallTimeoutMs?: number;
}

export type RecoveryOutcome =
  | { outcome: 'resumed' }
  | { outcome: 'resumed-incomplete'; state?: string; latestText?: string }
  | { outcome: 'terminal'; state: string }
  | { outcome: 'unknown' }
  | { outcome: 'budget-exhausted' }
  /** F4：非可重试硬失败（auth/protocol，LOCK_CANDIDATE_KINDS 同集）的首个
   * typed 失败即早退——预算不烧、不退避，原始 failure 随行供 provider 端
   * notifyHardFailure（locked-v3 锁检测）+ 预算耗尽等价信号语收口。 */
  | { outcome: 'fatal'; failure: WireFailure }
  /** 扩展位：abort/disconnect/新请求让位（generation fence）——非 spec 五态之一，
   * provider 据此静默收尾（manualRecover 映射为 null）。 */
  | { outcome: 'cancelled' };

// ─── 具名常量（spec 首版默认；recovery.test.ts 逐条钉） ─────────────────
export const DEFAULT_MAX_ATTEMPTS = 5;
export const DEFAULT_TOTAL_BUDGET_MS = 60_000;
export const DEFAULT_BACKOFF_CAPS_MS: readonly number[] = [1000, 2000, 4000, 8000];
export const DEFAULT_PER_CALL_TIMEOUT_MS = 10_000;

export const REFUSAL_REASON_CACHE_LIMIT = 'cache_limit';
export const REFUSAL_REASON_NOT_FOUND_OR_EXPIRED = 'not_found_or_expired';
/** 引 ReplayEvents 拒绝 reason 集（引擎 lowercase 口径） */
const REFUSAL_REASONS: ReadonlySet<string> = new Set([
  REFUSAL_REASON_CACHE_LIMIT, REFUSAL_REASON_NOT_FOUND_OR_EXPIRED,
]);

/** engine wire.py TERMINAL_STATES（GetTask 分诊口径） */
const TERMINAL_STATES: ReadonlySet<string> = new Set([
  'TASK_STATE_COMPLETED', 'TASK_STATE_FAILED',
  'TASK_STATE_CANCELED', 'TASK_STATE_REJECTED',
]);

export interface RecoveryClock {
  now(): number;
  sleep(ms: number): Promise<void>;
  /** backoff 抖动源；缺省 Math.random（测试注入确定值钉 [cap/2, cap]） */
  random?(): number;
}

const defaultClock = (): RecoveryClock => ({
  now: () => Date.now(),
  sleep: (ms) => new Promise<void>((resolve) => { setTimeout(resolve, ms); }),
  random: () => Math.random(),
});

function isTypedFailure(err: unknown): err is { failure: WireFailure } {
  const failure = (err as { failure?: unknown } | null | undefined)?.failure;
  return typeof err === 'object' && err !== null
    && typeof failure === 'object' && failure !== null;
}

/**
 * F4 硬失败判读：typed failure kind ∈ LOCK_CANDIDATE_KINDS(auth/protocol) → 非
 * 可重试。鉴权门对重放的重复拒绝不携带任何新信息（退避 4 轮只是烧 60s 预算）、
 * 协议破约不会自愈——首见即 fatal 早退，原始 failure 随行给 provider 做锁检测
 * 与收口播报（恢复分支此前缺 notifyHardFailure 面，属 B14 F4）。
 */
function fatalFailureOf(err: unknown): WireFailure | null {
  if (!isTypedFailure(err)) return null;
  return LOCK_CANDIDATE_KINDS.has(err.failure.kind) ? err.failure : null;
}

function statusStateOf(ev: WireEvent): string {
  const status = ev.member.status;
  if (typeof status === 'object' && status !== null
    && typeof (status as { state?: unknown }).state === 'string') {
    return (status as { state: string }).state;
  }
  return '';
}

function callTimeoutError(): Error & { failure: WireFailure } {
  return Object.assign(new Error('a2a recovery call timeout'), {
    failure: { kind: 'transport', reason: 'call_timeout' } as WireFailure,
  });
}

export class RecoveryRunner {
  private readonly engine: RecoveryEngine;
  private readonly maxAttempts: number;
  private readonly totalBudgetMs: number;
  private readonly backoffCaps: readonly number[];
  private readonly perCallTimeoutMs: number;
  private readonly clock: RecoveryClock;

  constructor(
    engine: RecoveryEngine,
    config: RecoveryConfig = {},
    clock: RecoveryClock = defaultClock(),
  ) {
    this.engine = engine;
    this.maxAttempts = config.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
    this.totalBudgetMs = config.totalBudgetMs ?? DEFAULT_TOTAL_BUDGET_MS;
    this.backoffCaps = config.backoffCapsMs ?? DEFAULT_BACKOFF_CAPS_MS;
    this.perCallTimeoutMs = config.perCallTimeoutMs ?? DEFAULT_PER_CALL_TIMEOUT_MS;
    this.clock = clock;
  }

  /**
   * 恢复收敛主回路。appliedSeq=null（known taskId 但零消费）→ sinceSeq=0。
   * onEvent 逐事件透传（保持事件次序与成员原貌——本层不合成不伪造）。
   */
  async run(
    taskId: string,
    appliedSeq: number | null,
    onEvent: (ev: WireEvent) => void,
    opts: { isCancelled?: () => boolean } = {},
  ): Promise<RecoveryOutcome> {
    const cancelled = opts.isCancelled ?? (() => false);
    const deadline = this.clock.now() + this.totalBudgetMs;
    const remaining = () => deadline - this.clock.now();
    const sinceSeq = appliedSeq ?? 0;       // 钉板：重放游标永不为 undefined
    let attempt = 0;
    for (;;) {
      if (cancelled()) return { outcome: 'cancelled' };
      if (remaining() <= 0) return { outcome: 'budget-exhausted' };
      if (attempt >= this.maxAttempts) return { outcome: 'budget-exhausted' };
      if (attempt > 0) {
        // 退避：cap 按上一 attempt 序取（attempt-1 索引），越界取末位；
        // sleep ∈ [cap/2, cap]；剩余预算不足即截断本次 sleep
        const cap = this.backoffCaps[Math.min(attempt - 1, this.backoffCaps.length - 1)] ?? 0;
        const jitter = this.clock.random?.() ?? Math.random();
        const sleepMs = Math.min(
          Math.floor(cap / 2 + jitter * (cap / 2)),
          Math.max(remaining(), 0),
        );
        await this.clock.sleep(sleepMs);
        if (cancelled()) return { outcome: 'cancelled' };
        if (remaining() <= 0) return { outcome: 'budget-exhausted' };
      }
      attempt += 1;
      const settled = await this.attempt(taskId, sinceSeq, onEvent, remaining, cancelled);
      if (settled !== null) return settled;  // 否则：attempt 失败 → 下一轮
    }
  }

  // ─── 内部 ─────────────────────────────────────────────────────────────

  /** 一次 attempt：replay 主路；typed 拒绝（cache_limit/not_found）转分诊链 */
  private async attempt(
    taskId: string, sinceSeq: number,
    onEvent: (ev: WireEvent) => void,
    remaining: () => number, cancelled: () => boolean,
  ): Promise<RecoveryOutcome | null> {
    try {
      return await this.consumeStream(
        this.engine.replay(taskId, sinceSeq), onEvent, remaining, cancelled,
        { cleanEnd: 'resumed', rideAfterActive: true },
      );
    } catch (err) {
      if (this.isRefusal(err)) {
        return await this.refuseTriage(taskId, onEvent, remaining, cancelled);
      }
      const fatal = fatalFailureOf(err);
      if (fatal) return { outcome: 'fatal', failure: fatal };
      return null;   // transport/其他 rpc/超时 → 可重试
    }
  }

  /** ReplayEvents 拒绝 → GetTask 三态分诊（同一 attempt、同一预算） */
  private async refuseTriage(
    taskId: string,
    onEvent: (ev: WireEvent) => void,
    remaining: () => number, cancelled: () => boolean,
  ): Promise<RecoveryOutcome | null> {
    let task: { found: boolean; state?: string; latestText?: string };
    try {
      task = await this.timedCall(this.engine.getTask(taskId), remaining);
    } catch (err) {
      const fatal = fatalFailureOf(err);
      if (fatal) return { outcome: 'fatal', failure: fatal };
      return null;   // GetTask 失败/超时：attempt 失败，下一轮重试
    }
    if (cancelled()) return { outcome: 'cancelled' };
    if (!task.found) return { outcome: 'unknown' };
    if (!TERMINAL_STATES.has(String(task.state ?? ''))) {
      // found + 活跃：任务可能在 GetTask→Subscribe 间隙终结——订阅面每个
      // next() 都受 per-call 限（不适用活跃豁免），静默收尾即 attempt 失败
      try {
        return await this.consumeStream(
          this.engine.subscribeFuture(taskId), onEvent, remaining, cancelled,
          { cleanEnd: 'retry', rideAfterActive: false },
        );
      } catch (err) {
        const fatal = fatalFailureOf(err);
        if (fatal) return { outcome: 'fatal', failure: fatal };
        return null;   // 竞态落空/断流/超时 → 下轮退避后重来
      }
    }
    // found + 终态：可取回结果，标"不完整"——帧合成归 provider，本层纯账
    return {
      outcome: 'resumed-incomplete',
      state: task.state,
      latestText: task.latestText,
    };
  }

  /**
   * 流消费公共回路。
   *  · rideAfterActive=true（replay）：首事件到达即活跃——之后事件等待不受
   *    per-call 10s 限（活跃 SSE 不继承；生成器即 live 续流，一直吃到 final）；
   *  · rideAfterActive=false（subscribe 竞态分支）：每个 next() 都受限。
   *  · cleanEnd='resumed'（replay 清洁收尾且已交付 ≥1 事件）→ 'resumed'；
   *    其余清洁收尾 → null（attempt 失败）。final → 'terminal'（带 state）。
   *  · 剩余预算耗尽发生在 per-call 等待前 → 'budget-exhausted'（不再发下一请求）。
   */
  private async consumeStream(
    iter: AsyncGenerator<WireEvent>,
    onEvent: (ev: WireEvent) => void,
    remaining: () => number, cancelled: () => boolean,
    opts: { cleanEnd: 'resumed' | 'retry'; rideAfterActive: boolean },
  ): Promise<RecoveryOutcome | null> {
    let active = false;
    let delivered = 0;
    try {
      for (;;) {
        if (cancelled()) return { outcome: 'cancelled' };
        const nextP = iter.next();
        let res: IteratorResult<WireEvent>;
        if (active && opts.rideAfterActive) {
          res = await nextP;                    // 活跃续流：不受 per-call 限
        } else {
          const budget = Math.min(this.perCallTimeoutMs, remaining());
          if (budget <= 0) return { outcome: 'budget-exhausted' };
          res = await this.raceTimeout(nextP, budget);
        }
        if (res.done) break;
        const ev = res.value;
        active = true;
        if (cancelled()) return { outcome: 'cancelled' };
        onEvent(ev);                            // 事件原样透传（次序保真）
        delivered += 1;
        if (ev.type === 'statusUpdate' && ev.metadata.final === 'true') {
          return { outcome: 'terminal', state: statusStateOf(ev) };
        }
      }
    } finally {
      // 弃流善后：超时/取消/逃逸后归还生成器（streamRpc 的 finally 会 cancel
      // body）。不可 await——async generator 悬挂在内部 await 时 return
      // completion 须等该 await 落定，持等待会反向卡死恢复循环（超时面失守）。
      try { iter.return?.(undefined)?.catch(() => undefined); } catch { /* 同步抛按无 return 面 */ }
    }
    if (delivered > 0 && opts.cleanEnd === 'resumed') {
      return { outcome: 'resumed' };            // 有进展、无终态：run 层面就此返
    }
    return null;                                // 零交付/竞态补看收尾：attempt 失败
  }

  /** typed failure 归类：refusal reason → 分诊；其余全可重试 */
  private isRefusal(err: unknown): boolean {
    if (!isTypedFailure(err)) return false;
    const failure = err.failure;
    if (failure.kind !== 'rpc' || !failure.rpcError) return false;
    const reason = (failure.rpcError.data as { reason?: unknown } | undefined)?.reason;
    return typeof reason === 'string' && REFUSAL_REASONS.has(reason);
  }

  /** per-call 超时 race：不越过剩余预算（budget 由调用方 min 过） */
  private raceTimeout<T>(p: Promise<T>, budgetMs: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(callTimeoutError()), budgetMs);
      p.then(
        (v) => { clearTimeout(timer); resolve(v); },
        (e) => { clearTimeout(timer); reject(e); },
      );
    });
  }

  /** promise 面 per-call 限（GetTask 单信封） */
  private async timedCall<T>(p: Promise<T>, remaining: () => number): Promise<T> {
    const budget = Math.min(this.perCallTimeoutMs, remaining());
    if (budget <= 0) return Promise.reject(callTimeoutError());
    return this.raceTimeout(p, budget);
  }
}