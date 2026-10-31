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

/** 对话「任务」入口依赖的意图识别 Skill。 */
export const TASK_INTENT_SKILL_NAME = 'task-intent-recognition';

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

function hasSkill(skills: BotSkillView[], name: string): boolean {
  return skills.some((skill) => skill.active && (skill.name === name || skill.skillId === name));
}

export const botSkillService = {
  async listSkills(bot: ChatBotView, userId: string, page = 1, pageSize = 50): Promise<DomainResult<BotSkillView[]>> {
    return botSkillService.listSkillsForBotId(bot.realBotId, userId, bot.ownerId, page, pageSize);
  },
  async listSkillsForBotId(
    botId: string,
    userId: string,
    ownerId?: string,
    page = 1,
    pageSize = 50,
  ): Promise<DomainResult<BotSkillView[]>> {
    try {
      const res = await listBotSkillsByBot(botId, {
        user_id: resolveUserId(userId),
        owner_id: ownerId,
        page,
        page_size: pageSize,
      });
      const items = res.data?.items ?? [];
      return { ok: true, data: items.map(toView) };
    } catch (err) {
      return { ok: false, error: toDomainError(err) };
    }
  },
  hasTaskIntentSkill(skills: BotSkillView[]): boolean {
    return hasSkill(skills, TASK_INTENT_SKILL_NAME);
  },
};
