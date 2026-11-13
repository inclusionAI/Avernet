/**
 * Turns a gateway refusal into what the user should read and do next.
 *
 * The gateway distinguishes three cases that must not collapse into one
 * "提交失败": a caller error that is fixable before retrying, a caller error
 * that is fixable by choosing another identity, and an outcome that is simply
 * unknown — where retrying blindly risks a duplicate run.
 */
import { AgentswarmRequestError, type AgentswarmErrorKind } from './http';

export interface SubmitRefusal {
  kind: AgentswarmErrorKind;
  /** What happened, in terms the user can act on. */
  message: string;
  /** Whether resubmitting the same payload can help. */
  retryable: boolean;
  /** Whether a run may already exist and must be verified first. */
  verifyBeforeRetry: boolean;
}

const MESSAGES: Partial<Record<AgentswarmErrorKind, string>> = {
  identity_source_missing: '请选择身份 Bot：包含 Bot 类型 Agent 的运行必须指定身份 Bot',
  identity_source_invalid: '所选 Bot 不能作为身份使用（不是你的 Bot，或尚未取得护照），请改选其他 Bot',
  not_found: '运行不存在，可能已被清理，请刷新列表确认',
};

export function explainSubmitRefusal(reason: unknown): SubmitRefusal {
  if (reason instanceof AgentswarmRequestError) {
    if (reason.kind === 'unconfirmed') {
      return {
        kind: reason.kind,
        message: `${reason.message}；结果可能尚未确认，请先刷新核实，不要重复提交`,
        retryable: false,
        verifyBeforeRetry: true,
      };
    }
    return {
      kind: reason.kind,
      message: MESSAGES[reason.kind] ?? reason.message,
      retryable: reason.kind !== 'refused',
      verifyBeforeRetry: false,
    };
  }
  // A validation refusal raised before any request was sent: nothing exists yet.
  if (reason instanceof Error) {
    return { kind: 'refused', message: reason.message, retryable: true, verifyBeforeRetry: false };
  }
  return { kind: 'unknown', message: '提交失败，请稍后重试', retryable: true, verifyBeforeRetry: false };
}