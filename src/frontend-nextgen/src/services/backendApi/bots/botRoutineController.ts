import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';
import { userScopedParams } from './botController';

export type BotRoutineDto = BackendUnknownRecord;
export type BotRoutineRunDto = BackendUnknownRecord;
export const BOT_ROUTINE_ENDPOINTS = {
  list: (bot_id: string) => `/openapi/v1/bots/${bot_id}/routines`,
  detail: (bot_id: string, routine_id: string) => `/openapi/v1/bots/${bot_id}/routines/${routine_id}`,
  run: (bot_id: string, routine_id: string) => `/openapi/v1/bots/${bot_id}/routines/${routine_id}/run`,
  runs: (bot_id: string, routine_id: string) => `/openapi/v1/bots/${bot_id}/routines/${routine_id}/runs`,
};
// 查询定时任务列表。
export function listBotRoutines(bot_id: string, params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotRoutineDto>>>(BOT_ROUTINE_ENDPOINTS.list(bot_id), {
    method: 'GET',
    params: userScopedParams(params),
  });
}
// 查询单个定时任务详情。
export function getBotRoutine(bot_id: string, routine_id: string) {
  return backendRequest<BackendApiEnvelope<BotRoutineDto>>(BOT_ROUTINE_ENDPOINTS.detail(bot_id, routine_id), {
    method: 'GET',
    params: userScopedParams(),
  });
}
// 创建定时任务。
export function createBotRoutine(bot_id: string, body: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BotRoutineDto>>(BOT_ROUTINE_ENDPOINTS.list(bot_id), {
    method: 'POST',
    params: userScopedParams(),
    data: body,
  });
}
// 运行定时任务。
export function runBotRoutine(bot_id: string, routine_id: string) {
  return backendRequest<BackendApiEnvelope<BotRoutineDto>>(BOT_ROUTINE_ENDPOINTS.run(bot_id, routine_id), {
    method: 'POST',
    params: userScopedParams(),
  });
}
// 查询定时任务运行记录。
export function listBotRoutineRuns(bot_id: string, routine_id: string, params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotRoutineRunDto>>>(
    BOT_ROUTINE_ENDPOINTS.runs(bot_id, routine_id),
    {
      method: 'GET',
      params: userScopedParams(params),
    },
  );
}
