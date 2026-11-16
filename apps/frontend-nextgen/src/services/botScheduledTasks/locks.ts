import type { EditLockDto } from '@/services/backendApi/bots/botEditorController';
import { backendRequest } from '@/services/backendApi/httpClient';
import type { BackendApiEnvelope } from '@/services/backendApi/types';
import { RoutineRequestError, sendRoutineRequest } from './http';
import type { RoutineContext, RoutineScope } from './types';

interface LegacyLockInfo {
  locked: boolean;
  need_lock: boolean;
  has_collaborators: boolean;
  is_owner: boolean;
  lock: { holder_user_id: string; holder_name?: string | null } | null;
}

export async function loadRoutineLock(
  scope: RoutineScope,
  context: Pick<RoutineContext, 'bot_type' | 'owner_id'>,
): Promise<EditLockDto> {
  if (!scope.userId || !context.owner_id) throw new Error('Bot 归属或当前登录身份尚未就绪');
  // The OpenAPI edit-lock endpoint explicitly rejects non-service Bots.
  // Personal Bots reuse the existing general read endpoint, including its need_lock policy.
  if (context.bot_type === 'service')
    return sendRoutineRequest<EditLockDto>(`/openapi/v1/bots/${encodeURIComponent(scope.botId)}/edit-lock`, {
      method: 'GET',
      params: { user_id: scope.userId, entity_id: context.owner_id },
    });

  const response = await backendRequest<BackendApiEnvelope<LegacyLockInfo>>('/api/bot/collaborator/lock/info', {
    method: 'GET',
    params: { bot_id: scope.botId, owner_id: context.owner_id, user_id: scope.userId },
    retryOnTransient: false,
  });
  const info = response.data;
  if (
    response.success !== true ||
    !info ||
    typeof info.locked !== 'boolean' ||
    typeof info.need_lock !== 'boolean' ||
    (info.locked && !info.lock?.holder_user_id)
  )
    throw new RoutineRequestError(response.message || '锁查询未返回有效状态', response.request_id);
  return {
    locked: info.locked,
    need_lock: info.need_lock,
    holder_user_id: info.lock?.holder_user_id,
    holder_name: info.lock?.holder_name,
    has_collaborators: info.has_collaborators,
    is_owner_holder: info.is_owner,
  };
}
