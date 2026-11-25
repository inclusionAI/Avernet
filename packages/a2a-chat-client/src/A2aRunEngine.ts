/**
 * A2A 运行管线引擎（review：A2AProvider 状态机拆分——run 消费/收账/恢复轮
 * 的完整单管线与游标三件套独立成引擎；provider 保留传输编排与宿主面）。
 *
 * 职责边界（迁自 A2AProvider.ts，实现逐字迁移仅 this. 指向重映射）：
 *   · run 事件消费 consumeWireEvent / 收账 settleRun / 恢复轮 runRecovery；
 *   · 恢复终局核销与信号语（handleRecoveryOutcome / surface*）；
 *   · 恢复引擎 adapter buildRecoveryEngine；
 *   · 游标/恢复锚/收账栅栏三件套字段（provider 公共只读面经 getter 透出）。
 * 宿主耦合经 A2aRunHost 窄缝（provider 构造期注入 getter/闭包，读实时值）。
 */
import { buildRpcRequest, type WireFailure } from './rpc';
import { rpcOnce } from './rpcOnce';
import { streamRpc, type WireEvent } from './stream';
import { translateEvent, type TranslateCtx } from './translate';
import { RecoveryRunner, type RecoveryEngine, type RecoveryConfig, type RecoveryOutcome } from './recovery';
import type { SdkParser } from './sdkParser';
import {
  STATE_CANCELED, STATE_COMPLETED, STATE_REJECTED,
  buildRunFailure, latestArtifactText, terminalStateOf,
  type A2aRunFailure,
} from './providerFacts';

// ─── B11 恢复面 UI 常量（spec §4.4/§3 信号语，导出供 UI 复用） ────────────

/** replay 拒绝 + GetTask 终态：结果可取回但标"不完整" */
export const RECOVERY_INCOMPLETE_MESSAGE = '恢复不完整，任务结果部分缺失';
/** 未知 taskId 断流（首 Task 事件前）/ not_found_or_expired：结果未知；
 * spec §3 #2 —— 永不自动恢复、永不自动重发 */
export const OUTCOME_UNKNOWN_MESSAGE = '结果未知';
/** 预算耗尽：停止自动请求的信号语（spec §4.4 原文） */
export const RECOVERY_STOPPED_MESSAGE = '恢复已停止，任务结果未确认';
/**
 * 恢复终局信号（B11）的 typed 承载：与 run_failed 同走 rpc 桶上浮 UI；
 * reason 三值与 RecoveryOutcome 的三个"止损面"一一对应。
 * B14/F4：非可重试硬失败（auth/protocol）早退按 recovery_stopped 收口，
 * 原始 failure 经 `cause` 随行（分诊/锁定二判用，不再二次丢失）。
 */
interface A2aRecoverySignalFailure extends WireFailure {
  reason: 'recovery_incomplete' | 'recovery_outcome_unknown' | 'recovery_stopped';
  /** F4：fatal 早退（RecoveryOutcome 'fatal'）时附录的原始 wire 失败 */
  cause?: WireFailure;
}
export interface RunOutcomeBag {
  parser: SdkParser;
  ctx: TranslateCtx;
  /** B9 契约：artifactUpdate 独占改写的滚动全文（跨恢复轮存活） */
  state: { fullText: string };
  outcome: 'completed' | 'cancelled' | 'failed' | null;
  runFailure: A2aRunFailure | null;
  /** 双终态防重入（replay 重发面/竞态补看，"no double final" 核销位） */
  terminalApplied: boolean;
}

/**
 * 宿主窄缝：provider 在构造期以 getter/闭包注入（字段读写恒取实时值）。
 * config 是构造期定格的配置引用（provider 该字段不可变）。
 */
export interface A2aRunHost {
  readonly config: { a2aUrls: { rpcUrl: string }; fetchImpl?: typeof fetch; recovery?: RecoveryConfig };
  generation(): number;
  resolveIamToken(): Promise<string | undefined>;
  notifyHardFailure(err: unknown): void;
  releaseBanOnNonCancelTerminal(contextId: string): void;
  /** settleRun 的 cancelled 收账补发（provider: this.onComplete?.([])） */
  emitCancelledComplete(): void;
  /** 信号语上浮（provider: this.onError?.(error)） */
  emitError(error: Error): void;
}

export class A2aRunEngine {
  /** ── run 游标三件套（随管线引擎走；provider 读写经 this.run.*） ── */
  appliedSeq: number | null = null;
  recoveryAnchor:
    | { taskRunId: string; sessionKey: string; state: { fullText: string } }
    | null = null;
  runSettled = false;

  constructor(private readonly host: A2aRunHost) {}

  consumeWireEvent(ev: WireEvent, bag: RunOutcomeBag): 'final' | 'error' | 'cont' {
    if (bag.ctx.taskRunId === null) {
      // wire task 成员带 id，其余成员带 taskId（同一 run id）——首帧即记
      const candidate = ev.type === 'task'
        ? (ev.member as { id?: unknown }).id
        : (ev.member as { taskId?: unknown }).taskId;
      if (typeof candidate === 'string' && candidate) {
        bag.ctx.taskRunId = candidate;
        this.recoveryAnchor = {
          taskRunId: candidate, sessionKey: bag.ctx.sessionKey, state: bag.state,
        };
      }
    }
    // spec §3.4：「同一 seq 上未应用的 final marker 必须应用」。引擎事实
    // （kernel.complete_run，kernel.py:156-180）：终态两连发——state-change
    // statusUpdate(COMPLETED) 先经 _publish 把 last_seq 推到 N+1 并消费（游标
    // →N+1），随后 final marker 以**同一 r.last_seq = N+1** 构造下发——inclusive
    // 去重会把 marker 当重发吞掉（final 永不应用 → settleRun 无账可收 → UI 挂死）。
    // 终态 marker 豁免 `<=` 去重；双终态防重由下方 terminalApplied 栅栏兜（同 run
    // 第二终帧不再入账），主回路/replay 恢复/手工轮同一管线同豁免。
    const isTerminalMarker = ev.type === 'statusUpdate' && ev.metadata.final === 'true';
    if (!isTerminalMarker
      && ev.seq !== null && this.appliedSeq !== null && ev.seq <= this.appliedSeq) {
      return 'cont';                // 已消费游标之前的事件（重发面）：纯去重
    }
    const out = translateEvent(ev, bag.ctx, bag.state);
    if (out === null) {
      // 无渲染面（task/message 成员、纯 WORKING 心跳）：游标仍推进
      if (ev.seq !== null) this.appliedSeq = ev.seq;
      return 'cont';
    }
    if ((out.kind === 'final' || out.kind === 'error') && bag.terminalApplied) {
      return 'cont';                // 同 run 第二终帧：防 parser 双 final
    }
    if (out.consumedSeq !== null) this.appliedSeq = out.consumedSeq;
    if (out.kind === 'final' || out.kind === 'error') {
      bag.terminalApplied = true;
      this.runSettled = true;
    }
    bag.parser.handleMessage(out.frame);
    if (out.kind === 'error') {
      // parser.onError 已驱动 UI（钉板 #8）；分完帧再收账
      bag.outcome = 'failed';
      bag.runFailure = buildRunFailure(ev.metadata);
      // §3.1 FAILED = 非 cancel 终态：按正常完成语义解禁（GetTask 确认同路）
      this.host.releaseBanOnNonCancelTerminal(bag.ctx.sessionKey);
      return 'error';
    }
    if (out.kind === 'final') {
      // ⚠️ B9 契约：CANCELED/REJECTED 也出 final 帧（parser 纯 no-op）——
      // 这里以成员终态区分 completion outcome 与 transport outcome
      const stateStr = terminalStateOf(ev.member);
      bag.outcome = (stateStr === STATE_CANCELED || stateStr === STATE_REJECTED)
        ? 'cancelled' : 'completed';
      if (bag.outcome !== 'cancelled') {
        // §3.1 非 cancel 终态（COMPLETED 等）：正常完成语义解禁——
        // CANCELED 走流也**不**解禁（解禁只认 engineAbort=confirmed）
        this.host.releaseBanOnNonCancelTerminal(bag.ctx.sessionKey);
      }
      return 'final';
    }
    return 'cont';
  }

  /** run 收账三分支（completed/resumed 的 UI 面由 parser.onComplete 钩子已驱动） */
  settleRun(bag: RunOutcomeBag): void {
    if (bag.outcome === 'failed' && bag.runFailure) {
      throw Object.assign(new Error(bag.runFailure.rpcError!.message), { failure: bag.runFailure });
    }
    if (bag.outcome === 'cancelled') {
      // v3 钉板：OpenClawProvider.js:291-299 abort() → onComplete([])；
      // parser 对废除帧 no-op（OpenClawParser.js:637），完成播报由本层补发
      this.host.emitCancelledComplete();
    }
  }

  /**
   * 自动恢复轮（request 断流内联调用）：known taskId + 已消费游标 →
   * RecoveryRunner（预算 spec §4.4 首版默认，config.recovery 可覆盖）。
   * 恢复事件与主回路同管线同栅栏（abort/disconnect/新请求即取消 runner）。
   */
  async runRecovery(
    generation: number, bag: RunOutcomeBag, ctrl: AbortController,
  ): Promise<RecoveryOutcome> {
    const taskId = bag.ctx.taskRunId!;
    const runner = new RecoveryRunner(
      this.buildRecoveryEngine(ctrl), this.host.config.recovery);
    return runner.run(
      taskId, this.appliedSeq,
      (ev) => {
        if (generation !== this.host.generation()) return;   // 迟帧栅栏：恢复流同门
        this.consumeWireEvent(ev, bag);
      },
      { isCancelled: () => generation !== this.host.generation() },
    );
  }

  /** 恢复轮终局核销：止损面 → onError 信号语 + typed 拒绝（settleRun 前） */
  handleRecoveryOutcome(bag: RunOutcomeBag, recovered: RecoveryOutcome): void {
    switch (recovered.outcome) {
      case 'resumed-incomplete':
        this.applyRecoveredIncomplete(bag, recovered);
        throw this.surfaceRecoverySignal('recovery_incomplete', RECOVERY_INCOMPLETE_MESSAGE);
      case 'unknown':
        throw this.surfaceRecoverySignal('recovery_outcome_unknown', OUTCOME_UNKNOWN_MESSAGE);
      case 'budget-exhausted':
        throw this.surfaceRecoverySignal('recovery_stopped', RECOVERY_STOPPED_MESSAGE);
      case 'fatal':
        // F4：恢复期硬失败（auth/protocol）——先向宿主播报（locked-v3 锁检测
        // 起点与 pre-task/请求期同路），再按预算耗尽等价信号语收口（原始
        // failure 经 cause 随行）。恢复中 auth 拒绝不再空烧 4 轮重放。
        throw this.surfaceFatalRecovery(recovered.failure);
      default:
        break;                      // resumed/terminal/cancelled → settleRun 收账
    }
  }

  /**
   * F4：恢复期硬失败（auth/protocol，NonRetryable early-exit）的对宿主播报——
   * 恢复分支此前缺 notifyHardFailure（pre-task 面有，恢复面没有），鉴权拒绝
   * 触发不了 bootstrap 的 lock 监听、还会烧满 5 attempt 预算。此处一次播报
   * （runner 已把 auth/protocol 设为非可重试终局，故每轮 recovery 至多一次）。
   */
  notifyFatalRecovery(failure: WireFailure): void {
    this.host.notifyHardFailure(Object.assign(new Error('a2a recovery hard failure'), { failure }));
  }

  /** fatal 收口 = 预算耗尽等价信号语 + 原始 failure 附录（cause 随行分诊） */
  private surfaceFatalRecovery(
    failure: WireFailure,
  ): Error & { failure: A2aRecoverySignalFailure } {
    this.notifyFatalRecovery(failure);
    return this.surfaceRecoverySignal('recovery_stopped', RECOVERY_STOPPED_MESSAGE, failure);
  }

  /** 信号语双面：onError 上浮 + typed 拒绝（reason 核销表见 A2aRecoverySignalFailure）
   *  cause 仅 fatal 收口随行（budget-equivalent 信号的原始失败附录）。 */
  private surfaceRecoverySignal(
    reason: A2aRecoverySignalFailure['reason'], message: string,
    cause?: WireFailure,
  ): Error & { failure: A2aRecoverySignalFailure } {
    const error = Object.assign(new Error(message), {
      failure: {
        kind: 'rpc', reason,
        ...(cause !== undefined ? { cause } : {}),
      } as A2aRecoverySignalFailure,
    });
    this.host.emitError(error);
    return error;
  }

  /**
   * 'resumed-incomplete' 的结果取回（recovery.ts 不伪造 wire 事件——帧合成归本层）：
   * COMPLETED → 按 B9 chatFrame 同构合成 final 全文快照帧喂 parser（结果可取回，
   * parser.onComplete 驱动展示）；取消/拒绝/失败终态无安全合成正断面——只标不完整。
   */
  applyRecoveredIncomplete(
    bag: RunOutcomeBag, outcome: { state?: string; latestText?: string },
  ): void {
    if (outcome.state !== STATE_COMPLETED) return;
    if (bag.terminalApplied) return;
    bag.terminalApplied = true;
    bag.outcome = 'completed';
    this.runSettled = true;
    // §3.1 GetTask 确认的 COMPLETED 终态：进非 cancel 终态解禁流
    this.host.releaseBanOnNonCancelTerminal(bag.ctx.sessionKey);
    const text = outcome.latestText ?? bag.state.fullText;
    const frame = {
      type: 'event',
      event: 'chat',
      seq: null,
      payload: {
        runId: bag.ctx.taskRunId ?? 'a2a-run',
        sessionKey: bag.ctx.sessionKey,          // 钉板 #2：同值过 includes 门
        request: { sessionKey: bag.ctx.sessionKey },   // 钉板 #4：request REQUIRED
        seq: null,
        state: 'final',
        message: { role: 'assistant', content: [{ type: 'text', text }] },
      },
    };
    bag.parser.handleMessage(frame);
  }

  /** 恢复引擎 adapter：streamRpc/rpcOnce over 实例面（iamToken 每调取新，signal 共请求 Ctrl） */
  buildRecoveryEngine(ctrl: AbortController): RecoveryEngine {
    const rpcUrl = this.host.config.a2aUrls.rpcUrl;
    const fetchImpl = this.host.config.fetchImpl ?? undefined;
    const resolveToken = this.host.resolveIamToken;
    return {
      // spec §3 #1：known-taskId 断流一律按已消费游标 ReplayEvents
      //（backlog 先回放后 live；拒绝以 RPC error 信封 typed 上浮）
      replay: async function* replay(taskId: string, sinceSeq: number) {
        const iamToken = await resolveToken();
        yield* streamRpc(
          rpcUrl, buildRpcRequest('ReplayEvents', { taskId, sinceSeq }),
          { iamToken, signal: ctrl.signal, fetchImpl });
      },
      // GetTask 竞态分支的唯一合法订阅位（kernel sinceSeq=None 空 backlog）
      subscribeFuture: async function* subscribeFuture(taskId: string) {
        const iamToken = await resolveToken();
        yield* streamRpc(
          rpcUrl, buildRpcRequest('SubscribeToTask', { taskId, sinceSeq: null }),
          { iamToken, signal: ctrl.signal, fetchImpl });
      },
      // 单信封 GetTask：not_found_or_expired → {found:false}（引擎契约）
      getTask: async (taskId: string) => {
        try {
          const parsed = await rpcOnce(
            rpcUrl, buildRpcRequest('GetTask', { taskId }),
            { iamToken: await resolveToken(), signal: ctrl.signal, fetchImpl });
          const task = (parsed.result as Record<string, unknown> | undefined)?.task;
          if (typeof task !== 'object' || task === null || Array.isArray(task)) {
            throw Object.assign(new Error('a2a unexpected result shape'), {
              failure: { kind: 'protocol', reason: 'unexpected_result_shape' } as WireFailure,
            });
          }
          const taskRecord = task as Record<string, unknown>;
          const status = taskRecord.status;
          const state = (typeof status === 'object' && status !== null
            && typeof (status as { state?: unknown }).state === 'string')
            ? (status as { state: string }).state : undefined;
          return { found: true, state, latestText: latestArtifactText(taskRecord) };
        } catch (err) {
          const failure = (err as { failure?: WireFailure }).failure;
          const reason = (failure?.rpcError?.data as { reason?: unknown } | undefined)?.reason;
          if (failure?.kind === 'rpc' && reason === 'not_found_or_expired') {
            return { found: false };
          }
          throw err;
        }
      },
    };
  }
}
