import type { EditLockDto } from '@/services/backendApi/bots/botEditorController';
import { sendRoutineRequest } from './http';
import type { RoutineContext, RoutineScope } from './types';

// 编辑锁只约束服务 Bot 的协作编辑。个人 Bot 与老前端 Cron 页口径一致：不查锁、不被锁门控。
export async function loadRoutineLock(
  scope: RoutineScope,
  context: Pick<RoutineContext, 'bot_type' | 'owner_id'>,
): Promise<EditLockDto> {
  if (context.bot_type !== 'service') throw new Error('个人 Bot 无编辑锁，不查询锁状态');
  if (!scope.userId || !context.owner_id) throw new Error('Bot 归属或当前登录身份尚未就绪');
  return sendRoutineRequest<EditLockDto>(`/openapi/v1/bots/${encodeURIComponent(scope.botId)}/edit-lock`, {
    method: 'GET',
    params: { user_id: scope.userId, owner_id: context.owner_id },
  });
}
