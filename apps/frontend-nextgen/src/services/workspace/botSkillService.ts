/** botSkillService — 查询 Bot 的 Skill 列表。 */
import { listBotSkillsByBot, type BotSkillDto } from '@/services/backendApi/bots/botSkillController';
import { resolveUserId, type ChatBotView } from './botSessionService';
import type { DomainError, DomainResult } from './identityService';

export interface BotSkillView {
  skillId: string;
  name: string;
  active: boolean;
  description: string;
  category: string;
}

function toDomainError(e: unknown): DomainError {
  const msg = e instanceof Error ? e.message : 'Skill 列表请求失败';
  return { code: 'BOT_SKILL_FAILED', friendlyMessage: msg, canRetry: true };
}

function toView(s: BotSkillDto): BotSkillView {
  return {
    skillId: s.skill_id,
    name: s.name,
    active: s.active,
    description: s.description ?? '',
    category: s.category ?? 'general',
  };
}

export const botSkillService = {
  async listSkills(bot: ChatBotView, userId: string, page = 1, pageSize = 50): Promise<DomainResult<BotSkillView[]>> {
    try {
      const params = { user_id: resolveUserId(userId), owner_id: bot.ownerId, page, page_size: pageSize };
      const res = await listBotSkillsByBot(bot.realBotId, params);
      const items = res.data?.items ?? [];
      return { ok: true, data: items.map(toView) };
    } catch (err) {
      return { ok: false, error: toDomainError(err) };
    }
  },
};
