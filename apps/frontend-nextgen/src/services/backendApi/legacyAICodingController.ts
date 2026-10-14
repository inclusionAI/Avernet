import { getCapabilities } from '@/capabilities';
import { backendRequest } from './httpClient';

export interface WorkflowItem {
  name: string;
  description?: string;
  category?: string;
  domain?: string;
  domain_name?: string;
  path: string;
  title?: string;
  desc?: string;
  tags?: string[];
}
export type CodeRepositoryTenant = 'ant' | 'xiaojin';

export interface CodeRepositorySearchOptions {
  /** AbortSignal，用于取消请求 */
  signal?: AbortSignal;
  /** 仓库租户：ant = 主站 AntCode，xiaojin = 消金代码平台；缺省按主站处理 */
  tenant?: CodeRepositoryTenant;
}

export interface AntCodeProject {
  name: string;
  web_url: string;
  description?: string;
  accessLevel?: number;
}
interface WorkflowResponse {
  success?: boolean;
  data?: WorkflowItem[];
}
interface AntCodeRaw {
  name?: string;
  name_with_namespace?: string;
  path_with_namespace?: string;
  description?: string | null;
  access_level?: number | string;
}
interface AntCodeResponse {
  list?: AntCodeRaw[];
}

export async function getWorkflows(): Promise<WorkflowItem[]> {
  const response = await backendRequest<WorkflowResponse>('/api/aicoding/workflows', {
    method: 'GET',
    retryOnTransient: true,
    operation: 'load-aicoding-workflows',
    target: 'legacy-agentclaw',
  });
  return response?.success && Array.isArray(response.data) ? response.data : [];
}

/** 旧版实际使用 AntCode webapi；TeamClaw 通过 backendRequest 保留同一协议，不引入旧仓库请求层。 */
export async function searchAntCodeProjects(
  query: string,
  options?: CodeRepositorySearchOptions,
): Promise<AntCodeProject[]> {
  const resources = getCapabilities().getAgentCodingInternalResources().value;
  const tenant: CodeRepositoryTenant = options?.tenant === 'xiaojin' ? 'xiaojin' : 'ant';
  const projectsApiUrl = tenant === 'xiaojin' ? resources.xiaojinCodeProjectsApiUrl : resources.antCodeProjectsApiUrl;
  const projectBaseUrl = tenant === 'xiaojin' ? resources.xiaojinCodeProjectBaseUrl : resources.antCodeProjectBaseUrl;
  if (!projectsApiUrl || !projectBaseUrl) return [];

  const response = await backendRequest<AntCodeResponse>(projectsApiUrl, {
    method: 'GET',
    params: { search: query, page: 1, per_page: 20 },
    signal: options?.signal,
    injectUserId: false,
    operation: 'search-antcode-projects',
  });
  return (response?.list ?? [])
    .filter((item) => item.path_with_namespace && item.name)
    .map((item) => ({
      name: item.name!,
      web_url: `${projectBaseUrl}/${item.path_with_namespace}`,
      description: item.description ?? undefined,
      accessLevel: typeof item.access_level === 'number' ? item.access_level : Number(item.access_level),
    }));
}
