import { getCapabilities } from '@/capabilities';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/** 解析 task API 路径前缀；capability 缺省回退内面 /api/v1。 */
function taskApiBase(): string {
  return getCapabilities().getTaskApiBase().value ?? '/api/v1/collaboration/tasks';
}

/**
 * 任务认领 Bot 授权 同源 HTTP 端点（前缀由 capability getTaskApiBase 注入）。
 *
 * 前端只经同源网关调部署态协作 API；浏览器同源自动携带 ant Cookie，网关/Avernet 透传到 secbaas admin。
 * - grant/revoke：body {"bcs_bot_id": <real:entity>}；bcs_bot_id = /mine 返回的 bot.id 原值。
 * 身份取自浏览器 Cookie（injectUserId:false），不经自动注入。
 */
export const TASK_GRANT_ENDPOINTS = {
  grant: 'grant',
  revoke: 'revoke',
};

export interface TaskClaimGrantResultDto {
  bcs_bot_id: string;
  api_key_prefix: string;
  grant_status: 'granted' | 'revoked';
  operator: string;
  gmt_modified: string;
}

/** 开启任务认领：grant 公共 api-key 给目标 Bot；幂等（已 granted 不报错）。 */
export function grantTaskClaim(body: { bcs_bot_id: string }, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<TaskClaimGrantResultDto>>(`${taskApiBase()}/${TASK_GRANT_ENDPOINTS.grant}`, {
    method: 'POST',
    data: body,
    injectUserId: false,
    signal,
  });
}

/** 关闭任务认领：真 revoke（secbaas .../allowed-bots/revoke），置 task_bot_grant.revoked。 */
export function revokeTaskClaim(body: { bcs_bot_id: string }, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<TaskClaimGrantResultDto>>(
    `${taskApiBase()}/${TASK_GRANT_ENDPOINTS.revoke}`,
    {
      method: 'POST',
      data: body,
      injectUserId: false,
      signal,
    },
  );
}
