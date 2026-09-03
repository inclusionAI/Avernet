import { isEnvelopeUnauthenticated } from '@/services/backendApi/types';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';

/**
 * 未登录失败的统一处置 decisions(两请求通道共用:通道 A `requestConfig` / 通道 B `backendRequest`)。
 * - `login-prompt-silent`:失败本身即「未登录」,登记弹窗信号(单飞)后静默上抛,不投递默认错误 toast
 *   ——未登录 UX 的唯一出口是 `ExternalLoginPromptModal`(spec: external-oauth-login「未登录静默与统一登录处置」)。
 * - `silent`:已确认未登录(`externalAuthStore.status==='unauthenticated'`)后的其余业务失败:登录前的接口错误对用户
 *   是噪音,按需求忽略(静默上抛,不提示);仍照常 throw 让调用方感知失败。
 * - `default`:既有路径(投递默认提示,由顶层观察者兜底发起)。
 * 判定仅限 `oauth-provider` 策略;`ace-gateway`(内部 ACE 反应式)一律 `default`,行为不变。
 */
export type AuthFailureDisposition = 'login-prompt-silent' | 'silent' | 'default';

/**
 * 判定一次接口失败在登录维度的处置方式。
 * 未登录判定口径(不再依赖 `data.error_code==='unauthenticated'` 的单一形态):
 * - HTTP 401(任意响应体——语义即未登录);
 * - 信封体未登录(双方言并集):`data.error_code==='unauthenticated'`,或 `code` 落 401 段
 *   (python 6 位 401000–401999 / BCS 5 位 40100–40199,兼容网关误包 HTTP 200 的形态)。
 * 命中未登录时本函数先经 `loginRedirectStore.requestPrompt()` 单飞登记弹窗信号(幂等,重复调用 no-op),
 * 调用方只需按返回值决定「静默上抛还是默认提示」。
 */
export function resolveAuthFailureDisposition(input: { status?: number; data?: unknown }): AuthFailureDisposition {
  if (useLoginStrategyStore.getState().loginStrategy !== 'oauth-provider') return 'default';
  if (input.status === 401 || isEnvelopeUnauthenticated(input.data)) {
    useLoginRedirectStore.getState().requestPrompt();
    return 'login-prompt-silent';
  }
  if (useExternalAuthStore.getState().status === 'unauthenticated') return 'silent';
  return 'default';
}
