import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export const ORG_USER_ENDPOINTS = {
  user: '/openapi/v1/org/user',
} as const;

/**
 * /openapi/v1/org/user 返回的当前登录用户组织信息。
 * 成功信封：{ code: 200000, message: 'OK', data, request_id }。
 */
export interface OrgUserDto {
  user_id: string;
  username: string;
  display_name: string;
  full_name: string;
  tenant: string;
  dept_no: string;
  /** 完整部门名称，形如「蚂蚁集团-大安全-大安全技术部-AI基础设施-全网巡检」。 */
  dept_name: string;
  /** 斜杠分隔的部门编码路径，形如「00001/36822/A0766/52146/A4195/86324」。 */
  dept_path: string;
}

/** 查询当前登录用户的组织信息（协作权限页 currentUser 数据源）。 */
export function getOrgUser(signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<OrgUserDto>>(ORG_USER_ENDPOINTS.user, {
    method: 'GET',
    // 该接口返回当前会话用户，凭 IAM_TOKEN cookie 鉴权，无需 user_id
    injectUserId: false,
    signal,
  });
}
