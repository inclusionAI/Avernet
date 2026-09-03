import { backendRequest } from './httpClient';
export interface YuquePrecheckSource {
  url: string;
  urlType?: string;
  status?: string;
  docCount?: number | null;
  reason?: string;
}
export interface YuquePrecheckWarning {
  type: string;
  message?: string;
  url?: string;
  estimatedDocCount?: number;
}
export async function verifyYuqueBinding(body: { url: string; team_token: string }) {
  return backendRequest<{
    success: boolean;
    data: { bound: boolean; login: string; namespace: string } | null;
    error: string | null;
  }>('/api/v1/yuque/verify', {
    method: 'POST',
    data: body,
    injectUserId: false,
    operation: 'verify-yuque-binding',
    target: 'legacy-agentclaw',
  });
}
export async function precheckYuqueInit(body: {
  yuqueUrls: Array<{ url: string; teamToken?: string }>;
  maxDocsWarningThreshold?: number;
  allowTeamYuqueExpand?: boolean;
}) {
  return backendRequest<{
    success: boolean;
    errorMsg?: string;
    data?: {
      passed: boolean;
      estimatedDocCount: number;
      warnings: YuquePrecheckWarning[];
      sources: YuquePrecheckSource[];
    };
  }>('/aixcore/memoryos/init/yuque/precheck', {
    method: 'POST',
    data: body,
    injectUserId: false,
    operation: 'precheck-yuque-init',
  });
}
