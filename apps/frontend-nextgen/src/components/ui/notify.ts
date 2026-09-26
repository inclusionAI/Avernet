// 统一错误/成功提示入口:集中 duration 与可关闭性,并把后端 message(+request_id)透传给用户。
// 规范:docs/design-system/ui-interaction-spec.md §11.5 —— 错误 6s、可手动关、位置右下角;
// 位置/颜色/可关闭由全局 <Toaster>(src/components/ui/sonner.tsx)承载,本 helper 只管文案与停留时长。
//
// 去重(global-error-notify-dedup):当调用方提供稳定 `id`(协议层 toastKey)时开启两层去重——
//  ① 同 id 在 ERROR_COOLDOWN_MS 内重复发起即静默 return(堵风暴/连点);
//  ② 该 id 一并作为 sonner 稳定 id 传入,同 id 后续为更新而非叠加(物理兜底网)。
//  不提供 `id` 的存量调用不参与去重,行为与既有保持一致(向后兼容 notify.test.ts)。
import { toast } from 'sonner';

export interface NotifyErrorOptions {
  /** 稳定的操作分类标题(如「创建团队失败」);提供时以「标题 + 后端 message」双行展示,更可读且保留原文。 */
  title?: string;
  /** 后端 request_id,作为 trace 追加到 description,便于排障关联。 */
  requestId?: string;
  /** 稳定去重/合并键(协议层 toastKey)。提供时开启冷静窗去重 + sonner 稳定 id 合并。 */
  id?: string;
}

const ERROR_DURATION_MS = 6000;
const SUCCESS_DURATION_MS = 4000;
const ERROR_COOLDOWN_MS = 3000;
// id -> 冷静到期时间戳(ms)。仅对显式提供 id 的调用生效;不提供 id 的调用不进表,不参与去重。
const errorCooldown = new Map<string, number>();

function buildDescription(message: string, requestId?: string): string {
  return requestId ? `${message}\ntrace: ${requestId}` : message;
}

/** 命中冷静窗返回 true(同 id 在 ERROR_COOLDOWN_MS 内已发起过);否则登记并返回 false。无 id 永不命中。 */
function registerCooldown(id: string | undefined): boolean {
  if (!id) return false;
  const now = Date.now();
  const expiresAt = errorCooldown.get(id);
  if (expiresAt !== undefined && now < expiresAt) return true; // 冷静窗内,抑制。
  errorCooldown.set(id, now + ERROR_COOLDOWN_MS);
  return false;
}

export interface NotifyErrorFn {
  /** 错误提示:停留 6s。默认单行 = 后端 message;提供 `title` 时双行展示(标题 + 后端 message + 可选 trace)。提供 `id` 时受冷静窗去重保护。 */
  (message: string, opts?: NotifyErrorOptions): void;
  /** 显式静默:把给定 id 纳入冷静窗,使随后该 id 的 notifyError 被抑制(供 Hook 取消协议层默认提示)。 */
  cancel: (id: string) => void;
}

/**
 * 错误提示:停留 6s。默认单行 = 后端 message;提供 `title` 时双行展示(标题 + 后端 message + 可选 trace)。
 * 提供 `id` 时:同 id 在 3s 冷静窗内重复发起即静默 return;并作为 sonner 稳定 id 传入(同 id 更新而非叠加)。
 */
export const notifyError: NotifyErrorFn = Object.assign(
  (message: string, opts?: NotifyErrorOptions): void => {
    const id = opts?.id;
    if (registerCooldown(id)) return;
    const duration = ERROR_DURATION_MS;
    if (opts?.title) {
      const sonnerOpts: { description: string; duration: number; id?: string } = {
        description: buildDescription(message, opts.requestId),
        duration,
      };
      if (id) sonnerOpts.id = id;
      toast.error(opts.title, sonnerOpts);
      return;
    }
    if (id) {
      toast.error(message, { duration, id });
      return;
    }
    toast.error(message, { duration });
  },
  {
    cancel: (id: string): void => {
      if (!id) return;
      // 把该 id 纳入冷静窗:随后(含协议层观察者兜底)的 notifyError(id) 在窗内被抑制。
      errorCooldown.set(id, Date.now() + ERROR_COOLDOWN_MS);
    },
  },
);

/** 成功提示:停留 4s。 */
export function notifySuccess(message: string): void {
  toast.success(message, { duration: SUCCESS_DURATION_MS });
}
