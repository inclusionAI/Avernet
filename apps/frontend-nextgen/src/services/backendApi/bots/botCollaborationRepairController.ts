import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
import { userScopedParams } from './botController';

// backend 修复报告固定三项（ownership / profile / managers）；caller_config 于 #3663 移出修复范围。
export type RepairComponent = 'ownership' | 'profile' | 'managers';
export type RepairItemStatus = 'succeeded' | 'failed' | 'unknown' | 'skipped_dependency';

export interface CollaborationRepairReport {
  bot_id: string;
  status: 'succeeded' | 'partial_failed' | 'failed';
  items: Array<{ component: RepairComponent; status: RepairItemStatus; error_code: string | null }>;
}

/**
 * 发起当前认证 Human 对某个 Backend Bot 的协作信息修复。
 * `ownerUserId` 必须是当前认证身份；Backend 仍执行最终 Owner 授权。
 * 合同要求空 body（不发送 data/rawBody）且禁止自动重试。
 */
export function repairBotCollaboration(botId: string, ownerUserId: string) {
  return backendRequest<BackendApiEnvelope<unknown>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/collaboration/repair`,
    {
      method: 'POST',
      params: userScopedParams({ user_id: ownerUserId }),
      retryOnTransient: false,
      operation: 'repair-bot-collaboration',
    },
  );
}
