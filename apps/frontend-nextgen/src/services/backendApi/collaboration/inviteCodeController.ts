import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/**
 * 邀请码准入门禁后端接口控制器（BCS bcs-api-http openapi v1 面，5 位码信封）。
 * 端点经后端 gate 中间件白名单放行（未绑定 Human 亦可调用 bind/me），详见
 * add-invite-code-gate design.md Context。调用方身份由 bcs_session 认证载荷解析，
 * 非 user_id query，故 injectUserId=false（对齐 collaborationRegistrationController）。
 */
export const INVITE_CODE_ENDPOINTS = {
  me: '/openapi/v1/collaboration/invite-codes/me',
  bind: '/openapi/v1/collaboration/invite-codes/bind',
} as const;

/** 邀请码绑定状态 DTO（信封 data 形状）。未绑定时 bound_at 缺省。 */
export interface InviteCodeBindingDto {
  bound: boolean;
  bound_at?: number;
}

/** 查询当前登录 Human 的邀请码绑定状态（GET /me）。返回 5 位码信封。 */
export function fetchMyInviteCodeBinding(signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<InviteCodeBindingDto>>(INVITE_CODE_ENDPOINTS.me, {
    method: 'GET',
    injectUserId: false,
    signal,
  });
}

/** 提交邀请码绑定（POST /bind），请求体 `{ code }`。返回 5 位码信封。 */
export function postBindInviteCode(code: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<InviteCodeBindingDto>>(INVITE_CODE_ENDPOINTS.bind, {
    method: 'POST',
    data: { code },
    injectUserId: false,
    signal,
  });
}
