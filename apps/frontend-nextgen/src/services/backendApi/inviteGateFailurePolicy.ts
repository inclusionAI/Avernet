import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { isEnvelopeSuccessAnyDialect, type BackendApiEnvelope } from './types';

/**
 * 邀请码门禁失败的统一处置决策（与 `resolveAuthFailureDisposition` 对偶；门禁信号源独立于 `loginRedirectStore`）。
 * - `gate-prompt-silent`：oauth-provider 策略下命中后端 gate 专属错误码 → 单飞置 `inviteCodeGateStore.requestPrompt()`
 *   并静默上抛（不投递默认错误 toast，未绑定用户的业务 403 不逐条轰炸；未登录 UX 的唯一出口是 `InviteCodeBindingModal`）。
 * - `default`：ace-gateway 策略一律 default（门禁不激活，既有 403 处置不变）；或非 gate 专属错误码（含通用 'forbidden'、
 *   真实权限拒绝）→ 既有默认提示路径，**不**被当作门禁吞掉。
 */
export type InviteGateDisposition = 'gate-prompt-silent' | 'default';

/**
 * 信封体「邀请码门禁拦截」判定（不依赖 HTTP status，双方言并集兜底）：
 * 后端 gate 失败用专属 `error_code='invite_code_required'`（区别于通用 `'forbidden'`）——这是前端外科手术式识别门禁的支点。
 * 成功信封不算；非对象/null 不算。
 */
function isEnvelopeInviteGateRequired(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false;
  if (isEnvelopeSuccessAnyDialect(data)) return false;
  const env = data as BackendApiEnvelope<unknown> & { data?: { error_code?: unknown } };
  return env.data?.error_code === 'invite_code_required';
}

/**
 * 判定一次接口失败在邀请码门禁维度的处置方式。识别键严格限定 `error_code==='invite_code_required'`，
 * 后端该码唯一用于 gate（源码实证）；HTTP 403 与信封 5 位码 40300 恒随该 error_code 同时下发，故 error_code 即充分条件。
 * Service 层（`httpClient`）调用本函数只置 store 信号，禁止直接 toast / import 组件 / 操作 `window.location`（守分层）。
 */
export function resolveInviteGateDisposition(input: { status?: number; data?: unknown }): InviteGateDisposition {
  if (useLoginStrategyStore.getState().loginStrategy !== 'oauth-provider') return 'default';
  if (!isEnvelopeInviteGateRequired(input.data)) return 'default';
  useInviteCodeGateStore.getState().requestPrompt();
  return 'gate-prompt-silent';
}
