/**
 * Identity-source options for an Agent Compute run.
 *
 * A run's `bot`-kind agents are provisioned under one identity Bot. The gateway
 * accepts a source only if it is a Bot owned by the caller holding an issued
 * passport — a fact this client cannot see: the Bot list carries no passport
 * status. So this module maps what is known and says what is not.
 */
import type { ChatBotView } from '@/services/workspace/botSessionService';

export interface IdentityOption {
  /** The value submitted as `identity_bot_id`. */
  botId: string;
  displayName: string;
  avatarUrl?: string;
  online: boolean;
  /**
   * Advisory only. The Bot list does not report passport status, so a selectable
   * option can still be refused by the gateway with 422 at submit.
   */
  selectable: boolean;
  /** Why the option cannot be selected, when it cannot be. */
  disabledReason?: string;
}

/**
 * Bots the caller can reasonably pick. A bot is selectable here when it is
 * usable at all; passport issuance is verified server-side at submit.
 */
export function toIdentityOptions(bots: readonly ChatBotView[]): IdentityOption[] {
  return bots.map((bot) => {
    const botId = (bot.realBotId || bot.botId || '').trim();
    const missingStatus = !bot.status || bot.status === 'ACTIVE';
    const usable = Boolean(botId) && missingStatus;
    return {
      botId,
      displayName: bot.displayName || botId,
      avatarUrl: bot.avatarUrl,
      online: Boolean(bot.online),
      selectable: usable,
      disabledReason: usable ? undefined : bot.status ? `Bot 状态为 ${bot.status}，暂不可用` : 'Bot 标识缺失',
    };
  });
}

/**
 * The Bot list cannot report passport status, so this always explains the
 * remaining risk rather than promising the choice will be accepted.
 */
export const IDENTITY_PICKER_HINT =
  'Bot 类型 Agent 将继承所选 Bot 的身份运行；该 Bot 必须属于你且已取得护照，否则提交会被拒绝。';

export function findIdentityOption(
  options: readonly IdentityOption[],
  botId: string | null | undefined,
): IdentityOption | undefined {
  if (!botId) return undefined;
  return options.find((option) => option.botId === botId);
}

/**
 * The Bot to preselect when the caller has no explicit preference.
 *
 * An existing choice always wins — a re-entered draft must not be reassigned
 * mid-edit. Otherwise the first usable Bot is picked, ordered so an offline one
 * never beats an online one: the gateway refuses a Bot without a passport, and
 * while this client cannot see passports it can at least prefer a Bot that is
 * demonstrably alive.
 */
export function defaultIdentityBotId(options: readonly IdentityOption[]): string | undefined {
  // `botId` is compared trimmed: a whitespace-only id is not a Bot, and
  // returning it verbatim would seed the draft with a value the gateway rejects.
  const usable = options.filter((option) => option.selectable && option.botId.trim());
  return (usable.find((option) => option.online) ?? usable[0])?.botId.trim();
}