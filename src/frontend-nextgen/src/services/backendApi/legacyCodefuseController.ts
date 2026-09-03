import { backendRequest } from './httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from './types';

export interface CodefuseTokenResponse {
  success?: boolean;
  bot_id?: string;
  provider?: string;
  message?: string;
}

export interface CallerCodefuseAuthResponse {
  success?: boolean;
  data?: {
    bot_id?: string;
    caller?: string;
    authorized?: boolean;
    revoked?: boolean;
  };
  message?: string;
}

export interface CodefuseModelDto extends BackendUnknownRecord {
  id?: string;
  model?: string;
  model_id?: string;
  name?: string;
  display_name?: string;
  provider?: string;
  runtime?: string;
  description?: string;
  caller_unauthorized?: boolean;
  engine?: string[] | string;
  models?: CodefuseModelDto[];
  visible?: boolean;
  modelName?: string;
  displayName?: string;
}

export function setCodefuseToken(botId: string, token: string) {
  return backendRequest<CodefuseTokenResponse>(`/api/aicoding/bots/${encodeURIComponent(botId)}/codefuse/auth`, {
    method: 'PUT',
    data: { token },
    operation: 'codefuse-auth',
    target: 'legacy-agentclaw',
  });
}

export function setCallerCodefuseAuth(botId: string, token: string) {
  return backendRequest<CallerCodefuseAuthResponse>(
    `/api/aicoding/bots/${encodeURIComponent(botId)}/caller/codefuse/auth`,
    { method: 'PUT', data: { token }, operation: 'caller-codefuse-auth', target: 'legacy-agentclaw' },
  );
}

export function getCallerCodefuseAuth(botId: string) {
  return backendRequest<CallerCodefuseAuthResponse>(
    `/api/aicoding/bots/${encodeURIComponent(botId)}/caller/codefuse/auth`,
    { method: 'GET', operation: 'caller-codefuse-auth-status', target: 'legacy-agentclaw' },
  );
}

export function revokeCallerCodefuseAuth(botId: string) {
  return backendRequest<CallerCodefuseAuthResponse>(
    `/api/aicoding/bots/${encodeURIComponent(botId)}/caller/codefuse/auth`,
    { method: 'DELETE', operation: 'caller-codefuse-revoke', target: 'legacy-agentclaw' },
  );
}

/**
 * 创建 Bot 时尚无 botId，沿用老版 CodeFuse 公共模型目录接口按用户工号查询。
 * URL 由 internal capability 注入，避免内部域名进入 Open Core。
 */
export function listCodefuseModelsForUser(userNo: string, modelsUrl: string) {
  return backendRequest<
    | CodefuseModelDto[]
    | { models?: CodefuseModelDto[]; data?: CodefuseModelDto[] }
    | BackendApiEnvelope<CodefuseModelDto[] | { items?: CodefuseModelDto[]; models?: CodefuseModelDto[] }>
  >(modelsUrl, {
    method: 'GET',
    headers: { 'X-AGENT-USER': userNo, 'Content-Type': 'application/json' },
    operation: 'codefuse-models',
    retryOnTransient: true,
  });
}
