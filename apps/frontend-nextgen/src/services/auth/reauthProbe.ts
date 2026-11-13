/**
 * ace-gateway 登录态探活（重登恢复感知主干，ace-gateway-popup-reauth D4）。
 *
 * **正证据制（真机回归修正）**：最初判据「响应不再是 ACE 拦截体 ⇒ 已恢复」为负向证据，
 * 真机上被证伪——探活请求可能不经 ACE 拦截即返回 200 业务体/HTML 兜底页，导致登录过期后
 * 首轮探活秒判已登录、Modal 闪关并弹出假"登录已恢复"。判据收敛为：
 * - `unauthenticated`（维持等待）：ACE 登录拦截体（附带网关本次下发的新鲜登录链接），
 *   或双方言未登录业务信封（`isEnvelopeUnauthenticated`，网关放行但后端判定未登录）；
 * - `logged-in`（确认恢复，唯一收口依据）：HTTP 2xx 且响应体为 JSON 对象、且不属于上述
 *   未登录形态——即"确实以登录身份拿到了业务层响应"的肯定性证据；
 * - `unknown`（维持等待，不误判）：其余一切——非 2xx、非 JSON（HTML 兜底页/代理错误页）、
 *   JSON 解析失败、网络异常。
 *
 * 探活目标选**登录才可见**的组织信息接口 `/openapi/v1/org/user`（当前身份注入工号）而非
 * 公开可匿名访问的通用列表接口——后者在真机上存在未经 ACE 拦截直接返回业务体的情况。
 *
 * 分层约束：本模块属 Service 层（放在 services/auth 而非 services/backendApi——后者按
 * TC-G002 属 API Controller 层，Hook 不可直接 import），不碰 store setter / toast / DOM；
 * 副作用（弹窗收口、提示）由组件层消费返回值完成。
 */
import { getCapabilities } from '@/capabilities';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { normalizeOpenApiUserId } from '@/domain/userIdentity';
import { extractLoginUrl, isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import { ORG_USER_ENDPOINTS } from '@/services/backendApi/org/orgUserController';
import { isEnvelopeUnauthenticated } from '@/services/backendApi/types';

export type GatewayReauthProbeResult =
  /** 肯定性已登录证据：2xx + JSON 对象 + 非 ACE 拦截体/未登录信封。 */
  | { state: 'logged-in' }
  /** 未恢复：ACE 拦截体（附带新鲜 pubLogin 链接，供调用方刷新待用 url——aceToken 短时效，D6）或未登录业务信封。 */
  | { state: 'unauthenticated'; loginUrl?: string }
  /** 不可判定（非 2xx / 非 JSON / 网络异常）：维持等待，绝不据此收口。 */
  | { state: 'unknown' };

/** 探活 URL：当前身份的工号注入 query（无身份时退化为无参——正证据制仍兜底）。 */
function resolveProbeUrl(): string {
  const identity = getCapabilities().getCurrentOpenApiUserId({
    activeIdentityId: useWorkspaceStore.getState().activeIdentityId,
  });
  const userId = identity.status === 'available' ? normalizeOpenApiUserId(identity.value) : '';
  return userId
    ? `${ORG_USER_ENDPOINTS.user}?user_id=${encodeURIComponent(userId)}`
    : ORG_USER_ENDPOINTS.user;
}

/** 探测当前网关登录态。三态判定见模块注释（正证据制）。 */
export async function probeGatewaySession(): Promise<GatewayReauthProbeResult> {
  try {
    const response = await fetch(resolveProbeUrl(), {
      method: 'GET',
      credentials: 'include',
    });
    const body: unknown = await response.json().catch(() => null);
    if (isAceLoginResponse(body)) {
      return { state: 'unauthenticated', loginUrl: extractLoginUrl(body) };
    }
    if (isEnvelopeUnauthenticated(body)) {
      return { state: 'unauthenticated' };
    }
    if (response.ok && typeof body === 'object' && body !== null) {
      return { state: 'logged-in' };
    }
    // 非 2xx / 非 JSON（HTML 兜底页等）：不可判定,维持等待,下一轮再探。
    return { state: 'unknown' };
  } catch {
    return { state: 'unknown' };
  }
}
