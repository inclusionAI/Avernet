export type BotFriendPlatform = 'teamclaw' | 'third_party';

const TEAMCLAW_BOT_ID = /^[^:]+:[^:]+$/;
const THIRD_PARTY_BOT_ID = /^bot_[^:]+$/;

/** 根据好友 Bot UUID 的稳定形态判断平台来源；未知格式不猜测。 */
export function resolveBotFriendPlatform(botUuid: string): BotFriendPlatform | null {
  const normalized = botUuid.trim();
  if (TEAMCLAW_BOT_ID.test(normalized)) return 'teamclaw';
  if (THIRD_PARTY_BOT_ID.test(normalized)) return 'third_party';
  return null;
}
