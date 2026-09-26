import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export const ORG_DEPT_ENDPOINTS = {
  dept: '/openapi/v1/org/dept',
} as const;

export interface ListOrgDeptParams {
  /** 搜索关键词（必传）。 */
  keyword: string;
}

/**
 * /openapi/v1/org/dept 返回的部门候选项。
 * 成功信封：{ code: 200000, message: 'OK', data: OrgDeptDto[], request_id }，data 为数组（非分页）。
 */
export interface OrgDeptDto {
  dept_no: string;
  /** 完整部门名称，形如「蚂蚁集团-大安全」。 */
  dept_name: string;
  /** 斜杠分隔的部门编码路径，形如「00001/36822/A0766」。 */
  dept_path: string;
}

/** 按关键词搜索部门（keyword 必传）。 */
export function listOrgDepts(params: ListOrgDeptParams, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<OrgDeptDto[]>>(ORG_DEPT_ENDPOINTS.dept, {
    method: 'GET',
    params: { keyword: params.keyword },
    injectUserId: false,
    signal,
  });
}
