// 统一错误/成功提示入口:集中 duration 与可关闭性,并把后端 message(+request_id)透传给用户。
// 规范:docs/design-system/ui-interaction-spec.md §11.5 —— 错误 6s、可手动关、位置右下角;
// 位置/颜色/可关闭由全局 <Toaster>(src/components/ui/sonner.tsx)承载,本 helper 只管文案与停留时长。
import { toast } from 'sonner';

export interface NotifyErrorOptions {
  /** 稳定的操作分类标题(如「创建团队失败」);提供时以「标题 + 后端 message」双行展示,更可读且保留原文。 */
  title?: string;
  /** 后端 request_id,作为 trace 追加到 description,便于排障关联。 */
  requestId?: string;
}

function buildDescription(message: string, requestId?: string): string {
  return requestId ? `${message}\ntrace: ${requestId}` : message;
}

/**
 * 错误提示:停留 6s。默认单行 = 后端 message;提供 `title` 时双行展示(标题 + 后端 message + 可选 trace)。
 * 双行结构解决两件事:稳定的中文标题提升可读性,后端 message 作为详情透传真实原因。
 */
export function notifyError(message: string, opts?: NotifyErrorOptions): void {
  const duration = 6000;
  if (opts?.title) {
    toast.error(opts.title, { description: buildDescription(message, opts.requestId), duration });
    return;
  }
  toast.error(message, { duration });
}

/** 成功提示:停留 4s。 */
export function notifySuccess(message: string): void {
  toast.success(message, { duration: 4000 });
}
