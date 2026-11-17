import { SessionRecoveryError } from './errors.js';
import type { RecoveryEffect, RecoveryPhase, RecoveryTaskStatus } from './task-contracts.js';
export type RecoveryTaskListQuery = { page: number; pageSize: number; botId: string; env: string; status: string };
export type RecoveryTaskListItem = { taskId: string; botId: string; entityId: string; env: string; sessionKey: string;
  status: RecoveryTaskStatus; phase: RecoveryPhase; effect: RecoveryEffect; createdAt: number | string };
export type RecoveryTaskListPage = { items: RecoveryTaskListItem[]; page: number; pageSize: number; total: number };
export function parseRecoveryTaskListQuery(input: Record<string, unknown>): RecoveryTaskListQuery {
  const page = Number(input.page ?? 1), pageSize = Number(input.pageSize ?? 20);
  const botId = String(input.botId ?? '').trim(), env = String(input.env ?? ''), status = String(input.status ?? '');
  if (Object.keys(input).some(key => !['page', 'pageSize', 'botId', 'env', 'status'].includes(key))
    || !Number.isSafeInteger(page) || page < 1 || page > 100000 || !Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 50
    || botId.length > 255 || !['', 'pre', 'prod', 'dev', 'gray'].includes(env) || !['', 'running', 'waiting_owner', 'waiting_effect', 'completed', 'failed', 'canceled'].includes(status)) {
    throw new SessionRecoveryError(422, 'invalid_session_recovery', '任务查询条件无效');
  }
  return { page, pageSize, botId, env, status };
}
