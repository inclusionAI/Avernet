import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';

/**
 * 登录确认前是否静默「业务层自管的自动加载失败 toast」。
 *
 * 背景:请求层(`requestConfig` / `httpClient`)已用 `resolveAuthFailureDisposition`
 * 在未登录时静默错误投递。但部分 Hook 在挂载自动加载后于 then/catch 自行 `toast.error`
 * (绕过统一处置通道),这类提示在登录确认前对未登录用户是噪音——未登录的统一 UX 由
 * `ExternalLoginPromptModal` 承担,不该再叠一条"加载可协作身份失败"之类。
 *
 * 与请求层同口径:仅 `oauth-provider` 策略生效;`ace-gateway`(内部 ACE 反应式)保持原行为。
 * 登录确认后(`status === 'authenticated'`)不静默,保留真实故障的用户反馈。
 *
 * @returns true 表示本次业务 toast 应静默(不调用 toast)
 */
export function shouldMuteNonAuthedToast(): boolean {
  if (useLoginStrategyStore.getState().loginStrategy !== 'oauth-provider') return false;
  return useExternalAuthStore.getState().status !== 'authenticated';
}
