/**
 * A2AProvider 的 wire 事实纯函数层（从 A2AProvider.ts 拆出，review：单文件
 * 超 1,000 行 CI 上限；纯函数零类耦合，随迁其唯一事实源常量/类型）。
 * 全部无状态：不 import 类侧任何运行时。
 */
import type { WireFailure } from './rpc';
import type { SdkParseResult } from './sdkParser';

/** wire run 失败 typed 承载（errorCode/errorMessage 自引擎 FAILED 终态 metadata） */
export interface A2aRunFailure extends WireFailure {
  reason: 'run_failed';
  errorCode?: string;
}

/** engine json-rpc 通用服务端错误码（run_failed 的 rpcError.code 事实源） */
export const RUN_FAILED_RPC_CODE = -32603;

/** wire 终态词汇（provider 与 run 引擎共用的事实源） */
export const STATE_CANCELED = 'TASK_STATE_CANCELED';
export const STATE_REJECTED = 'TASK_STATE_REJECTED';
export const STATE_COMPLETED = 'TASK_STATE_COMPLETED';
export const STATE_FAILED = 'TASK_STATE_FAILED';

export const EMPTY_PARSE_RESULT: SdkParseResult = {
  blocks: [], content: '', done: false, toolCalls: [], isApproval: false,
};

/** uuidv4 等价：真浏览器 secure context / node jest 均有 crypto.randomUUID；
 * uuid 包在精简 nodeenv 没装，v3 的 `import { v4 } from 'uuid'` 路径
 * （OpenClawProvider.js:8）走不通。 */
export function newMessageId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch { /* 下探 */ }
  return `a2a-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
}

/** 终态成员的 status.state 读取（wire 事实：终态只随 statusUpdate 成员下发出） */
export function terminalStateOf(member: Record<string, unknown>): string {
  const status = member.status;
  if (typeof status === 'object' && status !== null
    && typeof (status as { state?: unknown }).state === 'string') {
    return (status as { state: string }).state;
  }
  return '';
}

/** 首个非空字符串（审批交互身份的候选键序读取） */
export function firstStrOf(...candidates: unknown[]): string {
  for (const c of candidates) {
    if (typeof c === 'string' && c !== '') return c;
  }
  return '';
}

/**
 * v3 decision('allow-once'|'deny') → engine resolution（per interaction kind）。
 * 引擎词汇表（事实源）：
 *   exec        decision ∈ 'allow-once'|'allow-always'|'deny'（gateway
 *               resolve.ts validDecisions）——v3 两值恰在表内，直传；
 *   mode_switch decision ∈ 'proceed'|'stay'（同一 gateway 口径）——
 *               allow-once→proceed / deny→stay；
 *   ask_user    decision ∈ 'submit'|'cancel'，submit 需结构化答案载荷
 *               （answer/answers/values/selected_options，router
 *               `_validated_resolution`）——v3 decision 枚举零载荷面：
 *               allow-once → null（typed 拒绝，B13+ UI 供答案）；deny →
 *               'cancel'（无载荷正当路径）。
 * kind 缺省 → 'exec'：唯一无 kind 的请求帧是 v3 形 exec.approval.requested
 * （gateway interaction.requested 三类都显式带 kind）。
 * 未知 kind（引擎新词汇）：原样透传，合法性与 422 由引擎裁决——pending 保持。
 */
export function buildResolveResolution(
  kind: string | undefined, decision: 'allow-once' | 'deny',
): Record<string, unknown> | null {
  switch (kind ?? 'exec') {
    case 'exec':
      return { kind: 'exec', decision };
    case 'mode_switch':
      return { kind: 'mode_switch', decision: decision === 'allow-once' ? 'proceed' : 'stay' };
    case 'ask_user':
      return decision === 'deny'
        ? { kind: 'ask_user', decision: 'cancel' }
        : null;                         // submit 无答案载荷面 → typed 拒绝
    default:
      return { kind, decision };        // 未知 kind 透传（引擎裁决）
  }
}

/** 引擎 GetTask artifacts 的最新全量文本（[...latest_text...]——自后向前取首个非空） */
export function latestArtifactText(task: Record<string, unknown>): string | undefined {
  const artifacts = Array.isArray(task.artifacts) ? task.artifacts : [];
  for (let i = artifacts.length - 1; i >= 0; i -= 1) {
    const a = artifacts[i];
    if (typeof a !== 'object' || a === null || Array.isArray(a)) continue;
    const parts = Array.isArray((a as Record<string, unknown>).parts)
      ? (a as Record<string, unknown>).parts as unknown[] : [];
    const text = parts.map((p) =>
      (typeof p === 'object' && p !== null
        && typeof (p as { text?: unknown }).text === 'string')
        ? (p as { text: string }).text : '').join('');
    if (text !== '') return text;
  }
  return undefined;
}

/** wire FAILED 终态 metadata → typed failure（errorCode/errorMessage 事实源） */
export function buildRunFailure(metadata: Record<string, unknown>): A2aRunFailure {
  const errorCode = typeof metadata.errorCode === 'string' ? metadata.errorCode : undefined;
  const errorMessage = typeof metadata.errorMessage === 'string' ? metadata.errorMessage : undefined;
  return {
    kind: 'rpc',
    reason: 'run_failed',
    ...(errorCode !== undefined ? { errorCode } : {}),
    rpcError: {
      code: RUN_FAILED_RPC_CODE,
      message: errorMessage ?? 'a2a run failed',
    },
  };
}

