/** botSkillService — 查询 Bot 的 Skill 列表 + 「任务」入口的意图识别 Skill 探针。 */
import { listBotSkillsByBot, type BotSkillDto } from '@/services/backendApi/bots/botSkillController';
import type { ChatBotView } from './botSessionService';
import { resolveBotFriendPlatform } from '@/domain/botFriendPlatform';
import { matchTaskConfig } from '@/services/backendApi/tasks/taskConfigController';
import { isSameHumanIdentity } from '@/domain/userIdentity';
import { unwrapEnvelope } from '@/services/tasks/taskMapper';
import type { DomainError, DomainResult } from './identityService';
import { resolveUserId } from './resolveUserId';

export interface BotSkillView {
  skillId: string;
  name: string;
  active: boolean;
  description: string;
  category: string;
}

/** 对话「任务」入口依赖的意图识别 Skill。 */
export const TASK_INTENT_SKILL_NAME = 'task-intake';

/**
 * task_config 中维护「装了 task-intake 的 bot_uuid 集合」的配置名（spec 2026-11-02）。
 * 维护方（属主/外部服务）通过 POST /collaboration/tasks/config/set 写入 set 类型配置；
 * 非属主调用方无权列他人 Bot 的技能列表，改走 match 探针只回 matched: bool。
 */
export const TASK_INTENT_INSTALLED_BOTS_CONFIG = 'task_skill_installed_bots';

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

/** third_party Bot 不是 Teamclaw 本地 Skill 面，不能用 Skills 列表接口做能力探测。 */
function supportsTaskIntentSkillProbe(botUuid: string): boolean {
  return resolveBotFriendPlatform(botUuid) !== 'third_party';
}

/** Bot 是否属当前用户（compound botId 无 owner 尾 → 视为自有 bot，走属主可见的技能列表）。 */
function isOwnedByCurrentUser(ownerId: string | undefined, currentUserId: string): boolean {
  return ownerId === undefined || isSameHumanIdentity(ownerId, currentUserId);
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
  /**
   * 判定当前 Bot / 协作群 Driver Bot 是否安装可用的 task-intake。
   *
   * - Bot 属当前用户 → 走属主可见的 `/openapi/v1/bots/{bot_id}/skills` 列表再判 `hasTaskIntentSkill`
   *   （自有 bot，可列技能）。
   * - 否则（好友 / 协作群 Driver 等非属主 bot，当前用户无权列他人技能）→ 走 task_config
   *   `/collaboration/tasks/config/match` 探针：传 `config_name=task_skill_installed_bots`、
   *   `value=botUuid`（`bot_id:owner_id` 复合形态），按匹配结果取 `matched`（只回布尔，不回集合本身）。
   */
  async hasTaskIntentSkillForBot(query: {
    realBotId: string;
    botUuid: string;
    ownerId?: string;
    currentUserId: string;
  }): Promise<DomainResult<boolean>> {
    const { realBotId, botUuid, ownerId, currentUserId } = query;
    // DomainResult 是本方法的稳定边界：底层列表接口、match 探针、信封解析或防御性
    // 判断异常都必须收敛为可观测的 error，不向调用方抛出，也不触发全局错误 UI。
    try {
      if (!supportsTaskIntentSkillProbe(botUuid)) {
        return { ok: true, data: false };
      }

      if (isOwnedByCurrentUser(ownerId, currentUserId)) {
        const res = await botSkillService.listSkillsForBotId(realBotId, currentUserId, ownerId, 1, 100);
        if (!res.ok) return { ok: false, error: res.error };
        return { ok: true, data: botSkillService.hasTaskIntentSkill(res.data) };
      }

      const env = await matchTaskConfig({
        config_name: TASK_INTENT_INSTALLED_BOTS_CONFIG,
        value: botUuid,
      });
      const matched = unwrapEnvelope(env);
      return { ok: true, data: Boolean(matched.matched) };
    } catch (err) {
      return { ok: false, error: toDomainError(err) };
    }
  },
};
