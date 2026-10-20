/**
 * B12 — CancelTask 四态归类器 + UI 文案单源（spec stage2 §4.3）。
 * ============================================================================
 *
 * 引擎契约事实源（engine router.py `_cancel_task`，~行 527-562）：
 *   · 成功信封 result = { task: run_snapshot, engineAbort: <结局> }——
 *     snapshot.status.state === 'TASK_STATE_CANCELED'（kernel.cancel_run 的
 *     快照口径，kernel.py `run_snapshot`）；engineAbort ∈
 *     'confirmed' | 'not_found' | 'refused' | 'timeout' | 'unavailable'
 *     （router.py:462-466 注释表：除 confirmed 外任何值都不保证引擎已停）。
 *   · 已终态 run 上重复 cancel：JSON-RPC error
 *     {code:-32000, data:{reason:'task_terminal', state, already_terminal:true}}
 *     ——且**不再发 abort**（幂等契约：本路径 engineAbort 永远不可知）。
 *   · not_found_or_expired：{code:-32000, data:{reason:'not_found_or_expired'}}。
 *   · 请求流断（typed transport/protocol/auth failure）：取消结局不可知。
 *
 * 四态 == UI 面文案矩阵（CANCEL_UI_TEXT，B13 复用的单源）：
 *   cancel-rejected      请求显式拒绝（含 already-terminal repeat cancel 的
 *                         非 CANCELED 分支：state 已知、run 按 UI 已知状态继续）
 *   cancelled-clean      task=CANCELED + engineAbort=confirmed
 *   cancelled-unconfirmed task=CANCELED + engineAbort 非 confirmed（或缺失）
 *   cancel-unknown       请求流断，取消结局未知
 * 特例：already-terminal repeat cancel 且 data.state==='TASK_STATE_CANCELED' =
 * 已完成的取消——传输面确属 CANCELED，但 engineAbort 该路径不可得 → 归
 * cancelled-unconfirmed（不得因 repeat 自我安慰成 clean）。
 *
 * B14/F2 §3.1 行规则：cancel-rejected 区分两个 typed 行——
 *   cancel-rejected            state 已知（task_terminal 等）→ releaseOnTerminal
 *   cancel-rejected-unknown    not_found_or_expired（查无任务）→ **保持挂禁**
 *   spec §3.1 原文：GetTask 查无任务…均不构成底层执行已停止的证明，不解除
 *   禁发。两行共用同一 UI 文案（用户面无差），禁令核销行不同
 *   （A2AProvider.applyCancelBanDisposition 五行表逐行钉）。
 */
import type { RpcError, WireFailure } from './rpc';

export type CancelOutcomeKind = 'cancel-rejected' | 'cancel-rejected-unknown'
  | 'cancelled-clean' | 'cancelled-unconfirmed' | 'cancel-unknown';

export const CANCEL_UI_TEXT: Record<CancelOutcomeKind, string> = {
  'cancel-rejected': '取消未成功，保留已知任务状态',
  // F2：not_found 与显式拒绝同文案（用户无差），禁令核销在 provider 侧分行
  'cancel-rejected-unknown': '取消未成功，保留已知任务状态',
  'cancelled-clean': '已取消',
  'cancelled-unconfirmed': '已取消，底层执行停止未确认',
  'cancel-unknown': '取消结果未知，尝试查询任务状态',
};

export interface CancelOutcome {
  kind: CancelOutcomeKind;
  /** 成功信封附录的引擎 abort 结局（confirmed/not_found/refused/timeout/unavailable） */
  engineAbort?: string;
  /** 已知终局状态的附录（repeat task_terminal / 非 CANCELED 成功信封防御位） */
  terminalState?: string;
}

export interface CancelClassifyInput {
  /** 单信封成功标记（rpcOnce 解析成功即 true） */
  ok?: boolean;
  /** JSON-RPC result 成员（{task, engineAbort}） */
  result?: any;
  /** JSON-RPC error 信封（rpcOnce typed throw 时随 failure 携带） */
  rpcError?: RpcError;
  /** typed failure（流断/协议/鉴权/超时——rpcOnce 的 throw 面） */
  failure?: WireFailure;
}

const STATE_CANCELED = 'TASK_STATE_CANCELED';
const ABORT_CONFIRMED = 'confirmed';
/** router.py `_cancel_task` 已终态 repeat cancel 的 reason（不再发 abort） */
const REASON_TASK_TERMINAL = 'task_terminal';
/** 引擎 run 查无/已过期的 reason（F2：此行不构成停止证明，禁令核销上独立成行） */
const REASON_NOT_FOUND_OR_EXPIRED = 'not_found_or_expired';

/** engine 口径字符串读取（data.reason / data.state / result 成员检索用） */
function strOf(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}

/** 成功信封 {task:{status:{state}}, engineAbort} 的成员检索 */
function readResult(result: any): { state?: string; engineAbort?: string } {
  if (result == null || typeof result !== 'object') return {};
  const task = (result as Record<string, unknown>).task;
  if (task == null || typeof task !== 'object') return {};
  const status = (task as Record<string, unknown>).status;
  return {
    state: (status != null && typeof status === 'object')
      ? strOf((status as Record<string, unknown>).state) : undefined,
    engineAbort: strOf((result as Record<string, unknown>).engineAbort),
  };
}

/**
 * 纯归类器（零网络零副作用；CancelTask 响应分裂出的所有分支 → 四态之一）：
 *   1. rpcError（typed rpc throw 或直给）：
 *      reason==='task_terminal' → state CANCELED ? unconfirmed : rejected；
 *      reason==='not_found_or_expired' → cancel-rejected-unknown（F2：查无
 *      任务不构成停止证明；同 rejected 文案，禁令核销独立行）；
 *      其余 rpc error（resolve 门拒绝等）→ rejected。
 *   2. 无 rpcError 有 failure（流断/协议/鉴权/超时）→ unknown。
 *   3. 成功信封：CANCELED + confirmed → clean；CANCELED + 其他 engineAbort →
 *      unconfirmed；非 CANCELED（防御位）→ rejected（附终局状态）。
 *   4. 零信息（无 result 无 error）→ unknown（不臆断）。
 */
export function classifyCancel(input: CancelClassifyInput): CancelOutcome {
  const rpcError = input.rpcError ?? input.failure?.rpcError;
  if (rpcError) {
    const data = rpcError.data ?? {};
    const reason = strOf(data.reason);
    const state = strOf(data.state);
    if (reason === REASON_TASK_TERMINAL && state === STATE_CANCELED) {
      // 已完成态的 repeat cancel：传输面 CANCELED 属实，engineAbort 不可知
      return { kind: 'cancelled-unconfirmed', terminalState: state };
    }
    if (reason === REASON_NOT_FOUND_OR_EXPIRED) {
      // F2/§3.1：GetTask 查无任务 ≠ 底层执行已停止——不解除禁发（禁令核销
      // 归 provider 五行表：本行 begin 保持挂禁）
      return { kind: 'cancel-rejected-unknown' };
    }
    return {
      kind: 'cancel-rejected',
      ...(state !== undefined ? { terminalState: state } : {}),
    };
  }
  if (input.failure) {
    return { kind: 'cancel-unknown' };
  }
  if (input.result == null || typeof input.result !== 'object') {
    return { kind: 'cancel-unknown' };
  }
  const { state, engineAbort } = readResult(input.result);
  if (state === STATE_CANCELED) {
    return engineAbort === ABORT_CONFIRMED
      ? { kind: 'cancelled-clean', engineAbort }
      : { kind: 'cancelled-unconfirmed', ...(engineAbort !== undefined ? { engineAbort } : {}) };
  }
  // 非取消终局的成功信封（防御位）：状态已知 → 按显式不相容判读
  return {
    kind: 'cancel-rejected',
    ...(state !== undefined ? { terminalState: state } : {}),
  };
}