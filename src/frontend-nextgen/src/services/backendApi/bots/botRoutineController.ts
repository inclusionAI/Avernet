import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';
import { userScopedParams } from './botController';

export type BotRoutineDto = BackendUnknownRecord;
export type BotRoutineRunDto = BackendUnknownRecord;
export const BOT_ROUTINE_ENDPOINTS = {
  // owner 级聚合入口：不含 bot_id 字面量，必须声明在 list 之前，避免被 per-bot 通配抢走。
  all: () => `/openapi/v1/bots/routines/all`,
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
// owner 级聚合查询：一次拉取该用户（含协作）全部 Bot 的定时任务，跨 draft/verify/online 阶段，服务端统一分页。
export function listAllRoutines(params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotRoutineDto>>>(BOT_ROUTINE_ENDPOINTS.all(), {
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
