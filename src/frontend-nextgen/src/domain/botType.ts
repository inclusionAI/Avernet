/** Bot 列表对外展示使用的 bot_type 枚举。 */
export type BotType = 'personal' | 'service' | 'desktop';

export const BOT_TYPE_LABEL: Record<BotType, string> = {
  personal: '个人 Bot',
  service: '服务 Bot',
  desktop: '桌面 Bot',
};

export function getBotTypeLabel(botType?: string): string | undefined {
  if (!botType) return undefined;
  return BOT_TYPE_LABEL[botType as BotType];
}
