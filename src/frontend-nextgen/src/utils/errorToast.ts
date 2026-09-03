// 守卫式错误提示 helper(global-error-notify-dedup D4):给存量/新 Hook 一个一行收敛点,
// 依据错误对象是否已被协议层默认提示(alreadyHandled)/是否有稳定去重键(toastKey)决定是否发起 notifyError,
// 避免与协议层默认提示重复。
//
// 协议层投递的失败会在 throw 的错误对象上挂 `alreadyHandled: true` + `toastKey`(见通道 B/A 实现)。本 helper:
// - 已 `alreadyHandled` → 跳过(默认提示已由观察者发起,不重复);
// - 有 `toastKey` → 经 `notifyError(message,{ id: toastKey })` 发起,同 id 被冷静窗/sonner 合并去重;
// - 否则 → 直接 `notifyError(message)`(向后兼容无去重键的调用,如纯前端抛出的 Error)。
import { notifyError } from '@/components/ui/notify';

interface ReportedErrorLike {
  alreadyHandled?: boolean;
  toastKey?: string;
  message?: unknown;
}

export interface ReportErrorOptions {
  /** 稳定的操作分类标题(如「创建团队失败」);透传给 notifyError 双行展示。 */
  title?: string;
}

function readMessage(err: unknown): string {
  const message = (err as ReportedErrorLike | null)?.message;
  return typeof message === 'string' && message ? message : '操作失败，请重试';
}

/**
 * 幂等式错误提示:已被协议层默认提示的错误跳过(防重复);否则发起一次 notifyError(可带 title)。
 * 存量 `toast.error(err.message)` 调点的等价收敛目标:把裸 toast 收口到统一入口并受去重保护。
 */
export function safeReportError(err: unknown, opts?: ReportErrorOptions): void {
  const e = (err ?? {}) as ReportedErrorLike;
  if (e.alreadyHandled) return;
  const message = readMessage(err);
  if (e.toastKey) {
    notifyError(message, { id: e.toastKey, title: opts?.title });
    return;
  }
  notifyError(message, { title: opts?.title });
}
