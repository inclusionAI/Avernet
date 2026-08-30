import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

/** Skill DTO — 对应 openapi schema Skill。 */
export interface BotSkillDto {
  skill_id: string;
  name: string;
  active: boolean;
  category?: string | null;
  description?: string | null;
  tags?: string[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BotRequestParams {
  user_id: string;
  owner_id?: string;
}

export const BOT_SKILL_ENDPOINTS = {
  list: '/openapi/v1/bots/skills',
  upload: '/openapi/v1/bots/skills/upload',
  detail: (skill_id: string) => `/openapi/v1/bots/skills/${skill_id}`,
  activate: (skill_id: string) => `/openapi/v1/bots/skills/${skill_id}/activate`,
  deactivate: (skill_id: string) => `/openapi/v1/bots/skills/${skill_id}/deactivate`,
  listByBot: (bot_id: string) => `/openapi/v1/bots/${bot_id}/skills`,
};
// 查询 Skill 列表（全局）。
export function listBotSkills(params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotSkillDto>>>(BOT_SKILL_ENDPOINTS.list, {
    method: 'GET',
    params,
  });
}
// 查询某个 Bot 的 Skill 列表（按 bot_id）。
export function listBotSkillsByBot(botId: string, params: BotRequestParams & { page?: number; page_size?: number }) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotSkillDto>>>(BOT_SKILL_ENDPOINTS.listByBot(botId), {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}
// 激活 Skill。
export function activateBotSkill(skill_id: string) {
  return backendRequest<BackendApiEnvelope<BotSkillDto>>(BOT_SKILL_ENDPOINTS.activate(skill_id), { method: 'POST' });
}
// 停用 Skill。
export function deactivateBotSkill(skill_id: string) {
  return backendRequest<BackendApiEnvelope<BotSkillDto>>(BOT_SKILL_ENDPOINTS.deactivate(skill_id), { method: 'POST' });
}
